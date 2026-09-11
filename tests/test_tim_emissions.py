"""Tests for the TIM emissions provider. All HTTP is mocked; nothing hits the network."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from contrail.emissions.tim import (
    BATCH_SIZE,
    TIMEmissionsProvider,
    _as_detailed,
    _format_model_version,
)
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
    """Return a requests.post stand-in that answers by endpoint, recording calls.

    Three keys, because three endpoints matter: "exact" is
    computeDetailedFlightEmissions, "plain" is computeFlightEmissions (the
    undetailed one the 400 fallback drops to, which carries the same figures and
    no provenance), and "typical" is the route average.

    A value may be a payload, or a callable taking the request body, so a
    stand-in can answer differently per batch or raise the way TIM does.
    """
    calls = []

    def _post(url, json=None, timeout=None, headers=None):
        calls.append((url, json, headers or {}))
        if "computeTypicalFlightEmissions" in url:
            endpoint = "typical"
        elif "computeDetailedFlightEmissions" in url:
            endpoint = "exact"
        else:
            endpoint = "plain"
        answer = responses[endpoint]
        if callable(answer):
            answer = answer(json)
        return FakeResponse(answer)

    _post.calls = calls
    return _post


GRAMS = {"first": 400000, "business": 300000, "premiumEconomy": 200000, "economy": 100000}
BEFORE_DEPARTURE = datetime(2026, 3, 1, tzinfo=UTC)
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


def http_error(status: int | None):
    """An HTTPError shaped like the one raise_for_status() raises.

    ``status=None`` covers the case _post_detailed guards separately: an
    HTTPError carrying no response at all, which it cannot classify and so must
    not swallow.
    """
    if status is None:
        return requests.HTTPError("no response attached")
    return requests.HTTPError(
        f"{status} Client Error", response=SimpleNamespace(status_code=status)
    )


def detailed_for(body, bad_numbers=(), model_version=MODEL_VERSION):
    """TIM's detailed endpoint, modelled: one bad entry rejects the whole batch."""
    flights = body["flights"]
    if any(f["flightNumber"] in bad_numbers for f in flights):
        raise http_error(400)
    return {
        "modelVersion": model_version,
        "flightsWithDetailedEmissions": [
            detailed_entry(f["origin"], f["destination"], f["flightNumber"]) for f in flights
        ],
    }


def plain_for(body):
    """The undetailed endpoint: same figures, no emissionsMetadata."""
    return {
        "modelVersion": MODEL_VERSION,
        "flightEmissions": [
            {
                "flight": f,
                "emissionsGramsPerPax": GRAMS,
                "source": "TIM",
                "contrailsImpactBucket": "CONTRAILS_IMPACT_MODERATE",
            }
            for f in body["flights"]
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
    after = datetime(2026, 6, 1, tzinfo=UTC)  # fixture flights are in March
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)], now=after)

    assert [c[0] for c in post.calls] == [
        "https://travelimpactmodel.googleapis.com/v1/flights:computeTypicalFlightEmissions"
    ]
    assert results["tripit_ical:uid-1"].method == "typical_route_average"


# -- the detailed endpoint refusing a batch -------------------------------


def test_a_rejected_batch_is_split_rather_than_abandoned():
    """One bad entry rejects the whole batch. Falling the batch back to the plain
    endpoint would keep the figures and lose the provenance for every good flight
    in it, and provenance cannot be re-fetched once a flight departs."""
    post = mock_post(
        {
            "exact": lambda body: detailed_for(body, bad_numbers={102}),
            "plain": plain_for,
        }
    )
    flights = [flight(1, "AAA", "BBB"), flight(2, "CCC", "DDD")]
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute(flights, now=BEFORE_DEPARTURE)

    good = results["tripit_ical:uid-1"]
    bad = results["tripit_ical:uid-2"]

    # The good flight kept everything, despite sharing a batch with the bad one.
    assert good.method == "exact"
    assert good.distance_km == 1234
    assert good.aircraft_match == "AIRCRAFT_MAPPING_EXACT"

    # The bad one kept its figures. That is the whole point of the fallback.
    assert bad.method == "exact"
    assert bad.grams_economy == 100000
    assert bad.data_source == "TIM"

    urls = [url for url, _, _ in post.calls]
    # batch of two, then each half, then the plain endpoint for the one at fault
    assert len(urls) == 4
    assert sum("computeFlightEmissions" in u for u in urls) == 1


def test_only_the_flight_at_fault_loses_its_provenance():
    """The plain endpoint carries no emissionsMetadata, so the columns fed from
    it come back blank rather than wrong."""
    post = mock_post(
        {"exact": lambda body: detailed_for(body, bad_numbers={102}), "plain": plain_for}
    )
    flights = [flight(1, "AAA", "BBB"), flight(2, "CCC", "DDD")]
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute(flights, now=BEFORE_DEPARTURE)

    bad = results["tripit_ical:uid-2"]
    assert bad.distance_km is None
    assert bad.aircraft_match is None
    # What the plain endpoint does state is still kept.
    assert bad.contrails_impact == "moderate"


