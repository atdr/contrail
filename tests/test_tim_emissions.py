"""Tests for the TIM emissions provider. All HTTP is mocked; nothing hits the network."""

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from contrail.emissions.tim import BATCH_SIZE, TIMEmissionsProvider, _format_model_version
from contrail.models import FlightRecord


def flight(n: int = 1, origin="AAA", destination="BBB") -> FlightRecord:
    return FlightRecord(
        source="tripit_ical",
        source_id=f"uid-{n}",
        flight_date=date(2026, 3, 4),
        carrier_code="XX",
        flight_number=str(100 + n),
        origin=origin,
        destination=destination,
    )


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def mock_post(responses: dict):
    """Return a requests.post stand-in that answers by endpoint, recording calls."""
    calls = []

    def _post(url, json=None, timeout=None, headers=None):
        calls.append((url, json, headers or {}))
        endpoint = "typical" if "computeTypicalFlightEmissions" in url else "exact"
        return FakeResponse(responses[endpoint])

    _post.calls = calls
    return _post


GRAMS = {"first": 400000, "business": 300000, "premiumEconomy": 200000, "economy": 100000}
BEFORE_DEPARTURE = datetime(2026, 3, 1, tzinfo=timezone.utc)
MODEL_VERSION = {"major": 3, "minor": 0, "patch": 0, "dated": "20260814"}


def detailed_entry(origin="AAA", destination="BBB", number=101, grams=GRAMS, distance=1234):
    """One entry shaped like a real computeDetailedFlightEmissions response."""
    entry = {
        "flight": {
            "origin": origin,
            "destination": destination,
            "operatingCarrierCode": "XX",
            "flightNumber": number,
            "departureDate": {"year": 2026, "month": 3, "day": 4},
        },
        "flightEmissionsDetails": {
            "contrailsImpactBucket": "CONTRAILS_IMPACT_MODERATE",
            "source": "TIM",
            "emissionsBreakdown": {
                "wttEmissionsGramsPerPax": {"economy": 16000},
                "ttwEmissionsGramsPerPax": {"economy": 84000},
            },
        },
        "emissionsMetadata": {
            "emissionsProvenance": {
                "provenanceEntries": [
                    {
                        "provenanceEntryType": "FUEL_BURN",
                        "source": "EEA",
                        "fuelBurnEeaStrategy": "AIRCRAFT_MAPPING_EXACT",
                        "dataCategory": "PRIMARY",
                    },
                    {
                        "provenanceEntryType": "DISTANCE_ADJUSTMENT",
                        "estimatedFlightDistanceKm": distance,
                    },
                ]
            },
            "timWebsiteEmissionsCalculatorUrl": "https://travelimpactmodel.org/lookup/flight",
        },
    }
    if grams is not None:
        entry["flightEmissionsDetails"]["emissionsGramsPerPax"] = grams
    return entry


def detailed(entries):
    return {"modelVersion": MODEL_VERSION, "flightsWithDetailedEmissions": entries}


TYPICAL_PAYLOAD = {
    "modelVersion": MODEL_VERSION,
    "typicalFlightEmissions": [
        {
            "emissionsGramsPerPax": {
                "first": 360000,
                "business": 270000,
                "premiumEconomy": 180000,
                "economy": 90000,
            }
        }
    ],
}


def test_requires_an_api_key():
    with pytest.raises(ValueError, match="API key"):
        TIMEmissionsProvider("")


def test_exact_result_is_used_when_available():
    post = mock_post({"exact": detailed([detailed_entry()])})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)], now=BEFORE_DEPARTURE)

    result = results["tripit_ical:uid-1"]
    assert result.method == "exact"
    assert result.grams_economy == 100000
    assert result.grams_business == 300000
    assert len(post.calls) == 1  # no fallback needed
    assert "computeDetailedFlightEmissions" in post.calls[0][0]


def test_the_detail_worth_keeping_is_captured():
    """TIM will not price a departed flight again, so whatever is taken now is
    all there will ever be."""
    post = mock_post({"exact": detailed([detailed_entry(distance=9826)])})
    with patch("contrail.emissions.tim.requests.post", post):
        result = TIMEmissionsProvider("key").compute([flight(1)], now=BEFORE_DEPARTURE)[
            "tripit_ical:uid-1"
        ]

    assert result.model_version == "3.0.0+20260814"
    assert result.data_source == "TIM"
    assert result.contrails_impact == "moderate"
    assert result.distance_km == 9826
    assert result.aircraft_match == "AIRCRAFT_MAPPING_EXACT"
    # and the untouched payload, including what no column holds
    assert result.raw["flightEmissionsDetails"]["emissionsBreakdown"]["wttEmissionsGramsPerPax"]
    assert result.raw["request"]["operatingCarrierCode"] == "XX"


def test_results_are_matched_on_the_echoed_flight_identity():
    """A reordered response must not attribute one flight's emissions to another."""
    payload = detailed(
        [
            detailed_entry(origin="CCC", destination="DDD", number=102, grams={"economy": 50000}),
            detailed_entry(origin="AAA", destination="BBB", number=101, grams={"economy": 90000}),
        ]
    )
    post = mock_post({"exact": payload})
    flights = [flight(1, "AAA", "BBB"), flight(2, "CCC", "DDD")]
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute(flights, now=BEFORE_DEPARTURE)

    assert results["tripit_ical:uid-1"].grams_economy == 90000  # AAA->BBB
    assert results["tripit_ical:uid-2"].grams_economy == 50000  # CCC->DDD


