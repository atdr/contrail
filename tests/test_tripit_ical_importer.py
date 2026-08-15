"""Tests for the TripIt iCal importer.

Flight-event detection (including correctly *excluding* trains and hotels), field
extraction from SUMMARY versus DESCRIPTION, timezone-correct dates, codeshare
resolution, and partial recovery on an unparseable event.
"""

from datetime import date

import pytest

from contrail.airlines import AirlineResolver
from contrail.importers.tripit_ical import (
    TripItICalImporter,
    extract_flight_fields,
    extract_operating_flight,
    fetch_ical,
)
from contrail.models import FlightRecord, UnparsedEvent


@pytest.fixture
def parsed(sample_feed_bytes):
    # lookup disabled: the fixture must resolve from the feed alone, so the
    # suite (and CI's --dry-run) never touches the network.
    importer = TripItICalImporter(resolver=AirlineResolver(lookup=False))
    return list(importer.parse(sample_feed_bytes))


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
    assert len(parsed) == 6


def test_parses_clean_summary(flights):
    """TripIt's usual 'XX123 JFK to LHR' SUMMARY is parsed from SUMMARY alone."""
    flight = next(f for f in flights if f.source_id == "item-11111111-aaaa@example.invalid")
    assert (flight.carrier_code, flight.flight_number) == ("XX", "123")
    assert (flight.origin, flight.destination) == ("JFK", "LHR")
    assert flight.source == "tripit_ical"
    assert flight.cabin_class is None  # TripIt's feed never reports cabin

    # DTSTART is 2026-03-05T01:30Z, but that is 20:30 on 2026-03-04 at JFK.
    # TIM wants the local date at the origin, so this must not be the UTC date.
    assert flight.flight_date == date(2026, 3, 4)


def test_falls_back_to_description_and_location(flights):
    """An unhelpful SUMMARY falls back to the combined description blob."""
    flight = next(f for f in flights if f.source_id == "item-22222222-bbbb@example.invalid")
    assert (flight.carrier_code, flight.flight_number) == ("YY", "456")
    assert (flight.origin, flight.destination) == ("CDG", "FRA")
    assert flight.flight_date == date(2026, 6, 10)  # date-only DTSTART, nothing to convert


def test_detects_flight_without_the_tripit_tag(flights):
    """Events from other calendar tools are caught by the regex fallback."""
    flight = next(f for f in flights if f.source_id == "item-66666666-ffff@example.invalid")
    assert (flight.carrier_code, flight.flight_number) == ("ZZ", "7")
    assert (flight.origin, flight.destination) == ("LHR", "JFK")
    # London is on GMT in November, so the UTC date is already the local one.
    assert flight.flight_date == date(2026, 11, 18)


def test_unparseable_event_keeps_what_it_recovered(unparsed):
    """A [Flight]-tagged event we can't parse still yields its date and full text."""
    assert len(unparsed) == 1
    event = unparsed[0]
    assert event.source_id == "item-33333333-cccc@example.invalid"
    # No origin was recovered, so there is nothing to convert against.
    assert event.partial["flight_date"] == date(2026, 7, 22)
    assert "carrier_code" not in event.partial
    # raw_text carries description too, not just SUMMARY — a failed parse usually
    # means the details were somewhere SUMMARY doesn't reach.
    assert "Flight home" in event.raw_text
    assert "QWERTY" in event.raw_text


def test_unknown_airport_falls_back_to_the_utc_date(flights):
    """QQQ/ZZZ are in no database. 23:30Z would roll over for most western
    zones, so this proves the fallback keeps the date as given."""
    flight = next(f for f in flights if f.source_id == "item-77777777-gggg@example.invalid")
    assert (flight.origin, flight.destination) == ("QQQ", "ZZZ")
    assert flight.flight_date == date(2026, 4, 12)


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


