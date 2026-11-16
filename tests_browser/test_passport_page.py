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

from contrail.passport import render

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


def test_a_key_appears_only_when_a_chart_is_broken_down(open_passport):
    """Broken down, the stacks were colours with no key on the page; the
    only names were in a tooltip. Each breakdown now has an HTML key in the
    chart's stacking order, and Total, one colour, has none."""
    page = open_passport()
    keys = {
        "#trend-legend": "#trend-breakdown-tabs",
        "#pattern-legend": "#pattern-breakdown-tabs",
        "#driver-legend": "#driver-tabs",
    }
    for key in keys:
        expect(page.locator(key)).to_be_hidden()

    for key, tabs in keys.items():
        page.locator(f"{tabs} [data-value=cabin]").click()
        expect(page.locator(key)).to_be_visible()
    trend = page.locator("#trend-legend li").all_inner_texts()
    assert [entry.split()[-1] for entry in trend] == ["Business", "Economy", "Upcoming"]
    assert page.locator("#trend-legend li .category-icon").count() == 2
    assert [entry.split()[-1] for entry in page.locator("#driver-legend li").all_inner_texts()] == [
        "Business",
        "Economy",
    ]

    page.locator("#pattern-breakdown-tabs [data-value=reason]").click()
    pattern = [entry.split()[-1] for entry in page.locator("#pattern-legend li").all_inner_texts()]
    assert pattern == list(datasets(page, "pattern-chart"))
    swatch = page.locator("#pattern-legend .legend-swatch").first
    assert swatch.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(220, 128, 68)"

    for key, tabs in keys.items():
        page.locator(f"{tabs} [data-value=total]").click()
        expect(page.locator(key)).to_be_hidden()


def hover(page: Page, canvas: str, index: int) -> None:
    """Move the mouse onto the middle of bar `index` of the chart `canvas`."""
    x, y = page.evaluate(
        """([id, index]) => {
          const chart = Chart.getChart(id);
          const box = chart.canvas.getBoundingClientRect();
          const bar = chart.getDatasetMeta(0).data[index];
          const across = chart.options.indexAxis === 'y';
          return [box.left + (across ? chart.chartArea.left + 4 : bar.x),
                  box.top + (across ? bar.y : (chart.chartArea.top + chart.chartArea.bottom) / 2)];
        }""",
        [canvas, index],
    )
    page.mouse.move(x, y)


def tooltip_lines(page: Page) -> list[str]:
    tip = page.locator(".chart-tooltip")
    expect(tip).to_be_visible()
    return [
        " ".join(row.split())
        for row in tip.locator(".tooltip-title, .tooltip-row, .tooltip-total").all_inner_texts()
    ]


def test_chart_tooltips_are_one_html_element_without_zero_rows(open_passport, page):
    """Chart.js drew its own black box, unlike the map's, and in Cabin mode
    the trend listed every category twice, half of them "upcoming: 0 kg",
    each in its own unit. One HTML tooltip now serves every chart, shows
    only what is there in one unit, and totals a stack."""
    page.set_viewport_size(VIEWPORTS["desktop"])
    page = open_passport()
    assert page.evaluate(
        "Object.values(Chart.instances).every(c => c.options.plugins.tooltip.enabled === false)"
    )
    page.locator("#trend-breakdown-tabs [data-value=cabin]").click()
    page.locator("#trend-chart").scroll_into_view_if_needed()
    hover(page, "trend-chart", 1)  # 2026: 310 kg flown, 900 kg booked, all economy
    assert tooltip_lines(page) == [
        "2026",
        "Economy · upcoming 0.9 t",
        "Economy · completed 0.3 t",
        "Total 1.2 t",
    ]
    assert page.locator(".chart-tooltip").count() == 1

    # It stays on screen, and goes when the page scrolls.
    box = page.locator(".chart-tooltip").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= VIEWPORTS["desktop"]["width"]
    page.mouse.wheel(0, 200)
    expect(page.locator(".chart-tooltip")).to_be_hidden()