def test_falls_back_to_typical_when_the_flight_already_departed():
    """An empty exact result means TIM has no data for it."""
    post = mock_post({"exact": detailed([detailed_entry(grams=None)]), "typical": TYPICAL_PAYLOAD})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)], now=BEFORE_DEPARTURE)

    result = results["tripit_ical:uid-1"]
    assert result.method == "typical_route_average"
    assert result.grams_economy == 90000
    assert result.model_version == "3.0.0+20260814"
    # No detailed variant of the typical endpoint, so no provenance to keep.
    assert result.distance_km is None
    assert len(post.calls) == 2


def test_typical_fallback_dedups_markets():
    """Several flights on one route cost a single market lookup, not one per flight."""
    empty = detailed([detailed_entry(grams=None) for _ in range(3)])
    post = mock_post({"exact": empty, "typical": TYPICAL_PAYLOAD})
    flights = [flight(1), flight(2), flight(3)]
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute(flights, now=BEFORE_DEPARTURE)

    _, typical_body, _ = post.calls[1]
    assert typical_body == {"markets": [{"origin": "AAA", "destination": "BBB"}]}
    assert all(r.method == "typical_route_average" for r in results.values())
    assert len(results) == 3


def test_no_data_when_neither_endpoint_has_a_number():
    post = mock_post(
        {
            "exact": detailed([detailed_entry(grams=None)]),
            "typical": {"modelVersion": MODEL_VERSION, "typicalFlightEmissions": [{}]},
        }
    )
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)], now=BEFORE_DEPARTURE)

    assert results["tripit_ical:uid-1"].method == "no_data"


def test_requests_are_batched():
    """More flights than BATCH_SIZE means several requests, not one huge one."""
    count = BATCH_SIZE + 5
    # One entry per flight, each echoing that flight's own identifiers.
    payload = detailed([detailed_entry(number=100 + i) for i in range(count)])
    post = mock_post({"exact": payload})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute(
            [flight(i) for i in range(count)], now=BEFORE_DEPARTURE
        )

    assert len(post.calls) == 2
    assert len(post.calls[0][1]["flights"]) == BATCH_SIZE
    assert len(post.calls[1][1]["flights"]) == 5
    assert len(results) == count


def test_empty_input_makes_no_requests():
    post = mock_post({})
    with patch("contrail.emissions.tim.requests.post", post):
        assert TIMEmissionsProvider("key").compute([]) == {}
    assert post.calls == []


def test_flight_number_is_sent_as_an_integer():
    post = mock_post({"exact": detailed([detailed_entry()])})
    with patch("contrail.emissions.tim.requests.post", post):
        TIMEmissionsProvider("key").compute([flight(1)], now=BEFORE_DEPARTURE)

    sent = post.calls[0][1]["flights"][0]
    assert sent["flightNumber"] == 101
    assert sent["departureDate"] == {"year": 2026, "month": 3, "day": 4}
    assert sent["operatingCarrierCode"] == "XX"


def test_api_key_is_sent_as_a_header_never_in_the_url():
    """A key in the query string leaks into every HTTPError message, and cron
    setups routinely redirect stderr to a log file."""
    post = mock_post({"exact": detailed([detailed_entry()])})
    with patch("contrail.emissions.tim.requests.post", post):
        TIMEmissionsProvider("SUPERSECRET").compute([flight(1)], now=BEFORE_DEPARTURE)

    url, _, headers = post.calls[0]
    assert "SUPERSECRET" not in url
    assert "key=" not in url
    assert headers["x-goog-api-key"] == "SUPERSECRET"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ({"major": 3, "minor": 0, "patch": 0, "dated": "20260814"}, "3.0.0+20260814"),
        # A legitimate 0 major must not read as missing.
        ({"major": 0, "minor": 9, "patch": 1}, "0.9.1"),
        ({"major": 2, "dated": "20250101"}, "2.0.0+20250101"),
        ({}, ""),
        (None, ""),
    ],
)
def test_model_version_formatting(version, expected):
    assert _format_model_version(version) == expected


def test_a_partial_identity_mismatch_never_borrows_another_flights_entry():
    """Mixing identity and position would hand an unmatched flight an entry
    another flight already claimed — the cross-attribution identity matching
    exists to prevent."""
    payload = detailed(
        [
            # reversed order, and the AAA->BBB echo is mangled so only CCC matches
            detailed_entry(origin="CCC", destination="DDD", number=102, grams={"economy": 222000}),
            {
                **detailed_entry(
                    origin="AAA", destination="BBB", number=101, grams={"economy": 111000}
                ),
                "flight": {
                    "origin": "AAA",
                    "destination": "BBB",
                    "operatingCarrierCode": "BA/IB",
                    "flightNumber": 101,
                    "departureDate": {"year": 2026, "month": 3, "day": 4},
                },
            },
        ]
    )
    post = mock_post({"exact": payload, "typical": TYPICAL_PAYLOAD})
    flights = [flight(1, "AAA", "BBB"), flight(2, "CCC", "DDD")]
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute(flights, now=BEFORE_DEPARTURE)

    # CCC->DDD matched by identity and keeps its own figure...
    assert results["tripit_ical:uid-2"].grams_economy == 222000
    # ...and the unmatched one must not silently inherit it.
    assert results["tripit_ical:uid-1"].grams_economy != 222000


def test_departed_flights_never_reach_the_detailed_endpoint():
    """It rejects a past departure date with a 400 and fails the whole batch,
    where the plain endpoint merely returns nothing."""
    post = mock_post({"typical": TYPICAL_PAYLOAD})
    after = datetime(2026, 6, 1, tzinfo=timezone.utc)  # fixture flights are in March
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)], now=after)

    assert [c[0] for c in post.calls] == [
        "https://travelimpactmodel.googleapis.com/v1/flights:computeTypicalFlightEmissions"
    ]
    assert results["tripit_ical:uid-1"].method == "typical_route_average"
