"""Tests for the OpenFlights CSV importer. No test makes a real network call.

The resolver is constructed with ``lookup=False`` throughout, so an airline the
shipped table can't name stays unresolved rather than escaping to Wikidata. The
fixture leans on that: easyJet and Qantas are not in the table under those
names, which is what exercises the flight-number prefix.
"""

import io

import pytest

from contrail.airlines import AirlineResolver
from contrail.importers.openflights_csv import OpenFlightsCSVImporter, export_files
from contrail.models import FlightRecord, UnparsedEvent

HEADER = (
    "Date,From,To,Flight_Number,Airline,Distance,Duration,Seat,Seat_Type,Class,Reason,"
    "Plane,Registration,Trip,Note,From_OID,To_OID,Airline_OID,Plane_OID\n"
)


@pytest.fixture
def importer():
    return OpenFlightsCSVImporter(AirlineResolver(lookup=False))


@pytest.fixture
def items(importer, sample_openflights_path):
    return list(importer.fetch({"path": str(sample_openflights_path)}))


@pytest.fixture
def flights(items):
    return [i for i in items if isinstance(i, FlightRecord)]


def lines(*rows):
    return io.StringIO(HEADER + "".join(row + "\n" for row in rows))


def row(date="2019-05-17 16:25:00", origin="SFO", dest="LHR", number="286", **kw):
    airline = kw.get("airline", "British Airways")
    duration = kw.get("duration", "10:25")
    cabin, reason = kw.get("cabin", "C"), kw.get("reason", "B")
    return f'{date},{origin},{dest},{number},{airline},5367,{duration},,,{cabin},{reason},,,,"",,,,'


def find(flights, origin, destination):
    matches = [f for f in flights if f.origin == origin and f.destination == destination]
    assert matches, f"no {origin}->{destination}"
    return matches


def test_reads_the_fixture(items, flights):
    assert len(items) == 9
    assert len(flights) == 7


def test_a_byte_order_mark_does_not_hide_the_date_column(flights):
    """The backup starts with one, and read naively it renames ``Date``."""
    assert all(f.flight_date for f in flights)


def test_a_timed_row_departs_in_the_origins_zone(flights):
    (sfo,) = find(flights, "SFO", "LHR")
    assert sfo.departure_time.isoformat() == "2019-05-17T16:25:00-07:00"
    assert sfo.flight_date.isoformat() == "2019-05-17"


def test_a_bare_date_states_no_time(flights):
    (gva,) = find(flights, "LHR", "GVA")
    assert gva.flight_date.isoformat() == "2015-03-02"
    assert gva.departure_time is None


def test_no_row_gets_an_arrival_time(flights):
    """Duration may be OpenFlights' own estimate, and an arrival derived from it
    would read as a schedule."""
    assert all(f.arrival_time is None for f in flights)


@pytest.mark.parametrize(
    ("airline", "typed", "expected"),
    [
        ("British Airways", "286", ("BA", "286")),
        ("British Airways", "BA0286", ("BA", "286")),
        ("British Airways", "BAW0117", ("BA", "117")),
        ("British Airways", "0730", ("BA", "730")),
        ("British Airways", "ba 286", ("BA", "286")),
        # A prefix names its own airline, which the column may not.
        ("easyJet", "U28001", ("U2", "8001")),
        ("ENTER Air", "E4533", ("E4", "533")),
        ("Privilege Style", "P63449", ("P6", "3449")),
        # Leading zeros of a bare number are not a prefix.
        ("Thomsonfly", "035B", ("BY", "35B")),
        ("Thomsonfly", "0123A", ("BY", "123A")),
        ("easyJet", "6261", (None, "6261")),
    ],
)
def test_carrier_and_number(importer, airline, typed, expected):
    assert importer.carrier_and_number(airline, typed) == expected


def test_a_flight_number_prefix_resolves_an_airline_the_table_cannot(flights):
    (bcn,) = find(flights, "LGW", "BCN")
    assert (bcn.carrier_code, bcn.flight_number) == ("U2", "8001")


