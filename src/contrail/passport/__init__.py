"""Build a private, self-contained Passport from a contrail CSV."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path

from contrail import __version__, resync
from contrail.airports import details_for
from contrail.config import DEFAULT_PASSPORT_OUTPUT as DEFAULT_OUTPUT_PATH
from contrail.storage.local_csv import is_cancelled

EARTH_RADIUS_KM = 6371.0088


def _number(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _date(value) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _instant(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _airport(iata: str) -> dict | None:
    entry = details_for(iata)
    if not entry:
        return None
    lat = _number(entry.get("lat"))
    lon = _number(entry.get("lon"))
    if lat is None or lon is None:
        return None
    return {
        "iata": iata,
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "country": entry.get("country") or None,
    }


def great_circle_km(origin: str, destination: str) -> float | None:
    """Great-circle distance between two bundled airport coordinates."""
    start = _airport(origin)
    end = _airport(destination)
    if not start or not end:
        return None

    lat1, lon1 = math.radians(start["lat"]), math.radians(start["lon"])
    lat2, lon2 = math.radians(end["lat"]), math.radians(end["lon"])
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    haversine = min(1.0, max(0.0, haversine))
    central_angle = 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    return round(EARTH_RADIUS_KM * central_angle, 1)


def scheduled_hours(row: dict) -> float | None:
    """Scheduled gate-to-gate hours, where both source instants are usable."""
    departure = _instant(row.get("departure_time"))
    arrival = _instant(row.get("arrival_time"))
    if departure is None or arrival is None:
        return None
    hours = (arrival.astimezone(UTC) - departure.astimezone(UTC)).total_seconds() / 3600
    if not 0 < hours <= 36:
        return None
    return round(hours, 3)


def _flight(row: dict, now: datetime) -> dict | None:
    if is_cancelled(row):
        return None

    origin = (row.get("origin") or "").strip().upper()
    destination = (row.get("destination") or "").strip().upper()
    day = _date(row.get("flight_date"))
    emissions = _number(row.get("emissions_kg_actual"))
    distance = great_circle_km(origin, destination) if origin and destination else None
    start = _airport(origin) if origin else None
    end = _airport(destination) if destination else None
    departed = resync.has_departed(row, now)
    cabin = (row.get("cabin_class_known") or "").strip().replace("_", " ")
    route = " ↔ ".join(sorted((origin, destination))) if origin and destination else "Unknown"

    return {
        "date": day.isoformat() if day else None,
        "year": day.year if day else None,
        "origin": origin or None,
        "destination": destination or None,
        "route": route,
        "carrier": (
            (row.get("operating_carrier_code") or "").strip().upper()
            or (row.get("carrier_code") or "").strip().upper()
            or "Unknown"
        ),
        "flight": "".join(
            (
                (row.get("carrier_code") or "").strip().upper(),
                (row.get("flight_number") or "").strip(),
            )
        ),
        "cabin": cabin.title() if cabin else "Assumed economy",
        "cabinKnown": bool(cabin),
        "aircraft": (row.get("aircraft_type") or "").strip() or "Unknown",
        "reason": (row.get("flight_reason") or "").strip().title() or "Unknown",
        "emissionsSource": (row.get("emissions_source") or "").strip() or "no_data",
        "kg": round(emissions, 3) if emissions is not None else None,
        "distanceKm": distance,
        "durationHours": scheduled_hours(row),
        "departed": departed is True,
        "timDistanceKm": _number(row.get("distance_km")),
        "start": start,
        "end": end,
    }


def build_data(rows: list[dict], now: datetime | None = None) -> dict:
    """Reduce storage rows to the facts needed by the offline dashboard."""
    now = now or datetime.now(UTC)
    flights = [flight for row in rows if (flight := _flight(row, now)) is not None]
    years = sorted({flight["year"] for flight in flights if flight["year"]}, reverse=True)
    return {
        "meta": {
            "generatedAt": now.isoformat(),
            "contrailVersion": __version__,
            "distanceMethod": "WGS84 airport coordinates, Haversine great-circle distance",
            "durationMethod": "scheduled gate departure to scheduled gate arrival",
        },
        "years": years,
        "flights": flights,
    }


def render(rows: list[dict], output_path: str | Path, now: datetime | None = None) -> Path:
    """Write Passport as one offline HTML file and return its resolved path."""
    output = Path(output_path)
    package = files(__package__)
    template = package.joinpath("template.html").read_text(encoding="utf-8")
    payload = json.dumps(build_data(rows, now), ensure_ascii=False, separators=(",", ":"))
    # JSON sits in a script element. Prevent user-entered text from closing it.
    payload = payload.replace("</", "<\\/")
    assets = {
        "__PASSPORT_DATA__": payload,
        "__WORLD_GEOJSON__": package.joinpath("vendor", "world.geojson").read_text(
            encoding="utf-8"
        ),
        "__LEAFLET_CSS__": package.joinpath("vendor", "leaflet.css").read_text(encoding="utf-8"),
        "__LEAFLET_JS__": package.joinpath("vendor", "leaflet.js").read_text(encoding="utf-8"),
        "__CHARTJS_JS__": package.joinpath("vendor", "chart.umd.min.js").read_text(
            encoding="utf-8"
        ),
    }
    document = template
    for marker, asset in assets.items():
        document = document.replace(marker, asset.replace("</", "<\\/"), 1)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output.resolve()


__all__ = ["DEFAULT_OUTPUT_PATH", "build_data", "great_circle_km", "render", "scheduled_hours"]
