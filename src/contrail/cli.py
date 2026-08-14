"""Command line interface: ``contrail sync`` and ``contrail sources``."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import requests

from contrail import __version__
from contrail.config import DEFAULT_CSV_PATH, Config, ConfigError, load_config
from contrail.emissions import get_provider
from contrail.importers import IMPORTERS, get_importer
from contrail.models import FlightRecord, UnparsedEvent
from contrail.storage import recompute_cumulative
from contrail.storage.local_csv import LocalCSVStorage, actual_kg, row_key


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


def _drop_known(items, seen: set[str]) -> list:
    """Filter out anything already stored, and dedup within this run."""
    fresh = []
    for item in items:
        if item.key in seen:
            continue
        seen.add(item.key)
        fresh.append(item)
    return fresh


def _flight_row(flight: FlightRecord, result, now_iso: str) -> dict:
    row = {
        "sync_timestamp": now_iso,
        "source": flight.source,
        "source_id": flight.source_id,
        "flight_date": flight.flight_date.isoformat(),
        "carrier_code": flight.carrier_code,
        "flight_number": flight.flight_number,
        "origin": flight.origin,
        "destination": flight.destination,
        "cabin_class_known": flight.cabin_class or "",
        "emissions_source": result.method if result else "no_data",
        "model_version": (result.model_version if result else "") or "",
        "emissions_kg_first": _grams_to_kg(result.grams_first if result else None),
        "emissions_kg_business": _grams_to_kg(result.grams_business if result else None),
        "emissions_kg_premium_economy": _grams_to_kg(
            result.grams_premium_economy if result else None
        ),
        "emissions_kg_economy": _grams_to_kg(result.grams_economy if result else None),
        "cumulative_kg_actual": "",  # filled in by recompute_cumulative
        "raw_summary": flight.raw.get("summary", ""),
    }
    row["emissions_kg_actual"] = actual_kg(row)
    return row


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
        "origin": partial.get("origin") or "",
        "destination": partial.get("destination") or "",
        "cabin_class_known": "",
        "emissions_source": "unparsed",
        "model_version": "",
        "emissions_kg_first": "",
        "emissions_kg_business": "",
        "emissions_kg_premium_economy": "",
        "emissions_kg_economy": "",
        "emissions_kg_actual": "",
        "cumulative_kg_actual": "",
        "raw_summary": event.raw_text,
    }


def cmd_sync(args) -> int:
    config = load_config(config_path=args.config, csv_path=args.csv_path)

    storage = LocalCSVStorage(config.csv_path)
    existing_rows = storage.load()
    seen = {row_key(r) for r in existing_rows}

    flights, unparsed = collect(config)
    new_flights = _drop_known(flights, seen)
    new_unparsed = _drop_known(unparsed, seen)

    if not new_flights and not new_unparsed:
        print("No new flights found.")
        if args.dry_run:
            return 0
        # Still recompute and save: a figure filled in by hand on an `unparsed`
        # row, or a corrected cabin class, only reaches the cumulative total on a
        # later run. Identical content rewrites the same bytes, so a no-op run
        # produces no commit in contrail-gh.
        before = [dict(row) for row in existing_rows]
        rows = recompute_cumulative(existing_rows)
        if rows != before:
            storage.save(rows)
            total = rows[-1]["cumulative_kg_actual"] if rows else 0
            print(f"  Recomputed edited rows. Cumulative total: {total} kg CO2e.")
        else:
            print("  CSV is already up to date.")
        return 0

    print(
        f"Found {len(new_flights) + len(new_unparsed)} new flight event(s): "
        f"{len(new_flights)} parsed, {len(new_unparsed)} could not be parsed."
    )

    if args.dry_run:
        print(f"\nDry run — nothing written to {config.csv_path}, no emissions API calls made.\n")
        for flight in new_flights:
            print(
                f"  {flight.flight_date}  {flight.carrier_code}{flight.flight_number:>5}  "
                f"{flight.origin} -> {flight.destination}  [{flight.source}]"
            )
        for event in new_unparsed:
            print(f"  UNPARSED  [{event.source}]  {event.raw_text[:100]}")
        return 0

    results = {}
    if new_flights:
        provider_cls = get_provider(config.provider_name)
        provider = provider_cls(config.require_api_key())
        results = provider.compute(new_flights)
        fallback_count = sum(1 for r in results.values() if r.method == "typical_route_average")
        if fallback_count:
            print(
                f"{fallback_count} flight(s) had already departed; used the route-average fallback."
            )

    now_iso = datetime.now(timezone.utc).isoformat()
    new_rows = [_flight_row(f, results.get(f.key), now_iso) for f in new_flights]
    new_rows += [_unparsed_row(e, now_iso) for e in new_unparsed]

    all_rows = recompute_cumulative(existing_rows + new_rows)
    storage.save(all_rows)

    added_kg = sum(
        float(r["emissions_kg_actual"]) for r in new_rows if r["emissions_kg_actual"] not in ("",)
    )
    total = all_rows[-1]["cumulative_kg_actual"] if all_rows else 0

    print(f"Added {len(new_rows)} row(s) to {config.csv_path}.")
    print(f"  +{added_kg:.1f} kg CO2e from new flights.")
    print(f"  New cumulative total: {total} kg CO2e.")
    if new_unparsed:
        print(
            f"  {len(new_unparsed)} event(s) could not be parsed automatically — check rows "
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
