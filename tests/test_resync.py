"""Re-syncing: what a sync may and may not change about a stored flight.

The rule under test throughout: a flight that hasn't departed is contrail's to
correct; one that has is left alone.
"""

from datetime import date, datetime, timezone

import pytest

from contrail.cli import _merge_row, reconcile
from contrail.models import EmissionsResult, FlightRecord, UnparsedEvent
from contrail.resync import can_cancel, differences, is_better, is_open
from contrail.storage import normalize_rows, total_kg
from contrail.storage.local_csv import CSV_FIELDS, STATUS_CANCELLED, actual_kg

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
PAST = "2026-05-01"
FUTURE = "2026-12-01"


def row(source_id="uid-1", flight_date=FUTURE, **extra):
    data = {field: "" for field in CSV_FIELDS}
    data.update(
        sync_timestamp="2026-01-01T00:00:00+00:00",
        source="tripit_ical",
        source_id=source_id,
        flight_date=flight_date,
        carrier_code="BA",
        flight_number="896",
        origin="LHR",
        destination="PFO",
        emissions_source="typical_route_average",
        emissions_kg_economy="247.027",
        emissions_kg_actual="247.027",
    )
    data.update(extra)
    return data


def flight(source_id="uid-1", flight_date=date(2026, 12, 1), **extra):
    kwargs = dict(
        source="tripit_ical",
        source_id=source_id,
        flight_date=flight_date,
        carrier_code="BA",
        flight_number="896",
        origin="LHR",
        destination="PFO",
    )
    kwargs.update(extra)
    return FlightRecord(**kwargs)


def result(method="exact", economy=200000):
    return EmissionsResult(
        method=method,
        model_version="3" if method == "exact" else None,
        grams_first=800000,
        grams_business=600000,
        grams_premium_economy=400000,
        grams_economy=economy,
    )


# -- the open/frozen boundary ------------------------------------------------


def test_a_flight_departing_today_is_still_open():
    """TIM still answers for a flight departing today, so it can still improve."""
    assert is_open(row(flight_date="2026-08-14"), NOW)


def test_yesterdays_flight_is_frozen():
    assert not is_open(row(flight_date="2026-08-13"), NOW)


def test_a_row_with_no_date_stays_open_but_is_never_cancelled():
    """Usually an unparsed event still in the feed: keep it eligible to upgrade,
    but absence can't be read as cancellation when we can't date it."""
    dateless = row(flight_date="")
    assert is_open(dateless, NOW)
    assert not can_cancel(dateless, NOW)


# -- reconciliation ----------------------------------------------------------


def test_departed_flights_are_never_touched():
    stored = [row(flight_date=PAST)]
    changed = flight(flight_date=date(2026, 5, 2), destination="MAD")

    plan = reconcile(stored, [changed], [], NOW)

    assert plan.updates == []
    assert plan.cancellations == []
    assert not plan


def test_an_upcoming_flight_still_in_the_feed_is_repriced():
    """Even with nothing changed. Short-haul aircraft swap right up to
    departure, so an exact figure from weeks ago can be stale."""
    stored = [row(emissions_source="exact")]
    plan = reconcile(stored, [flight()], [], NOW)

    assert len(plan.updates) == 1
    _, _, fields = plan.updates[0]
    assert fields == []  # nothing differs, but it is still re-priced
    assert plan.repriceable


def test_a_changed_upcoming_flight_reports_which_fields_moved():
    stored = [row()]
    moved = flight(flight_date=date(2026, 12, 3), destination="MAD")

    plan = reconcile(stored, [moved], [], NOW)
    _, _, fields = plan.updates[0]

    assert set(fields) == {"flight_date", "destination"}


def test_an_upcoming_flight_missing_from_the_feed_is_cancelled():
    """The source did report other flights, so this one's absence means something."""
    stored = [row(source_id="gone"), row(source_id="kept")]
    plan = reconcile(stored, [flight(source_id="kept")], [], NOW)

    assert [r["source_id"] for r in plan.cancellations] == ["gone"]


def test_a_source_that_returned_nothing_cancels_none_of_its_rows():
    """A rotated feed URL, or a source dropped from the config, must not read as
    'every one of those flights was called off'."""
    stored = [
        row(source_id="tripit-a"),
        {**row(source_id="flighty-b"), "source": "flighty_csv"},
    ]
    plan = reconcile(stored, [flight(source_id="tripit-a")], [], NOW)

    assert plan.cancellations == []  # flighty_csv said nothing at all this run


def test_an_entirely_empty_feed_cancels_nothing():
    plan = reconcile([row()], [], [], NOW)
    assert plan.cancellations == []


