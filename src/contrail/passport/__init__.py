"""Build a private, self-contained Passport from a contrail CSV."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path

from contrail import __version__, resync
from contrail.airlines import airline_name
from contrail.airports import details_for
from contrail.config import DEFAULT_PASSPORT_OUTPUT as DEFAULT_OUTPUT_PATH
from contrail.config import PASSPORT_FORMATS
from contrail.storage.local_csv import is_cancelled

EARTH_RADIUS_KM = 6371.0088

# Passport's authored script, in the order the files are joined into the one
# inline <script>. Each file adds a module to the `Passport` namespace and may
# read only the modules before it, so the order is part of the code: format.js
# declares the namespace and goes first, main.js draws the page and goes last.
PASSPORT_SCRIPTS = ("format.js", "aggregate.js", "charts.js", "map.js", "panel.js", "main.js")


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


# TIM's figure for each cabin, under the payload's own names. A flight priced
# before TIM answered per cabin, or not priced at all, has none of them.
_CABIN_FIGURES = (
    ("economy", "emissions_kg_economy"),
    ("premiumEconomy", "emissions_kg_premium_economy"),
    ("business", "emissions_kg_business"),
    ("first", "emissions_kg_first"),
)


def _cabin_kg(row: dict) -> dict | None:
    figures = {name: _number(row.get(field)) for name, field in _CABIN_FIGURES}
    if all(value is None for value in figures.values()):
        return None
    return {name: round(value, 3) if value is not None else None for name, value in figures.items()}


def _scheduled(value) -> str | None:
    """A stored instant as the page shows it: local wall time to the minute
    with its offset, so the page can print it without converting zones."""
    instant = _instant(value)
    return instant.isoformat(timespec="minutes") if instant else None


def _optional(flight: dict, **values) -> dict:
    """Add each value that is known. The payload carries every flight, so a
    key that would only ever say null is left out, and the page reads a
    missing key as null."""
    flight.update({key: value for key, value in values.items() if value is not None})
    return flight


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
    carrier = (
        (row.get("operating_carrier_code") or "").strip().upper()
        or (row.get("carrier_code") or "").strip().upper()
        or "Unknown"
    )

    flight = {
        "date": day.isoformat() if day else None,
        "year": day.year if day else None,
        "origin": origin or None,
        "destination": destination or None,
        "route": route,
        "carrier": carrier,
        # None where the bundled table lacks the code or holds it under more
        # than one airline: the page then shows the code alone.
        "carrierName": airline_name(carrier) if carrier != "Unknown" else None,
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
        "start": start,
        "end": end,
    }
    return _optional(
        flight,
        departure=_scheduled(row.get("departure_time")),
        arrival=_scheduled(row.get("arrival_time")),
        cabinKg=_cabin_kg(row),
        contrailsImpact=(row.get("contrails_impact") or "").strip().lower() or None,
        modelVersion=(row.get("model_version") or "").strip() or None,
    )


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


# Each asset the page is drawn from besides its payload, by the template
# marker it fills: the element that marker sits in, and the file a site
# publishes it as under assets/. Script elements run in the template's order,
# whichever format is built. The authored script is one file in a site too,
# the same text a standalone page runs, so the two cannot drift apart.
_ASSETS = {
    "__PASSPORT_CSS__": ("style", "passport.css"),
    "__WORLD_TOPOJSON__": ("json", "countries-110m.json"),
    "__CHARTJS_JS__": ("script", "chart.umd.min.js"),
    "__D3_ARRAY_JS__": ("script", "d3-array.min.js"),
    "__D3_GEO_JS__": ("script", "d3-geo.min.js"),
    "__TOPOJSON_JS__": ("script", "topojson-client.min.js"),
    "__PASSPORT_JS__": ("script", "passport.js"),
}

# A site's layout. Everything but the page and its data is under assets/, and
# assets/ is the only folder a render removes anything from.
SITE_INDEX = "index.html"
SITE_DATA = "data.json"
SITE_ASSETS = "assets"


def _read_assets() -> dict[str, str]:
    package = files(__package__)

    def vendor(name: str) -> str:
        return package.joinpath("vendor", name).read_text(encoding="utf-8")

    return {
        # Natural Earth's countries as TopoJSON, and the three bundles that
        # project them: d3-geo needs d3-array, so it comes after it.
        "__WORLD_TOPOJSON__": vendor("countries-110m.json"),
        "__D3_ARRAY_JS__": vendor("d3-array.min.js"),
        "__D3_GEO_JS__": vendor("d3-geo.min.js"),
        "__TOPOJSON_JS__": vendor("topojson-client.min.js"),
        "__CHARTJS_JS__": vendor("chart.umd.min.js"),
        # The authored styles and script. Separate files so each can be read,
        # reviewed and checked as the language it is.
        "__PASSPORT_CSS__": package.joinpath("passport.css").read_text(encoding="utf-8"),
        "__PASSPORT_JS__": "\n".join(
            package.joinpath(name).read_text(encoding="utf-8") for name in PASSPORT_SCRIPTS
        ),
    }


def _policy(scripts: str, styles: str, *, connect: str | None = None) -> str:
    """The Content-Security-Policy every Passport carries.

    Nothing loads from anywhere the policy does not name: no CDN, no tile
    server, no font host. Chart.js draws its canvases from data: and blob:
    URLs, and the favicon is an empty data: URL. base-uri and form-action are
    not covered by default-src, and the page has no use for either. A policy
    in a meta element cannot set frame-ancestors, so framing is left to
    whatever serves the page."""
    directives = [
        ("default-src", "'none'"),
        ("script-src", scripts),
        ("style-src", styles),
        ("img-src", "data: blob:"),
        ("connect-src", connect),
        ("base-uri", "'none'"),
        ("form-action", "'none'"),
    ]
    return "; ".join(f"{name} {sources}" for name, sources in directives if sources)


def _escape(asset: str) -> str:
    """Keep inlined text from closing the element it sits in."""
    return asset.replace("</", "<\\/")


def _inline_hash(template: str, marker: str, tag: str, asset: str) -> str:
    """The CSP source for one inline block, hashed over exactly the text the
    browser will see between the tags: the template's own whitespace around
    the marker, and the asset as `_fill` escapes it.

    Hashed from the template rather than found in the filled page, so the
    payload, which is user data, can never add or move a hash."""
    found = re.findall(rf"<{tag}>(\s*){re.escape(marker)}(\s*)</{tag}>", template)
    if len(found) != 1:
        raise ValueError(f"Passport template must hold {marker} alone in one <{tag}>")
    before, after = found[0]
    digest = hashlib.sha256(f"{before}{_escape(asset)}{after}".encode()).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def _standalone(template: str, assets: dict[str, str]) -> str:
    """Everything inline: one file that opens from disk."""
    inline = {
        kind: [m for m, (k, _) in _ASSETS.items() if k == kind] for kind in ("script", "style")
    }
    # Hashes rather than 'unsafe-inline': the policy then admits these exact
    # blocks and nothing else, so text that ever got into the page some other
    # way still could not run.
    policy = _policy(
        " ".join(_inline_hash(template, m, "script", assets[m]) for m in inline["script"]),
        " ".join(_inline_hash(template, m, "style", assets[m]) for m in inline["style"]),
    )
    return _fill(template, {**assets, "__PASSPORT_CSP__": policy})


# How a site links each kind of element to its file instead of holding it.
# A JSON element stays, emptied, and names its file for main.js to fetch.
_LINKS = {
    "script": (r"<script>\s*{}\s*</script>", lambda match, url: f'<script src="{url}"></script>'),
    "style": (
        r"<style>\s*{}\s*</style>",
        lambda match, url: f'<link rel="stylesheet" href="{url}" />',
    ),
    "json": (
        r'(<script [^>]*type="application/json")>\s*{}\s*</script>',
        lambda match, url: f'{match.group(1)} data-src="{url}"></script>',
    ),
}


def _link(template: str, marker: str, kind: str, url: str) -> str:
    pattern, element = _LINKS[kind]
    linked, count = re.subn(
        pattern.format(re.escape(marker)), lambda match: element(match, url), template
    )
    if count != 1:
        raise ValueError(f"Passport template must hold {marker} alone in one {kind} element")
    return linked


def _site(template: str, assets: dict[str, str]) -> dict[str, str]:
    """A folder for a server: the page, its data, and each asset as a file
    of its own, all fetched from the page's own origin.

    Each asset keeps one name across versions, so a release that leaves a
    file alone leaves it byte for byte alone, and history holds no renames.
    Its URL carries a hash of its content instead, so a browser that cached
    the last version fetches the new one the moment it changes."""
    built = {}
    for marker, (kind, name) in _ASSETS.items():
        path = f"{SITE_ASSETS}/{name}"
        built[path] = assets[marker]
        version = hashlib.sha256(assets[marker].encode()).hexdigest()[:12]
        template = _link(template, marker, kind, f"{path}?v={version}")
    built[SITE_DATA] = assets["__PASSPORT_DATA__"]
    template = _link(template, "__PASSPORT_DATA__", "json", SITE_DATA)
    # Last, so a render writes the page only after everything it names.
    built[SITE_INDEX] = _fill(
        template, {"__PASSPORT_CSP__": _policy("'self'", "'self'", connect="'self'")}
    )
    return built


def _document(rows: list[dict], now: datetime | None, mode: str = "standalone") -> dict[str, str]:
    """Every file Passport writes in `mode`, by its path inside the output.

    A standalone Passport is one file, and its one path is "": the output
    path itself. A site's paths are relative to the folder it names."""
    if mode not in PASSPORT_FORMATS:
        raise ValueError(f"Unknown Passport format {mode!r}. Expected one of: {PASSPORT_FORMATS}.")
    template = files(__package__).joinpath("template.html").read_text(encoding="utf-8")
    assets = _read_assets()
    assets["__PASSPORT_DATA__"] = json.dumps(
        build_data(rows, now), ensure_ascii=False, separators=(",", ":")
    )
    if mode == "site":
        return _site(template, assets)
    return {"": _standalone(template, assets)}