def test_a_letter_suffixed_charter_is_unparsed(items):
    (charter,) = [i for i in items if isinstance(i, UnparsedEvent) and "714K" in i.raw_text]
    assert charter.partial["flight_number"] == "714K"
    assert charter.partial["origin"] == "BRS"
    assert charter.partial["flight_date"].isoformat() == "2009-02-14"


def test_an_unresolvable_airline_is_unparsed(items):
    (pfo,) = [i for i in items if isinstance(i, UnparsedEvent) and "PFO" in i.raw_text]
    assert "carrier_code" not in pfo.partial
    assert pfo.source_id == "2012-07-16:BRS-PFO"


@pytest.mark.parametrize(
    ("code", "expected"),
    [("F", "first"), ("C", "business"), ("P", "premium_economy"), ("Y", "economy"), ("", None)],
)
def test_cabin_class(importer, code, expected):
    (flight,) = importer.parse(lines(row(cabin=code)))
    assert flight.cabin_class == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [("B", "business"), ("L", "leisure"), ("O", None), ("C", None), ("", None)],
)
def test_flight_reason(importer, code, expected):
    """O (other) and C (crew) say nothing about business or leisure."""
    (flight,) = importer.parse(lines(row(reason=code)))
    assert flight.flight_reason == expected


def test_aircraft_is_taken_as_written(flights):
    (sfo,) = find(flights, "SFO", "LHR")
    assert sfo.aircraft_type == "Boeing 777-200"
    (gva,) = find(flights, "LHR", "GVA")
    assert gva.aircraft_type is None


def test_the_key_is_the_date_and_route(flights):
    (sfo,) = find(flights, "SFO", "LHR")
    assert sfo.key == "openflights_csv:2019-05-17:SFO-LHR"


def test_editing_a_row_keeps_its_key(importer):
    """No per-flight id exists, so anything else in the key would re-key a row
    whenever it was corrected in OpenFlights, and an unparsed row would then be
    added a second time."""
    (before,) = importer.parse(lines(row(number="714K", cabin="Y")))
    (after,) = importer.parse(lines(row(date="2019-05-17 23:59:00", number="0714", cabin="F")))
    assert before.key == after.key


def test_one_route_twice_in_a_day_is_numbered_in_departure_order(flights):
    """Departure order rather than file order, so moving a row in OpenFlights
    does not swap the two keys."""
    first, second = sorted(find(flights, "LHR", "JFK"), key=lambda f: f.departure_time)
    assert first.source_id == "2018-08-29:LHR-JFK"
    assert second.source_id == "2018-08-29:LHR-JFK#2"


def test_a_leg_departing_before_the_previous_lands_is_reported(
    importer, sample_openflights_path, capsys
):
    """The fixture's SIN-SYD connection is dated a day early, so it departs
    while the LHR-SIN leg it connects from is still in the air."""
    list(importer.fetch({"path": str(sample_openflights_path)}))
    err = capsys.readouterr().err
    assert err.count("WARNING") == 1
    assert "LHR-SIN at 2023-01-09 21:00:00 departing before QF1 SIN-SYD" in err


def test_legs_in_order_are_not_reported(importer, capsys):
    list(
        importer.parse(
            lines(
                row(date="2019-05-17 16:25:00"),
                row(date="2019-05-18 16:25:00", origin="LHR", dest="SFO"),
            )
        )
    )
    assert capsys.readouterr().err == ""


def test_a_missing_path_warns_rather_than_failing(tmp_path, importer, capsys):
    assert list(importer.fetch({"path": str(tmp_path / "nope.csv")})) == []
    assert "No OpenFlights export found" in capsys.readouterr().err


def test_a_source_without_a_path_is_an_error(importer):
    with pytest.raises(ValueError, match="needs a 'path'"):
        list(importer.fetch({}))


def test_fetch_can_override_airline_lookup(tmp_path, importer):
    assert list(importer.fetch({"path": str(tmp_path), "airline_lookup": True})) == []
    assert importer.resolver.lookup is True


def test_export_files_sorts_newest_first(tmp_path):
    names = ("openflights-backup-2024-05-01-0900.csv", "openflights-backup-2026-10-22-1914.csv")
    for name in names:
        (tmp_path / name).touch()
    assert [p.name for p in export_files(str(tmp_path))] == list(reversed(names))