def test_a_ranking_tooltip_gives_the_entry_in_full(open_passport, page):
    page.set_viewport_size(VIEWPORTS["desktop"])
    page = open_passport()
    page.locator("#driver-tabs [data-value=cabin]").click()
    page.locator("#routes").scroll_into_view_if_needed()
    hover(page, "routes", 0)  # JFK-LHR: 1.5 t business, 250 kg economy
    assert tooltip_lines(page)[:6] == [
        "JFK ↔ LHR",
        "Business 1.5 t",
        "Economy 0.3 t",
        "Total 1.8 t",
        "Flights 2",
        "Share of CO₂e 96.7%",
    ]
    details = page.locator(".chart-tooltip .tooltip-details").inner_text()
    assert "g/km" in details and "kg/hr" in details
    # The tooltip is hidden from assistive technology, so the same details
    # are in the canvas's accessible name.
    label = page.locator("#routes").get_attribute("aria-label")
    assert "Flights 2, Share of CO₂e 96.7%" in label

    # The chart tooltip and the map's are one style.
    style = """el => {
      const s = getComputedStyle(el);
      return [s.backgroundColor, s.borderRadius, s.fontSize];
    }"""
    chart_style = page.locator(".chart-tooltip").evaluate(style)
    page.locator(".leaflet-airports-pane path").last.hover(force=True)
    assert page.locator(".leaflet-tooltip").evaluate(style) == chart_style


def test_a_tooltip_stays_above_the_bottom_of_the_viewport(open_passport, page):
    """With a chart only partly on screen, index interaction could pick a
    point whose caret sat below the viewport, and the tooltip followed it
    off the bottom edge."""
    page.set_viewport_size({"width": 1280, "height": 500})
    page = open_passport()
    page.evaluate(
        """() => {
          const box = document.getElementById('pattern-chart').getBoundingClientRect();
          window.scrollBy(0, box.top - window.innerHeight + 60);
        }"""
    )
    # A scroll hides the tooltip, and its event lands a frame later.
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    page.evaluate(
        """() => {
          const chart = Chart.getChart('pattern-chart');
          const index = chart.data.datasets[0].data.findIndex((value) => value > 0);
          chart.tooltip.setActiveElements([{ datasetIndex: 0, index }],
            { x: chart.getDatasetMeta(0).data[index].x, y: chart.chartArea.bottom });
          chart.update('none');
        }"""
    )
    tip = page.locator(".chart-tooltip")
    expect(tip).to_be_visible()
    box = tip.bounding_box()
    assert box["y"] + box["height"] <= 500 - 8


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
    # What a hover gives on the chart, a row gives as text.
    expect(rows_shown.first.locator(".ranking-details")).to_contain_text("Flight 1, Share of CO₂e")
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
    expect(rows_shown.first.locator(".ranking-label")).to_have_text("BA239 · LHR–JFK · 20 Dec 2025")
    expect(rows_shown.first.locator(".ranking-segment")).to_have_count(1)
    assert rows_shown.locator(".ranking-segment").count() == 240
    # The figures are visible text, since a touch screen never shows a title.
    expect(rows_shown.first.locator(".ranking-breakdown")).to_have_text("Economy 339 kg")
    expect(page.locator("#ranking-dialog-list")).to_have_attribute("role", "list")


def test_flight_dates_read_in_the_locale(open_passport):
    """Cards and flight labels showed ISO dates while the footer used the
    reader's locale. They all use the locale now."""
    page = open_passport()
    expect(page.locator("#heaviest-total p")).to_have_text("1.5 t CO₂e · BA1 · 10 May 2025")
    assert page.evaluate("Chart.getChart('flights').data.labels[0]") == (
        "BA1 · LHR–JFK · 10 May 2025"
    )


@pytest.mark.parametrize("timezone", ["America/Los_Angeles", "Pacific/Kiritimati"])
def test_a_flight_date_never_moves_a_day(browser, tmp_path, timezone):
    """A calendar day read as local midnight shows as the day before west of
    UTC. It is read and written as UTC, so it reads the same everywhere."""
    output = render(fixture_rows(), tmp_path / "passport.html", NOW)
    context = browser.new_context(locale="en-GB", timezone_id=timezone)
    try:
        page = context.new_page()
        page.goto(output.as_uri())
        expect(page.locator("#heaviest-total p")).to_contain_text("10 May 2025")
    finally:
        context.close()


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


