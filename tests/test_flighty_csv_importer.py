"""Tests for the Flighty CSV importer. No test makes a real network call.

Every airline in the fixture resolves from the table shipped with contrail, so
the resolver is constructed with ``lookup=False`` throughout: if a lookup ever
does escape to the network, these tests fail rather than quietly passing.
"""

import csv

import pytest

from contrail.airlines import AirlineResolver
from contrail.importers.flighty_csv import FlightyCSVImporter, export_files
from contrail.models import FlightRecord, UnparsedEvent


@pytest.fixture
def importer():
    return FlightyCSVImporter(AirlineResolver(lookup=False))


@pytest.fixture
def items(importer, sample_flighty_path):
    return list(importer.fetch({"path": str(sample_flighty_path)}))


@pytest.fixture
def flights(items):
    return [i for i in items if isinstance(i, FlightRecord)]


def find(flights, origin, destination, date=None):
    matches = [
        f
        for f in flights
        if f.origin == origin
        and f.destination == destination
        and (date is None or f.flight_date.isoformat() == date)
    ]
    assert len(matches) == 1, f"expected one {origin}->{destination}, got {len(matches)}"
    return matches[0]


# -- parsing ------------------------------------------------------------------


def test_reads_every_row(items, flights):
    assert len(items) == 11
    assert len(flights) == 10


def test_maps_the_columns_contrail_needs(flights):
    flight = find(flights, "SFO", "LHR")
    assert flight.source == "flighty_csv"
    assert flight.source_id == "00000000-0000-4000-8000-000000000002"
    assert flight.flight_date.isoformat() == "2019-05-17"
    assert flight.carrier_code == "BA"  # BAW in the export; TIM wants IATA
    assert flight.flight_number == "286"
    assert flight.cabin_class == "business"
    assert flight.aircraft_type == "Boeing 777-200"
    assert flight.flight_reason == "business"
    assert flight.cancelled is False


def test_keeps_the_whole_source_row_for_joining_back(flights):
    """Seat and PNR get no column of their own; the raw dict is how they stay
    reachable, and the Flighty id is how the CSV row joins to them."""
    flight = find(flights, "SFO", "LHR")
    assert flight.raw["Seat"] == "2A"
    assert flight.raw["PNR"] == "BBB222"


def test_departure_time_is_localized_to_the_origin(flights):
    """Flighty writes naive local wall-clock. Attaching the origin's zone is what
    lets the freeze boundary be exact instead of a date comparison."""
    flight = find(flights, "SFO", "LHR")
    assert flight.departure_time.isoformat() == "2019-05-17T16:25:00-07:00"


def test_a_cancelled_flight_is_reported_as_such(flights):
    assert find(flights, "LGW", "EDI").cancelled is True


def test_private_is_not_a_cabin_contrail_knows(flights):
    """TIM's per-cabin figures describe a seat on an airliner and say nothing
    useful about a charter, so the honest answer is 'unknown'."""
    flight = find(flights, "STN", "IBZ")
    assert flight.cabin_class is None
    assert flight.raw["Cabin Class"] == "PRIVATE"


def test_a_row_that_cannot_be_parsed_keeps_what_it_had(items):
    unparsed = [i for i in items if isinstance(i, UnparsedEvent)]
    assert len(unparsed) == 1
    assert unparsed[0].partial["flight_date"].isoformat() == "2024-01-01"
    assert unparsed[0].partial["origin"] == "LHR"
    assert "destination" not in unparsed[0].partial
    assert "LHR" in unparsed[0].raw_text


def test_a_non_numeric_flight_number_does_not_parse(importer, sample_flighty_path):
    """TIM sends the number as an integer and fails a whole batch over one bad
    entry, so a number it cannot take is not a successful parse."""
    lines = sample_flighty_path.read_text().splitlines(keepends=True)
    lines[1] = lines[1].replace(",8001,", ",N/A,")
    items = list(importer.parse(lines))
    assert isinstance(items[0], UnparsedEvent)
    assert items[0].partial["flight_number"] == "N/A"


# -- identity -----------------------------------------------------------------


def test_legs_of_one_flight_number_stay_separate(flights):
    """BA16 flies SYD-SIN-LHR. Two legs, two cabins, two emissions figures — so
    two identities. Matching on the flight number would fold them into one."""
    first = find(flights, "SYD", "SIN")
    second = find(flights, "SIN", "LHR")

    assert first.flight_number == second.flight_number == "16"
    assert first.identity != second.identity
    assert (first.cabin_class, second.cabin_class) == ("business", "first")


def test_a_codeshare_listed_twice_shares_one_identity(flights):
    """The same flight under both its marketing and operating number. The
    importer reports both; collapsing them is reconciliation's job."""
    pair = [f for f in flights if f.identity == ("2023-04-02", "LHR", "MAD")]
    assert {f.carrier_code + f.flight_number for f in pair} == {"IB3643", "BA458"}


# -- where the file comes from ------------------------------------------------


def test_accepts_a_directory_and_reads_newest_first(tmp_path, importer, sample_flighty_path):
    """The CLI keeps the first record it sees for a key, so the newest export has
    to come first for it to win. Flighty names exports by date, and a checkout
    gives every file the same mtime, so ordering is by name."""
    rows = list(csv.DictReader(sample_flighty_path.open()))
    header = ",".join(rows[0]) + "\n"

    def export(name, cabin):
        row = {**rows[0], "Cabin Class": cabin}
        (tmp_path / name).write_text(header + ",".join(row.values()) + "\n")

    export("FlightyExport-2024-01-01.csv", "ECONOMY")
    export("FlightyExport-2026-01-01.csv", "FIRST")

    found = list(importer.fetch({"path": str(tmp_path)}))
    assert [f.cabin_class for f in found] == ["first", "economy"]


def test_accepts_a_glob(tmp_path, importer, sample_flighty_path):
    (tmp_path / "FlightyExport-2026-01-01.csv").write_text(sample_flighty_path.read_text())
    found = list(importer.fetch({"path": str(tmp_path / "Flighty*.csv")}))
    assert len(found) == 11


def test_an_empty_directory_yields_nothing(tmp_path, importer):
    """What makes the contrail-gh template's empty flighty/ inert rather than an
    error on every scheduled run."""
    assert list(importer.fetch({"path": str(tmp_path)})) == []
    assert list(export_files(str(tmp_path))) == []


def test_a_path_that_matches_nothing_warns_rather_than_failing(tmp_path, importer, capsys):
    """An export arrives by hand, so "not there yet" is ordinary. Raising would
    let one unconfigured source take down a sync with a good TripIt feed in it."""
    assert list(importer.fetch({"path": str(tmp_path / "nope" / "*.csv")})) == []
    assert "No Flighty export found" in capsys.readouterr().err


def test_a_missing_path_and_an_empty_directory_behave_alike(tmp_path, importer, capsys):
    assert list(importer.fetch({"path": str(tmp_path)})) == []
    empty_dir = capsys.readouterr().err
    assert list(importer.fetch({"path": str(tmp_path / "gone")})) == []
    assert bool(empty_dir) == bool(capsys.readouterr().err)


def test_a_source_without_a_path_is_an_error(importer):
    with pytest.raises(ValueError, match="needs a 'path'"):
        list(importer.fetch({}))


def test_export_files_sorts_newest_first(tmp_path):
    for name in ("FlightyExport-2024-05-01.csv", "FlightyExport-2026-08-15.csv"):
        (tmp_path / name).touch()
    assert [p.name for p in export_files(str(tmp_path))] == [
        "FlightyExport-2026-08-15.csv",
        "FlightyExport-2024-05-01.csv",
    ]
