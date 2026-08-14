"""TripIt iCal importer.

Ported from the prototype's ``parse_flights_from_ical`` / ``is_flight_event`` /
``extract_flight_fields``. The regexes below are unchanged: they were validated
against a real TripIt feed, so resist the urge to "tidy" them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from hashlib import sha1
from pathlib import Path
from urllib.parse import urlparse

import requests
from icalendar import Calendar

from contrail.airlines import AirlineResolver
from contrail.airports import departure_date
from contrail.models import FlightRecord, UnparsedEvent

# Matches "(SFO)" style airport codes anywhere in text
AIRPORT_CODE_RE = re.compile(r"\(([A-Z]{3})\)")
# Matches "from X (SFO) to Y (JFK)"-style phrasing
FROM_TO_RE = re.compile(
    r"from\s+.*?\(([A-Z]{3})\).*?\bto\b\s+.*?\(([A-Z]{3})\)", re.IGNORECASE | re.DOTALL
)
# Matches airport codes separated by an arrow/dash, e.g. "SFO -> JFK", "SFO-JFK", "SFO to JFK"
CODE_PAIR_RE = re.compile(r"\b([A-Z]{3})\b\s*(?:->|→|-|to)\s*\b([A-Z]{3})\b")
# Matches a carrier+flight number token, e.g. "UA523", "UA 523", "LH 123"
FLIGHT_NO_RE = re.compile(r"\b([A-Z]{2}|[A-Z]\d|\d[A-Z])\s?-?\s?(\d{1,4})\b")
# TripIt spells the operating flight out in DESCRIPTION on its own line, e.g.
# "British Airways 458, Terminal TERMINAL 5, Gate". The trailing comma is what
# keeps this off lines like "Terminal TERM 4 SATELLITE, Gate".
OPERATING_RE = re.compile(r"^([A-Za-z][A-Za-z .&'-]+?)\s+(\d{1,4})\s*,", re.MULTILINE)

FETCH_TIMEOUT = 30


def _extract_from_blob(blob: str):
    origin = destination = None
    m = FROM_TO_RE.search(blob)
    if m:
        origin, destination = m.group(1), m.group(2)
    else:
        m = CODE_PAIR_RE.search(blob)
        if m:
            origin, destination = m.group(1), m.group(2)
        else:
            codes = AIRPORT_CODE_RE.findall(blob)
            if len(codes) >= 2:
                origin, destination = codes[0], codes[1]

    carrier_code = flight_number = None
    m = FLIGHT_NO_RE.search(blob)
    if m:
        carrier_code, flight_number = m.group(1), m.group(2)

    return carrier_code, flight_number, origin, destination


def extract_flight_fields(summary: str, description: str, location: str):
    """Best-effort extraction of (carrier_code, flight_number, origin, destination)
    from free-text iCal fields. Returns None for any field that couldn't be found.

    TripIt's SUMMARY field is normally a clean "BA896 LHR to PFO"-style string,
    so we try that alone first (least chance of a false match) before falling
    back to the noisier combined SUMMARY + DESCRIPTION + LOCATION text.
    """
    summary_clean = (summary or "").replace("\n", " ")
    result = _extract_from_blob(summary_clean)
    if all(result):
        return result

    blob = " ".join(filter(None, [summary, description, location])).replace("\n", " ")
    blob_result = _extract_from_blob(blob)
    # Prefer whichever fields the summary-only pass already found
    return tuple(r if r is not None else b for r, b in zip(result, blob_result, strict=True))


def is_flight_event(component) -> bool:
    """TripIt tags flight events with a literal "[Flight]" marker inside
    DESCRIPTION. That's the most reliable signal. As a fallback (for other
    calendar tools / edge cases), also treat an event as a flight if we can
    fully extract carrier + flight number + origin + destination from it."""
    description = str(component.get("description", ""))
    categories = str(component.get("categories", ""))
    if "[flight]" in description.lower() or "flight" in categories.lower():
        return True

    summary = str(component.get("summary", ""))
    location = str(component.get("location", ""))
    fields = extract_flight_fields(summary, description, location)
    return all(fields)


def extract_operating_flight(description: str) -> tuple[str | None, str | None]:
    """The operating airline name and flight number named in DESCRIPTION.

    On a direct flight this repeats what SUMMARY already said; on a codeshare it
    names the airline that actually operates it. Returns (None, None) when the
    feed doesn't say.
    """
    match = OPERATING_RE.search(description or "")
    if not match:
        return None, None
    return match.group(1).strip(), match.group(2)


def _looks_like_url(source: str) -> bool:
    return urlparse(source).scheme in ("http", "https")


def _source_id(uid: str, summary: str, dtstart_raw: str) -> str:
    """The event's UID, or a content hash when it has none.

    TripIt always sets a UID, but this importer reads any iCal file and plenty
    of exporters omit it. Without a fallback every UID-less event would share
    the key ``tripit_ical:``, so the first one written would mask every other
    one — in this run and in every run afterwards.
    """
    if uid:
        return uid
    digest = sha1(f"{dtstart_raw}\n{summary}".encode(), usedforsecurity=False).hexdigest()
    return f"sha1:{digest[:16]}"


def fetch_ical(url_or_path: str) -> bytes:
    """Read an iCal feed from an http(s) URL, a ``file://`` URL, or a local path.

    Local paths are supported so CI can run ``contrail sync --dry-run`` against
    the test fixture with no network and no mocking.
    """
    if _looks_like_url(url_or_path):
        resp = requests.get(url_or_path, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        return resp.content

    parsed = urlparse(url_or_path)
    if parsed.scheme == "file":
        return Path(parsed.path).read_bytes()
    return Path(url_or_path).read_bytes()


class TripItICalImporter:
    """Reads flights from a TripIt calendar feed URL (or any iCal file)."""

    id = "tripit_ical"

    def __init__(self, resolver: AirlineResolver | None = None):
        self.resolver = resolver if resolver is not None else AirlineResolver()

    def fetch(self, config: dict) -> Iterable[FlightRecord | UnparsedEvent]:
        url = config.get("url") or config.get("ical_url")
        if not url:
            raise ValueError(
                f"Source of type {self.id!r} needs a 'url' (a TripIt calendar feed URL, "
                "or a path to an .ics file)."
            )
        if "airline_lookup" in config:
            self.resolver.lookup = bool(config["airline_lookup"])
        return self.parse(fetch_ical(url))

    def parse(self, ical_bytes: bytes) -> Iterator[FlightRecord | UnparsedEvent]:
        """Turn raw iCal bytes into FlightRecords and UnparsedEvents.

        Two passes, because resolving a codeshare needs the whole feed: a direct
        segment on the operating airline teaches us its IATA code, and that then
        resolves any codeshare it operates, with no lookup needed.
        """
        cal = Calendar.from_ical(ical_bytes)
        events = [c for c in cal.walk() if c.name == "VEVENT" and is_flight_event(c)]

        parsed = [self._read_event(c) for c in events]

        # Pass 1: learn airline name -> IATA code wherever the description simply
        # restates the flight from the summary, i.e. it isn't a codeshare.
        for item in parsed:
            if item["operating_number"] and item["operating_number"] == item["flight_number"]:
                self.resolver.learn(item["operating_name"], item["carrier_code"])

        # Pass 2: build the records, resolving operating carriers as needed.
        for item in parsed:
            yield self._build_record(item)

    def _read_event(self, component) -> dict:
        uid = str(component.get("uid", ""))
        summary = str(component.get("summary", ""))
        description = str(component.get("description", ""))
        location = str(component.get("location", ""))
        dtstart = component.get("dtstart")

        carrier_code, flight_number, origin, destination = extract_flight_fields(
            summary, description, location
        )
        operating_name, operating_number = extract_operating_flight(description)

        return {
            "source_id": _source_id(uid, summary, str(dtstart) if dtstart is not None else ""),
            "summary": summary,
            "description": description,
            "location": location,
            "carrier_code": carrier_code,
            "flight_number": flight_number,
            "origin": origin,
            "destination": destination,
            "operating_name": operating_name,
            "operating_number": operating_number,
            # Origin first: the departure date is the local date *there*, and
            # TripIt states times in UTC.
            "flight_date": departure_date(dtstart.dt if dtstart is not None else None, origin),
        }

    def _operating_flight(self, item: dict) -> tuple[str | None, str | None]:
        """(carrier code, flight number) of the airline actually operating this."""
        number = item["operating_number"]
        if not number:
            return None, None
        if number == item["flight_number"]:
            return item["carrier_code"], number  # not a codeshare
        code = self.resolver.resolve(item["operating_name"])
        if not code:
            # Unresolvable: leave it priced under the marketing number, which is
            # what contrail did before codeshares were handled at all.
            return None, None
        return code, number

    def _build_record(self, item: dict):
        flight_date = item["flight_date"]
        required = [
            item["carrier_code"],
            item["flight_number"],
            item["origin"],
            item["destination"],
            flight_date,
        ]
        if all(required):
            operating_code, operating_number = self._operating_flight(item)
            return FlightRecord(
                source=self.id,
                source_id=item["source_id"],
                flight_date=flight_date,
                carrier_code=item["carrier_code"],
                flight_number=item["flight_number"],
                origin=item["origin"],
                destination=item["destination"],
                operating_carrier_code=operating_code,
                operating_flight_number=operating_number,
                raw={
                    "summary": item["summary"],
                    "description": item["description"],
                    "location": item["location"],
                },
            )
        return self._build_unparsed(item)

    def _build_unparsed(self, item: dict) -> UnparsedEvent:
        # Keep whatever we did recover. A partial flight_date in particular
        # keeps the row in chronological order in the CSV.
        partial = {
            "flight_date": item["flight_date"],
            "carrier_code": item["carrier_code"],
            "flight_number": item["flight_number"],
            "origin": item["origin"],
            "destination": item["destination"],
        }
        return UnparsedEvent(
            source=self.id,
            source_id=item["source_id"],
            # SUMMARY alone often omits the very details a failed parse needs,
            # so give the reviewer everything.
            raw_text=" | ".join(
                filter(None, [item["summary"], item["description"], item["location"]])
            ),
            partial={k: v for k, v in partial.items() if v},
        )