def test_unknown_ranks_last_and_grey_whatever_its_size(open_passport):
    """A heavy "Unknown" aircraft topped the ranking in the same teal as a
    real aircraft. It is no one aircraft, so it sorts after every known
    entry, in the chart and in See all, and its bar is grey."""
    known = [f"Airbus A3{20 + index}" for index in range(7)]
    rows = [
        row(
            flight_date=f"2026-0{index + 1}-01",
            origin="LHR",
            destination="JFK",
            flight_number=str(index),
            aircraft_type=name,
            emissions_kg_actual=str(100 + index),
        )
        for index, name in enumerate([*known, ""])
    ]
    rows[-1]["emissions_kg_actual"] = "5000"
    page = open_passport(rows)

    labels = page.evaluate("Chart.getChart('aircraft').data.labels")
    assert labels[0] == "Airbus A326"
    assert "Unknown" not in labels  # the top six are all known
    colours = page.evaluate("Chart.getChart('aircraft').data.datasets[0].backgroundColor")
    assert len(set(colours)) == 1

    page.locator("[data-ranking=aircraft]").click()
    shown = page.locator("#ranking-dialog-list .ranking-label").all_inner_texts()
    assert shown[-1] == "Unknown" and len(shown) == 8
    fills = page.locator("#ranking-dialog-list .ranking-segment").evaluate_all(
        "segments => segments.map(segment => getComputedStyle(segment).backgroundColor)"
    )
    assert fills[-1] == "rgb(118, 130, 123)" and fills[0] != fills[-1]

    page = open_passport(rows[-2:], name="two.html")
    assert page.evaluate("Chart.getChart('aircraft').data.labels") == ["Airbus A326", "Unknown"]
    colours = page.evaluate("Chart.getChart('aircraft').data.datasets[0].backgroundColor")
    assert colours[1] == "#76827b" and colours[0] != colours[1]


def test_unknown_reason_is_the_last_category(open_passport):
    page = open_passport()
    reasons = page.locator("#reasons-legend .composition-label").all_inner_texts()
    assert reasons[-1].endswith("Unknown"), reasons
    page.locator("#pattern-breakdown-tabs [data-value=reason]").click()
    assert list(datasets(page, "pattern-chart"))[-1] == "Unknown"


def test_country_charts_name_countries_without_flags(open_passport):
    """Windows draws a flag emoji as two letters, so the canvas showed
    "GB United Kingdom". The chart names the country; the See all list,
    where the name is also shown, keeps the flag."""
    rows = [
        row(
            flight_date=f"2026-0{index + 1}-01",
            origin="LHR",
            destination=destination,
            flight_number=str(index),
            emissions_kg_actual=str(100 * (index + 1)),
        )
        for index, destination in enumerate(["JFK", "CDG", "AMS", "FRA", "MAD", "FCO", "DXB"])
    ]
    page = open_passport(rows)
    labels = page.evaluate("Chart.getChart('countries').data.labels")
    assert labels[:3] == ["United Kingdom", "United Arab Emirates", "Italy"]
    assert all(label.isascii() for label in labels), labels

    page.locator("[data-ranking=countries]").click()
    first = page.locator("#ranking-dialog-list .ranking-label").first
    expect(first).to_have_text("🇬🇧 United Kingdom")
    assert first.locator(".ranking-flag").get_attribute("aria-hidden") == "true"


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


