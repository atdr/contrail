"""Flighty CSV export importer.

Flighty is a flight tracker whose export is the one source contrail has that
reports **the cabin actually flown**. Everywhere else the log has to assume
economy, which on a long-haul business seat understates by roughly four times.

Unlike a calendar feed this is a file, not a URL, and a full-history snapshot
rather than a rolling window — a single export can cover two decades, most of it
long past. Three consequences shape this module:

- **Almost every row is a departed flight**, so almost every row prices to a
  route average. That is the honest figure for a flight TIM will never quote
  again, and it is still far better than nothing.
- **The export overlaps whatever else is configured.** Matching that up is not
  this importer's job; it yields what the file says and ``resync`` decides what
  is a duplicate.
- **A re-export repeats everything**, so the same flight keeps its Flighty UUID
  and the same key run after run. Nothing is re-priced and nothing is rewritten.

The format is far tidier than a calendar feed: fixed columns, no free text to
regex at. Two things still need care, and both are verified against a real
export rather than assumed:

- ``Airline`` is **ICAO** (``BAW``), while TIM wants IATA (``BA``).
- ``Date`` is already the local calendar date at the origin, matching the
  scheduled gate departure on every row, which is exactly what TIM asks for.
"""

from __future__ import annotations

import csv
import sys
from collections.abc import Iterable, Iterator
from datetime import date, datetime
from glob import glob
from pathlib import Path

from contrail.airlines import AirlineResolver
from contrail.airports import timezone_for
from contrail.models import FlightRecord, UnparsedEvent

# Flighty's own spelling, mapped onto contrail's. PREMIUM_ECONOMY has not been
# seen in an export but is the obvious fourth, and costs nothing to accept.
CABIN_CLASSES = {
    "ECONOMY": "economy",
    "PREMIUM_ECONOMY": "premium_economy",
    "PREMIUM ECONOMY": "premium_economy",
    "BUSINESS": "business",
    "FIRST": "first",
    # PRIVATE is deliberately absent. TIM's per-cabin figures describe a seat on
    # a scheduled airliner and say nothing useful about a private charter, so the
    # honest answer is to leave the cabin unknown and let the row be corrected by
    # hand.
}

# Departure times, best first. Scheduled comes before actual because it is what
# the schedule TIM prices against says, and it is the only one an upcoming
# flight has.
DEPARTURE_COLUMNS = (
    "Gate Departure (Scheduled)",
    "Take off (Scheduled)",
    "Gate Departure (Actual)",
    "Take off (Actual)",
)

# Columns worth showing a human on a row that failed to parse. The rest of the
# export is Flighty's own UUIDs, which tell a reader nothing.
SUMMARY_COLUMNS = (
    "Date",
    "Airline",
    "Flight",
    "From",
    "To",
    "Cabin Class",
    "Gate Departure (Scheduled)",
)


def _text(row: dict, column: str) -> str:
    return (row.get(column) or "").strip()


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def departure_datetime(row: dict, origin: str | None) -> datetime | None:
    """The scheduled departure, given the origin's own timezone.

    Flighty writes wall-clock local time with no offset (``2026-09-02T07:50``).
    Attaching the origin's zone turns that into a real instant, which is what
    lets the freeze boundary be exact to the minute instead of a date comparison.
    An airport contrail can't identify leaves the value naive, which the boundary
    already knows how to fall back from.
    """
    for column in DEPARTURE_COLUMNS:
        parsed = _parse_datetime(_text(row, column))
        if parsed is not None:
            zone = timezone_for(origin)
            return parsed.replace(tzinfo=zone) if zone and parsed.tzinfo is None else parsed
    return None


def cabin_class(value: str) -> str | None:
    return CABIN_CLASSES.get(value.strip().upper())


def flight_reason(value: str) -> str | None:
    reason = value.strip().lower()
    return reason or None


def is_cancelled(value: str) -> bool:
    return value.strip().lower() == "true"


def export_files(path: str) -> list[Path]:
    """Every export the configured path names, newest first.

    A file, a directory of them, or a glob. Newest first because the CLI keeps
    the *first* record it sees for a key, so the most recent export wins wherever
    two of them disagree about the same flight.

    Ordering is by filename, not modification time: Flighty names its exports
    ``FlightyExport-YYYY-MM-DD.csv``, so the name sorts correctly, and a repo
    checkout gives every file the same mtime anyway.

    **Finding nothing is a warning, not an error**, and an empty directory and a
    missing one behave alike. An export arrives by hand, so "not there yet" is an
    ordinary state — the contrail-gh template ships an empty ``flighty/`` for
    exactly that reason. Raising would let one unconfigured source take down a
    sync that had a perfectly good TripIt feed alongside it. Nothing else breaks:
    a source that returns nothing already can't cancel its own rows, and a run
    where *every* source is empty is refused separately.
    """
    target = Path(path).expanduser()

    if target.is_dir():
        matches = list(target.glob("*.csv"))
    elif target.exists():
        matches = [target]
    else:
        matches = [Path(p) for p in glob(str(target))]

    if not matches:
        print(
            f"No Flighty export found at {path!r} — no cabin classes will be filled in "
            "from one this run.\n"
            "  'path' takes an exported CSV, a directory of them, or a glob.",
            file=sys.stderr,
        )

    return sorted(matches, key=lambda p: p.name, reverse=True)


