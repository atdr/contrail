"""Passport: reducing the log to the facts the offline dashboard shows.

Everything here is pure. The template and the vendored assets are read from the
installed package, which is also what proves they were packaged at all.
"""

import base64
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from importlib.resources import files

import pytest

import contrail.passport as passport
from contrail import __version__
from contrail.passport import (
    build_data,
    great_circle_km,
    render,
    scheduled_hours,
)
from contrail.storage.local_csv import CSV_FIELDS, STATUS_CANCELLED

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
DEPARTURE = "2026-03-04T20:30:00-05:00"
ARRIVAL = "2026-03-05T08:15:00+00:00"


def row(**extra):
    data = {field: "" for field in CSV_FIELDS}
    data.update(
        source="tripit_ical",
        source_id="uid-1",
        flight_date="2026-03-04",
        carrier_code="BA",
        flight_number="112",
        origin="JFK",
        destination="LHR",
        departure_time=DEPARTURE,
        arrival_time=ARRIVAL,
        emissions_source="exact",
        emissions_kg_economy="247.027",
        emissions_kg_actual="247.027",
    )
    data.update(extra)
    return data


def only(rows, now=NOW):
    flights = build_data(rows, now)["flights"]
    assert len(flights) == 1
    return flights[0]


# -- distance -----------------------------------------------------------------


def test_distance_is_the_great_circle_between_bundled_coordinates():
    """Every flight measured the same way, which is the point of computing it
    here rather than reading TIM's own figure for the rows that have one."""
    assert great_circle_km("JFK", "LHR") == pytest.approx(5539.6, abs=1.0)


def test_distance_does_not_depend_on_the_direction_flown():
    assert great_circle_km("JFK", "LHR") == great_circle_km("LHR", "JFK")


def test_an_airport_with_no_coordinates_has_no_distance():
    """A flight still counts towards a total; it just cannot be plotted or
    measured, and inventing a distance would corrupt the per-kilometre figure."""
    assert great_circle_km("LHR", "ZZZ") is None
    assert only([row(destination="ZZZ")])["distanceKm"] is None
    assert only([row(destination="ZZZ")])["end"] is None


def test_an_airport_with_incomplete_coordinates_is_not_plotted(monkeypatch):
    """One coordinate cannot place an airport or form a distance, and treating a
    missing value as zero would draw a plausible but false route."""
    monkeypatch.setattr(passport, "details_for", lambda _iata: {"lat": 51.5, "lon": None})

    assert great_circle_km("LHR", "JFK") is None


# -- scheduled block time -----------------------------------------------------


def test_block_time_spans_the_two_stored_instants():
    assert scheduled_hours(row()) == 6.75  # a JFK-LHR night flight, in two zones


def test_one_endpoint_alone_is_not_a_duration():
    assert scheduled_hours(row(arrival_time="")) is None
    assert scheduled_hours(row(departure_time="")) is None


def test_a_naive_instant_is_not_usable():
    """Without an offset there is no telling which zone it was written in, and
    a duration off by the zone difference would look perfectly plausible."""
    assert scheduled_hours(row(arrival_time="2026-03-05T08:15:00")) is None


def test_a_malformed_instant_is_not_usable():
    """A hand-edited timestamp should remove the derived duration instead of
    preventing the entire Passport from rendering."""
    assert scheduled_hours(row(arrival_time="not-a-time")) is None


@pytest.mark.parametrize("hours", [-2, 0, 37])
def test_a_duration_no_flight_has_is_refused(hours):
    """Arrival before departure is what a source that forgot the overnight date
    rollover produces; 36 hours is past the longest flight anyone sells."""
    arrival = datetime.fromisoformat(DEPARTURE) + timedelta(hours=hours)
    assert scheduled_hours(row(arrival_time=arrival.isoformat())) is None


# -- what a row becomes -------------------------------------------------------


def test_a_cancelled_flight_is_not_in_the_dashboard():
    """It counts towards no total in the CSV either — see `actual_kg`."""
    assert build_data([row(status=STATUS_CANCELLED)], NOW)["flights"] == []


def test_a_flight_with_no_stated_cabin_is_marked_as_assumed():
    """The figure it is counted at is economy, so the chart says economy — and
    says that it was assumed, which is what the estimate quality section reads."""
    flight = only([row()])
    assert flight["cabin"] == "Assumed economy"
    assert flight["cabinKnown"] is False

    flown = only([row(cabin_class_known="premium_economy")])
    assert flown["cabin"] == "Premium Economy"
    assert flown["cabinKnown"] is True


