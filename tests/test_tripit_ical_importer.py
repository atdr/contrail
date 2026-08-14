"""Tests for the TripIt iCal importer.

These cover the cases the prototype was manually validated against: flight-event
detection (including correctly *excluding* trains and hotels), field extraction
from SUMMARY versus DESCRIPTION, and partial recovery on an unparseable event.
"""

from datetime import date

import pytest

from contrail.importers.tripit_ical import (
    TripItICalImporter,
    extract_flight_fields,
    fetch_ical,
)
from contrail.models import FlightRecord, UnparsedEvent


@pytest.fixture
def parsed(sample_feed_bytes):
    return list(TripItICalImporter().parse(sample_feed_bytes))


@pytest.fixture
def flights(parsed):
    return [item for item in parsed if isinstance(item, FlightRecord)]


@pytest.fixture
def unparsed(parsed):
    return [item for item in parsed if isinstance(item, UnparsedEvent)]


def test_non_flight_events_are_excluded(parsed):
    """The train and hotel events must not be picked up at all."""
    ids = {item.source_id for item in parsed}
    assert "item-44444444-dddd@example.invalid" not in ids  # train
    assert "item-55555555-eeee@example.invalid" not in ids  # hotel
    assert len(parsed) == 4


def test_parses_clean_summary(flights):
    """TripIt's usual 'XX123 AAA to BBB' SUMMARY is parsed from SUMMARY alone."""
    flight = next(f for f in flights if f.source_id == "item-11111111-aaaa@example.invalid")
    assert (flight.carrier_code, flight.flight_number) == ("XX", "123")
    assert (flight.origin, flight.destination) == ("AAA", "BBB")
    assert flight.flight_date == date(2026, 3, 4)
    assert flight.source == "tripit_ical"
    assert flight.cabin_class is None  # TripIt's feed never reports cabin


def test_falls_back_to_description_and_location(flights):
    """An unhelpful SUMMARY falls back to the combined description blob."""
    flight = next(f for f in flights if f.source_id == "item-22222222-bbbb@example.invalid")
    assert (flight.carrier_code, flight.flight_number) == ("YY", "456")
    assert (flight.origin, flight.destination) == ("CCC", "DDD")
    assert flight.flight_date == date(2026, 6, 10)  # date-only DTSTART


def test_detects_flight_without_the_tripit_tag(flights):
    """Events from other calendar tools are caught by the regex fallback."""
    flight = next(f for f in flights if f.source_id == "item-66666666-ffff@example.invalid")
    assert (flight.carrier_code, flight.flight_number) == ("ZZ", "7")
    assert (flight.origin, flight.destination) == ("BBB", "AAA")


def test_unparseable_event_keeps_what_it_recovered(unparsed):
    """A [Flight]-tagged event we can't parse still yields its date and full text."""
    assert len(unparsed) == 1
    event = unparsed[0]
    assert event.source_id == "item-33333333-cccc@example.invalid"
    assert event.partial["flight_date"] == date(2026, 7, 22)
    assert "carrier_code" not in event.partial
    # raw_text carries description too, not just SUMMARY — a failed parse usually
    # means the details were somewhere SUMMARY doesn't reach.
    assert "Flight home" in event.raw_text
    assert "QWERTY" in event.raw_text


def test_dedup_keys_are_namespaced_by_source(flights):
    for flight in flights:
        assert flight.key == f"tripit_ical:{flight.source_id}"


def test_summary_wins_over_noisier_blob():
    """Fields found in SUMMARY are preferred over the combined text."""
    result = extract_flight_fields(
        summary="AB100 LHR to JFK",
        description="[Flight] some other flight CD200 from X (SFO) to Y (LAX)",
        location="Heathrow (LHR)",
    )
    assert result == ("AB", "100", "LHR", "JFK")


def test_partial_summary_is_topped_up_from_blob():
    """A SUMMARY with only some fields keeps those and fills the rest from the blob."""
    carrier, number, origin, destination = extract_flight_fields(
        summary="LHR to JFK",
        description="[Flight] AB 100 confirmation XYZ",
        location="",
    )
    assert (origin, destination) == ("LHR", "JFK")
    assert (carrier, number) == ("AB", "100")


def test_fetch_reads_a_local_path(sample_feed_path):
    """Local paths and file:// URLs work, so CI can run --dry-run without network."""
    assert fetch_ical(str(sample_feed_path)).startswith(b"BEGIN:VCALENDAR")
    assert fetch_ical(sample_feed_path.as_uri()).startswith(b"BEGIN:VCALENDAR")


def test_fetch_requires_a_url_in_config():
    with pytest.raises(ValueError, match="needs a 'url'"):
        list(TripItICalImporter().fetch({}))
