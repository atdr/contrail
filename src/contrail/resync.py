"""Deciding what a sync may change about a flight it has already stored.

The governing rule: **anything that hasn't flown yet is fair game; anything in
the past is left alone.** That is partly conservatism and partly necessity —
TripIt's feed only exposes recent and upcoming trips, so "absent from the feed"
is ambiguous between *cancelled* and *aged out of the window*. Confining every
change to future flights removes the ambiguity, because a future flight cannot
have aged out.
"""

from __future__ import annotations

from datetime import date, datetime

from contrail.airports import today_at
from contrail.models import FlightRecord
from contrail.storage.local_csv import STATUS_CANCELLED, is_cancelled

# The fields a source is authoritative about. cabin_class_known is deliberately
# absent: no importer can currently supply it, so overwriting it would be data
# loss rather than a correction.
FEED_FIELDS = (
    "flight_date",
    "departure_time",
    "carrier_code",
    "flight_number",
    "operating_carrier_code",
    "operating_flight_number",
    "origin",
    "destination",
)

# How good a figure is, so a transient miss can never downgrade a stored one.
QUALITY = {
    "exact": 3,
    "typical_route_average": 2,
    "no_data": 1,
    "unparsed": 0,
    "": 0,
}


def row_date(row: dict) -> date | None:
    try:
        return datetime.strptime(row["flight_date"], "%Y-%m-%d").date()
    except (KeyError, TypeError, ValueError):
        return None


def row_departure(row: dict) -> datetime | None:
    """The stored departure instant, if the source gave a time."""
    value = (row.get("departure_time") or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def has_departed(row: dict, now: datetime) -> bool | None:
    """Whether the flight has already gone, or None when it can't be told.

    Three levels of confidence, best first:

    1. A stored departure instant answers exactly, with no date fuzz at all.
    2. Otherwise compare the local date *at the origin* — never the UTC date,
       which is wrong for part of every day by the origin's offset.
    3. With no usable origin, fall back to the UTC date.
    """
    departure = row_departure(row)
    if departure is not None:
        return now >= departure

    flight_date = row_date(row)
    if flight_date is None:
        return None
    return flight_date < today_at(row.get("origin"), now)


def is_open(row: dict, now: datetime) -> bool:
    """True while the flight still hasn't departed, so contrail may change it.

    A row we cannot date counts as open: it is almost always an unparsed event
    still sitting in the feed, and keeping it open is what lets it upgrade once
    the feed fills the details in.
    """
    departed = has_departed(row, now)
    return True if departed is None else not departed


def can_cancel(row: dict, now: datetime) -> bool:
    """Whether disappearing from the feed may be read as a cancellation.

    Stricter than :func:`is_open`: an undatable row is left alone, because there
    is no way to tell a cancellation from a flight that simply aged out of a
    feed that only carries recent trips.
    """
    departed = has_departed(row, now)
    return departed is False


def feed_view(flight: FlightRecord) -> dict:
    """What the feed says about a flight, in the CSV's own string terms."""
    return {
        "flight_date": flight.flight_date.isoformat(),
        "departure_time": flight.departure_time.isoformat() if flight.departure_time else "",
        "carrier_code": flight.carrier_code,
        "flight_number": flight.flight_number,
        "operating_carrier_code": flight.operating_carrier_code or "",
        "operating_flight_number": flight.operating_flight_number or "",
        "origin": flight.origin,
        "destination": flight.destination,
    }


def differences(row: dict, flight: FlightRecord) -> list[str]:
    """Fields where the feed now disagrees with the stored row.

    A column the row simply doesn't have is back-fill, not disagreement. Gaining
    a column would otherwise make every stored row look changed on the first sync
    after an upgrade — and "changed" is what lets a worse figure replace a better
    one, on the grounds that a rebooked flight is a different flight. Applied to
    a whole file at once that would quietly downgrade every exact figure still
    open, then freeze it that way at departure.
    """
    view = feed_view(flight)
    return [
        field for field in FEED_FIELDS if field in row and (row.get(field) or "") != view[field]
    ]


def is_better(new_method: str, existing_method: str) -> bool:
    """Whether a freshly computed figure is at least as good as the stored one."""
    return QUALITY.get(new_method, 0) >= QUALITY.get(existing_method or "", 0)


# Every open row is re-priced on every sync, changed or not. Skipping rows
# already priced `exact` looks like an easy saving and is wrong: TIM's exact
# figure depends on the aircraft, and short-haul equipment changes repeatedly
# right up to departure — A319/A320/A321, ceo against neo. A figure captured
# weeks out can be stale by departure day, and letting it keep catching up is
# the entire point of freezing only once the flight goes.


def restored(row: dict) -> bool:
    """A cancelled row that has reappeared in the feed."""
    return is_cancelled(row)


def cancel(row: dict) -> dict:
    """Mark a row cancelled, keeping every figure it already had.

    Only ``emissions_kg_actual`` is cleared, by ``actual_kg`` on the next
    normalize; the per-cabin figures stay because TIM will never price a past
    flight again, so a wrong cancellation would otherwise destroy them for good.
    """
    return {**row, "status": STATUS_CANCELLED}