def test_a_single_bad_flight_goes_straight_to_the_plain_endpoint():
    """Nothing left to split, so there is no second detailed attempt."""
    post = mock_post(
        {"exact": lambda body: detailed_for(body, bad_numbers={101}), "plain": plain_for}
    )
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)], now=BEFORE_DEPARTURE)

    assert results["tripit_ical:uid-1"].grams_economy == 100000
    urls = [url for url, _, _ in post.calls]
    assert sum("computeDetailedFlightEmissions" in u for u in urls) == 1
    assert sum("computeFlightEmissions" in u for u in urls) == 1


@pytest.mark.parametrize("status", [None, 403, 429, 500])
def test_errors_other_than_a_400_are_not_narrowed_down(status):
    """Only a 400 means "one of these entries is bad". A 403 or a quota error
    says nothing about the batch, and bisecting it would multiply the calls."""

    def refuse(body):
        raise http_error(status)

    post = mock_post({"exact": refuse})
    flights = [flight(1, "AAA", "BBB"), flight(2, "CCC", "DDD")]
    with patch("contrail.emissions.tim.requests.post", post), pytest.raises(requests.HTTPError):
        TIMEmissionsProvider("key").compute(flights, now=BEFORE_DEPARTURE)

    assert len(post.calls) == 1  # raised, not split


def test_a_split_keeps_whichever_half_states_a_model_version():
    def answer(body):
        if len(body["flights"]) > 1:
            raise http_error(400)
        version = MODEL_VERSION if body["flights"][0]["flightNumber"] == 102 else None
        return detailed_for(body, model_version=version)

    post = mock_post({"exact": answer})
    flights = [flight(1, "AAA", "BBB"), flight(2, "CCC", "DDD")]
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute(flights, now=BEFORE_DEPARTURE)

    assert results["tripit_ical:uid-1"].model_version == "3.0.0+20260814"
    assert results["tripit_ical:uid-2"].model_version == "3.0.0+20260814"


def test_reshaping_a_plain_reply_keeps_the_figures_and_nothing_else():
    reshaped = _as_detailed(
        {
            "modelVersion": MODEL_VERSION,
            "flightEmissions": [
                {
                    "flight": {"origin": "AAA"},
                    "emissionsGramsPerPax": GRAMS,
                    "source": "TIM",
                    "contrailsImpactBucket": "CONTRAILS_IMPACT_MODERATE",
                    "somethingElse": "dropped",
                }
            ],
        }
    )

    assert reshaped["modelVersion"] == MODEL_VERSION
    entry = reshaped["flightsWithDetailedEmissions"][0]
    assert entry["flight"] == {"origin": "AAA"}
    assert set(entry["flightEmissionsDetails"]) == {
        "emissionsGramsPerPax",
        "source",
        "contrailsImpactBucket",
    }


def test_reshaping_tolerates_an_entry_that_states_nothing():
    """An absent key must stay absent rather than become a null the CSV records."""
    reshaped = _as_detailed({"flightEmissions": [{}]})

    assert reshaped["modelVersion"] is None
    entry = reshaped["flightsWithDetailedEmissions"][0]
    assert entry["flight"] is None
    assert entry["flightEmissionsDetails"] == {}


def test_reshaping_an_empty_reply():
    assert _as_detailed({}) == {"modelVersion": None, "flightsWithDetailedEmissions": []}


# -- matching responses back to flights -----------------------------------


def test_position_is_used_only_when_identity_tells_us_nothing():
    """A response that echoes no identifier we recognise is still usable, as long
    as it is the only reading available: one entry per flight, none matching."""
    payload = detailed(
        [detailed_entry(origin="EEE", destination="FFF", number=999, grams={"economy": 90000})]
    )
    post = mock_post({"exact": payload})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)], now=BEFORE_DEPARTURE)

    assert results["tripit_ical:uid-1"].grams_economy == 90000
    assert len(post.calls) == 1  # priced exactly, so no fallback


def test_typical_trusts_the_echoed_market_over_position():
    """The market comes back in the response. Matching on it is what stops one
    route's average being attributed to a different flight."""
    payload = {
        "modelVersion": MODEL_VERSION,
        "typicalFlightEmissions": [
            {
                "market": {"origin": "CCC", "destination": "DDD"},
                "emissionsGramsPerPax": {"economy": 50000},
            },
            {
                "market": {"origin": "AAA", "destination": "BBB"},
                "emissionsGramsPerPax": {"economy": 90000},
            },
        ],
    }
    post = mock_post({"exact": detailed([]), "typical": payload})
    flights = [flight(1, "AAA", "BBB"), flight(2, "CCC", "DDD")]
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute(flights, now=BEFORE_DEPARTURE)

    assert results["tripit_ical:uid-1"].grams_economy == 90000  # AAA->BBB
    assert results["tripit_ical:uid-2"].grams_economy == 50000  # CCC->DDD


def test_typical_skips_an_unattributable_extra_entry():
    """An entry naming no market we asked about, in a position past the batch, is
    not attributable to anything. Dropping it beats indexing off the end."""
    payload = {
        "modelVersion": MODEL_VERSION,
        "typicalFlightEmissions": [
            {"emissionsGramsPerPax": {"economy": 90000}},
            {"emissionsGramsPerPax": {"economy": 11111}},
        ],
    }
    post = mock_post({"exact": detailed([]), "typical": payload})
    with patch("contrail.emissions.tim.requests.post", post):
        results = TIMEmissionsProvider("key").compute([flight(1)], now=BEFORE_DEPARTURE)

    assert results["tripit_ical:uid-1"].grams_economy == 90000
