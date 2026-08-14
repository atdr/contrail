"""TripIt iCal importer.

Ported from the prototype's ``parse_flights_from_ical`` / ``is_flight_event`` /
``extract_flight_fields``. The regexes below are unchanged: they were validated
against a real TripIt feed, so resist the urge to "tidy" them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from icalendar import Calendar

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


def _looks_like_url(source: str) -> bool:
    return urlparse(source).scheme in ("http", "https")


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

    def fetch(self, config: dict) -> Iterable[FlightRecord | UnparsedEvent]:
        url = config.get("url") or config.get("ical_url")
        if not url:
            raise ValueError(
                f"Source of type {self.id!r} needs a 'url' (a TripIt calendar feed URL, "
                "or a path to an .ics file)."
            )
        return self.parse(fetch_ical(url))

    def parse(self, ical_bytes: bytes) -> Iterator[FlightRecord | UnparsedEvent]:
        """Turn raw iCal bytes into FlightRecords and UnparsedEvents."""
        cal = Calendar.from_ical(ical_bytes)
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            if not is_flight_event(component):
                continue

            uid = str(component.get("uid", ""))
            summary = str(component.get("summary", ""))
            description = str(component.get("description", ""))
            location = str(component.get("location", ""))

            dtstart = component.get("dtstart")
            flight_date = None
            if dtstart is not None:
                dt = dtstart.dt
                flight_date = dt.date() if isinstance(dt, datetime) else dt

            carrier_code, flight_number, origin, destination = extract_flight_fields(
                summary, description, location
            )

            if all([carrier_code, flight_number, origin, destination, flight_date]):
                yield FlightRecord(
                    source=self.id,
                    source_id=uid,
                    flight_date=flight_date,
                    carrier_code=carrier_code,
                    flight_number=flight_number,
                    origin=origin,
                    destination=destination,
                    raw={
                        "summary": summary,
                        "description": description,
                        "location": location,
                    },
                )
            else:
                # Keep whatever we did recover. A partial flight_date in
                # particular keeps the row in chronological order in the CSV.
                partial = {
                    "flight_date": flight_date,
                    "carrier_code": carrier_code,
                    "flight_number": flight_number,
                    "origin": origin,
                    "destination": destination,
                }
                yield UnparsedEvent(
                    source=self.id,
                    source_id=uid,
                    # SUMMARY alone often omits the very details a failed parse
                    # needs, so give the reviewer everything.
                    raw_text=" | ".join(filter(None, [summary, description, location])),
                    partial={k: v for k, v in partial.items() if v},
                )
