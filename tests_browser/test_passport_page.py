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

    page.locator("#metric-tabs [data-metric=distance]").click()
    pressed(page, "#metric-tabs [data-metric=distance]")
    assert re.fullmatch(r"[\d.,]+ g CO₂e / passenger-km", text(page, "#hero-value"))
    expect(page.locator("#hero-context")).to_contain_text("great-circle distance")

    page.locator("#metric-tabs [data-metric=duration]").click()
    assert re.fullmatch(r"[\d.,]+ kg CO₂e / block hour", text(page, "#hero-value"))
    expect(page.locator("#hero-context")).to_contain_text("gate-to-gate")

    page.locator("#metric-tabs [data-metric=total]").click()
    assert text(page, "#hero-value") == "1.8 t CO₂e"


def test_the_trend_breaks_down_by_cabin_and_reason(open_passport):
    page = open_passport()
    assert datasets(page, "trend-chart") == {"Completed": [1500, 310], "Upcoming": [0, 900]}

    page.locator("[data-trend-breakdown=cabin]").click()
    pressed(page, "[data-trend-breakdown=cabin]")
    by_cabin = datasets(page, "trend-chart")
    assert by_cabin["Business · completed"] == [1500, 0]
    assert by_cabin["Economy · upcoming"] == [0, 900]

    page.locator("[data-trend-breakdown=reason]").click()
    by_reason = datasets(page, "trend-chart")
    assert by_reason["Leisure · completed"] == [0, 60]
    assert by_reason["Unknown · upcoming"] == [0, 900]


def test_patterns_and_rankings_break_down_by_cabin_and_reason(open_passport):
    page = open_passport()

    page.locator("[data-pattern-breakdown=reason]").click()
    assert set(datasets(page, "pattern-chart")) == {"Business", "Unknown", "Leisure"}
    assert total(page, "pattern-chart") == 1810

    page.locator("#pattern-tabs [data-pattern=weekday]").click()
    pressed(page, "#pattern-tabs [data-pattern=weekday]")
    assert total(page, "pattern-chart") == 1810

    page.locator("#driver-tabs [data-breakdown=cabin]").click()
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
    labels = page.evaluate("Chart.getChart('ranking-dialog-canvas').data.labels")
    assert len(labels) == len(destinations)
    page.locator("#ranking-dialog-close").click()
    expect(dialog).to_be_hidden()

    see_all.click()
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()


def test_a_short_ranking_has_no_see_all(open_passport):
    page = open_passport()
    expect(page.locator("[data-ranking=routes]")).to_be_hidden()


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
    page.locator("#metric-tabs [data-metric=duration]").click()
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

    page.get_by_role("button", name="2026", exact=True).click()
    page.locator("#metric-tabs [data-metric=distance]").click()
    page.locator("[data-trend-breakdown=cabin]").click()
    assert text(page, "#flight-count") == "3"

    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"page scrolls sideways by {overflow}px"


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
