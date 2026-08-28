"""Command line interface: sync, source inspection, and Passport generation."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

import requests

from contrail import __version__, resync
from contrail.config import DEFAULT_CSV_PATH, Config, ConfigError, load_config
from contrail.emissions import get_provider
from contrail.importers import IMPORTERS, get_importer
from contrail.models import FlightRecord, UnparsedEvent
from contrail.passport import DEFAULT_OUTPUT_PATH
from contrail.passport import render as render_passport
from contrail.storage import JSONLRawLog, kg_value, normalize_rows, total_kg
from contrail.storage.local_csv import STATUS_CANCELLED, LocalCSVStorage, actual_kg, row_key
from contrail.storage.raw_log import default_path as default_raw_path


def _now():
    """The current instant, in UTC.

    Indirected so tests can pin it: the open/frozen boundary is a comparison
    against now, and a suite that depends on the real clock quietly starts
    failing once its fixture dates fall into the past.
    """
    return datetime.now(UTC)


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


def _one_flight(a: FlightRecord, b: FlightRecord) -> bool:
    """Whether two records sharing an identity really are the same flight.

    Route and date normally settle it. The exception is a disagreement about
    cancellation, where one identity covers two quite different situations:

    - **One source, two different flight numbers.** A flight called off and
      rebooked the same day on the same route. Two flights, one of them actually
      flown, and folding them would put the cancellation on the one that flew and
      zero it out for good.
    - **Anything else.** One reading of one flight is simply stale — two sources
      disagreeing, or one source listing the same flight twice. Folding is right,
      and the cancellation stands, or else which source sits first in the config
      would decide whether a called-off flight counts.

    Sources are the discriminator rather than the flight number alone, because
    two sources legitimately label one flight differently: on a codeshare, TripIt
    may say ``IB3643`` where a Flighty export says ``BA458``.
    """
    if a.cancelled == b.cancelled:
        return True
    if a.source != b.source:
        return True
    return (a.carrier_code, a.flight_number) == (b.carrier_code, b.flight_number)


def _collapse(flights: dict) -> tuple[dict, list[tuple[FlightRecord, FlightRecord]]]:
    """Fold records that are the same flight into one, keeping the first.

    Two sources reporting the same flight is the ordinary case; one source
    reporting it twice happens too, when an export lists a codeshare under both
    the marketing and the operating number. Either way it is one flight, one
    emissions figure and one row.

    The winner takes anything the loser knew and it didn't, and records the
    loser's key so the two can still be joined back together. First wins, so the
    order sources appear in the config decides — deterministic, and the earlier
    source stays the one that owns its rows.

    Returns the surviving records and the pairs collapsed, so the run can say so
    out loud rather than quietly dropping a flight someone can see in their file.
    """
    by_identity: dict[tuple, FlightRecord] = {}
    survivors: dict = {}
    collapsed: list[tuple[FlightRecord, FlightRecord]] = []

    for key, flight in flights.items():
        identity = flight.identity
        winner = by_identity.get(identity) if identity else None
        if winner is not None and not _one_flight(winner, flight):
            winner = None
        if winner is None:
            # A record that counts takes the identity over one that doesn't, so a
            # cancellation seen first can't stop the flight that replaced it from
            # matching whatever comes next.
            if identity and (identity not in by_identity or by_identity[identity].cancelled):
                by_identity[identity] = flight
            survivors[key] = flight
            continue

        collapsed.append((winner, flight))
        if flight.key not in winner.also_seen:
            winner.also_seen.append(flight.key)
        for field in (
            "arrival_time",
            "cabin_class",
            "aircraft_type",
            "flight_reason",
            # Whichever record knows who really operates the flight, the survivor
            # has to end up holding it: TIM prices only the operating flight, and
            # a codeshare asked about as booked silently falls to a route average.
            "operating_carrier_code",
            "operating_flight_number",
        ):
            if getattr(winner, field) is None and getattr(flight, field) is not None:
                setattr(winner, field, getattr(flight, field))
        # Only reached when the two agree they are one flight, so a stated
        # cancellation from either is about this flight and has to survive.
        winner.cancelled = winner.cancelled or flight.cancelled

    return survivors, collapsed


def _through_conflicts(flights: dict, rows: dict) -> list[tuple[str, list[FlightRecord], tuple]]:
    """Legs of a multi-leg flight number that also exist as one through entry.

    ``BA16`` on a given day is SYD-SIN and SIN-LHR: two legs, two cabins, two
    rows. A source that instead reports the published route SYD-LHR describes the
    same journey as a single flight, and nothing matches it to the legs — so the
    file would carry both and count the journey nearly twice.

    Report it and change nothing. Which representation is right is genuinely
    ambiguous, and the legs carry two different cabins that one through row
    cannot express, so a wrong automatic answer is worse than a visible one.
    """
    by_number: dict[tuple, list[FlightRecord]] = {}
    for flight in flights.values():
        if flight.identity:
            by_number.setdefault(
                (flight.flight_date.isoformat(), flight.carrier_code, flight.flight_number), []
            ).append(flight)

    conflicts = []
    for (day, carrier, number), entries in by_number.items():
        if len(entries) < 2:
            continue
        # From `identity`, which upper-cases. Comparing raw codes against rows
        # that were normalized means a lower-case feed never trips the warning.
        origins = {e.identity[1] for e in entries}
        destinations = {e.identity[2] for e in entries}
        # The endpoints of a chain a->b->c: the only origin that is nobody's
        # destination, and the only destination that is nobody's origin.
        starts = origins - destinations
        ends = destinations - origins
        if len(starts) != 1 or len(ends) != 1:
            continue

        through = (day, next(iter(starts)), next(iter(ends)))
        if through not in rows and not any(f.identity == through for f in flights.values()):
            continue

        # Report the chain itself, not the through entry sitting alongside it,
        # and in the order it is flown.
        legs = [e for e in entries if e.identity != through]
        ordered, at = [], through[1]
        by_origin = {leg.identity[1]: leg for leg in legs}
        while at in by_origin:
            ordered.append(by_origin.pop(at))
            at = ordered[-1].identity[2]
        conflicts.append((f"{carrier}{number} on {day}", ordered or legs, through))
    return conflicts


class Reconciliation:
    """What a sync intends to do, worked out before anything is written."""

    def __init__(self):
        self.new_flights: list[FlightRecord] = []
        self.new_unparsed: list[UnparsedEvent] = []
        self.updates: list[tuple[dict, FlightRecord, list[str]]] = []  # row, flight, changed
        self.duplicates: list[tuple[dict, FlightRecord, list[str]]] = []  # row, flight, filled
        self.collapsed: list[tuple[FlightRecord, FlightRecord]] = []  # kept, folded in
        self.conflicts: list[tuple[str, list[FlightRecord], tuple]] = []
        self.cancellations: list[dict] = []
        self.restorations: list[dict] = []

    @property
    def repriceable(self) -> list[FlightRecord]:
        # Duplicates are absent on purpose: the row already has its figure from
        # whichever source owns it, and asking again would spend an API call to
        # learn nothing.
        return self.new_flights + [flight for _, flight, _ in self.updates]

    def __bool__(self) -> bool:
        return bool(
            self.new_flights
            or self.new_unparsed
            or self.updates
            # A duplicate that filled nothing in is a link already recorded on a
            # previous run. Counting it would make every sync look like it had
            # something to do.
            or any(fields for _, _, fields in self.duplicates)
            or self.cancellations
        )


def reconcile(existing_rows, flights, unparsed, now) -> Reconciliation:
    """Work out what changes against what is already stored.

    Three outcomes per stored row: unknown to us (new), already departed
    (frozen, never touched), or still upcoming (open, and contrail's to correct).
    """
    plan = Reconciliation()
    by_key = {row_key(row): row for row in existing_rows}
    # Two kinds of row are excluded from claiming an identity.
    #
    # A cancelled one: if one source called a flight off and another says it
    # flew, the second is describing something that happened and deserves a row
    # of its own rather than being filed against a row that counts for nothing.
    #
    # And an unparsed one, which keeps whatever route it could recover — so it
    # has an identity, and would absorb the properly parsed reading of the same
    # flight that another source later supplies. That reading is never priced
    # (duplicates aren't), so the flight would sit at 0 kg until someone noticed.
    by_identity = {
        identity: row
        for row in existing_rows
        if (identity := resync.identity(row))
        and not resync.is_cancelled(row)
        and row.get("emissions_source") != "unparsed"
    }

    feed_flights, plan.collapsed = _collapse(_dedup(flights))
    plan.conflicts = _through_conflicts(feed_flights, by_identity)
    # A key that parsed is a key that parsed: never also queue it as unparsed,
    # or it would be written twice under one dedup key and the priced flight
    # discarded. Two UID-less events can hash alike while only one parses.
    feed_unparsed = {
        key: event for key, event in _dedup(unparsed).items() if key not in feed_flights
    }
    # Folded keys count as reported. Collapsing removes a record from
    # ``feed_flights``, but the source did return it, so a row it owns has not
    # gone anywhere and must never be read as cancelled.
    feed_keys = (
        set(feed_flights)
        | set(feed_unparsed)
        | {key for flight in feed_flights.values() for key in flight.also_seen}
    )

    # Only an importer that actually returned something this run may have its
    # rows cancelled. Otherwise one silently empty feed — a rotated URL, a
    # source removed from the config — would cancel all of its flights while
    # other sources kept the global guard happy.
    contributing = {item.source for item in list(flights) + list(unparsed)}

    for key, flight in feed_flights.items():
        # Every key this record answers to, not just its own: collapsing folded
        # other records into it, and the stored row may be owned by one of those.
        # Missing that would treat an upcoming flight as somebody else's row —
        # never corrected from the feed again, and never re-priced, which is the
        # whole reason open rows are re-asked about.
        row = next((by_key[k] for k in (key, *flight.also_seen) if k in by_key), None)
        if row is None:
            # Unknown by key, but the flight itself may already be in the file
            # under whichever source found it first. That source keeps the row;
            # this one fills in blanks and leaves its key behind to be joined on.
            owner = (
                by_identity.get(flight.identity)
                if flight.identity and not flight.cancelled
                else None
            )
            if owner is not None:
                merged, filled = resync.backfill(owner, flight)
                plan.duplicates.append((merged, flight, filled))
            else:
                plan.new_flights.append(flight)
        elif resync.is_open(row, now):
            changed = resync.differences(row, flight)
            plan.updates.append((row, flight, changed))
            if resync.restored(row) and not flight.cancelled:
                plan.restorations.append(row)
        else:
            # Departed, so the route, the date and the figures are settled. A
            # blank is not: adding a Flighty export to a log built from TripIt is
            # exactly how years of past rows learn which cabin they were flown
            # in, and refusing that would refuse the point of the export.
            merged, filled = resync.backfill(row, flight)
            if filled:
                plan.duplicates.append((merged, flight, filled))

    for key, event in feed_unparsed.items():
        if key not in by_key:
            plan.new_unparsed.append(event)

    # A row another source still reports is not a row that vanished, even if the
    # source that owns it went quiet. Cancelling it would drop a flight from the
    # total that contrail has just been told is happening.
    still_reported = feed_keys | {row_key(row) for row, _, _ in plan.duplicates}

    for key, row in by_key.items():
        if key in still_reported or resync.is_cancelled(row):
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
        "also_seen_as": " ".join(sorted(set(flight.also_seen))),
        "flight_date": flight.flight_date.isoformat(),
        "carrier_code": flight.carrier_code,
        "flight_number": flight.flight_number,
        "operating_carrier_code": flight.operating_carrier_code or "",
        "operating_flight_number": flight.operating_flight_number or "",
        "origin": flight.origin,
        "destination": flight.destination,
        "departure_time": flight.departure_time.isoformat() if flight.departure_time else "",
        "arrival_time": flight.arrival_time.isoformat() if flight.arrival_time else "",
        "status": STATUS_CANCELLED if flight.cancelled else "",
        "cabin_class_known": flight.cabin_class or "",
        "aircraft_type": flight.aircraft_type or "",
        "flight_reason": flight.flight_reason or "",
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

    Two things survive regardless of what the feed says. The back-fill fields
    (arrival, cabin, aircraft, reason) are only ever filled in, never replaced:
    a stored value may be a hand edit or a source fact, and either way it is the
    only copy. And a worse emissions figure is refused on an unchanged flight,
    so a transient TIM miss can't downgrade a good number. If the flight's
    details changed, it is a different flight and whatever comes back is the
    truth.
    """
    fresh = _flight_row(flight, result, now_iso)
    merged = {**row, **{field: fresh[field] for field in resync.FEED_FIELDS}}
    # Cancellation is the source's to state, and its absence from a feed is the
    # only other way a row gets marked. Present and not called off means open.
    merged["status"] = STATUS_CANCELLED if flight.cancelled else ""
    # Its own key as well as the folded ones: when the stored row is owned by a
    # record that got folded in, the winner's key is the only new information,
    # and `linked` drops whichever of them is the row's own.
    merged["also_seen_as"] = resync.linked(row, [flight.key, *flight.also_seen])["also_seen_as"]
    for field in resync.BACKFILL_FIELDS:
        merged[field] = row.get(field) or fresh[field]
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
        "also_seen_as": "",
        "flight_date": flight_date.isoformat() if flight_date else "",
        "carrier_code": partial.get("carrier_code") or "",
        "flight_number": partial.get("flight_number") or "",
        "operating_carrier_code": "",
        "operating_flight_number": "",
        "origin": partial.get("origin") or "",
        "destination": partial.get("destination") or "",
        "departure_time": "",
        "arrival_time": "",
        "status": "",
        "cabin_class_known": "",
        "aircraft_type": "",
        "flight_reason": "",
        "emissions_source": "unparsed",
        "model_version": "",
        "emissions_data_source": "",
        "contrails_impact": "",
        "distance_km": "",
        "aircraft_match": "",
        "emissions_kg_first": "",
        "emissions_kg_business": "",
        "emissions_kg_premium_economy": "",
        "emissions_kg_economy": "",
        "emissions_kg_actual": "",
        "raw_summary": event.raw_text,
    }