def test_a_route_reads_the_same_flown_either_way():
    """Otherwise a return trip is two routes and neither shows the real total."""
    assert only([row()])["route"] == only([row(origin="LHR", destination="JFK")])["route"]


def test_the_operating_airline_is_named_where_the_table_can_say_who():
    """The page labels an airline "British Airways (BA)", or the code alone
    where the bundled table holds the code under more than one airline."""
    flight = only([row(operating_carrier_code="AA")])
    assert (flight["carrier"], flight["carrierName"]) == ("AA", "American Airlines")
    assert only([row()])["carrierName"] == "British Airways"
    assert only([row(carrier_code="AN")])["carrierName"] is None
    unknown = only([row(carrier_code="")])
    assert (unknown["carrier"], unknown["carrierName"]) == ("Unknown", None)


def test_an_unpriced_flight_has_no_figure_rather_than_a_zero():
    assert only([row(emissions_source="no_data", emissions_kg_actual="")])["kg"] is None


def test_a_flight_is_departed_against_the_clock_it_is_given():
    assert only([row()], datetime(2026, 3, 5, 12, 0, tzinfo=UTC))["departed"] is True
    assert only([row()], datetime(2026, 3, 1, 12, 0, tzinfo=UTC))["departed"] is False


def test_a_flight_carries_what_its_detail_view_shows():
    """The flight panel shows TIM's figure for every cabin, the contrail
    impact, the model version and the scheduled times, all from the row."""
    flight = only(
        [
            row(
                emissions_kg_business="716.4",
                emissions_kg_first="",
                contrails_impact="Moderate",
                model_version="3.0.0+20260814",
            )
        ]
    )
    assert flight["cabinKg"] == {
        "economy": 247.027,
        "premiumEconomy": None,
        "business": 716.4,
        "first": None,
    }
    assert flight["contrailsImpact"] == "moderate"
    assert flight["modelVersion"] == "3.0.0+20260814"
    # Local wall time with its offset, so the page prints it unconverted.
    assert flight["departure"] == "2026-03-04T20:30-05:00"
    assert flight["arrival"] == "2026-03-05T08:15+00:00"


def test_facts_a_flight_lacks_are_left_out_of_the_payload():
    """Every flight is embedded, so a key that would only say null costs
    bytes on every one of them. The page reads a missing key as unknown."""
    flight = only([row(emissions_kg_economy="", emissions_kg_actual="", arrival_time="not-a-time")])
    for key in ("cabinKg", "contrailsImpact", "modelVersion", "arrival"):
        assert key not in flight, key
    assert flight["departure"] == "2026-03-04T20:30-05:00"


def test_tim_distance_is_not_embedded():
    """Passport measures every flight one way; TIM's own distance was
    embedded but never read."""
    assert "timDistanceKm" not in only([row(distance_km="5555")])


def test_years_are_newest_first_and_only_of_dated_flights():
    rows = [row(flight_date="2024-01-01"), row(), row(flight_date="")]
    assert build_data(rows, NOW)["years"] == [2026, 2024]


def test_the_meta_block_says_how_the_derived_figures_were_derived():
    meta = build_data([row()], NOW)["meta"]
    assert meta["contrailVersion"] == __version__
    assert meta["generatedAt"] == NOW.isoformat()
    assert "Haversine" in meta["distanceMethod"]
    assert "scheduled gate" in meta["durationMethod"]


# -- the written file ---------------------------------------------------------

MARKERS = (
    "PASSPORT_DATA",
    "WORLD_TOPOJSON",
    "D3_ARRAY_JS",
    "D3_GEO_JS",
    "TOPOJSON_JS",
    "CHARTJS_JS",
    "PASSPORT_CSS",
    "PASSPORT_JS",
    "PASSPORT_CSP",
)
AUTHORED = ("template.html", "passport.css", *passport.PASSPORT_SCRIPTS)


def payload(document: str) -> dict:
    start = document.index('<script id="passport-data" type="application/json">')
    start = document.index(">", start) + 1
    return json.loads(document[start : document.index("</script>", start)])


def test_render_writes_one_self_contained_file(tmp_path):
    output = render([row()], tmp_path / "reports" / "passport.html", NOW)

    document = output.read_text(encoding="utf-8")
    assert output == (tmp_path / "reports" / "passport.html").resolve()  # made the directory
    for marker in MARKERS:
        assert f"__{marker}__" not in document
    # The vendored assets landed.
    assert "geoEqualEarth" in document and "topojson" in document and "Chart" in document


