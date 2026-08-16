#!/usr/bin/env python3
"""Regenerate ``src/contrail/data/airline_codes.csv`` from Wikidata.

One table serves both airline lookups contrail needs:

- **name -> IATA**, for the operating airline a TripIt description names in prose
  ("British Airways 458").
- **ICAO -> IATA**, for Flighty's ``Airline`` column, which is ICAO (``BAW``)
  while TIM's ``operatingCarrierCode`` wants IATA (``BA``).

Wikidata is CC0, so the result can be vendored. It is queried at refresh time and
never during a sync: the shipped table makes the common case offline and
deterministic, and ``AirlineResolver`` only reaches the network for what the
table misses.

Ambiguity is dropped rather than guessed. An ICAO code or a name that maps to
more than one IATA code is omitted and reported, so the live lookup decides
instead of the table silently picking a side.

Run it by hand when the table looks stale; it needs network, so CI never does:

    ./venv/bin/python scripts/refresh_airline_codes.py
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import requests

from contrail import __version__

SPARQL_URL = "https://query.wikidata.org/sparql"
OUTPUT = Path(__file__).resolve().parent.parent / "src" / "contrail" / "data" / "airline_codes.csv"

# Wikimedia asks for a descriptive User-Agent identifying the tool. Built from
# the installed version so it can't drift away from what's actually running.
USER_AGENT = f"contrail/{__version__} (https://github.com/atdr/contrail)"
REQUEST_TIMEOUT = 180

# Aliases exist to catch the spellings a calendar feed actually uses, not to
# mirror Wikidata. A handful each keeps the shipped file small.
MAX_ALIASES = 6

# P229 IATA designator, P230 ICAO designator, P1448 official name,
# P1813 short name, P576 dissolved/abolished date.
#
# Both designators are required: a row without an IATA code answers nothing, and
# a row without an ICAO code cannot serve the Flighty lookup. Names and aliases
# are aggregated so one carrier is one row.
QUERY = """
SELECT ?item ?iata ?icao ?dissolved
       (SAMPLE(?label) AS ?name)
       (GROUP_CONCAT(DISTINCT ?alias; separator="|") AS ?aliases)
