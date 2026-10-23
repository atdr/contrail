"""OpenFlights CSV backup importer.

OpenFlights is a flight log people have kept for years, often long before
TripIt or Flighty, so its export is frequently the only record of a person's
oldest flights. What sets it apart from every other source is that **every
value in it was typed by hand**. Nothing is checked against a schedule:
times, aircraft and flight numbers are what someone remembered or copied, and
``Duration`` may be OpenFlights' own estimate from the distance, with nothing
in the file to say which. That shapes this module:

- **It is the source of last resort.** List it after any schedule-backed
  importer, so a flight both report is owned by the one with real data and
  this one only fills in blanks.
- **No arrival time.** Departure plus ``Duration`` would read as a schedule in
  the log and in Passport while possibly being an estimate.
- **The key is the date and route, nothing else.** There is no per-flight id,
  and keying on the flight number or the time would re-key a row every time
  someone corrects one in OpenFlights. For a parsed flight that is harmless,
  but an unparsed row would be added again as a second one.
- **Hand-entered dates get checked.** A post-midnight departure is easily
  logged under the previous day, which then matches no other source's reading
  of the flight and is counted twice. A leg that departs before the previous
  one has landed is reported, using ``Duration`` only for that.

The format, as verified against a real backup:

- ``Date`` is the local departure, ``YYYY-MM-DD HH:MM:SS`` or a bare date.
- ``Airline`` is a display name, not a code.
- ``Flight_Number`` is free text: bare (``123``), prefixed and zero-padded
  (``BA0123``), or letter-suffixed (``714L``, a real charter number).
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta
from itertools import pairwise

from contrail.airlines import AirlineResolver
from contrail.airports import timezone_for
from contrail.config import warn
from contrail.importers import files
from contrail.models import FlightRecord, UnparsedEvent

CABIN_CLASSES = {
    "F": "first",
    "C": "business",
    "P": "premium_economy",
    "Y": "economy",
}

# B and L map onto contrail's two reasons. O (other) and C (crew) say nothing
# either way, so they stay unknown.
FLIGHT_REASONS = {
    "B": "business",
    "L": "leisure",
}

SUMMARY_COLUMNS = ("Date", "Airline", "Flight_Number", "From", "To", "Class")

# A flight number as typed, split into an airline prefix and the number. The
# two-character prefix must contain a letter, or a zero-padded bare number such
# as ``0123A`` would lose its first two digits to it.
ICAO_PREFIX_RE = re.compile(r"^([A-Z]{3})(\d+[A-Z]?)$")
IATA_PREFIX_RE = re.compile(r"^([A-Z][A-Z0-9]|[0-9][A-Z])(\d+[A-Z]?)$")

DURATION_RE = re.compile(r"^(\d+):(\d{2})$")


def _text(row: dict, column: str) -> str:
    return (row.get(column) or "").strip()


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def departure_datetime(value: str, origin: str | None) -> datetime | None:
    """The departure as an instant in the origin's zone, or None.

    None for a bare date, which states no time at all, and naive for an
    airport contrail can't place, which the freeze boundary already falls back
    from.
    """
    if len(value) <= 10:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    zone = timezone_for(origin)
    return parsed.replace(tzinfo=zone) if zone and parsed.tzinfo is None else parsed


def duration(value: str) -> timedelta | None:
    match = DURATION_RE.match(value.strip())
    if not match:
        return None
    return timedelta(hours=int(match[1]), minutes=int(match[2]))


def cabin_class(value: str) -> str | None:
    return CABIN_CLASSES.get(value.strip().upper())


def flight_reason(value: str) -> str | None:
    return FLIGHT_REASONS.get(value.strip().upper())


def export_files(path: str):
    return files.export_files(path, f"No OpenFlights export found at {path!r}.")


class OpenFlightsCSVImporter:
    """Reads flights from one or more OpenFlights CSV backups."""

    id = "openflights_csv"
    # Read by the CLI's one-day-off warning, to say which side to doubt.
    user_entered = True

    def __init__(self, resolver: AirlineResolver | None = None):
        self.resolver = resolver if resolver is not None else AirlineResolver()

    def fetch(self, config: dict) -> Iterable[FlightRecord | UnparsedEvent]:
        path = config.get("path")
        if not path:
            raise ValueError(
                f"Source of type {self.id!r} needs a 'path' (an OpenFlights CSV backup, "
                "a directory of them, or a glob)."
            )
        if "airline_lookup" in config:
            self.resolver.lookup = bool(config["airline_lookup"])

        # Only the newest backup. Each one is the whole account, so an older
        # one adds nothing but rows since corrected: the date is part of the
        # key, so a row whose date was fixed would come back from the older
        # file under its old key and be counted twice.
        for export in export_files(str(path))[:1]:
            # The backup starts with a byte order mark, which would otherwise
            # become part of the first column's name.
            with open(export, newline="", encoding="utf-8-sig") as f:
                yield from self.parse(f)

    def parse(self, lines: Iterable[str]) -> Iterator[FlightRecord | UnparsedEvent]:
        """Turn an open backup into FlightRecords and UnparsedEvents.

        Two passes: keys are numbered per date and route across the whole file,
        and the overlap check compares each leg with the one before it.
        """
        rows = list(csv.DictReader(lines))
        keys = self._source_ids(rows)
        warn_overlaps(rows)
        for row, key in zip(rows, keys, strict=True):
            yield self._build(row, key)

    def _source_ids(self, rows: list[dict]) -> list[str]:
        """``YYYY-MM-DD:FROM-TO``, with ``#2`` onwards for repeats.

        A repeat is the same route flown twice on one day, a diversion flown
        again say. They are numbered in departure order rather than file order,
        so moving a row in OpenFlights does not swap their keys.
        """
        base = [self._base_id(row) for row in rows]
        order = sorted(range(len(rows)), key=lambda i: (base[i], _text(rows[i], "Date"), i))
        keys = [""] * len(rows)
        seen: dict[str, int] = {}
        for i in order:
            seen[base[i]] = seen.get(base[i], 0) + 1
            keys[i] = base[i] if seen[base[i]] == 1 else f"{base[i]}#{seen[base[i]]}"
        return keys

    def _base_id(self, row: dict) -> str:
        flight_date = _parse_date(_text(row, "Date"))
        origin, destination = _text(row, "From").upper(), _text(row, "To").upper()
        if flight_date and origin and destination:
            return f"{flight_date.isoformat()}:{origin}-{destination}"
        # Without the identity fields there is nothing stable to key on, so key
        # on everything legible rather than letting every such row share one.
        parts = [_text(row, c) for c in ("Date", "From", "To", "Flight_Number")]
        return "row:" + "-".join(part or "?" for part in parts)

    def carrier_and_number(self, airline: str, typed: str) -> tuple[str | None, str]:
        """The carrier a typed flight number belongs to, and the bare number.

        A prefix on the number wins over the ``Airline`` column: whoever typed
        ``U2239`` said which airline that number is, and on a codeshare the
        column may name the other one. A bare number takes the airline named.
        Leading zeros go either way, since TIM takes the number as an integer.
        """
        number = "".join(typed.split()).upper()
        carrier = None

        match = ICAO_PREFIX_RE.match(number)
        if match and (iata := self.resolver.resolve_icao(match[1])):
            carrier, number = iata, match[2]
        elif match := IATA_PREFIX_RE.match(number):
            carrier, number = match[1], match[2]
        else:
            carrier = self.resolver.resolve(airline)

        return carrier, number.lstrip("0") or number

    def _build(self, row: dict, key: str) -> FlightRecord | UnparsedEvent:
        origin = _text(row, "From").upper()
        destination = _text(row, "To").upper()
        flight_date = _parse_date(_text(row, "Date"))
        carrier, number = self.carrier_and_number(
            _text(row, "Airline"), _text(row, "Flight_Number")
        )

        # A letter-suffixed charter number is not a parse: TIM takes the
        # number as an integer, and a whole batch fails on one bad entry.
        if not (flight_date and origin and destination and carrier and number.isdigit()):
            return UnparsedEvent(
                source=self.id,
                source_id=key,
                raw_text=" | ".join(
                    f"{c}={_text(row, c)}" for c in SUMMARY_COLUMNS if _text(row, c)
                ),
                partial={
                    k: v
                    for k, v in {
                        "flight_date": flight_date,
                        "carrier_code": carrier,
                        "flight_number": number,
                        "origin": origin,
                        "destination": destination,
                    }.items()
                    if v
                },
            )

        return FlightRecord(
            source=self.id,
            source_id=key,
            flight_date=flight_date,
            departure_time=departure_datetime(_text(row, "Date"), origin),
            carrier_code=carrier,
            flight_number=number,
            origin=origin,
            destination=destination,
            cabin_class=cabin_class(_text(row, "Class")),
            aircraft_type=_text(row, "Plane") or None,
            flight_reason=flight_reason(_text(row, "Reason")),
            raw={"summary": f"{carrier}{number} {origin}->{destination} {flight_date}", **row},
        )


def warn_overlaps(rows: list[dict]) -> None:
    """Report a leg that departs before the previous leg has landed.

    The usual cause is a post-midnight departure entered under the previous
    day. Uncorrected, it matches no other source's reading of the flight and is
    counted twice. Every row with a time and a placeable origin takes part;
    only the earlier leg of a pair needs a ``Duration``, since that is what
    says when it lands. It is only used here: it may be an estimate, which is
    close enough to catch a whole day's error.
    """
    legs = []
    for row in rows:
        departure = departure_datetime(_text(row, "Date"), _text(row, "From").upper())
        if departure is None or departure.tzinfo is None:
            continue
        length = duration(_text(row, "Duration"))
        legs.append((departure, departure + length if length else None, row))
    legs.sort(key=lambda leg: leg[0])

    for (_, landed, before), (departed, _, after) in pairwise(legs):
        if landed is not None and departed < landed:
            warn(
                f"WARNING: OpenFlights has {_describe(after)} departing before "
                f"{_describe(before)} lands.\n"
                "  One of the two dates is probably a day off, often a departure just "
                "after midnight entered under the day before. Correct it in OpenFlights "
                "and export again, and delete any row a sync already wrote under the "
                "wrong date: a departed row is never removed by a later sync."
            )


def _describe(row: dict) -> str:
    number = _text(row, "Flight_Number")
    route = f"{_text(row, 'From')}-{_text(row, 'To')} at {_text(row, 'Date')}"
    return f"{number} {route}" if number else route