UID_LESS_FEED = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//exporter without uids//EN
BEGIN:VEVENT
DTSTART:20260304T083000Z
SUMMARY:XX123 AAA to BBB
DESCRIPTION:[Flight] one
END:VEVENT
BEGIN:VEVENT
DTSTART:20260610T093000Z
SUMMARY:YY456 CCC to DDD
DESCRIPTION:[Flight] two
END:VEVENT
END:VCALENDAR
"""


def test_events_without_a_uid_get_distinct_keys():
    """Plenty of exporters omit UID. Without a fallback every such event would
    key to `tripit_ical:`, so the first one written would mask all the others,
    permanently."""
    records = list(TripItICalImporter().parse(UID_LESS_FEED))

    assert len(records) == 2
    keys = {r.key for r in records}
    assert len(keys) == 2
    assert "tripit_ical:" not in keys


def test_uid_less_keys_are_stable_across_runs():
    """The fallback key must not change between syncs, or every run re-imports."""
    first = [r.key for r in TripItICalImporter().parse(UID_LESS_FEED)]
    second = [r.key for r in TripItICalImporter().parse(UID_LESS_FEED)]
    assert first == second


def test_codeshare_is_priced_as_the_operating_flight(flights):
    """YY999 is marketed, but Example Airways 456 actually flies it. TIM only
    prices the operating flight, so that is what must be sent."""
    flight = next(f for f in flights if f.source_id == "item-88888888-hhhh@example.invalid")

    assert (flight.carrier_code, flight.flight_number) == ("YY", "999")  # as booked
    assert (flight.operating_carrier_code, flight.operating_flight_number) == ("XX", "456")
    assert (flight.pricing_carrier_code, flight.pricing_flight_number) == ("XX", "456")
    assert flight.is_codeshare


def test_codeshare_resolves_without_any_lookup(flights):
    """The airline code is learned from a direct segment elsewhere in the same
    feed, so a codeshare on a carrier you also fly directly costs no request."""
    flight = next(f for f in flights if f.source_id == "item-88888888-hhhh@example.invalid")
    assert flight.operating_carrier_code == "XX"  # from the XX123 segment


def test_direct_flights_are_not_marked_as_codeshares(flights):
    direct = next(f for f in flights if f.source_id == "item-11111111-aaaa@example.invalid")
    assert not direct.is_codeshare
    assert (direct.pricing_carrier_code, direct.pricing_flight_number) == ("XX", "123")


def test_unresolvable_codeshare_keeps_the_marketing_flight():
    """With no way to learn the operating airline's code, pricing falls back to
    what is on the ticket — the behaviour before codeshares were handled."""
    feed = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:solo-codeshare@example.invalid
DTSTART:20260305T120000Z
SUMMARY:YY999 LHR to CDG
DESCRIPTION:[Flight] LHR to CDG\\n \\nUnknown Air 456, Terminal 5, Gate A1
END:VEVENT
END:VCALENDAR
"""
    importer = TripItICalImporter(resolver=AirlineResolver(lookup=False))
    flight = next(iter(importer.parse(feed)))

    assert flight.operating_carrier_code is None
    assert (flight.pricing_carrier_code, flight.pricing_flight_number) == ("YY", "999")
    assert not flight.is_codeshare


def test_extract_operating_flight_reads_tripits_layout():
    description = (
        "[Flight] LHR to MAD\n \nBritish Airways 458, Terminal TERMINAL 5, Gate \n \n"
        "11:15 AM CEST\nArrive Madrid (MAD)\nTerminal TERM 4 SATELLITE, Gate "
    )
    assert extract_operating_flight(description) == ("British Airways", "458")


def test_extract_operating_flight_ignores_terminal_lines():
    """'Terminal TERM 4 SATELLITE, Gate' must not read as an airline and number."""
    assert extract_operating_flight("Terminal TERM 4 SATELLITE, Gate ") == (None, None)
    assert extract_operating_flight("") == (None, None)
