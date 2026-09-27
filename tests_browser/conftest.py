"""Open a freshly rendered Passport in a real browser.

This suite sits outside `testpaths` on purpose: plain `pytest` never collects
it, so the Python matrix and the coverage job need no browser. Run it with
`pytest tests_browser` once the `browser` extra and Chromium are installed.

Every test opens the generated file, not the template, so what is exercised is
exactly what `contrail passport` writes. The page must make no request beyond
the file itself: anything over the network is aborted and fails the test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from contrail.passport import render
from contrail.storage.local_csv import CSV_FIELDS

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)

# Schemes a self-contained page may load from. Leaflet and Chart.js build
# canvases and inline images from data: and blob: URLs.
LOCAL_SCHEMES = ("file:", "data:", "blob:", "about:")


def row(**extra) -> dict:
    data = {field: "" for field in CSV_FIELDS}
    data.update(
        source="tripit_ical",
        carrier_code="BA",
        flight_number="1",
        emissions_source="exact",
    )
    data.update(extra)
    data.setdefault("source_id", f"{data['flight_date']}-{data['origin']}")
    return data


def fixture_rows() -> list[dict]:
    """A small synthetic itinerary. Priced figures are chosen so the totals
    read cleanly: 1.5 t in 2025, 310 kg completed in 2026, 900 kg planned.

    LHR connects the most CO2e and sits close to CDG, so the heaviest airport
    has a lighter neighbour drawn near it on the map."""
    return [
        row(
            flight_date="2025-05-10",
            origin="LHR",
            destination="JFK",
            departure_time="2025-05-10T10:00:00+01:00",
            arrival_time="2025-05-10T13:00:00-04:00",
            cabin_class_known="business",
            flight_reason="business",
            emissions_kg_actual="1500",
        ),
        row(
            flight_date="2026-03-04",
            origin="JFK",
            destination="LHR",
            departure_time="2026-03-04T20:30:00-05:00",
            arrival_time="2026-03-05T08:15:00+00:00",
            emissions_kg_actual="250",
        ),
        row(
            flight_date="2026-06-01",
            origin="LHR",
            destination="CDG",
            flight_reason="leisure",
            emissions_source="typical_route_average",
            emissions_kg_actual="60",
        ),
        # Completed, but unpriced and to an airport with no coordinates.
        row(flight_date="2026-07-01", origin="CDG", destination="XXX", emissions_source=""),
        # Upcoming.
        row(
            flight_date="2026-09-01",
            origin="LHR",
            destination="SIN",
            departure_time="2026-09-01T21:00:00+01:00",
            arrival_time="2026-09-02T17:00:00+08:00",
            emissions_kg_actual="900",
        ),
        # No date at all: it cannot be placed in a year or called departed.
        row(flight_date="", origin="SIN", destination="LHR", emissions_source=""),
    ]


@pytest.fixture
def browser_context_args(browser_context_args):
    # formatNumber() uses the browser's locale, so pin it for stable text.
    return {**browser_context_args, "locale": "en-GB", "timezone_id": "Europe/London"}


@pytest.fixture
def problems(page):
    """Page errors, console errors and non-local requests, collected as the
    page runs and asserted empty when the test ends."""
    found: list[str] = []
    page.on("pageerror", lambda error: found.append(f"page error: {error}"))
    page.on(
        "console",
        lambda message: message.type == "error" and found.append(f"console: {message.text}"),
    )
    page.on(
        "request",
        lambda request: (
            request.url.startswith(LOCAL_SCHEMES) or found.append(f"request: {request.url}")
        ),
    )
    page.context.route("http*://**", lambda route: route.abort())
    yield found
    assert found == []


@pytest.fixture
def open_passport(page, problems, tmp_path):
    """Render rows at a given clock, open the file and wait for the first draw."""

    def open_(rows: list[dict] | None = None, now: datetime = NOW, name="passport.html"):
        output = render(fixture_rows() if rows is None else rows, tmp_path / name, now)
        # networkidle, so a request the page makes after load is recorded
        # before the test inspects `problems`, not only at teardown.
        page.goto(output.as_uri(), wait_until="networkidle")
        page.locator("#hero-value").filter(has_text="CO").wait_for()
        return page

    return open_