def test_flight_tags_have_a_dark_scheme_of_their_own(open_passport, page):
    """In dark mode the tags kept their light pastel fills and white ring,
    bright stickers on a dark card. Each now has a dark variant."""
    # Each colour's relative luminance, 0 to 255. Chrome reports a plain
    # colour as rgb() and a color-mix() as color(srgb r g b) with 0 to 1
    # channels; both are parsed here.
    styles = """els => {
      const luminance = colour => {
        const srgb = colour.match(/^color\\(srgb ([\\d.]+) ([\\d.]+) ([\\d.]+)/);
        const [r, g, b] = srgb
          ? srgb.slice(1, 4).map(channel => Number(channel) * 255)
          : colour.match(/[\\d.]+/g).slice(0, 3).map(Number);
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
      };
      return els.map(el => {
        const style = getComputedStyle(el);
        return [el.className, luminance(style.backgroundColor), luminance(style.color),
                style.boxShadow];
      });
    }"""

    page.emulate_media(color_scheme="light")
    page = open_passport()
    light = page.locator(".flight-tag").evaluate_all(styles)
    page.emulate_media(color_scheme="dark")
    # The scheme change redraws the page, cards and their tags included, in
    # a task of its own. Read the tags only once that redraw has replaced
    # them, or a detached tag reports no colour at all.
    page.wait_for_function("() => matchMedia('(prefers-color-scheme: dark)').matches")
    page.evaluate("() => new Promise(r => setTimeout(() => requestAnimationFrame(r)))")
    dark = page.locator(".flight-tag").evaluate_all(styles)

    assert {name for name, *_ in light} >= {
        "flight-tag cabin-business",
        "flight-tag cabin-economy",
        "flight-tag reason-business",
    }
    for (name, light_fill, light_ink, light_ring), (_, fill, ink, ring) in zip(
        light, dark, strict=True
    ):
        assert ring != light_ring, name
        # Dark ink on a light fill becomes light ink on a dark one.
        assert light_fill > 180 > light_ink, (name, light_fill, light_ink)
        assert fill < 90 < ink, (name, fill, ink)


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


TAP_TARGETS = ".see-all, .metric-tabs button, .pattern-tabs button, .leaflet-control-zoom a"


def tap_target_sizes(page: Page) -> list[tuple[str, float, float]]:
    # See all buttons hide on a short ranking; measure them shown.
    page.evaluate("document.querySelectorAll('.see-all').forEach(b => { b.hidden = false; })")
    return page.locator(TAP_TARGETS).evaluate_all(
        """els => els.map(el => {
          const box = el.getBoundingClientRect();
          return [el.className || el.textContent.trim(), box.width, box.height];
        })"""
    )


def test_touch_screens_get_44px_tap_targets(browser, tmp_path):
    """See all was 63x28, segmented buttons 36px tall and the map's zoom
    buttons 30x30: under a fingertip on a phone. Where the pointer is coarse
    each is at least 44px tall, and the zoom buttons 44px square."""
    output = render(fixture_rows(), tmp_path / "passport.html", NOW)
    context = browser.new_context(
        viewport=VIEWPORTS["mobile"], has_touch=True, is_mobile=True, locale="en-GB"
    )
    try:
        page = context.new_page()
        page.goto(output.as_uri())
        page.locator("#hero-value").filter(has_text="CO").wait_for()
        sizes = tap_target_sizes(page)
        # Six See all, the headline measure, five segmented controls, zoom.
        assert len(sizes) == 6 + 3 + 14 + 2, sizes
        assert [size for size in sizes if size[2] < 44] == []
        zoom = [size for size in sizes if "leaflet-control-zoom" in size[0]]
        assert len(zoom) == 2 and all(width >= 44 for _, width, _ in zoom)
    finally:
        context.close()


def test_a_mouse_keeps_the_compact_controls(open_passport, page):
    page.set_viewport_size(VIEWPORTS["desktop"])
    page = open_passport()
    heights = {name: height for name, _, height in tap_target_sizes(page)}
    assert max(heights.values()) < 44, heights


@pytest.mark.parametrize(
    ("viewport", "columns"),
    [(VIEWPORTS["desktop"], 3), ({"width": 768, "height": 1024}, 3), (VIEWPORTS["mobile"], 2)],
    ids=["desktop", "tablet", "mobile"],
)
def test_the_summary_is_an_even_grid(open_passport, page, viewport, columns):
    """The six stats wrapped five and one, leaving Countries alone on a row."""
    page.set_viewport_size(viewport)
    page = open_passport()
    boxes = page.locator(".summary-item").evaluate_all(
        "els => els.map(el => [Math.round(el.getBoundingClientRect().left),"
        " Math.round(el.getBoundingClientRect().top)])"
    )
    assert len({left for left, _ in boxes}) == columns
    assert len({top for _, top in boxes}) == 6 // columns


