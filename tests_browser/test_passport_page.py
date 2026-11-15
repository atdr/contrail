"""The generated Passport, opened and used in Chromium.

The Python suite proves what goes into the page. This proves the page then
shows it: the authored script runs without error, the controls change what
they claim to, and nothing is fetched. Assertions read the DOM, and Chart.js's
own record of each chart's data, rather than pixels.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from conftest import NOW, fixture_rows, row
from playwright.sync_api import Page, expect

VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "mobile": {"width": 390, "height": 844},
    "narrow": {"width": 320, "height": 640},
}


def text(page: Page, selector: str) -> str:
    """Visible text with whitespace collapsed; the hero splits across lines."""
    return " ".join(page.locator(selector).inner_text().split())


def datasets(page: Page, canvas: str) -> dict[str, list[float]]:
    return dict(
        page.evaluate(
            "id => Chart.getChart(id).data.datasets.map(set => [set.label, set.data])",
            canvas,
        )
    )


def total(page: Page, canvas: str) -> float:
    return sum(sum(values) for values in datasets(page, canvas).values())


def sideways_scroll(page: Page) -> int:
    return page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )


def pressed(page: Page, selector: str):
    return expect(page.locator(selector)).to_have_attribute("aria-pressed", "true")


# -- loading ------------------------------------------------------------------


def test_the_page_loads_without_errors_or_requests(open_passport, problems):
    """`problems` fails the test on any page error, console error, or request
    that is not the file itself. Asserted here, not only at teardown, so this
    test names the guarantee."""
    page = open_passport()
    expect(page.locator("#generated")).to_contain_text("Keep this file private")
    assert page.evaluate("typeof L.map") == "function"
    assert problems == []


def test_the_hero_and_summary_match_the_fixture(open_passport):
    page = open_passport()

    assert text(page, "#hero-value") == "1.8 t CO₂e"
    assert text(page, "#hero-context") == "Passenger emissions across 4 completed flights"
    assert text(page, "#flight-count") == "4"
    assert text(page, "#planned-total") == "900 kg CO₂e"
    assert text(page, "#airport-count") == "4"
    assert text(page, "#country-count") == "3"
    assert total(page, "pattern-chart") == 1810


# -- controls -----------------------------------------------------------------


def test_choosing_a_year_rescopes_the_totals_and_charts(open_passport):
    page = open_passport()
    assert page.locator("#periods button").all_inner_texts() == ["All time", "2026", "2025"]

    page.get_by_role("button", name="2026", exact=True).click()
    pressed(page, "#periods button:text-is('2026')")
    assert text(page, "#hero-value") == "310 kg CO₂e"
    assert text(page, "#flight-count") == "3"
    assert total(page, "pattern-chart") == 310
    assert page.evaluate("Chart.getChart('routes').data.labels") == ["JFK ↔ LHR", "CDG ↔ LHR"]

    page.get_by_role("button", name="2025", exact=True).click()
    assert text(page, "#hero-value") == "1.5 t CO₂e"
    assert page.evaluate("Chart.getChart('routes').data.labels") == ["JFK ↔ LHR"]

    page.get_by_role("button", name="All time").click()
    assert text(page, "#hero-value") == "1.8 t CO₂e"


def test_the_headline_measure_switches(open_passport):
    page = open_passport()

    page.locator("#metric-tabs [data-value=distance]").click()
    pressed(page, "#metric-tabs [data-value=distance]")
    assert re.fullmatch(r"[\d.,]+ g CO₂e / passenger-km", text(page, "#hero-value"))
    expect(page.locator("#hero-context")).to_contain_text("great-circle distance")

    page.locator("#metric-tabs [data-value=duration]").click()
    assert re.fullmatch(r"[\d.,]+ kg CO₂e / block hour", text(page, "#hero-value"))
    expect(page.locator("#hero-context")).to_contain_text("gate-to-gate")

    page.locator("#metric-tabs [data-value=total]").click()
    assert text(page, "#hero-value") == "1.8 t CO₂e"


def test_the_trend_breaks_down_by_cabin_and_reason(open_passport):
    page = open_passport()
    assert datasets(page, "trend-chart") == {"Completed": [1500, 310], "Upcoming": [0, 900]}

    page.locator("#trend-breakdown-tabs [data-value=cabin]").click()
    pressed(page, "#trend-breakdown-tabs [data-value=cabin]")
    by_cabin = datasets(page, "trend-chart")
    assert by_cabin["Business · completed"] == [1500, 0]
    assert by_cabin["Economy · upcoming"] == [0, 900]

    page.locator("#trend-breakdown-tabs [data-value=reason]").click()
    by_reason = datasets(page, "trend-chart")
    assert by_reason["Leisure · completed"] == [0, 60]
    assert by_reason["Unknown · upcoming"] == [0, 900]


def test_patterns_and_rankings_break_down_by_cabin_and_reason(open_passport):
    page = open_passport()

    page.locator("#pattern-breakdown-tabs [data-value=reason]").click()
    assert set(datasets(page, "pattern-chart")) == {"Business", "Unknown", "Leisure"}
    assert total(page, "pattern-chart") == 1810

    page.locator("#pattern-tabs [data-value=weekday]").click()
    pressed(page, "#pattern-tabs [data-value=weekday]")
    assert total(page, "pattern-chart") == 1810

    page.locator("#driver-tabs [data-value=cabin]").click()
    assert datasets(page, "routes") == {"Business": [1500, 0], "Economy": [250, 60]}

    expect(page.locator("#cabins-legend")).to_contain_text("Business")
    expect(page.locator("#cabins-legend")).to_contain_text("82.9%")


def test_the_ranking_dialog_opens_and_closes(open_passport):
    destinations = ["JFK", "CDG", "SIN", "AMS", "DXB", "HND", "SYD", "LAX"]
    rows = [
        row(
            flight_date=f"2026-0{month}-01",
            origin="LHR",
            destination=destination,
            emissions_kg_actual=str(100 * month),
        )
        for month, destination in enumerate(destinations, start=1)
    ]
    page = open_passport(rows)
    dialog = page.locator("#ranking-dialog")
    see_all = page.locator("[data-ranking=routes]")

    see_all.click()
    expect(dialog).to_be_visible()
    expect(page.locator("#ranking-dialog-title")).to_have_text("All routes · total CO₂e")
    rows_shown = page.locator("#ranking-dialog-list > li")
    expect(rows_shown).to_have_count(len(destinations))
    expect(rows_shown.first.locator(".ranking-rank")).to_have_text("1")
    expect(rows_shown.first.locator(".ranking-label")).to_have_text("LAX ↔ LHR")
    expect(rows_shown.first.locator(".ranking-value")).to_have_text("800 kg")
    assert dialog.locator("canvas").count() == 0
    page.locator("#ranking-dialog-close").click()
    expect(dialog).to_be_hidden()

    see_all.click()
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()

    see_all.click()
    page.mouse.click(5, 5)  # the backdrop
    expect(dialog).to_be_hidden()


def test_the_ranking_dialog_lists_every_flight_with_its_breakdown(open_passport):
    """The dialog drew one canvas 34px per entry. A long log passed iOS
    Safari's canvas size limit and showed nothing, so it is an HTML list: a
    row per entry, with a bar segment per category when broken down."""
    rows = [
        row(
            flight_date=f"2025-{1 + index % 12:02d}-{1 + index // 12:02d}",
            origin="LHR",
            destination="JFK",
            flight_number=str(index),
            cabin_class_known="business" if index % 3 == 0 else "economy",
            emissions_kg_actual=str(100 + index),
        )
        for index in range(240)
    ]
    page = open_passport(rows)
    page.locator("#driver-tabs [data-value=cabin]").click()
    page.locator("[data-ranking=flights]").click()

    expect(page.locator("#ranking-dialog-title")).to_have_text(
        "All flights by impact · total CO₂e by cabin"
    )
    rows_shown = page.locator("#ranking-dialog-list > li")
    expect(rows_shown).to_have_count(240)
    expect(rows_shown.first.locator(".ranking-label")).to_have_text("BA239 · LHR–JFK · 2025-12-20")
    expect(rows_shown.first.locator(".ranking-segment")).to_have_count(1)
    assert rows_shown.locator(".ranking-segment").count() == 240
    # The figures are visible text, since a touch screen never shows a title.
    expect(rows_shown.first.locator(".ranking-breakdown")).to_have_text("Economy 339 kg")
    expect(page.locator("#ranking-dialog-list")).to_have_attribute("role", "list")


def test_ranking_labels_fit_one_shared_gutter(open_passport, page):
    """At 390px the ranking axes cut long labels off on the left, and each
    chart sized its own gutter, so neighbouring bars started at different x.
    Every ranking now shares one gutter and cuts a label to fit it, keeping
    the full label for the tooltip."""
    page.set_viewport_size(VIEWPORTS["mobile"])
    long_name = "Boeing 787-10 Dreamliner with a very long name"
    rows = [
        row(
            flight_date="2026-03-04",
            origin="DXB",
            destination="GRU",
            carrier_code="EK",
            flight_number="2107",
            aircraft_type=long_name,
            emissions_kg_actual="2500",
        ),
        *fixture_rows(),
    ]
    page = open_passport(rows)
    rankings = ["routes", "carriers", "aircraft", "flights"]
    gutters = {
        page.evaluate("id => Chart.getChart(id).scales.y.width", canvas) for canvas in rankings
    }
    assert len(gutters) == 1, gutters

    for canvas in [*rankings, "airports", "countries"]:
        overflow = page.evaluate(
            """id => {
              const chart = Chart.getChart(id);
              const scale = chart.scales.y;
              chart.ctx.font = `12px ${Chart.defaults.font.family}`;
              // Labels are right-aligned at their translation; each must
              // start inside the canvas and the scale.
              return scale.getLabelItems()
                .filter(item => item.options.translation[0]
                  - chart.ctx.measureText(item.label).width < scale.left)
                .map(item => item.label);
            }""",
            canvas,
        )
        assert overflow == [], canvas

    aircraft = page.evaluate("Chart.getChart('aircraft').scales.y.ticks.map(tick => tick.label)")
    assert aircraft[0].endswith("…") and long_name.startswith(aircraft[0][:-1])
    assert page.evaluate("Chart.getChart('aircraft').data.labels[0]") == long_name
    assert page.evaluate("Chart.getChart('flights').scales.y.ticks[0].label").startswith("EK2107")


def test_a_short_ranking_has_no_see_all(open_passport):
    page = open_passport()
    expect(page.locator("[data-ranking=routes]")).to_be_hidden()


def test_charts_follow_a_scheme_change_while_open(open_passport, page):
    """Chart colours were fixed hex picked once, so switching the OS between
    light and dark left the charts in the old scheme's ink."""
    page.emulate_media(color_scheme="light")
    page = open_passport()
    tick = "id => Chart.getChart(id).options.scales.x.ticks.color"
    assert page.evaluate(tick, "trend-chart") == "rgb(100, 112, 104)"
    light_grid = page.evaluate("Chart.getChart('trend-chart').options.scales.y.grid.color")

    page.emulate_media(color_scheme="dark")
    page.wait_for_function(
        "Chart.getChart('trend-chart').options.scales.x.ticks.color === 'rgb(165, 178, 170)'"
    )
    assert page.evaluate(tick, "pattern-chart") == "rgb(165, 178, 170)"
    assert page.evaluate("Chart.getChart('routes').options.scales.y.ticks.color") == (
        "rgb(241, 246, 242)"
    )
    assert page.evaluate("Chart.getChart('trend-chart').options.scales.y.grid.color") != light_grid