def _fill(template: str, assets: dict[str, str]) -> str:
    """Put each asset in place of its marker, in one pass over the template.

    One pass, so nothing inserted is ever scanned again: a payload string that
    happens to spell a marker stays data rather than drawing a bundle into the
    data block. Each marker must appear exactly once, so a misspelt or dropped
    one fails here rather than shipping as literal text in the page."""
    for marker in assets:
        count = template.count(marker)
        if count != 1:
            raise ValueError(f"Passport template has {count} of {marker}, expected exactly one")
    # JSON sits in a script element too. Prevent user-entered text from closing it.
    escaped = {marker: _escape(asset) for marker, asset in assets.items()}
    pattern = re.compile("|".join(map(re.escape, assets)))
    return pattern.sub(lambda match: escaped[match.group(0)], template)


def render(
    rows: list[dict], output_path: str | Path, now: datetime | None = None, *, mode="standalone"
) -> Path:
    """Write Passport, one offline HTML file or a site folder, and return its
    resolved path."""
    return render_if_changed(rows, output_path, now, force=True, mode=mode)[0]


# The one part of the page that moves on every build. Each flight's departed
# flag also depends on the clock, but that is a real change worth writing.
_GENERATED_AT = re.compile(r'"generatedAt":"[^"]*"')


def _differs(path: Path, text: str) -> bool:
    if not path.is_file():
        return True
    existing = path.read_text(encoding="utf-8")
    return _GENERATED_AT.sub("", existing) != _GENERATED_AT.sub("", text)