def test_the_trend_note_sits_level_with_the_chart(open_passport, page):
    """The note was pinned to the bottom of its column, with empty space
    above it beside the chart."""
    page.set_viewport_size(VIEWPORTS["desktop"])
    page = open_passport()
    note = page.locator(".trend-note").bounding_box()
    chart = page.locator(".trend-chart").bounding_box()
    assert abs(note["y"] - chart["y"]) < 1


# -- drill-down ---------------------------------------------------------------


def panel_says(page: Page, title: str, count: str) -> None:
    expect(page.locator("#flight-panel")).to_be_visible()
    expect(page.locator("#flight-panel-title")).to_have_text(title)
    expect(page.locator("#flight-panel-count")).to_have_text(count)


def close_panel(page: Page) -> None:
    page.keyboard.press("Escape")
    expect(page.locator("#flight-panel")).to_be_hidden()


def click_bar(page: Page, canvas: str, index: int) -> None:
    """Click the middle of bar `index`, or its row's label on a ranking."""
    page.locator(f"#{canvas}").scroll_into_view_if_needed()
    x, y = page.evaluate(
        """([id, index]) => {
          const chart = Chart.getChart(id);
          const box = chart.canvas.getBoundingClientRect();
          const bar = chart.getDatasetMeta(0).data[index];
          const across = chart.options.indexAxis === 'y';
          return [box.left + (across ? chart.chartArea.left + 4 : bar.x),
                  box.top + (across ? bar.y : chart.chartArea.bottom - 2)];
        }""",
        [canvas, index],
    )
    page.mouse.click(x, y)


def test_a_chart_bar_opens_the_flights_behind_it(open_passport, page):
    """Nothing on the page could be clicked for detail. Every chart now
    opens the flight panel for what was clicked: a ranking row, a month and
    a cabin's share of the composition bar."""
    page.set_viewport_size(VIEWPORTS["desktop"])
    page = open_passport()

    click_bar(page, "routes", 0)
    panel_says(page, "JFK ↔ LHR", "2 flights")
    expect(page.locator("#flight-panel-kind")).to_have_text("Route")
    expect(page.locator("#flight-panel-total")).to_have_text("1.8 t CO₂e")
    close_panel(page)

    click_bar(page, "pattern-chart", 4)  # May
    panel_says(page, "May", "1 flight")
    close_panel(page)

    page.get_by_role("button", name="2026", exact=True).click()
    click_bar(page, "carriers", 0)
    panel_says(page, "British Airways (BA)", "3 flights")
    expect(page.locator("#flight-panel-kind")).to_have_text("Operating airline · 2026")
    # An unpriced flight is listed, and the heading says it has no figure.
    expect(page.locator("#flight-panel-note")).to_have_text("1 without an estimate")


def test_a_see_all_row_opens_its_flights_and_escape_returns_to_it(open_passport):
    """The dialog's rows are buttons, the keyboard's way into a ranking that
    is otherwise drawn on a canvas. The panel opens over the dialog, and
    Escape closes only the panel and puts focus back on the row."""
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
    page.locator("[data-ranking=routes]").click()
    first = page.locator("#ranking-dialog-list > li button").first
    first.focus()
    page.keyboard.press("Enter")
    panel_says(page, "LAX ↔ LHR", "1 flight")

    close_panel(page)
    expect(page.locator("#ranking-dialog")).to_be_visible()
    expect(first).to_be_focused()


def test_a_key_entry_and_a_composition_row_open_their_category(open_passport):
    page = open_passport()
    page.locator("#trend-breakdown-tabs [data-value=cabin]").click()
    page.locator("#trend-legend button", has_text="Business").click()
    panel_says(page, "Business", "1 flight")
    expect(page.locator("#flight-panel-kind")).to_have_text("Cabin")
    close_panel(page)

    page.locator("#trend-legend button", has_text="Upcoming").click()
    panel_says(page, "Upcoming flights", "1 flight")
    close_panel(page)

    page.locator("#reasons-legend button", has_text="Leisure").click()
    panel_says(page, "Leisure", "1 flight")


def test_a_card_opens_its_flight_with_the_detail_shown(open_passport):
    page = open_passport()
    page.locator("#heaviest-total").click()
    panel_says(page, "BA1 · LHR → JFK · 10 May 2025", "1 flight")
    expect(page.locator(".flight-detail")).to_contain_text("Business")
    # A list of one needs no search.
    expect(page.locator("#flight-panel-tools")).to_be_hidden()


