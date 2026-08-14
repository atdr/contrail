"""Resolving an airline's display name to its IATA code.

Needed because a calendar feed names the *operating* airline in prose ("British
Airways 458") while TIM's ``operatingCarrierCode`` wants ``BA``.

Two sources, cheapest first:

1. **The feed itself.** On a non-codeshare segment the description repeats the
   flight already given in the summary, which hands us a (name -> code) pair for
   free. Someone who flies BA directly and also holds a BA-operated Iberia
   codeshare gets the codeshare resolved with no network call at all.
2. **Wikidata**, as a fallback for names the feed never taught us. Property P229
   is the IATA airline designator. Structured data beats scraping the Wikipedia
   infobox, whose wikitext yields things like ``BAW; SHT`` for a single ICAO
   field.

Every failure is soft: an unresolvable name leaves the flight priced under its
marketing number, which is exactly what contrail did before any of this.
"""

from __future__ import annotations

import requests

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
IATA_PROPERTY = "P229"  # IATA airline designator
SEARCH_LIMIT = 8
REQUEST_TIMEOUT = 15

# Wikimedia asks for a descriptive User-Agent identifying the tool.
USER_AGENT = "contrail/0.1 (https://github.com/atdr/contrail)"


def _iata_from_claims(entity: dict) -> str | None:
    claims = entity.get("claims", {}).get(IATA_PROPERTY)
    if not claims:
        return None
    try:
        code = claims[0]["mainsnak"]["datavalue"]["value"]
    except (KeyError, IndexError, TypeError):
        return None
    return code if isinstance(code, str) and code.strip() else None


def wikidata_iata_code(name: str, session: requests.Session | None = None) -> str | None:
    """Look up an airline's IATA code on Wikidata, or None.

    Searches by name and returns the first hit that actually carries an IATA
    designator. That check is what disambiguates: searching "Iberia" surfaces
    the Iberian Peninsula first, and only the airline has a P229.
    """
    if not name or not name.strip():
        return None
    session = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}

    resp = session.get(
        WIKIDATA_API,
        params={
            "action": "wbsearchentities",
            "search": name.strip(),
            "language": "en",
            "limit": SEARCH_LIMIT,
            "format": "json",
        },
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    ids = [hit["id"] for hit in resp.json().get("search", []) if hit.get("id")]
    if not ids:
        return None

    resp = session.get(
        WIKIDATA_API,
        params={
            "action": "wbgetentities",
            "props": "claims",
            "ids": "|".join(ids),
            "format": "json",
        },
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    entities = resp.json().get("entities", {})

    for qid in ids:  # preserve the search ranking
        code = _iata_from_claims(entities.get(qid, {}))
        if code:
            return code
    return None


class AirlineResolver:
    """Resolves airline names to IATA codes, caching within a run."""

    def __init__(self, lookup: bool = True, session: requests.Session | None = None):
        self.lookup = lookup
        self.session = session
        self._learned: dict[str, str] = {}
        self._cache: dict[str, str | None] = {}

    @staticmethod
    def _key(name: str) -> str:
        return " ".join(name.split()).casefold()

    def learn(self, name: str, code: str) -> None:
        """Record a (name -> code) pair observed directly in the source data."""
        if name and code:
            self._learned.setdefault(self._key(name), code)

    def resolve(self, name: str | None) -> str | None:
        """IATA code for an airline name, or None if it can't be determined."""
        if not name or not name.strip():
            return None
        key = self._key(name)
        if key in self._learned:
            return self._learned[key]
        if key in self._cache:
            return self._cache[key]
        if not self.lookup:
            return None

        try:
            code = wikidata_iata_code(name, self.session)
        except requests.RequestException:
            # A lookup service being unreachable must never fail a sync.
            code = None
        self._cache[key] = code
        return code