def test_every_chart_ticks_at_one_size_in_a_system_font(open_passport):
    """The trend's year axis was 11px against 12px elsewhere, and the stack
    began with Inter, which is not bundled, so charts measured their labels
    in whichever font a machine happened to have."""
    page = open_passport()
    sizes = page.evaluate(
        """() => Object.values(Chart.instances).flatMap(chart =>
             Object.values(chart.scales)
               .filter(scale => scale.options.display !== false)
               .map(scale => [chart.canvas.id, scale.id, scale._resolveTickFontOptions(0).size]))"""
    )
    assert sizes and {size for _, _, size in sizes} == {12}, sizes
    font = page.evaluate("getComputedStyle(document.documentElement).fontFamily")
    assert "Inter" not in font and font.startswith("ui-sans-serif")


# -- incomplete data ----------------------------------------------------------


def test_planned_unpriced_and_incomplete_flights_are_counted_honestly(open_passport):
    """The upcoming flight is planned, not completed; the dateless one is
    neither in a year nor departed; the unpriced one is completed but missing
    a figure; the unknown airport is counted but cannot be mapped."""
    page = open_passport()

    assert text(page, "#quality-total") == "4"
    assert text(page, "#missing-count") == "1 · 25%"
    assert text(page, "#duration-available") == "2 · 50%"
    assert text(page, "#duration-missing") == "2 · 50%"
    assert text(page, "#planned-total") == "900 kg CO₂e"
    assert page.locator(".leaflet-airports-pane path").count() == 3  # LHR, JFK, CDG