def _label(flight: FlightRecord) -> str:
    """How a source labels a flight. The source is part of it: two sources
    usually agree on the number, so without it a collapse reads as a no-op."""
    return (
        f"{flight.carrier_code}{flight.flight_number} "
        f"{flight.origin}->{flight.destination} on {flight.flight_date} [{flight.source}]"
    )


def _describe(plan: Reconciliation) -> None:
    filled = [d for d in plan.duplicates if d[2]]

    if plan.collapsed and (plan.new_flights or filled):
        # Only when the collapse bears on something the run is actually doing,
        # and then a summary rather than a line each. Sources overlapping is the
        # steady state, not news: every flight in a directory of exports
        # collapses on every run, and repeating that daily would bury whatever
        # did change. `--dry-run` prints the detail.
        print(
            f"{len(plan.collapsed)} record(s) matched a flight another source had already "
            "reported; kept one row each."
        )
    for label, legs, through in plan.conflicts:
        print(
            f"WARNING: {label} is reported both as {len(legs)} legs "
            f"({' + '.join(f'{leg.origin}-{leg.destination}' for leg in legs)}) and as one "
            f"through flight {through[1]}-{through[2]}.\n"
            "  Both are being kept, so this journey is counted about twice. contrail can't "
            "tell which is right — the legs may have been flown in different cabins, which a "
            "single row can't express — so delete whichever you don't want.",
            file=sys.stderr,
        )
    if filled:
        print(f"{len(filled)} stored flight(s) gained details from another source:")
        for row, _flight, fields in filled:
            print(f"  {row['carrier_code']}{row['flight_number']}: {', '.join(fields)}")
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
    for kept, folded in plan.collapsed:
        print(
            f"  MATCHED   {_label(folded)} is the same flight as {_label(kept)}; "
            f"keeping one row, linked as {folded.key}."
        )
    for flight in plan.new_flights:
        print(
            f"  NEW       {flight.flight_date}  "
            f"{flight.carrier_code}{flight.flight_number:>5}  "
            f"{flight.origin} -> {flight.destination}  [{flight.source}]"
        )
    for event in plan.new_unparsed:
        print(f"  UNPARSED  [{event.source}]  {event.raw_text[:100]}")
    for row, flight, fields in plan.duplicates:
        print(
            f"  LINKED    {flight.flight_date}  "
            f"{flight.carrier_code}{flight.flight_number:>5}  "
            f"{flight.origin} -> {flight.destination}  "
            f"(already stored as {row.get('source')}"
            + (f"; fills {', '.join(fields)}" if fields else "")
            + ")"
        )
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

    now_iso = datetime.now(UTC).isoformat()
    replacements: dict[str, dict] = {}

    for flight in plan.new_flights:
        replacements[flight.key] = _flight_row(flight, results.get(flight.key), now_iso)
    for event in plan.new_unparsed:
        replacements[event.key] = _unparsed_row(event, now_iso)
    for row, flight, fields in plan.updates:
        merged = _merge_row(row, flight, results.get(flight.key), now_iso, bool(fields))
        if merged.get("cabin_class_known") and fields:
            print(
                f"  Kept cabin_class_known='{merged['cabin_class_known']}' on "
                f"{merged['carrier_code']}{merged['flight_number']} — a stored cabin is never "
                "overwritten, so check it still applies after this change."
            )
        replacements[row_key(row)] = merged
    # Duplicates carry no new figures, only links and fills, so a row that gained
    # neither must not have its timestamp bumped — that would rewrite the file
    # every run for nothing.
    for row, _flight, fields in plan.duplicates:
        replacements[row_key(row)] = {**row, "sync_timestamp": now_iso} if fields else row
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