class FlightyCSVImporter:
    """Reads flights from one or more Flighty CSV exports."""

    id = "flighty_csv"

    def __init__(self, resolver: AirlineResolver | None = None):
        self.resolver = resolver if resolver is not None else AirlineResolver()

    def fetch(self, config: dict) -> Iterable[FlightRecord | UnparsedEvent]:
        path = config.get("path") or config.get("csv_path")
        if not path:
            raise ValueError(
                f"Source of type {self.id!r} needs a 'path' (a Flighty export CSV, "
                "a directory of them, or a glob)."
            )
        if "airline_lookup" in config:
            self.resolver.lookup = bool(config["airline_lookup"])

        for export in export_files(str(path)):
            with open(export, newline="", encoding="utf-8-sig") as f:
                yield from self.parse(f)

    def parse(self, lines: Iterable[str]) -> Iterator[FlightRecord | UnparsedEvent]:
        """Turn an open export into FlightRecords and UnparsedEvents.

        One pass, unlike the iCal importer: every field is its own column, so
        there is nothing to learn from the rest of the file first.
        """
        for row in csv.DictReader(lines):
            yield self._build(row)

    def _source_id(self, row: dict) -> str:
        """Flighty's own UUID for the flight.

        Stable across exports, which is what makes a re-export idempotent, and
        the key to join a contrail row back to everything the export holds that
        contrail does not store — seat, PNR, tail number, terminals.
        """
        return _text(row, "Flight Flighty ID")

    def _build(self, row: dict) -> FlightRecord | UnparsedEvent:
        origin = _text(row, "From").upper()
        destination = _text(row, "To").upper()
        flight_date = _parse_date(_text(row, "Date"))
        number = _text(row, "Flight")
        icao = _text(row, "Airline").upper()

        departure = departure_datetime(row, origin)
        if flight_date is None and departure is not None:
            flight_date = departure.date()

        # A flight number TIM cannot be asked about is not a parse: it sends the
        # number as an integer, and a whole batch fails on one bad entry.
        if not (flight_date and origin and destination and number.isdigit()):
            return self._unparsed(row, flight_date, icao, number, origin, destination)

        carrier = self.resolver.resolve_icao(icao) or icao
        return FlightRecord(
            source=self.id,
            source_id=self._source_id(row) or self._fallback_id(row),
            flight_date=flight_date,
            departure_time=departure,
            carrier_code=carrier,
            flight_number=number,
            origin=origin,
            destination=destination,
            cabin_class=cabin_class(_text(row, "Cabin Class")),
            aircraft_type=_text(row, "Aircraft Type Name") or None,
            flight_reason=flight_reason(_text(row, "Flight Reason")),
            cancelled=is_cancelled(_text(row, "Canceled")),
            raw={"summary": f"{carrier}{number} {origin}->{destination} {flight_date}", **row},
        )

    def _fallback_id(self, row: dict) -> str:
        """A key for a row Flighty gave no UUID.

        Every export seen has one on every row, but without a fallback a file
        that didn't would put every such flight under the key ``flighty_csv:``,
        where the first would mask all the rest — in this run and every one
        after.
        """
        parts = [_text(row, c) for c in ("Date", "Airline", "Flight", "From", "To")]
        return "row:" + "-".join(part or "?" for part in parts)

    def _unparsed(
        self,
        row: dict,
        flight_date: date | None,
        carrier: str,
        number: str,
        origin: str,
        destination: str,
    ) -> UnparsedEvent:
        # Keep whatever was recovered. A partial flight_date above all: storage
        # sorts on it, and a dateless row sorts to the top of the file and stays
        # there.
        partial = {
            "flight_date": flight_date,
            "carrier_code": carrier,
            "flight_number": number,
            "origin": origin,
            "destination": destination,
        }
        return UnparsedEvent(
            source=self.id,
            source_id=self._source_id(row) or self._fallback_id(row),
            raw_text=" | ".join(f"{c}={_text(row, c)}" for c in SUMMARY_COLUMNS if _text(row, c)),
            partial={k: v for k, v in partial.items() if v},
        )