def test_a_period_without_durations_says_so(open_passport):
    page = open_passport(
        [row(flight_date="2026-06-01", origin="LHR", destination="CDG", emissions_kg_actual="60")]
    )
    page.locator("#metric-tabs [data-value=duration]").click()
    assert text(page, "#hero-value") == "Needs arrival times"
    assert text(page, "#duration-total") == "Needs arrival times"


def test_an_empty_log_renders_without_error(open_passport):
    page = open_passport([])
    assert text(page, "#hero-value") == "0 kg CO₂e"
    expect(page.locator(".map-caption span")).to_have_text("No priced routes in this period")


# -- layout -------------------------------------------------------------------


@pytest.mark.parametrize("viewport", VIEWPORTS.values(), ids=VIEWPORTS.keys())
def test_core_interactions_fit_the_viewport(open_passport, page, viewport):
    page.set_viewport_size(viewport)
    page = open_passport()
    overflow = sideways_scroll(page)
    assert overflow <= 0, f"page scrolls sideways by {overflow}px on load"

    page.get_by_role("button", name="2026", exact=True).click()
    page.locator("#metric-tabs [data-value=distance]").click()
    page.locator("#trend-breakdown-tabs [data-value=cabin]").click()
    assert text(page, "#flight-count") == "3"

    overflow = sideways_scroll(page)
    assert overflow <= 0, f"page scrolls sideways by {overflow}px"