def _visible(folder: Path) -> list[Path]:
    """A folder's entries, less the hidden ones: a .DS_Store or a .gitkeep is
    neither Passport's to remove nor a reason to refuse the folder."""
    return sorted(entry for entry in folder.iterdir() if not entry.name.startswith("."))


def _claim(folder: Path) -> None:
    """Refuse a folder that holds anything a site render did not write.

    A site removes the assets it no longer ships, so it has to own its folder
    outright. Pointed by mistake at the repository root, or anywhere else
    with files in it, it stops here before writing a byte. Symlinks are
    refused too: one would carry a removal outside the folder."""
    if not folder.exists():
        return
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError(f"{folder} is not a folder, and a Passport site is written as one")
    foreign = [
        entry
        for entry in _visible(folder)
        if entry.is_symlink()
        or not (
            (entry.name in (SITE_INDEX, SITE_DATA) and entry.is_file())
            or (entry.name == SITE_ASSETS and entry.is_dir())
        )
    ]
    assets = folder / SITE_ASSETS
    if assets.is_dir() and not assets.is_symlink():
        foreign += [
            entry for entry in _visible(assets) if entry.is_symlink() or not entry.is_file()
        ]
    if foreign:
        names = ", ".join(str(entry.relative_to(folder)) for entry in foreign)
        raise ValueError(
            f"{folder} holds what a Passport site did not write ({names}). A site owns its "
            "folder and removes the files it no longer needs, so give it a new or empty one."
        )


def _remove_stale(folder: Path, built: dict[str, str]) -> bool:
    """Remove each asset an earlier version wrote and this one does not.
    Only ever inside assets/, which `_claim` has checked holds only files."""
    keep = {Path(path).name for path in built if path.startswith(f"{SITE_ASSETS}/")}
    stale = [entry for entry in _visible(folder / SITE_ASSETS) if entry.name not in keep]
    for entry in stale:
        entry.unlink()
    return bool(stale)


def render_if_changed(
    rows: list[dict],
    output_path: str | Path,
    now: datetime | None = None,
    *,
    force=False,
    mode="standalone",
) -> tuple[Path, bool]:
    """Write each Passport file that differs from what is there by more than
    its generatedAt stamp. Returns the resolved output path and whether
    anything was written or removed.

    A page rebuilt daily would otherwise be a new ~800 KB version in history
    every day with nothing in it changed: the same rule the CSV follows. A
    site compares file by file, so a day with nothing new writes nothing."""
    output = Path(output_path)
    built = _document(rows, now, mode)
    if mode == "site":
        _claim(output)
    elif output.is_dir():
        raise ValueError(f"{output} is a folder, and a standalone Passport is one HTML file")

    written = False
    for path, text in built.items():
        target = output / path if path else output
        if force or _differs(target, text):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            written = True
    if mode == "site":
        written = _remove_stale(output, built) or written
    return output.resolve(), written


__all__ = [
    "DEFAULT_OUTPUT_PATH",
    "build_data",
    "great_circle_km",
    "render",
    "render_if_changed",
    "scheduled_hours",
]