def test_a_key_arriving_as_both_parsed_and_unparsed_is_written_once():
    """Two UID-less events can hash alike while only one parses. Writing both
    would duplicate a dedup key forever and discard the priced flight."""
    event = UnparsedEvent(source="tripit_ical", source_id="uid-1", raw_text="same key")
    plan = reconcile([], [flight()], [event], NOW)

    assert len(plan.new_flights) == 1
    assert plan.new_unparsed == []  # the parsed reading wins


def test_a_departed_flight_missing_from_the_feed_is_left_alone():
    """TripIt only exposes recent trips, so a past flight leaving the feed means
    it aged out — not that it was cancelled."""
    plan = reconcile([row(flight_date=PAST)], [], [], NOW)
    assert plan.cancellations == []


def test_a_cancelled_flight_that_reappears_is_restored():
    stored = [row(status=STATUS_CANCELLED)]
    plan = reconcile(stored, [flight()], [], NOW)

    assert len(plan.restorations) == 1
    assert len(plan.updates) == 1


def test_an_already_cancelled_flight_is_not_cancelled_again():
    plan = reconcile([row(status=STATUS_CANCELLED)], [], [], NOW)
    assert plan.cancellations == []


def test_a_flight_not_yet_stored_is_new():
    plan = reconcile([], [flight()], [], NOW)
    assert len(plan.new_flights) == 1
    assert plan.updates == []


# -- merging -----------------------------------------------------------------


def test_a_better_figure_replaces_a_worse_one():
    merged = _merge_row(row(), flight(), result("exact"), "NOW", changed=False)
    assert merged["emissions_source"] == "exact"
    assert merged["emissions_kg_economy"] == "200.0"
    assert merged["sync_timestamp"] == "NOW"


def test_a_worse_figure_is_refused_on_an_unchanged_flight():
    """A transient TIM miss must not downgrade a good stored figure."""
    stored = row(emissions_source="exact", emissions_kg_economy="97.125", model_version="3")
    merged = _merge_row(stored, flight(), None, "NOW", changed=False)

    assert merged["emissions_source"] == "exact"
    assert merged["emissions_kg_economy"] == "97.125"
    assert merged["sync_timestamp"] == stored["sync_timestamp"]  # nothing moved


def test_a_worse_figure_is_accepted_when_the_flight_itself_changed():
    """Rebooked to a different route, so the old figure describes another
    flight and has to go even if the replacement is weaker."""
    stored = row(emissions_source="exact", emissions_kg_economy="97.125")
    rerouted = flight(destination="MAD")
    merged = _merge_row(stored, rerouted, result("typical_route_average"), "NOW", changed=True)

    assert merged["emissions_source"] == "typical_route_average"
    assert merged["destination"] == "MAD"


def test_cabin_class_survives_a_rebuild():
    """No importer reports cabin, so overwriting it would destroy the only copy."""
    stored = row(cabin_class_known="business", emissions_kg_business="600.0")
    merged = _merge_row(stored, flight(destination="MAD"), result(), "NOW", changed=True)

    assert merged["cabin_class_known"] == "business"
    assert merged["emissions_kg_actual"] == merged["emissions_kg_business"]


def test_an_unchanged_row_keeps_its_original_timestamp():
    """Otherwise every daily sync rewrites the file and commits for nothing."""
    stored = row(
        emissions_source="exact",
        model_version="3",
        emissions_kg_first="800.0",
        emissions_kg_business="600.0",
        emissions_kg_premium_economy="400.0",
        emissions_kg_economy="200.0",
        emissions_kg_actual="200.0",
    )
    merged = _merge_row(stored, flight(), result("exact"), "NOW", changed=False)

    assert merged == stored
    assert merged["sync_timestamp"] == stored["sync_timestamp"]


def test_a_moved_figure_does_bump_the_timestamp():
    """The mirror of the above: a genuine change must be recorded."""
    stored = row(
        emissions_source="exact",
        model_version="3",
        emissions_kg_first="800.0",
        emissions_kg_business="600.0",
        emissions_kg_premium_economy="400.0",
        emissions_kg_economy="200.0",
        emissions_kg_actual="200.0",
    )
    swapped = _merge_row(stored, flight(), result("exact", economy=180000), "NOW", changed=False)

    assert swapped["emissions_kg_economy"] == "180.0"
    assert swapped["sync_timestamp"] == "NOW"


# -- cancelled rows and the total --------------------------------------------


def test_a_cancelled_row_keeps_its_figures_but_leaves_the_total():
    """The per-cabin numbers stay recoverable: once the date passes TIM will
    never price that flight again."""
    rows = [
        row(source_id="flown", flight_date=PAST, emissions_kg_economy="100.0"),
        row(source_id="scrapped", status=STATUS_CANCELLED),
    ]
    normalize_rows(rows)

    cancelled = next(r for r in rows if r["source_id"] == "scrapped")
    assert cancelled["emissions_kg_actual"] == ""
    assert cancelled["emissions_kg_economy"] == "247.027"  # preserved
    assert total_kg(rows) == 100.0