def test_the_page_fetches_nothing_when_it_opens(tmp_path):
    """The whole reason the assets are vendored: a dashboard of someone's travel
    history must not announce itself to a CDN or a tile server to render.

    The authored sources are what this guards. The vendored bundles carry URLs
    of their own, in licence headers and comments, so scanning the rendered
    file for a hostname says nothing; scanning it for a map tile server, the
    one fetch a map could regrow, says plenty.
    """
    fetches = ('href="http', "href='http", 'src="http', "src='http", "url(http", "@import")
    package = files("contrail.passport")
    for name in AUTHORED:
        source = package.joinpath(name).read_text(encoding="utf-8")
        for fetched in fetches:
            assert fetched not in source, f"{name} fetches {fetched!r}"

    document = render([row()], tmp_path / "passport.html", NOW).read_text(encoding="utf-8")
    assert "<script src" not in document
    assert "tile.openstreetmap" not in document


def test_the_authored_styles_hold_nothing_the_inline_escape_would_corrupt():
    """Every inlined asset has `</` escaped to `<\\/` so it cannot close its
    element. Harmless in JS, but in CSS it would change what the rule says."""
    css = files("contrail.passport").joinpath("passport.css").read_text(encoding="utf-8")
    assert "</" not in css


def test_the_embedded_data_is_what_build_data_produced(tmp_path):
    output = render([row()], tmp_path / "passport.html", NOW)
    assert payload(output.read_text(encoding="utf-8")) == build_data([row()], NOW)


def test_itinerary_text_cannot_close_the_script_element(tmp_path):
    """The payload is user data sitting in HTML. A source that names an aircraft
    `</script>` would otherwise end the element early and break the page."""
    output = render([row(aircraft_type="</script><b>")], tmp_path / "passport.html", NOW)

    document = output.read_text(encoding="utf-8")
    assert payload(document)["flights"][0]["aircraft"] == "</script><b>"


@pytest.mark.parametrize("marker", [f"__{name}__" for name in MARKERS])
def test_itinerary_text_that_spells_a_marker_stays_data(tmp_path, marker):
    """Substitution is one pass, so a feed value that happens to be a marker
    name is never mistaken for the marker and filled with a bundle."""
    output = render([row(aircraft_type=marker)], tmp_path / "passport.html", NOW)

    document = output.read_text(encoding="utf-8")
    assert payload(document)["flights"][0]["aircraft"] == marker
    assert document.count(marker) == 1  # only the one inside the data
    assert "geoEqualEarth" in document and "Chart" in document


@pytest.mark.parametrize("template", ["no marker here", "__A__ and __A__ again"])
def test_a_template_without_exactly_one_marker_is_refused(template):
    with pytest.raises(ValueError, match="__A__"):
        passport._fill(template, {"__A__": "asset"})


def test_every_asset_lands_where_its_marker_was():
    filled = passport._fill("<__A__|__B__>", {"__A__": "__B__</x>", "__B__": "b"})
    assert filled == "<__B__<\\/x>|b>"


# -- the Content-Security-Policy ----------------------------------------------


def policy(document: str) -> dict[str, list[str]]:
    found = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)" />', document)
    assert found, "no policy in the page"
    return {
        name: sources
        for name, *sources in (directive.split() for directive in found.group(1).split("; "))
    }


