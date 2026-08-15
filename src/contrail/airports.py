"""Airport timezone lookup, used to derive a flight's local departure date.

The TIM API is explicit that ``departureDate`` is "the date of the flight in the
time zone of the origin airport". Calendar feeds routinely give departure times
in UTC instead — TripIt's does — so an evening departure west of Greenwich, or
an early-morning one far to the east, lands on the wrong calendar day if the UTC
date is taken at face value.

That costs twice over: the stored ``flight_date`` is wrong, *and* the exact
emissions lookup misses, silently downgrading the row to a route average.

Note that TIM wants a date, not a timestamp. The zone is only ever scaffolding
for picking the right calendar day.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import airportsdata

_AIRPORTS: dict | None = None
_ZONES: dict[str, ZoneInfo | None] = {}


def _airports() -> dict:
    """Load the bundled IATA database once, on first use."""
    global _AIRPORTS
    if _AIRPORTS is None:
        _AIRPORTS = airportsdata.load("IATA")
    return _AIRPORTS


def timezone_for(iata: str | None) -> ZoneInfo | None:
    """IANA timezone for an IATA airport code, or None if it isn't known."""
    if not iata:
        return None
    code = iata.strip().upper()
    if code in _ZONES:
        return _ZONES[code]

    entry = _airports().get(code)
    zone: ZoneInfo | None = None
    if entry and entry.get("tz"):
        try:
            zone = ZoneInfo(entry["tz"])
        except (ZoneInfoNotFoundError, ValueError):
            zone = None  # no tz database on this platform
    _ZONES[code] = zone
    return zone


def today_at(origin: str | None, now: datetime) -> date:
    """The current calendar date at an airport.

    The freeze boundary compares a stored ``flight_date`` — which is local to the
    origin — against "today". Using the UTC date instead is wrong for part of
    every day by the origin's offset: east of UTC it keeps a departed flight
    looking upcoming, west of UTC it freezes one that hasn't left yet.
    """
    zone = timezone_for(origin)
    return now.astimezone(zone).date() if zone else now.date()


def departure_datetime(dtstart, origin: str | None) -> datetime | None:
    """The departure as an instant, expressed in the origin's own timezone.

    Stored so the boundary can be exact rather than a date comparison. All-day
    events have no time and yield None; a naive time is RFC 5545 floating, i.e.
    already local, so it is left as-is rather than shifted.
    """
    if not isinstance(dtstart, datetime):
        return None
    if dtstart.tzinfo is None:
        return dtstart
    zone = timezone_for(origin)
    return dtstart.astimezone(zone) if zone else dtstart


def departure_date(dtstart, origin: str | None) -> date | None:
    """The local calendar date of a departure at ``origin``.

    Falls back to the value as given whenever the conversion can't be trusted:

    - an all-day event has no time to convert
    - a naive datetime is already a local wall-clock time (RFC 5545 "floating"),
      so converting it would be wrong
    - an unrecognised airport code leaves nothing to convert against
    """
    if dtstart is None:
        return None
    if not isinstance(dtstart, datetime):
        return dtstart  # already a plain date
    if dtstart.tzinfo is None:
        return dtstart.date()

    zone = timezone_for(origin)
    if zone is None:
        return dtstart.date()
    return dtstart.astimezone(zone).date()