def test_actual_kg_is_blank_for_a_cancelled_row_whatever_the_cabin():
    assert actual_kg(row(status=STATUS_CANCELLED, cabin_class_known="business")) == ""


def test_restoring_a_row_brings_it_back_into_the_total():
    stored = row(status=STATUS_CANCELLED)
    merged = _merge_row(stored, flight(), result("exact"), "NOW", changed=False)
    normalize_rows([merged])

    assert merged["status"] == ""
    assert merged["emissions_kg_actual"] == "200.0"


# -- small helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("new", "old", "expected"),
    [
        ("exact", "typical_route_average", True),
        ("exact", "exact", True),
        ("typical_route_average", "exact", False),
        ("no_data", "typical_route_average", False),
        ("typical_route_average", "unparsed", True),
    ],
)
def test_quality_ordering(new, old, expected):
    assert is_better(new, old) is expected


def test_differences_ignores_cabin_class():
    """cabin_class_known is never compared: the feed has no opinion on it."""
    stored = row(cabin_class_known="business")
    assert differences(stored, flight()) == []


def test_a_blank_answer_never_erases_hand_entered_figures():
    """TIM returns nothing for a flight it can't price, and the README tells
    people to fill exactly those rows in themselves."""
    stored = row(
        emissions_source="no_data",
        emissions_kg_economy="250.0",
        emissions_kg_actual="250.0",
    )
    merged = _merge_row(stored, flight(), None, "NOW", changed=False)

    assert merged["emissions_kg_economy"] == "250.0"
    assert merged["emissions_kg_actual"] == "250.0"


def test_a_blank_answer_does_not_erase_them_even_when_the_flight_changed():
    stored = row(
        emissions_source="unparsed",
        emissions_kg_economy="250.0",
        emissions_kg_actual="250.0",
    )
    merged = _merge_row(stored, flight(destination="MAD"), None, "NOW", changed=True)

    assert merged["destination"] == "MAD"  # details still corrected
    assert merged["emissions_kg_economy"] == "250.0"  # the figure survives


def test_a_blank_answer_is_fine_on_a_row_that_had_no_figures():
    stored = row(emissions_source="", emissions_kg_economy="", emissions_kg_actual="")
    merged = _merge_row(stored, flight(), None, "NOW", changed=False)
    assert merged["emissions_source"] == "no_data"


def test_raw_summary_follows_a_reroute():
    stored = row(raw_summary="BA896 LHR to PFO")
    rerouted = flight(destination="MAD", raw={"summary": "BA896 LHR to MAD"})
    merged = _merge_row(stored, rerouted, result(), "NOW", changed=True)

    assert merged["raw_summary"] == "BA896 LHR to MAD"


# -- the freeze boundary, in the origin's timezone ---------------------------


def at(tzname, y, m, d, hh, mm=0):
    from zoneinfo import ZoneInfo

    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tzname))


def test_a_departed_flight_east_of_utc_is_frozen():
    """At 08:00 in Tokyo the UTC date is still yesterday. Comparing against it
    left a flown flight looking upcoming — and therefore cancellable."""
    flown = row(flight_date="2026-10-11", origin="HND", departure_time="")
    now = at("Asia/Tokyo", 2026, 10, 12, 8)

    assert not is_open(flown, now)
    assert not can_cancel(flown, now)


def test_an_upcoming_flight_west_of_utc_stays_open():
    """At 18:00 in Los Angeles the UTC date is already tomorrow, which used to
    freeze a flight hours before it left."""
    upcoming = row(flight_date="2026-10-11", origin="LAX", departure_time="")
    assert is_open(upcoming, at("America/Los_Angeles", 2026, 10, 11, 18))


def test_a_stored_departure_time_is_exact_to_the_minute():
    flight_row = row(
        flight_date="2026-10-11",
        origin="LAX",
        departure_time="2026-10-11T20:00:00-07:00",
    )
    assert is_open(flight_row, at("America/Los_Angeles", 2026, 10, 11, 19, 59))
    assert not is_open(flight_row, at("America/Los_Angeles", 2026, 10, 11, 20, 1))


def test_an_unknown_airport_falls_back_to_the_utc_date():
    unknown = row(flight_date="2026-10-11", origin="QQQ", departure_time="")
    assert is_open(unknown, datetime(2026, 10, 11, 23, 0, tzinfo=timezone.utc))
    assert not is_open(unknown, datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc))


def test_a_naive_or_unparseable_departure_time_is_ignored():
    """Falls through to the date comparison rather than guessing at an offset."""
    for value in ("2026-10-11T20:00:00", "not a timestamp"):
        r = row(flight_date="2026-10-11", origin="LAX", departure_time=value)
        assert is_open(r, at("America/Los_Angeles", 2026, 10, 11, 18))