def sha256(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


class _InlineBlocks(HTMLParser):
    """Each attribute-free <script> and <style> element's text, as a browser
    reads it: a real HTML parser rather than a regular expression, which
    would miss what a parser accepts, such as an upper case tag."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.blocks = {"script": [], "style": []}
        self._open = None

    def handle_starttag(self, tag, attrs):
        self._open = tag if tag in self.blocks and not attrs else None
        if self._open:
            self.blocks[tag].append("")

    def handle_data(self, data):
        if self._open:
            self.blocks[self._open][-1] += data

    def handle_endtag(self, tag):
        self._open = None


def inline_blocks(document: str) -> tuple[list[str], list[str]]:
    parser = _InlineBlocks()
    parser.feed(document)
    parser.close()
    return parser.blocks["script"], parser.blocks["style"]


def test_the_policy_admits_exactly_the_blocks_the_page_inlines(tmp_path):
    """Hashes, not 'unsafe-inline': each script and style element the page
    holds is admitted by its own hash, and nothing else is admitted at all."""
    document = render([row()], tmp_path / "passport.html", NOW).read_text(encoding="utf-8")
    directives = policy(document)

    scripts, styles = inline_blocks(document)
    assert len(scripts) == [kind for kind, _ in passport._ASSETS.values()].count("script")
    assert directives["script-src"] == [sha256(script) for script in scripts]
    assert directives["style-src"] == [sha256(style) for style in styles]
    assert directives["default-src"] == ["'none'"]
    assert directives["img-src"] == ["data:", "blob:"]
    assert "connect-src" not in directives  # a standalone page fetches nothing
    assert "'unsafe-inline'" not in document


def test_itinerary_text_never_moves_a_hash(tmp_path):
    """The payload is not hashed, and nothing in it can add a source."""
    plain = render([row()], tmp_path / "a.html", NOW).read_text(encoding="utf-8")
    hostile = render(
        [row(aircraft_type="<script>alert(1)</script><style>*{}</style>")], tmp_path / "b.html", NOW
    ).read_text(encoding="utf-8")
    assert policy(hostile) == policy(plain)


def test_the_page_asks_not_to_be_indexed(tmp_path):
    document = render([row()], tmp_path / "passport.html", NOW).read_text(encoding="utf-8")
    assert '<meta name="robots" content="noindex" />' in document
    # The policy comes first, so it governs every element after it.
    assert document.index("Content-Security-Policy") < document.index("<style>")


# -- the site format ----------------------------------------------------------

SITE_FILES = {
    "index.html",
    "data.json",
    "assets/passport.css",
    "assets/passport.js",
    "assets/chart.umd.min.js",
    "assets/d3-array.min.js",
    "assets/d3-geo.min.js",
    "assets/topojson-client.min.js",
    "assets/countries-110m.json",
}


def site(tmp_path, rows=None, now=NOW, name="site"):
    return render([row()] if rows is None else rows, tmp_path / name, now, mode="site")


def tree(folder) -> set[str]:
    return {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}


def test_a_site_is_a_page_its_data_and_one_file_per_asset(tmp_path):
    folder = site(tmp_path)
    assert folder == (tmp_path / "site").resolve()
    assert tree(folder) == SITE_FILES

    index = (folder / "index.html").read_text(encoding="utf-8")
    for marker in MARKERS:
        assert f"__{marker}__" not in index
    assert "<style>" not in index and "<script>" not in index  # nothing inline to run
    assert json.loads((folder / "data.json").read_text(encoding="utf-8")) == build_data(
        [row()], NOW
    )


def test_a_site_ships_the_same_bytes_a_standalone_page_inlines(tmp_path):
    """One authored script in both formats, so they cannot behave differently."""
    folder = site(tmp_path)
    page = render([row()], tmp_path / "passport.html", NOW).read_text(encoding="utf-8")
    script = (folder / "assets" / "passport.js").read_text(encoding="utf-8")
    assert script.replace("</", "<\\/") in page
    vendor = files("contrail.passport").joinpath("vendor", "chart.umd.min.js")
    assert (folder / "assets" / "chart.umd.min.js").read_text(encoding="utf-8") == (
        vendor.read_text(encoding="utf-8")
    )


def test_a_site_loads_its_scripts_in_the_standalone_order(tmp_path):
    index = (site(tmp_path) / "index.html").read_text(encoding="utf-8")
    sources = re.findall(r'<script src="assets/([^"?]+)\?v=[0-9a-f]{12}"></script>', index)
    assert sources == [
        "chart.umd.min.js",
        "d3-array.min.js",
        "d3-geo.min.js",
        "topojson-client.min.js",
        "passport.js",
    ]
    assert 'id="passport-data" type="application/json" data-src="data.json"></script>' in index
    assert re.search(r'id="world-data" [^>]*data-src="assets/countries-110m.json\?v=', index)


def test_an_asset_url_changes_exactly_when_its_content_does(tmp_path, monkeypatch):
    """Names stay put, so history holds no renames; the version in the URL
    is what makes a cached copy stale."""
    before = (site(tmp_path) / "index.html").read_text(encoding="utf-8")
    real = passport._read_assets
    monkeypatch.setattr(
        passport,
        "_read_assets",
        lambda: {**real(), "__PASSPORT_CSS__": real()["__PASSPORT_CSS__"] + "\n/* new */\n"},
    )
    after = (site(tmp_path) / "index.html").read_text(encoding="utf-8")

    changed = [
        (old, new)
        for old, new in zip(before.splitlines(), after.splitlines(), strict=True)
        if old != new
    ]
    assert len(changed) == 1 and "assets/passport.css?v=" in changed[0][0]


def test_site_data_is_plain_json_with_nothing_escaped(tmp_path):
    """data.json is fetched, not inlined, so it needs no `</` escape."""
    folder = site(tmp_path, [row(aircraft_type="</script><b>")])
    text = (folder / "data.json").read_text(encoding="utf-8")
    assert "</script><b>" in text
    assert json.loads(text)["flights"][0]["aircraft"] == "</script><b>"
    assert "</script><b>" not in (folder / "index.html").read_text(encoding="utf-8")


def test_the_site_policy_admits_its_own_origin_and_nothing_inline(tmp_path):
    directives = policy((site(tmp_path) / "index.html").read_text(encoding="utf-8"))
    assert directives == {
        "default-src": ["'none'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "img-src": ["data:", "blob:"],
        "connect-src": ["'self'"],
        "base-uri": ["'none'"],
        "form-action": ["'none'"],
    }


def mtimes(folder) -> dict[str, int]:
    return {
        path.relative_to(folder).as_posix(): path.stat().st_mtime_ns
        for path in folder.rglob("*")
        if path.is_file()
    }


def test_a_site_with_only_a_new_clock_writes_nothing(tmp_path):
    folder = site(tmp_path)
    before = mtimes(folder)

    path, written = passport.render_if_changed(
        [row()], folder, NOW + timedelta(hours=1), mode="site"
    )

    assert (path, written) == (folder, False)
    assert mtimes(folder) == before


def test_a_site_rewrites_only_the_files_that_changed(tmp_path):
    folder = site(tmp_path)
    for path in folder.rglob("*"):  # a known stamp, so a write cannot hide in the clock's grain
        if path.is_file():
            os.utime(path, ns=(1, 1))

    _, written = passport.render_if_changed(
        [row(), row(source_id="uid-2", flight_date="2026-04-01")], folder, NOW, mode="site"
    )

    assert written
    assert [name for name, stamp in mtimes(folder).items() if stamp != 1] == ["data.json"]


def test_a_site_removes_what_an_earlier_version_shipped(tmp_path):
    folder = site(tmp_path)
    (folder / "assets" / "old-bundle.js").write_text("old", encoding="utf-8")
    (folder / "assets" / ".keep").write_text("", encoding="utf-8")  # hidden: not ours to judge

    _, written = passport.render_if_changed([row()], folder, NOW, mode="site")

    assert written
    assert tree(folder) == SITE_FILES | {"assets/.keep"}


@pytest.mark.parametrize(
    "intruder",
    ["notes.txt", "assets/nested/file.js", "data.json/inside", "index.html/inside"],
)
def test_a_site_refuses_a_folder_it_did_not_write(tmp_path, intruder):
    """Pointed at a folder with anything else in it, such as the repository
    root, a site would remove what is not its own. It writes nothing instead."""
    folder = tmp_path / "site"
    (folder / intruder).parent.mkdir(parents=True)
    (folder / intruder).write_text("mine", encoding="utf-8")
    before = tree(folder)

    with pytest.raises(ValueError, match="did not write"):
        site(tmp_path)
    assert tree(folder) == before


def test_a_site_refuses_a_symlink_that_would_carry_a_removal_outside(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.js").write_text("keep", encoding="utf-8")
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "assets").symlink_to(elsewhere)

    with pytest.raises(ValueError, match="did not write"):
        site(tmp_path)
    assert (elsewhere / "precious.js").exists()


def test_a_site_is_never_written_over_a_file(tmp_path):
    (tmp_path / "site").write_text("a file", encoding="utf-8")
    with pytest.raises(ValueError, match="not a folder"):
        site(tmp_path)


def test_a_standalone_page_is_never_written_over_a_folder(tmp_path):
    (tmp_path / "passport").mkdir()
    with pytest.raises(ValueError, match="is a folder"):
        render([row()], tmp_path / "passport", NOW)


def test_an_unknown_format_is_refused(tmp_path):
    with pytest.raises(ValueError, match="Unknown Passport format"):
        render([row()], tmp_path / "x", NOW, mode="zip")
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize("marker", [f"__{name}__" for name in MARKERS])
def test_itinerary_text_that_spells_a_marker_stays_data_in_a_site(tmp_path, marker):
    folder = site(tmp_path, [row(aircraft_type=marker)])
    assert (
        json.loads((folder / "data.json").read_text(encoding="utf-8"))["flights"][0]["aircraft"]
        == marker
    )
    assert marker not in (folder / "index.html").read_text(encoding="utf-8")