WHERE {
  ?item wdt:P229 ?iata ;
        wdt:P230 ?icao .
  OPTIONAL { ?item wdt:P576 ?dissolved }
  OPTIONAL { ?item rdfs:label ?label FILTER(LANG(?label) = "en") }
  OPTIONAL { ?item skos:altLabel ?altLabel FILTER(LANG(?altLabel) = "en") }
  OPTIONAL { ?item wdt:P1448 ?officialName FILTER(LANG(?officialName) = "en") }
  OPTIONAL { ?item wdt:P1813 ?shortName FILTER(LANG(?shortName) = "en") }
  BIND(COALESCE(?altLabel, ?officialName, ?shortName) AS ?alias)
}
GROUP BY ?item ?iata ?icao ?dissolved
"""


def normalize(name: str) -> str:
    """The same key ``AirlineResolver`` uses, so ambiguity is judged as it is looked up."""
    return " ".join(name.split()).casefold()


def fetch() -> list[dict]:
    resp = requests.get(
        SPARQL_URL,
        params={"query": QUERY, "format": "json"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def value(binding: dict, key: str) -> str:
    return (binding.get(key) or {}).get("value", "").strip()


def build_rows(bindings: list[dict]) -> tuple[list[dict], list[str]]:
    """Collapse the query result into one row per carrier, dropping ambiguity.

    Two kinds of ambiguity, and they are not the same thing:

    - **An entity carrying more than one designator of a kind.** Wikidata has a
      few of these, and the query's join turns them into a cross-product. They
      are unusable and are skipped whole, which is also what rescues the *other*
      carriers sharing a code with them: dropping AJet's duplicate TK/VF claim
      leaves ICAO ``THY`` resolving cleanly to Turkish Airlines.
    - **Several carriers sharing one ICAO code.** Usually a parent and its
      subsidiaries, which agree on the IATA code and merely differ in name.
      Genuine disagreement is dropped and reported.
    """
    by_entity: dict[str, dict] = {}
    for binding in bindings:
        iata = value(binding, "iata").upper()
        icao = value(binding, "icao").upper()
        # IATA designators are two characters, ICAO three. Anything else is a
        # typo or a range in the source data, and would only ever mis-resolve.
        if len(iata) != 2 or len(icao) != 3:
            continue
        item = value(binding, "item")
        entity = by_entity.setdefault(
            item,
            {
                "iata": set(),
                "icao": set(),
                "name": value(binding, "name"),
                "aliases": [a for a in value(binding, "aliases").split("|") if a],
                "dissolved": bool(value(binding, "dissolved")),
            },
        )
        entity["iata"].add(iata)
        entity["icao"].add(icao)

    by_icao: dict[str, list[dict]] = defaultdict(list)
    for entity in by_entity.values():
        if len(entity["iata"]) != 1 or len(entity["icao"]) != 1:
            continue
        icao = next(iter(entity["icao"]))
        by_icao[icao].append({**entity, "iata": next(iter(entity["iata"])), "icao": icao})

    rows: list[dict] = []
    dropped: list[str] = []

    for icao, candidates in sorted(by_icao.items()):
        codes = {c["iata"] for c in candidates}
        if len(codes) > 1:
            # A still-operating carrier is the one a current feed means. Only if
            # that fails to single one out is the code genuinely ambiguous.
            live = [c for c in candidates if not c["dissolved"]]
            if len({c["iata"] for c in live}) != 1:
                dropped.append(f"ICAO {icao}: {', '.join(sorted(codes))}")
                continue
            candidates = live

        # Which of a parent and its subsidiaries should name the row: the one
        # still operating, then the best-documented. Alias count is a decent
        # proxy for prominence — it is what picks "Lufthansa" over "Lufthansa
        # Regional", and "Qantas Airways" over "Qantas Freight".
        best = max(candidates, key=lambda c: (not c["dissolved"], len(c["aliases"])))
        codes_seen = {best["iata"], icao}
        merged: list[str] = []
        for candidate in candidates:
            for alias in [candidate["name"], *candidate["aliases"]]:
                # The designators turn up as altLabels. They are no use to a
                # lookup that is handed prose, and they would eat the cap.
                if (
                    alias
                    and alias != best["name"]
                    and alias.upper() not in codes_seen
                    and alias not in merged
                ):
                    merged.append(alias)

        rows.append(
            {
                "iata": best["iata"],
                "icao": icao,
                "name": best["name"],
                "aliases": "|".join(merged[:MAX_ALIASES]),
            }
        )

    return rows, dropped


def drop_ambiguous_names(rows: list[dict]) -> list[str]:
    """Blank any name or alias that maps to more than one IATA code.

    Left in place they would resolve a codeshare to whichever carrier happened to
    sort first, which is worse than not resolving it: the flight would be priced
    confidently against the wrong airline instead of falling back to a route
    average.
    """
    owners: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for name in [row["name"], *row["aliases"].split("|")]:
            if name:
                owners[normalize(name)].add(row["iata"])

    ambiguous = {key for key, codes in owners.items() if len(codes) > 1}
    if not ambiguous:
        return []

    for row in rows:
        if normalize(row["name"]) in ambiguous:
            row["name"] = ""
        row["aliases"] = "|".join(
            alias
            for alias in row["aliases"].split("|")
            if alias and normalize(alias) not in ambiguous
        )

    return sorted(ambiguous)


def main() -> int:
    print(f"Querying {SPARQL_URL} ...")
    bindings = fetch()
    print(f"  {len(bindings)} binding(s) returned.")

    rows, dropped_codes = build_rows(bindings)
    dropped_names = drop_ambiguous_names(rows)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # LF, not the csv module's default CRLF: the repo normalizes line endings
    # (.gitattributes `* text=auto`), so CRLF here leaves the checked-out file
    # permanently differing from what git stores.
    with open(OUTPUT, "w", newline="\n") as f:
        writer = csv.DictWriter(
            f, fieldnames=["iata", "icao", "name", "aliases"], lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} airline(s) to {OUTPUT}.")
    named = sum(1 for row in rows if row["name"])
    print(f"  {named} carry a usable name, {len(rows) - named} are ICAO-only.")
    if dropped_codes:
        print(f"  Dropped {len(dropped_codes)} ambiguous ICAO code(s):")
        for entry in dropped_codes:
            print(f"    {entry}")
    if dropped_names:
        print(f"  Blanked {len(dropped_names)} name(s) shared by several carriers:")
        for name in dropped_names[:20]:
            print(f"    {name}")
        if len(dropped_names) > 20:
            print(f"    ... and {len(dropped_names) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
