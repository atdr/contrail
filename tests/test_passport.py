"""Passport: reducing the log to the facts the offline dashboard shows.

Everything here is pure. The template and the vendored assets are read from the
installed package, which is also what proves they were packaged at all.
"""

import json
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import pytest

from contrail import __version__
from contrail.passport import (
    build_data,
    great_circle_km,
    render,
    scheduled_hours,
)
from contrail.storage.local_csv import CSV_FIELDS, STATUS_CANCELLED

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
DEPARTURE = "2026-03-04T20:30:00-05:00"
ARRIVAL = "2026-03-05T08:15:00+00:00"


def row(**extra):
    data = {field: "" for field in CSV_FIELDS}
    data.update(
        source="tripit_ical",
        source_id="uid-1",
        flight_date="2026-03-04",
        carrier_code="BA",
        flight_number="112",
        origin="JFK",
        destination="LHR",
        departure_time=DEPARTURE,
        arrival_time=ARRIVAL,
        emissions_source="exact",
        emissions_kg_economy="247.027",
        emissions_kg_actual="247.027",
    )
    data.update(extra)
    return data


def only(rows, now=NOW):
    flights = build_data(rows, now)["flights"]
    assert len(flights) == 1
    return flights[0]


# -- distance -----------------------------------------------------------------


def test_distance_is_the_great_circle_between_bundled_coordinates():
    """Every flight measured the same way, which is the point of computing it
    here rather than reading TIM's own figure for the rows that have one."""
    assert great_circle_km("JFK", "LHR") == pytest.approx(5539.6, abs=1.0)


def test_distance_does_not_depend_on_the_direction_flown():
    assert great_circle_km("JFK", "LHR") == great_circle_km("LHR", "JFK")


def test_an_airport_with_no_coordinates_has_no_distance():
    """A flight still counts towards a total; it just cannot be plotted or
    measured, and inventing a distance would corrupt the per-kilometre figure."""
    assert great_circle_km("LHR", "ZZZ") is None
    assert only([row(destination="ZZZ")])["distanceKm"] is None
    assert only([row(destination="ZZZ")])["end"] is None


# -- scheduled block time -----------------------------------------------------


def test_block_time_spans_the_two_stored_instants():
    assert scheduled_hours(row()) == 6.75  # a JFK-LHR night flight, in two zones


def test_one_endpoint_alone_is_not_a_duration():
    assert scheduled_hours(row(arrival_time="")) is None
    assert scheduled_hours(row(departure_time="")) is None


def test_a_naive_instant_is_not_usable():
    """Without an offset there is no telling which zone it was written in, and
    a duration off by the zone difference would look perfectly plausible."""
    assert scheduled_hours(row(arrival_time="2026-03-05T08:15:00")) is None


@pytest.mark.parametrize("hours", [-2, 0, 37])
def test_a_duration_no_flight_has_is_refused(hours):
    """Arrival before departure is what a source that forgot the overnight date
    rollover produces; 36 hours is past the longest flight anyone sells."""
    arrival = datetime.fromisoformat(DEPARTURE) + timedelta(hours=hours)
    assert scheduled_hours(row(arrival_time=arrival.isoformat())) is None


# -- what a row becomes -------------------------------------------------------


def test_a_cancelled_flight_is_not_in_the_dashboard():
    """It counts towards no total in the CSV either — see `actual_kg`."""
    assert build_data([row(status=STATUS_CANCELLED)], NOW)["flights"] == []


def test_a_flight_with_no_stated_cabin_is_marked_as_assumed():
    """The figure it is counted at is economy, so the chart says economy — and
    says that it was assumed, which is what the estimate quality section reads."""
    flight = only([row()])
    assert flight["cabin"] == "Assumed economy"
    assert flight["cabinKnown"] is False

    flown = only([row(cabin_class_known="premium_economy")])
    assert flown["cabin"] == "Premium Economy"
    assert flown["cabinKnown"] is True


def test_a_route_reads_the_same_flown_either_way():
    """Otherwise a return trip is two routes and neither shows the real total."""
    assert only([row()])["route"] == only([row(origin="LHR", destination="JFK")])["route"]


def test_an_unpriced_flight_has_no_figure_rather_than_a_zero():
    assert only([row(emissions_source="no_data", emissions_kg_actual="")])["kg"] is None


def test_a_flight_is_departed_against_the_clock_it_is_given():
    assert only([row()], datetime(2026, 3, 5, 12, 0, tzinfo=UTC))["departed"] is True
    assert only([row()], datetime(2026, 3, 1, 12, 0, tzinfo=UTC))["departed"] is False


def test_years_are_newest_first_and_only_of_dated_flights():
    rows = [row(flight_date="2024-01-01"), row(), row(flight_date="")]
    assert build_data(rows, NOW)["years"] == [2026, 2024]


def test_the_meta_block_says_how_the_derived_figures_were_derived():
    meta = build_data([row()], NOW)["meta"]
    assert meta["contrailVersion"] == __version__
    assert meta["generatedAt"] == NOW.isoformat()
    assert "Haversine" in meta["distanceMethod"]
    assert "scheduled gate" in meta["durationMethod"]


# -- the written file ---------------------------------------------------------


def payload(document: str) -> dict:
    start = document.index('<script id="passport-data" type="application/json">')
    start = document.index(">", start) + 1
    return json.loads(document[start : document.index("</script>", start)])


def test_render_writes_one_self_contained_file(tmp_path):
    output = render([row()], tmp_path / "reports" / "passport.html", NOW)

    document = output.read_text(encoding="utf-8")
    assert output == (tmp_path / "reports" / "passport.html").resolve()  # made the directory
    for marker in ("PASSPORT_DATA", "WORLD_GEOJSON", "LEAFLET_CSS", "LEAFLET_JS", "CHARTJS_JS"):
        assert f"__{marker}__" not in document
    assert "L.map" in document and "Chart" in document  # the vendored assets landed


def test_the_page_fetches_nothing_when_it_opens(tmp_path):
    """The whole reason the assets are vendored: a dashboard of someone's travel
    history must not announce itself to a CDN or a tile server to render.

    The authored template is what this guards. The vendored bundles carry URLs
    of their own — Leaflet writes its attribution as an anchor — so scanning the
    rendered file for a hostname says nothing; scanning it for a map tile
    server, the one fetch this design could regrow, says plenty.
    """
    template = files("contrail.passport").joinpath("template.html").read_text(encoding="utf-8")
    for fetched in ('href="http', "href='http", 'src="http', "src='http", "url(http", "@import"):
        assert fetched not in template

    document = render([row()], tmp_path / "passport.html", NOW).read_text(encoding="utf-8")
    assert "<script src" not in document
    assert "tile.openstreetmap" not in document
    assert "L.tileLayer" not in document


def test_the_embedded_data_is_what_build_data_produced(tmp_path):
    output = render([row()], tmp_path / "passport.html", NOW)
    assert payload(output.read_text(encoding="utf-8")) == build_data([row()], NOW)


def test_itinerary_text_cannot_close_the_script_element(tmp_path):
    """The payload is user data sitting in HTML. A source that names an aircraft
    `</script>` would otherwise end the element early and break the page."""
    output = render([row(aircraft_type="</script><b>")], tmp_path / "passport.html", NOW)

    document = output.read_text(encoding="utf-8")
    assert payload(document)["flights"][0]["aircraft"] == "</script><b>"