@pytest.mark.parametrize("viewport", VIEWPORTS.values(), ids=VIEWPORTS.keys())
def test_period_charts_fit_their_card(open_passport, page, viewport):
    """The trend and pattern charts had a minimum width, so on a phone they
    scrolled inside the card from the oldest year. They fit the card now,
    with level ticks, and months as initials when the chart is narrow."""
    page.set_viewport_size(viewport)
    page = open_passport()
    for canvas in ["trend-chart", "pattern-chart"]:
        chart = page.evaluate(
            """id => {
              const chart = Chart.getChart(id);
              const card = chart.canvas.closest('section');
              const style = getComputedStyle(card);
              const inner = card.clientWidth - parseFloat(style.paddingLeft)
                - parseFloat(style.paddingRight);
              return { width: chart.width, inner, rotation: chart.scales.x.labelRotation,
                       ticks: chart.scales.x.ticks.map(tick => tick.label) };
            }""",
            canvas,
        )
        assert chart["width"] <= chart["inner"] + 0.5, canvas
        assert chart["rotation"] == 0, canvas
    months = page.evaluate("Chart.getChart('pattern-chart').scales.x.ticks.map(t => t.label)")
    narrow = page.evaluate("Chart.getChart('pattern-chart').width") < 420
    assert months[:3] == (["J", "F", "M"] if narrow else ["Jan", "Feb", "Mar"])


