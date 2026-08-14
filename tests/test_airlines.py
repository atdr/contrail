"""Tests for airline name -> IATA code resolution. All HTTP is mocked."""

import pytest
import requests

from contrail.airlines import AirlineResolver, wikidata_iata_code


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def wikidata_session(search_hits, entities):
    """A session stand-in answering the two Wikidata calls, recording requests."""
    calls = []

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            calls.append(params)
            if params["action"] == "wbsearchentities":
                return FakeResponse({"search": [{"id": i} for i in search_hits]})
            return FakeResponse({"entities": entities})

    s = Session()
    s.calls = calls
    return s


def claim(code):
    return {"P229": [{"mainsnak": {"datavalue": {"value": code}}}]}


def test_learned_names_need_no_lookup():
    """The feed teaches us BA, so no network call happens at all."""
    session = wikidata_session([], {})
    resolver = AirlineResolver(session=session)
    resolver.learn("British Airways", "BA")

    assert resolver.resolve("British Airways") == "BA"
    assert session.calls == []


def test_learning_is_case_and_whitespace_insensitive():
    resolver = AirlineResolver(lookup=False)
    resolver.learn("British Airways", "BA")
    assert resolver.resolve("  british   airways ") == "BA"


def test_first_learned_code_wins():
    """A later contradictory observation must not overwrite the first."""
    resolver = AirlineResolver(lookup=False)
    resolver.learn("British Airways", "BA")
    resolver.learn("British Airways", "XX")
    assert resolver.resolve("British Airways") == "BA"


def test_falls_back_to_wikidata():
    session = wikidata_session(["Q8766"], {"Q8766": {"claims": claim("BA")}})
    resolver = AirlineResolver(session=session)

    assert resolver.resolve("British Airways") == "BA"
    assert session.calls[0]["action"] == "wbsearchentities"
    assert session.calls[1]["action"] == "wbgetentities"


def test_skips_hits_without_an_iata_code():
    """Searching 'Iberia' surfaces the Iberian Peninsula first; only the airline
    carries a P229, which is what disambiguates it."""
    session = wikidata_session(
        ["Q12837", "Q189227"],
        {"Q12837": {"claims": {}}, "Q189227": {"claims": claim("IB")}},
    )
    assert AirlineResolver(session=session).resolve("Iberia") == "IB"


def test_returns_none_when_nothing_has_a_code():
    session = wikidata_session(["Q1"], {"Q1": {"claims": {}}})
    assert AirlineResolver(session=session).resolve("Not An Airline") is None


def test_results_are_cached_including_misses():
    session = wikidata_session(["Q1"], {"Q1": {"claims": {}}})
    resolver = AirlineResolver(session=session)

    assert resolver.resolve("Mystery Air") is None
    assert resolver.resolve("Mystery Air") is None
    assert len(session.calls) == 2  # one search + one fetch, not four


def test_lookup_can_be_disabled():
    session = wikidata_session(["Q8766"], {"Q8766": {"claims": claim("BA")}})
    resolver = AirlineResolver(lookup=False, session=session)

    assert resolver.resolve("British Airways") is None
    assert session.calls == []


def test_network_failure_never_breaks_a_sync():
    class Failing:
        def get(self, *a, **kw):
            raise requests.ConnectionError("wikidata unreachable")

    assert AirlineResolver(session=Failing()).resolve("British Airways") is None


def test_blank_names_resolve_to_nothing():
    resolver = AirlineResolver(lookup=False)
    assert resolver.resolve(None) is None
    assert resolver.resolve("   ") is None


@pytest.mark.parametrize("payload", [{"search": []}, {}])
def test_wikidata_helper_handles_empty_search(payload):
    class Session:
        def get(self, *a, **kw):
            return FakeResponse(payload)

    assert wikidata_iata_code("Nothing", Session()) is None


def test_wikidata_helper_sends_a_descriptive_user_agent():
    seen = {}

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            seen.update(headers or {})
            return FakeResponse({"search": []})

    wikidata_iata_code("British Airways", Session())
    # Wikimedia asks clients to identify themselves.
    assert "contrail" in seen["User-Agent"]