def test_a_quality_count_opens_its_bucket(open_passport):
    page = open_passport()
    expect(page.locator("#missing-count")).to_have_text("1 · 25%")
    page.locator("#missing-count").click()
    panel_says(page, "No emissions estimate", "1 flight")
    expect(page.locator("#flight-panel-total")).to_have_text("0 kg CO₂e")
    expect(page.locator("#flight-panel-rows .cell-route")).to_have_text("▸ CDG → XXX".lstrip("▸ "))
    close_panel(page)

    page.locator("#cabin-assumed").click()
    panel_says(page, "Cabin assumed economy", "3 flights")


def test_the_hero_stats_open_completed_and_upcoming_flights(open_passport):
    page = open_passport()
    page.locator("#flight-count").click()
    panel_says(page, "All flights", f"{text(page, '#flight-count')} flights")
    close_panel(page)
    expect(page.locator("button:has(#flight-count)")).to_be_focused()

    page.locator("#planned-total").click()
    panel_says(page, "Upcoming flights", "1 flight")
    expect(page.locator("#flight-panel-total")).to_have_text("900 kg CO₂e")


def test_a_map_airport_opens_its_flights(open_passport):
    page = open_passport()
    page.locator(".leaflet-airports-pane path").last.click(force=True)  # LHR, the heaviest
    panel_says(page, "LHR", "3 flights")
    expect(page.locator("#flight-panel-kind")).to_have_text("Airport")
    close_panel(page)

    # A route's middle can sit under an airport, so the click goes to the line.
    page.locator(".leaflet-routes-pane path").first.dispatch_event("click")
    expect(page.locator("#flight-panel-kind")).to_have_text("Route")


def test_the_flight_list_sorts_and_searches(open_passport):
    destinations = ["JFK", "CDG", "SIN", "AMS", "DXB", "HND", "SYD", "LAX"]
    rows = [
        row(
            flight_date=f"2026-0{month}-01",
            origin="LHR",
            destination=destination,
            operating_carrier_code="QF" if destination == "SYD" else "",
            emissions_kg_actual=str(100 * ((month * 3) % 8 + 1)),
        )
        for month, destination in enumerate(destinations, start=1)
    ]
    page = open_passport(rows)
    page.locator("#flight-count").click()
    kg = page.locator("#flight-panel-rows .cell-kg")
    header = page.locator("#flight-panel-head th[data-sort=kg]")
    date_header = page.locator("#flight-panel-head th[data-sort=date]")

    # Newest first by default.
    expect(date_header).to_have_attribute("aria-sort", "descending")
    expect(page.locator("#flight-panel-rows .cell-date").first).to_have_text("1 Aug 2026")

    header.locator("button").click()
    expect(header).to_have_attribute("aria-sort", "descending")
    expect(date_header).not_to_have_attribute("aria-sort", re.compile("."))
    assert kg.all_inner_texts()[0] == "800.0"
    header.locator("button").click()
    expect(header).to_have_attribute("aria-sort", "ascending")
    assert kg.all_inner_texts()[0] == "100.0"

    page.locator("#flight-panel-search").fill("qf")  # the operating airline's code
    expect(page.locator("#flight-panel-rows tr")).to_have_count(1)
    expect(page.locator("#flight-panel-rows .cell-route")).to_have_text("LHR → SYD")
    expect(page.locator("#flight-panel-status")).to_have_text("Showing 1 of 8")
    page.locator("#flight-panel-search").fill("lhr hnd")
    expect(page.locator("#flight-panel-rows tr")).to_have_count(1)
    page.locator("#flight-panel-search").fill("zzz")
    expect(page.locator("#flight-panel-empty")).to_have_text("No flights match that search.")


