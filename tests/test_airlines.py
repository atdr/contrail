"""Tests for airline name -> IATA code resolution. All HTTP is mocked."""

import pytest
import requests

from contrail.airlines import (
    AirlineResolver,
    _iata_from_claims,
    wikidata_iata_code,
    wikidata_iata_for_icao,
)


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


# An ICAO code the bundled table does not answer. The table short-circuits
# resolve_icao() before any of the lookup path runs, so a real code like BAW
# cannot exercise it.
UNKNOWN_ICAO = "ALB"


def wikidata_icao_session(search_titles, entities):
    """A session stand-in for the ICAO path, recording requests.

    Shaped differently from wikidata_session() on purpose: the statement search
    is action=query, and it returns hits as {"query": {"search": [{"title": ...}]}}
    rather than the {"search": [{"id": ...}]} that wbsearchentities returns.
    """
    calls = []

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            calls.append(params)
            if params["action"] == "query":
                return FakeResponse({"query": {"search": [{"title": t} for t in search_titles]}})
            return FakeResponse({"entities": entities})

    s = Session()
    s.calls = calls
    return s


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
    """A name the bundled table has never heard of is the only one that reaches
    the network, so the fixtures here use an invented airline throughout."""
    session = wikidata_session(["Q8766"], {"Q8766": {"claims": claim("AZ")}})
    resolver = AirlineResolver(session=session)

    assert resolver.resolve("Albatross Airways") == "AZ"
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
    session = wikidata_session(["Q8766"], {"Q8766": {"claims": claim("AZ")}})
    resolver = AirlineResolver(lookup=False, session=session)

    assert resolver.resolve("Albatross Airways") is None
    assert session.calls == []


def test_disabling_lookup_leaves_the_bundled_table_working():
    """``airline_lookup: false`` opts out of the *network*, not of resolution."""
    session = wikidata_session([], {})
    resolver = AirlineResolver(lookup=False, session=session)

    assert resolver.resolve("British Airways") == "BA"
    assert resolver.resolve_icao("BAW") == "BA"
    assert session.calls == []


def test_network_failure_never_breaks_a_sync():
    class Failing:
        def get(self, *a, **kw):
            raise requests.ConnectionError("wikidata unreachable")

    assert AirlineResolver(session=Failing()).resolve("Albatross Airways") is None


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


# -- resolving from an ICAO code ------------------------------------------


def test_resolve_icao_falls_back_to_wikidata():
    """An ICAO code the bundled table misses is looked up by statement, not by
    text: a plain search for three letters returns whatever mentions them."""
    session = wikidata_icao_session(["Q8766"], {"Q8766": {"claims": claim("AZ")}})
    resolver = AirlineResolver(session=session)

    assert resolver.resolve_icao(UNKNOWN_ICAO) == "AZ"
    assert session.calls[0]["action"] == "query"
    assert session.calls[0]["srsearch"] == f"haswbstatement:P230={UNKNOWN_ICAO}"
    assert session.calls[1]["action"] == "wbgetentities"


def test_resolve_icao_normalizes_before_searching():
    """Flighty has been seen emitting lower case. The statement search is exact,
    so an un-normalized code would silently find nothing."""
    session = wikidata_icao_session(["Q8766"], {"Q8766": {"claims": claim("AZ")}})

    assert AirlineResolver(session=session).resolve_icao(f"  {UNKNOWN_ICAO.lower()} ") == "AZ"
    assert session.calls[0]["srsearch"] == f"haswbstatement:P230={UNKNOWN_ICAO}"


def test_blank_icao_codes_resolve_to_nothing():
    session = wikidata_icao_session([], {})
    resolver = AirlineResolver(session=session)

    assert resolver.resolve_icao(None) is None
    assert resolver.resolve_icao("   ") is None
    assert session.calls == []


def test_icao_lookup_skips_hits_without_an_iata_code():
    """Ranking is preserved, but only an entity carrying a P229 can answer."""
    session = wikidata_icao_session(
        ["Q1", "Q2"],
        {"Q1": {"claims": {}}, "Q2": {"claims": claim("AZ")}},
    )
    assert AirlineResolver(session=session).resolve_icao(UNKNOWN_ICAO) == "AZ"


def test_icao_lookup_returns_none_when_nothing_has_a_code():
    session = wikidata_icao_session(["Q1"], {"Q1": {"claims": {}}})
    assert AirlineResolver(session=session).resolve_icao(UNKNOWN_ICAO) is None


