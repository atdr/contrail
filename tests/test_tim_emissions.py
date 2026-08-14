"""Tests for the TIM emissions provider. All HTTP is mocked; nothing hits the network."""

from datetime import date
from unittest.mock import patch

import pytest

from contrail.emissions.tim import BATCH_SIZE, TIMEmissionsProvider
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


EXACT_PAYLOAD = {
    "modelVersion": {"major": "1"},
    "flightEmissions": [
        {
            "emissionsGramsPerPax": {
                "first": 400000,
                "business": 300000,
                "premiumEconomy": 200000,
                "economy": 100000,
            }
        }
    ],
}

TYPICAL_PAYLOAD = {
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
    post = mock_post({"exact": EXACT_PAYLOAD})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)])

    result = results["tripit_ical:uid-1"]
    assert result.method == "exact"
    assert result.model_version == "1"
    assert result.grams_economy == 100000
    assert result.grams_business == 300000
    # Only the exact endpoint should have been called.
    assert len(post.calls) == 1


def test_falls_back_to_typical_when_the_flight_already_departed():
    """An empty exact result means the flight has flown; TIM has no data for it."""
    empty_exact = {"modelVersion": {"major": "1"}, "flightEmissions": [{}]}
    post = mock_post({"exact": empty_exact, "typical": TYPICAL_PAYLOAD})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)])

    result = results["tripit_ical:uid-1"]
    assert result.method == "typical_route_average"
    assert result.grams_economy == 90000
    assert result.model_version is None  # route averages aren't tied to a model version
    assert len(post.calls) == 2


def test_typical_fallback_dedups_markets():
    """Several flights on one route cost a single market lookup, not one per flight."""
    empty_exact = {"modelVersion": {"major": "1"}, "flightEmissions": [{}, {}, {}]}
    post = mock_post({"exact": empty_exact, "typical": TYPICAL_PAYLOAD})
    flights = [flight(1), flight(2), flight(3)]
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute(flights)

    _, typical_body, _ = post.calls[1]
    assert typical_body == {"markets": [{"origin": "AAA", "destination": "BBB"}]}
    assert all(r.method == "typical_route_average" for r in results.values())
    assert len(results) == 3


def test_no_data_when_neither_endpoint_has_a_number():
    empty_exact = {"modelVersion": {}, "flightEmissions": [{}]}
    empty_typical = {"typicalFlightEmissions": [{}]}
    post = mock_post({"exact": empty_exact, "typical": empty_typical})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)])

    assert results["tripit_ical:uid-1"].method == "no_data"


def test_requests_are_batched():
    """More flights than BATCH_SIZE means several requests, not one huge one."""
    count = BATCH_SIZE + 5
    payload = {
        "modelVersion": {"major": "1"},
        "flightEmissions": [EXACT_PAYLOAD["flightEmissions"][0]] * count,
    }
    post = mock_post({"exact": payload})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(i) for i in range(count)])

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
    post = mock_post({"exact": EXACT_PAYLOAD})
    with patch("contrail.emissions.tim.requests.post", post):
        TIMEmissionsProvider("key").compute([flight(1)])

    sent = post.calls[0][1]["flights"][0]
    assert sent["flightNumber"] == 101
    assert sent["departureDate"] == {"year": 2026, "month": 3, "day": 4}
    assert sent["operatingCarrierCode"] == "XX"


def test_api_key_is_sent_as_a_header_never_in_the_url():
    """A key in the query string leaks into every HTTPError message, and cron
    setups routinely redirect stderr to a log file."""
    post = mock_post({"exact": EXACT_PAYLOAD})
    with patch("contrail.emissions.tim.requests.post", post):
        TIMEmissionsProvider("SUPERSECRET").compute([flight(1)])

    url, _, headers = post.calls[0]
    assert "SUPERSECRET" not in url
    assert "key=" not in url
    assert headers["x-goog-api-key"] == "SUPERSECRET"


def test_a_model_version_of_zero_is_not_treated_as_missing():
    """`major` is an int, so a legitimate 0 must not fall through to the fallback."""
    payload = dict(EXACT_PAYLOAD, modelVersion={"major": 0, "minor": 9})
    post = mock_post({"exact": payload})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)])

    assert results["tripit_ical:uid-1"].model_version == "0"


def test_typical_results_are_matched_on_the_echoed_market():
    """Matching positionally would attribute one route's emissions to another
    flight if the API ever reordered its response."""
    empty_exact = {"modelVersion": {"major": "1"}, "flightEmissions": [{}, {}]}
    reversed_response = {
        "typicalFlightEmissions": [
            {
                "market": {"origin": "CCC", "destination": "DDD"},
                "emissionsGramsPerPax": {"economy": 50000},
            },
            {
                "market": {"origin": "AAA", "destination": "BBB"},
                "emissionsGramsPerPax": {"economy": 90000},
            },
        ]
    }
    post = mock_post({"exact": empty_exact, "typical": reversed_response})
    flights = [flight(1, "AAA", "BBB"), flight(2, "CCC", "DDD")]
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute(flights)

    assert results["tripit_ical:uid-1"].grams_economy == 90000  # AAA->BBB
    assert results["tripit_ical:uid-2"].grams_economy == 50000  # CCC->DDD


def test_typical_results_still_work_without_an_echoed_market():
    """Falls back to position when the response omits the market field."""
    empty_exact = {"modelVersion": {"major": "1"}, "flightEmissions": [{}]}
    post = mock_post({"exact": empty_exact, "typical": TYPICAL_PAYLOAD})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)])

    assert results["tripit_ical:uid-1"].grams_economy == 90000