def cmd_passport(args) -> int:
    """Generate one private, offline HTML dashboard from the stored CSV."""
    config = load_config(config_path=args.config, csv_path=args.csv_path)
    csv_path = Path(config.csv_path)
    if not csv_path.exists():
        raise ValueError(f"Flight log not found: {csv_path}")

    rows = LocalCSVStorage(str(csv_path)).load()
    if not rows:
        raise ValueError(f"Flight log is empty: {csv_path}")

    output_path = Path(args.output)
    if output_path.resolve() == csv_path.resolve():
        raise ValueError("Passport output must not overwrite the flight log")

    output = render_passport(rows, output_path, now=_now())
    print(f"Wrote {output}.")
    print("  Passport embeds your flight history. Keep the HTML private.")
    if args.open and not webbrowser.open(output.as_uri()):
        print(f"  Could not open a browser automatically. Open {output} by hand.", file=sys.stderr)
    return 0


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

    passport = subparsers.add_parser(
        "passport", help="build a private, offline HTML dashboard from the CSV"
    )
    passport.add_argument("--config", metavar="PATH", help="path to a config.json/config.yaml")
    passport.add_argument(
        "--csv-path",
        metavar="PATH",
        help=f"flight log to read (default: ./{DEFAULT_CSV_PATH})",
    )
    passport.add_argument(
        "--output",
        metavar="PATH",
        default=DEFAULT_OUTPUT_PATH,
        help=f"HTML file to write (default: ./{DEFAULT_OUTPUT_PATH})",
    )
    passport.add_argument(
        "--open",
        action="store_true",
        help="open the generated Passport in the default browser",
    )
    passport.set_defaults(func=cmd_passport)

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