def test_icao_results_are_cached_including_misses():
    session = wikidata_icao_session(["Q1"], {"Q1": {"claims": {}}})
    resolver = AirlineResolver(session=session)

    assert resolver.resolve_icao(UNKNOWN_ICAO) is None
    assert resolver.resolve_icao(UNKNOWN_ICAO) is None
    assert len(session.calls) == 2  # one search + one fetch, not four


def test_icao_lookup_can_be_disabled():
    session = wikidata_icao_session(["Q8766"], {"Q8766": {"claims": claim("AZ")}})
    resolver = AirlineResolver(lookup=False, session=session)

    assert resolver.resolve_icao(UNKNOWN_ICAO) is None
    assert session.calls == []


def test_icao_network_failure_never_breaks_a_sync():
    """Unresolved is survivable: the flight prices to a route average, which
    needs no carrier code at all."""

    class Failing:
        def get(self, *a, **kw):
            raise requests.ConnectionError("wikidata unreachable")

    assert AirlineResolver(session=Failing()).resolve_icao(UNKNOWN_ICAO) is None


@pytest.mark.parametrize(
    "payload", [{"query": {"search": []}}, {"query": {}}, {}], ids=["empty", "no-search", "bare"]
)
def test_icao_helper_handles_empty_search(payload):
    calls = []

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            calls.append(params)
            return FakeResponse(payload)

    assert wikidata_iata_for_icao(UNKNOWN_ICAO, Session()) is None
    assert len(calls) == 1  # nothing to fetch, so no second round trip


def test_icao_helper_ignores_hits_with_no_title():
    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            return FakeResponse({"query": {"search": [{}, {"title": None}]}})

    assert wikidata_iata_for_icao(UNKNOWN_ICAO, Session()) is None


def test_icao_helper_sends_a_descriptive_user_agent():
    seen = {}

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            seen.update(headers or {})
            return FakeResponse({"query": {"search": []}})

    wikidata_iata_for_icao(UNKNOWN_ICAO, Session())
    # Wikimedia asks clients to identify themselves.
    assert "contrail" in seen["User-Agent"]


@pytest.mark.parametrize("icao", [None, "", "   "])
def test_icao_helper_rejects_blank_codes(icao):
    class Session:
        def get(self, *a, **kw):
            raise AssertionError("should not reach the network")

    assert wikidata_iata_for_icao(icao, Session()) is None


# -- reading a claim off an entity ----------------------------------------


@pytest.mark.parametrize(
    "entity",
    [
        pytest.param({}, id="no-claims"),
        pytest.param({"claims": {"P229": []}}, id="empty-claim-list"),
        pytest.param({"claims": {"P229": [{}]}}, id="no-mainsnak"),
        pytest.param({"claims": {"P229": [{"mainsnak": {}}]}}, id="no-datavalue"),
        pytest.param({"claims": {"P229": [{"mainsnak": {"datavalue": {}}}]}}, id="no-value"),
        pytest.param({"claims": {"P229": "BA"}}, id="claims-not-a-list"),
        pytest.param({"claims": claim(None)}, id="null-value"),
        pytest.param({"claims": claim(42)}, id="non-string-value"),
        pytest.param({"claims": claim("  ")}, id="blank-value"),
    ],
)
def test_malformed_claims_yield_no_code(entity):
    """Wikidata is crowd-edited. A claim that is present but unusable must read
    as "unknown", not raise on a shape nobody anticipated."""
    assert _iata_from_claims(entity) is None


# -- guards on the name path ----------------------------------------------


@pytest.mark.parametrize("name", [None, "", "   "])
def test_wikidata_name_helper_rejects_blank_names(name):
    class Session:
        def get(self, *a, **kw):
            raise AssertionError("should not reach the network")

    assert wikidata_iata_code(name, Session()) is None


@pytest.mark.parametrize(
    ("name", "code"),
    [("", "BA"), ("Albatross Airways", ""), ("", "")],
    ids=["no-name", "no-code", "neither"],
)
def test_learning_ignores_blank_pairs(name, code):
    """A half-empty pair would answer for a name it cannot actually resolve."""
    resolver = AirlineResolver(lookup=False)
    resolver.learn(name, code)

    # Nothing was learned, so an airline the bundled table misses stays unknown.
    assert resolver.resolve("Albatross Airways") is None