@pytest.mark.parametrize("viewport", VIEWPORTS.values(), ids=VIEWPORTS.keys())
def test_segmented_controls_stay_one_row(open_passport, page, viewport):
    """Below 620px a wrapped pill turned into a lopsided blob. Each control
    is one row no wider than its section, and is a labelled group."""
    page.set_viewport_size(viewport)
    page = open_passport()
    controls = page.locator(".metric-tabs, .pattern-tabs")
    for index in range(controls.count()):
        control = controls.nth(index)
        name = control.get_attribute("id")
        box = control.bounding_box()
        button = control.locator("button").first.bounding_box()
        section = control.evaluate("el => el.closest('section').getBoundingClientRect().right")
        assert box["height"] < button["height"] * 1.5, name
        assert box["x"] + box["width"] <= section, name
        # It may scroll inside itself rather than wrap, but every control
        # Passport ships fits down to 320px without that.
        assert control.evaluate("el => el.scrollWidth <= el.clientWidth"), name
    expect(page.get_by_role("group", name="Headline measure")).to_be_visible()
    expect(page.get_by_role("group", name="Carbon impact grouping")).to_be_visible()


# -- regressions from shipped fixes -------------------------------------------


def test_the_heaviest_airport_is_drawn_on_top(open_passport):
    """#67: Leaflet stacks SVG markers in draw order, so they must be drawn
    lightest first. Lightness falls as CO2e rises, so it may never increase
    down the document, and the last marker drawn is the heaviest airport."""
    page = open_passport()
    markers = page.locator(".leaflet-airports-pane path")
    fills = markers.evaluate_all("paths => paths.map(path => path.getAttribute('fill'))")
    lightness = [float(re.search(r"([\d.]+)%\)$", fill).group(1)) for fill in fills]
    assert lightness == sorted(lightness, reverse=True)

    markers.last.hover(force=True)
    expect(page.locator(".leaflet-tooltip")).to_contain_text("LHR")


def test_the_latest_year_tile_skips_a_year_of_bookings_only(open_passport):
    """With no year chosen, a newest year that holds only upcoming flights
    made the tile read zero. It reads the newest year anything was flown in,
    and a chosen year still reads that year."""
    rows = [
        row(flight_date="2025-05-10", origin="LHR", destination="JFK", emissions_kg_actual="1500"),
        row(
            flight_date=f"{NOW.year + 1}-02-01",
            origin="LHR",
            destination="SIN",
            emissions_kg_actual="400",
        ),
    ]
    page = open_passport(rows)
    assert text(page, "#trend-note-label") == "Latest year"
    assert text(page, "#trend-note-value") == "1.5 t CO₂e"

    page.get_by_role("button", name=str(NOW.year + 1), exact=True).click()
    assert text(page, "#trend-note-label") == str(NOW.year + 1)
    assert text(page, "#trend-note-value") == "0 kg CO₂e"
    expect(page.locator("#trend-note-copy")).to_contain_text("400 kg")


def test_a_departure_moves_the_flight_from_planned_to_completed(open_passport):
    """#72: a sync either side of a departure rewrites the file. The page
    rendered after it must show the flight as flown, not only differ."""
    departure = NOW.replace(month=9, day=1, hour=20)
    before = open_passport(fixture_rows(), departure - timedelta(hours=1), "before.html")
    assert text(before, "#planned-total") == "900 kg CO₂e"
    assert text(before, "#flight-count") == "4"
    assert text(before, "#airport-count") == "4"

    after = open_passport(fixture_rows(), departure + timedelta(hours=1), "after.html")
    assert text(after, "#planned-total") == "0 kg CO₂e"
    assert text(after, "#flight-count") == "5"
    assert text(after, "#hero-value") == "2.7 t CO₂e"
    assert text(after, "#airport-count") == "5"  # SIN is now flown to
