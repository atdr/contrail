"""Deciding what a sync may change about a flight it has already stored.

The governing rule: **anything that hasn't flown yet is fair game; anything in
the past is left alone.** That is partly conservatism and partly necessity —
TripIt's feed only exposes recent and upcoming trips, so "absent from the feed"
is ambiguous between *cancelled* and *aged out of the window*. Confining every
change to future flights removes the ambiguity, because a future flight cannot
have aged out.

There is exactly one exception, and it is narrow: :func:`backfill` may fill a
*blank* field on a past row when a second source turns out to know the same
flight. Its docstring explains why that doesn't reopen anything the freeze is
there to protect.
"""

from __future__ import annotations

from datetime import date, datetime

from contrail.airports import today_at
from contrail.models import FlightRecord
from contrail.storage.local_csv import STATUS_CANCELLED, is_cancelled, row_key

# The fields a source is authoritative about, and may correct on every sync.
FEED_FIELDS = (
    "flight_date",
    "departure_time",
    "arrival_time",
    "carrier_code",
    "flight_number",
    "operating_carrier_code",
    "operating_flight_number",
    "origin",
    "destination",
)

# Feed fields a *silent* source doesn't get to blank. Arrival is the one a feed
# may legitimately omit — an iCal VEVENT need not carry a DTEND — and `backfill`
# may have filled it from a second source that does state one. Reading that
# silence as a correction would wipe the value on one sync and refill it on the
# next. A *stated* arrival still corrects a stored one, because pinning it while
# `departure_time` moves is how a reschedule ends up with a departure and an
# arrival that describe no single flight.
OPTIONAL_FEED_FIELDS = ("arrival_time",)

# Fields a source may *fill in* but never overwrite. A stored value may be a
# source fact or a hand edit, and either way replacing it would be loss rather
# than a safe correction.
BACKFILL_FIELDS = ("cabin_class_known", "aircraft_type", "flight_reason")

# What makes two entries the same flight, whichever source found them.
#
# Route and date, deliberately not the flight number. One number can be two legs
# on one day — BA16 flies SYD-SIN-LHR, and they can be different cabins — so
# matching on the number would fold two flights into one and lose an emissions
# figure. Two flights on the same route on the same calendar day, on the other
# hand, is a thing one person cannot do.
IDENTITY_FIELDS = ("flight_date", "origin", "destination")

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
        "arrival_time": flight.arrival_time.isoformat() if flight.arrival_time else "",
        "carrier_code": flight.carrier_code,
        "flight_number": flight.flight_number,
        "operating_carrier_code": flight.operating_carrier_code or "",
        "operating_flight_number": flight.operating_flight_number or "",
        "origin": flight.origin,
        "destination": flight.destination,
    }


def stated(view: dict, field: str) -> bool:
    """Whether the feed said anything about a field it is allowed to omit."""
    return bool(view[field]) or field not in OPTIONAL_FEED_FIELDS


def corrections(view: dict) -> dict:
    """The feed values that replace what a stored row holds.

    Everything :func:`differences` reports, and nothing it doesn't: a field the
    two disagree about must be a field the merge then settles, or the row is
    reported changed on every sync from here to departure and never converges.
    """
    return {field: view[field] for field in FEED_FIELDS if stated(view, field)}


def differences(row: dict, flight: FlightRecord) -> list[str]:
    """Fields where the feed now disagrees with the stored row.

    A column the row simply doesn't have is back-fill, not disagreement. Gaining
    a column would otherwise make every stored row look changed on the first sync
    after an upgrade — and "changed" is what lets a worse figure replace a better
    one, on the grounds that a rebooked flight is a different flight. Applied to
    a whole file at once that would quietly downgrade every exact figure still
    open, then freeze it that way at departure.

    A feed saying nothing about an optional field is not disagreement either,
    for the same reason: silence is not a correction, so it cannot be one here
    and be ignored by the merge.
    """
    view = feed_view(flight)
    return [
        field
        for field in FEED_FIELDS
        if field in row and stated(view, field) and (row.get(field) or "") != view[field]
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


def identity(row: dict) -> tuple[str, str, str] | None:
    """What makes a stored row the *same flight* as a record from any source.

    None when a field is blank, which is what keeps an ``unparsed`` row — which
    routinely has no route — from matching every other undatable row in the file.
    """
    values = [(row.get(field) or "").strip() for field in IDENTITY_FIELDS]
    if not all(values):
        return None
    return (values[0], values[1].upper(), values[2].upper())


def links(row: dict) -> list[str]:
    """The other sources' keys stored against a row."""
    return (row.get("also_seen_as") or "").split()


def linked(row: dict, keys) -> dict:
    """Record other sources' keys against a row, sorted and without repeats.

    The row's own key is filtered out wherever it appears. The column means "who
    else calls this flight something", and a row that lists itself would be
    counted twice by anything joining on it — see ``storage/local_csv.py``.

    Sorted so that a row whose content has not changed stays byte-identical
    between runs — otherwise contrail-gh commits a reshuffled column every day.
    """
    own = row_key(row)
    return {**row, "also_seen_as": " ".join(sorted({*links(row), *keys} - {own}))}


def backfill(row: dict, flight: FlightRecord) -> tuple[dict, list[str]]:
    """Fold a second reading of a flight into the row that already owns it.

    Something else knows a flight this row already covers — another source
    reporting it, or a record that had another source's reading folded into it
    upstream. It does not get to restate the route, the date or the emissions:
    the owning source is authoritative for those, and a second opinion is not a
    correction. What it may do is fill in a blank, and leave its key behind so
    the two can be joined.

    ``status`` is not among the blanks it may fill, deliberately. A second source
    saying a flight was cancelled is not evidence against the source that owns the
    row and still reports it — a Flighty export in particular is a snapshot, and
    can be months stale. Where both sources are read in the same run the question
    doesn't arise: ``cli._collapse`` keeps a stated cancellation from either.

    **This is allowed on a row that has already departed**, which nothing else in
    this module is. The freeze exists because a past flight's absence is
    ambiguous and because TIM will not re-price one; neither applies here. Filling
    in arrival or descriptive detail changes no pricing; filling a cabin only
    changes which already-stored per-cabin figure ``emissions_kg_actual`` reads
    from, precisely the hand edit the README documents. A Flighty export is
    almost entirely past flights, so refusing it would refuse the point.
    """
    # Its own key plus everything folded into it. Without the folded keys a
    # codeshare listed twice in one export would lose one of its two ids.
    # ``linked`` drops the row's own key, whichever of these it turns out to be.
    merged = linked(row, [flight.key, *flight.also_seen])
    changed = [] if merged["also_seen_as"] == (row.get("also_seen_as") or "") else ["also_seen_as"]

    for field, value in (
        (
            "arrival_time",
            flight.arrival_time.isoformat() if flight.arrival_time else None,
        ),
        ("cabin_class_known", flight.cabin_class),
        ("aircraft_type", flight.aircraft_type),
        ("flight_reason", flight.flight_reason),
    ):
        if value and not (row.get(field) or "").strip():
            merged[field] = value
            changed.append(field)

    return merged, changed


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