def test_flight_detail_shows_every_cabin_and_the_contrail_impact(open_passport):
    page = open_passport(
        [
            row(
                flight_date="2026-03-04",
                origin="JFK",
                destination="LHR",
                flight_number="112",
                departure_time="2026-03-04T20:30:00-05:00",
                arrival_time="2026-03-05T08:15:00+00:00",
                cabin_class_known="business",
                emissions_kg_actual="716.4",
                emissions_kg_economy="247",
                emissions_kg_business="716.4",
                emissions_kg_first="1004.5",
                contrails_impact="moderate",
                model_version="3.0.0+20260814",
            ),
            row(
                flight_date="2026-04-01", origin="LHR", destination="CDG", emissions_kg_actual="60"
            ),
        ]
    )
    page.locator("#flight-count").click()
    page.locator("#flight-panel").get_by_role("button", name="JFK → LHR").click()
    detail = page.locator(".flight-detail")
    expect(detail).to_be_visible()
    expect(page.locator("#flight-panel").get_by_role("button", name="JFK → LHR")).to_have_attribute(
        "aria-expanded", "true"
    )
    figures = detail.locator(".cabin-figures li").all_inner_texts()
    assert figures == [
        "In economy this flight would have been 247 kg CO₂e.",
        "In first this flight would have been 1,004.5 kg CO₂e.",
    ]
    facts = dict(
        zip(
            detail.locator("dt").all_inner_texts(),
            detail.locator("dd").all_inner_texts(),
            strict=True,
        )
    )
    assert facts["Contrail impact"] == "Moderate"
    assert facts["Model version"] == "TIM 3.0.0+20260814"
    assert facts["Estimate"] == "Exact TIM estimate"
    assert facts["Scheduled departure"] == "20:30 · 4 Mar 2026 (UTC−05:00)"
    assert facts["Scheduled arrival"] == "08:15 · 5 Mar 2026 (UTC+00:00)"
    assert facts["Block time"] == "6 h 45 min"

    page.locator("#flight-panel").get_by_role("button", name="JFK → LHR").click()
    expect(detail).to_have_count(0)


def test_the_panel_fits_a_320px_phone(open_passport, page):
    page.set_viewport_size(VIEWPORTS["narrow"])
    page = open_passport()
    page.locator("#flight-count").click()
    expect(page.locator("#flight-panel")).to_be_visible()
    page.locator(".flight-toggle").first.click()
    expect(page.locator(".flight-detail")).to_be_visible()
    assert sideways_scroll(page) <= 0
    scroll = page.locator(".panel-scroll")
    assert scroll.evaluate("el => el.scrollWidth <= el.clientWidth")
    box = page.locator("#flight-panel").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= VIEWPORTS["narrow"]["width"]
    # Stacked rows, not a table with headings.
    expect(page.locator("#flight-panel-head")).to_be_hidden()
    expect(page.locator("#flight-panel-sort")).to_be_visible()


def test_the_panel_is_a_side_sheet_on_a_desktop(open_passport, page):
    page.set_viewport_size(VIEWPORTS["desktop"])
    page = open_passport()
    page.locator("#flight-count").click()
    box = page.locator("#flight-panel").bounding_box()
    assert box["x"] + box["width"] == pytest.approx(VIEWPORTS["desktop"]["width"], abs=1)
    assert box["height"] == pytest.approx(VIEWPORTS["desktop"]["height"], abs=1)
    expect(page.locator("#flight-panel-sort")).to_be_hidden()
    page.mouse.click(10, 400)  # the backdrop
    expect(page.locator("#flight-panel")).to_be_hidden()


def test_a_long_log_opens_quickly(open_passport):
    """1000 flights as plain DOM rows, drawn in well under 200 ms."""
    rows = [
        row(
            flight_date=f"{2000 + index // 50}-{1 + index % 12:02d}-{1 + index % 28:02d}",
            origin="LHR",
            destination=["JFK", "CDG", "SIN", "SYD"][index % 4],
            flight_number=str(index),
            source_id=str(index),
            emissions_kg_actual=str(100 + index),
        )
        for index in range(1000)
    ]
    page = open_passport(rows)
    elapsed = page.evaluate(
        """() => {
          const start = performance.now();
          document.getElementById('flight-count').click();
          document.body.offsetHeight;  // include layout
          return performance.now() - start;
        }"""
    )
    expect(page.locator("#flight-panel-rows tr")).to_have_count(1000)
    assert elapsed < 200, elapsed


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
