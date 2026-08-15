"""Command line interface: ``contrail sync`` and ``contrail sources``."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import requests

from contrail import __version__, resync
from contrail.config import DEFAULT_CSV_PATH, Config, ConfigError, load_config
from contrail.emissions import get_provider
from contrail.importers import IMPORTERS, get_importer
from contrail.models import FlightRecord, UnparsedEvent
from contrail.storage import JSONLRawLog, kg_value, normalize_rows, total_kg
from contrail.storage.local_csv import LocalCSVStorage, actual_kg, row_key
from contrail.storage.raw_log import default_path as default_raw_path


def _now():
    """The current instant, in UTC.

    Indirected so tests can pin it: the open/frozen boundary is a comparison
    against now, and a suite that depends on the real clock quietly starts
    failing once its fixture dates fall into the past.
    """
    return datetime.now(timezone.utc)


def _grams_to_kg(grams) -> str:
    """Grams to kilograms, as a string.

    Every value in a row dict is a string, so a freshly built row and one loaded
    back from the CSV compare and aggregate identically.
    """
    return "" if grams is None else str(round(grams / 1000, 3))


def collect(config: Config) -> tuple[list[FlightRecord], list[UnparsedEvent]]:
    """Run every configured source and return what they found."""
    flights: list[FlightRecord] = []
    unparsed: list[UnparsedEvent] = []

    for source_config in config.require_sources():
        type_name = source_config.get("type")
        if not type_name:
            raise ConfigError(f"Source entry is missing a 'type': {source_config!r}")
        importer = get_importer(type_name)()
        print(f"Fetching from {type_name}...")
        for item in importer.fetch(source_config):
            if isinstance(item, FlightRecord):
                flights.append(item)
            else:
                unparsed.append(item)

    return flights, unparsed


def _dedup(items) -> dict:
    """Feed items keyed for lookup, first occurrence winning."""
    seen: dict = {}
    for item in items:
        seen.setdefault(item.key, item)
    return seen


class Reconciliation:
    """What a sync intends to do, worked out before anything is written."""

    def __init__(self):
        self.new_flights: list[FlightRecord] = []
        self.new_unparsed: list[UnparsedEvent] = []
        self.updates: list[tuple[dict, FlightRecord, list[str]]] = []  # row, flight, changed
        self.cancellations: list[dict] = []
        self.restorations: list[dict] = []

    @property
    def repriceable(self) -> list[FlightRecord]:
        return self.new_flights + [flight for _, flight, _ in self.updates]

    def __bool__(self) -> bool:
        return bool(self.new_flights or self.new_unparsed or self.updates or self.cancellations)


def reconcile(existing_rows, flights, unparsed, now) -> Reconciliation:
    """Work out what changes against what is already stored.

    Three outcomes per stored row: unknown to us (new), already departed
    (frozen, never touched), or still upcoming (open, and contrail's to correct).
    """
    plan = Reconciliation()
    by_key = {row_key(row): row for row in existing_rows}

    feed_flights = _dedup(flights)
    # A key that parsed is a key that parsed: never also queue it as unparsed,
    # or it would be written twice under one dedup key and the priced flight
    # discarded. Two UID-less events can hash alike while only one parses.
    feed_unparsed = {
        key: event for key, event in _dedup(unparsed).items() if key not in feed_flights
    }
    feed_keys = set(feed_flights) | set(feed_unparsed)

    # Only an importer that actually returned something this run may have its
    # rows cancelled. Otherwise one silently empty feed — a rotated URL, a
    # source removed from the config — would cancel all of its flights while
    # other sources kept the global guard happy.
    contributing = {item.source for item in list(flights) + list(unparsed)}

    for key, flight in feed_flights.items():
        row = by_key.get(key)
        if row is None:
            plan.new_flights.append(flight)
        elif resync.is_open(row, now):
            changed = resync.differences(row, flight)
            plan.updates.append((row, flight, changed))
            if resync.restored(row):
                plan.restorations.append(row)
        # else: departed, and therefore settled

    for key, event in feed_unparsed.items():
        if key not in by_key:
            plan.new_unparsed.append(event)

    for key, row in by_key.items():
        if key in feed_keys or resync.is_cancelled(row):
            continue
        if row.get("source") not in contributing:
            continue  # that source told us nothing this run, so absence proves nothing
        if resync.can_cancel(row, now):
            plan.cancellations.append(row)

    return plan


def _flight_row(flight: FlightRecord, result, now_iso: str) -> dict:
    row = {
        "sync_timestamp": now_iso,
        "source": flight.source,
        "source_id": flight.source_id,
        "flight_date": flight.flight_date.isoformat(),
        "carrier_code": flight.carrier_code,
        "flight_number": flight.flight_number,
        "operating_carrier_code": flight.operating_carrier_code or "",
        "operating_flight_number": flight.operating_flight_number or "",
        "origin": flight.origin,
        "destination": flight.destination,
        "departure_time": flight.departure_time.isoformat() if flight.departure_time else "",
        "status": "",
        "cabin_class_known": flight.cabin_class or "",
        "emissions_source": result.method if result else "no_data",
        "model_version": (result.model_version if result else "") or "",
        "emissions_data_source": (result.data_source if result else "") or "",
        "contrails_impact": (result.contrails_impact if result else "") or "",
        "distance_km": str(result.distance_km) if result and result.distance_km else "",
        "aircraft_match": (result.aircraft_match if result else "") or "",
        "emissions_kg_first": _grams_to_kg(result.grams_first if result else None),
        "emissions_kg_business": _grams_to_kg(result.grams_business if result else None),
        "emissions_kg_premium_economy": _grams_to_kg(
            result.grams_premium_economy if result else None
        ),
        "emissions_kg_economy": _grams_to_kg(result.grams_economy if result else None),
        "raw_summary": flight.raw.get("summary", ""),
    }
    row["emissions_kg_actual"] = actual_kg(row)
    return row


def _merge_row(row: dict, flight: FlightRecord, result, now_iso: str, changed: bool) -> dict:
    """Fold a fresh reading of an upcoming flight into the row already stored.

    Two things survive regardless of what the feed says. ``cabin_class_known``
    is carried over because no importer can supply it, so overwriting it would
    destroy the only copy. And a worse emissions figure is refused on an
    unchanged flight, so a transient TIM miss can't downgrade a good number —
    unless the flight's details changed, in which case it is a different flight
    and whatever comes back is the truth.
    """
    fresh = _flight_row(flight, result, now_iso)
    merged = {**row, **{field: fresh[field] for field in resync.FEED_FIELDS}}
    merged["status"] = ""  # present in the feed, so not cancelled
    merged["cabin_class_known"] = row.get("cabin_class_known", "")
    # Keep the source text in step with the details, or a rerouted row reads
    # MAD while its raw_summary still says PFO — and that column is the first
    # thing anyone checks when a row looks wrong.
    merged["raw_summary"] = fresh["raw_summary"]

    method = fresh["emissions_source"]
    # An answer carrying no figures at all never overwrites one that has them.
    # TIM returns nothing for a flight it cannot price, and the README tells
    # people to fill exactly those rows in by hand — blanking that is pure loss,
    # not a correction, even when the flight itself changed.
    priced = any(
        fresh[field]
        for field in (
            "emissions_kg_first",
            "emissions_kg_business",
            "emissions_kg_premium_economy",
            "emissions_kg_economy",
        )
    )
    has_figures = any(
        row.get(field)
        for field in (
            "emissions_kg_first",
            "emissions_kg_business",
            "emissions_kg_premium_economy",
            "emissions_kg_economy",
        )
    )
    if (priced or not has_figures) and (
        changed or resync.is_better(method, row.get("emissions_source", ""))
    ):
        for field in (
            "emissions_source",
            "model_version",
            "emissions_data_source",
            "contrails_impact",
            "distance_km",
            "aircraft_match",
            "emissions_kg_first",
            "emissions_kg_business",
            "emissions_kg_premium_economy",
            "emissions_kg_economy",
        ):
            merged[field] = fresh[field]
    merged["emissions_kg_actual"] = actual_kg(merged)

    # The timestamp marks a real change, not merely that a sync looked at the
    # row. Bumping it every run would rewrite the file daily for nothing.
    if merged != {**row, "emissions_kg_actual": merged["emissions_kg_actual"]}:
        merged["sync_timestamp"] = now_iso
    return merged


def _unparsed_row(event: UnparsedEvent, now_iso: str) -> dict:
    partial = event.partial
    flight_date = partial.get("flight_date")
    return {
        "sync_timestamp": now_iso,
        "source": event.source,
        "source_id": event.source_id,
        "flight_date": flight_date.isoformat() if flight_date else "",
        "carrier_code": partial.get("carrier_code") or "",
        "flight_number": partial.get("flight_number") or "",
        "operating_carrier_code": "",
        "operating_flight_number": "",
        "origin": partial.get("origin") or "",
        "destination": partial.get("destination") or "",
        "departure_time": "",
        "status": "",
        "cabin_class_known": "",
        "emissions_source": "unparsed",
        "model_version": "",
        "emissions_kg_first": "",
        "emissions_kg_business": "",
        "emissions_kg_premium_economy": "",
        "emissions_kg_economy": "",
        "emissions_kg_actual": "",
        "raw_summary": event.raw_text,
    }


def _describe(plan: Reconciliation) -> None:
    if plan.new_flights or plan.new_unparsed:
        print(
            f"Found {len(plan.new_flights) + len(plan.new_unparsed)} new flight event(s): "
            f"{len(plan.new_flights)} parsed, {len(plan.new_unparsed)} could not be parsed."
        )
    changed = [u for u in plan.updates if u[2]]
    if changed:
        print(f"{len(changed)} upcoming flight(s) changed in the feed:")
        for row, flight, fields in changed:
            print(
                f"  {row['carrier_code']}{row['flight_number']}: "
                f"{', '.join(fields)} — now {flight.flight_date} "
                f"{flight.origin}->{flight.destination}"
            )
    if plan.restorations:
        print(f"{len(plan.restorations)} cancelled flight(s) reappeared and were restored.")
    if plan.cancellations:
        print(f"{len(plan.cancellations)} upcoming flight(s) left the feed, marking cancelled:")
        for row in plan.cancellations:
            print(f"  {row['flight_date']}  {row['carrier_code']}{row['flight_number']}")


def _dry_run_report(plan: Reconciliation, csv_path: str) -> int:
    print(f"\nDry run — nothing written to {csv_path}, no emissions API calls made.\n")
    for flight in plan.new_flights:
        print(
            f"  NEW       {flight.flight_date}  "
            f"{flight.carrier_code}{flight.flight_number:>5}  "
            f"{flight.origin} -> {flight.destination}  [{flight.source}]"
        )
    for event in plan.new_unparsed:
        print(f"  UNPARSED  [{event.source}]  {event.raw_text[:100]}")
    for _row, flight, fields in plan.updates:
        label = "CHANGED" if fields else "REPRICE"
        print(
            f"  {label:<9} {flight.flight_date}  "
            f"{flight.carrier_code}{flight.flight_number:>5}  "
            f"{flight.origin} -> {flight.destination}"
            + (f"  ({', '.join(fields)})" if fields else "")
        )
    for row in plan.cancellations:
        print(f"  CANCEL    {row['flight_date']}  {row['carrier_code']}{row['flight_number']}")
    return 0


def cmd_sync(args) -> int:
    config = load_config(config_path=args.config, csv_path=args.csv_path)

    storage = LocalCSVStorage(config.csv_path)
    existing_rows = storage.load()
    # Snapshot before anything mutates a row, so the file is only rewritten when
    # its content genuinely changed. Re-pricing every upcoming flight on every
    # run would otherwise bump sync_timestamp daily and commit in contrail-gh
    # even when not one figure moved.
    snapshot = [dict(row) for row in existing_rows]

    flights, unparsed = collect(config)

    # A feed that yields nothing is far more likely to be broken than to mean
    # every trip was called off, and acting on it would mark the lot cancelled.
    # A dry run cannot cancel anything, and inspecting a feed that legitimately
    # holds no flights is precisely what it is for, so let that through.
    if not flights and not unparsed and existing_rows and not args.dry_run:
        print(
            "The feed returned no flights at all, which usually means a broken or "
            "expired feed URL rather than a genuinely empty calendar.\n"
            "Refusing to cancel anything. Nothing was written.",
            file=sys.stderr,
        )
        return 1

    now = _now()
    plan = reconcile(existing_rows, flights, unparsed, now)

    if not plan:
        print("No new or changed flights found.")

    _describe(plan)

    if args.dry_run:
        return _dry_run_report(plan, config.csv_path)

    results = {}
    if plan.repriceable:
        provider_cls = get_provider(config.provider_name)
        provider = provider_cls(config.require_api_key())
        results = provider.compute(plan.repriceable, now=now)
        fallback = sum(1 for r in results.values() if r.method == "typical_route_average")
        if fallback:
            print(f"{fallback} flight(s) had no exact figure; used the route-average fallback.")

        # Keep everything the provider said, not only what the CSV has columns
        # for: TIM will not price a departed flight again, so this is the only
        # chance to record the provenance behind each figure.
        raw_log = JSONLRawLog(
            config.raw_path or default_raw_path(config.csv_path), enabled=config.raw_log
        )
        captured = raw_log.append(
            [
                {"key": key, "method": result.method, "response": result.raw}
                for key, result in results.items()
                if result.raw
            ]
        )
        if captured:
            print(f"  Recorded {captured} provider response(s) in {raw_log.path}.")

    now_iso = datetime.now(timezone.utc).isoformat()
    replacements: dict[str, dict] = {}

    for flight in plan.new_flights:
        replacements[flight.key] = _flight_row(flight, results.get(flight.key), now_iso)
    for event in plan.new_unparsed:
        replacements[event.key] = _unparsed_row(event, now_iso)
    for row, flight, fields in plan.updates:
        merged = _merge_row(row, flight, results.get(flight.key), now_iso, bool(fields))
        if merged.get("cabin_class_known") and fields:
            print(
                f"  Kept your cabin_class_known='{merged['cabin_class_known']}' on "
                f"{merged['carrier_code']}{merged['flight_number']} — no source reports "
                "cabin, so check it still applies after this change."
            )
        replacements[row_key(row)] = merged
    for row in plan.cancellations:
        replacements[row_key(row)] = resync.cancel({**row, "sync_timestamp": now_iso})

    added_rows = [replacements[item.key] for item in plan.new_flights + plan.new_unparsed]
    all_rows = [replacements.get(row_key(row), row) for row in existing_rows]
    all_rows += added_rows

    all_rows = normalize_rows(all_rows)

    # Only write when something actually moved. Re-pricing runs unconditionally,
    # so most days produce an identical file — and an identical file must not
    # become a commit.
    # Compare against the rows exactly as they were read, not a normalized copy:
    # re-deriving emissions_kg_actual from a hand-edited figure is itself a
    # change that has to reach the file.
    if all_rows == snapshot:
        print(f"  CSV is already up to date. Total: {total_kg(all_rows):.1f} kg CO2e.")
        return 0

    storage.save(all_rows)

    print(f"Wrote {config.csv_path}.")
    if added_rows:
        print(f"  +{sum(kg_value(row) for row in added_rows):.1f} kg CO2e from new flights.")
    print(f"  Total across {len(all_rows)} row(s): {total_kg(all_rows):.1f} kg CO2e.")
    if plan.new_unparsed:
        print(
            f"  {len(plan.new_unparsed)} event(s) could not be parsed automatically — check rows "
            "with emissions_source='unparsed' and fill them in manually if you want them counted."
        )
    return 0


def cmd_sources(args) -> int:
    try:
        config = load_config(config_path=args.config)
        configured = [s.get("type") for s in config.sources]
    except ConfigError as exc:
        print(f"(could not read config: {exc})\n", file=sys.stderr)
        configured = []

    print("Available importers:")
    for name in sorted(IMPORTERS):
        mark = "configured" if name in configured else "not configured"
        print(f"  {name:<16} {mark}")

    unknown = [t for t in configured if t not in IMPORTERS]
    for name in unknown:
        print(f"  {name:<16} CONFIGURED BUT UNKNOWN — no importer by that name")
    return 1 if unknown else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contrail",
        description="Estimate the CO2e emissions of flights you've taken or booked.",
    )
    parser.add_argument("--version", action="version", version=f"contrail {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="fetch flights, price them, update the CSV")
    sync.add_argument("--config", metavar="PATH", help="path to a config.json/config.yaml")
    sync.add_argument(
        "--csv-path",
        metavar="PATH",
        help=f"where to write the log (default: ./{DEFAULT_CSV_PATH})",
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and report only: no emissions API calls, no writes",
    )
    sync.set_defaults(func=cmd_sync)

    sources = subparsers.add_parser("sources", help="list available and configured importers")
    sources.add_argument("--config", metavar="PATH", help="path to a config.json/config.yaml")
    sources.set_defaults(func=cmd_sources)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        # Report the failure, not a traceback: a 403 or a quota error is an
        # ordinary outcome, and cron setups routinely pipe stderr to a log file.
        print(f"Network error talking to an API: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
