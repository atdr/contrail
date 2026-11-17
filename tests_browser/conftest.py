"""Open a freshly rendered Passport in a real browser.

This suite sits outside `testpaths` on purpose: plain `pytest` never collects
it, so the Python matrix and the coverage job need no browser. Run it with
`pytest tests_browser` once the `browser` extra and Chromium are installed.

Every test opens the generated file, not the template, so what is exercised is
exactly what `contrail passport` writes. The page must make no request beyond
the file itself: anything over the network is aborted and fails the test.

Each test opens the standalone page. One marked `both_formats` runs a second
time against a site folder, served from 127.0.0.1 because fetch() refuses a
file: URL, and then the only requests allowed are to that server.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from contrail.passport import render
from contrail.storage.local_csv import CSV_FIELDS

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)

# Schemes a self-contained page may load from. Chart.js builds
# canvases and inline images from data: and blob: URLs.
LOCAL_SCHEMES = ("file:", "data:", "blob:", "about:")

# How the page reports a Content-Security-Policy violation to `problems`.
CSP_REPORT = "CSP violation:"


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


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "both_formats: run against a served site folder as well as the standalone page"
    )


def pytest_generate_tests(metafunc):
    if metafunc.definition.get_closest_marker("both_formats"):
        metafunc.parametrize("passport_format", ["standalone", "site"])


@pytest.fixture
def passport_format() -> str:
    """The format `open_passport` renders. Overridden by `both_formats`."""
    return "standalone"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture
def site_origin(passport_format, tmp_path):
    """A server for the test's own folder, on loopback, for a site only."""
    if passport_format != "site":
        yield None
        return
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def browser_context_args(browser_context_args):
    # formatNumber() uses the browser's locale, so pin it for stable text.
    return {**browser_context_args, "locale": "en-GB", "timezone_id": "Europe/London"}


class Problems(list):
    """What `problems` found, and how much of it the call phase already reported."""

    reported = 0

    def unreported(self) -> list[str]:
        return self[self.reported :]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """Fail the test itself, not its teardown, on anything `problems` found.

    pytest-playwright keeps a retain-on-failure trace only when the call phase
    fails, so a violation first raised at fixture teardown would discard the
    very trace that explains it."""
    result = yield
    found = item.funcargs.get("problems")
    if found:
        found.reported = len(found)
        raise AssertionError(f"the page misbehaved: {list(found)}")
    return result


@pytest.fixture
def problems(page, site_origin):
    """Page errors, console errors and non-local requests, collected as the
    page runs. Asserted empty as the test body ends, by pytest_runtest_call
    above, and again at teardown for anything that arrived after it.

    For a site, its own server is local too, and the only origin let through."""
    local = LOCAL_SCHEMES + ((f"{site_origin}/",) if site_origin else ())
    found = Problems()
    page.on("pageerror", lambda error: found.append(f"page error: {error}"))
    page.on(
        "console",
        lambda message: message.type == "error" and found.append(f"console: {message.text}"),
    )
    page.on(
        "request",
        lambda request: request.url.startswith(local) or found.append(f"request: {request.url}"),
    )
    page.context.route(
        "http*://**",
        lambda route: route.continue_() if route.request.url.startswith(local) else route.abort(),
    )
    # Chrome logs most refusals to the console, but not as errors in every
    # case. The event is the policy's own report of each one.
    page.on(
        "console",
        lambda message: message.text.startswith(CSP_REPORT) and found.append(message.text),
    )
    page.add_init_script(
        "document.addEventListener('securitypolicyviolation', (event) => console.info("
        f"'{CSP_REPORT}', event.effectiveDirective, event.blockedURI, event.sample));"
    )
    yield found
    assert found.unreported() == []


@pytest.fixture
def open_passport(page, problems, tmp_path, passport_format, site_origin):
    """Render rows at a given clock, open the page and wait for the first draw.

    A site is rendered to a folder named after the file, less `.html`, and
    opened from the test's server."""

    def open_(rows: list[dict] | None = None, now: datetime = NOW, name="passport.html"):
        rows = fixture_rows() if rows is None else rows
        if passport_format == "site":
            folder = name.removesuffix(".html")
            render(rows, tmp_path / folder, now, mode="site")
            url = f"{site_origin}/{folder}/"
        else:
            url = render(rows, tmp_path / name, now).as_uri()
        # networkidle, so a request the page makes after load is recorded
        # before the test inspects `problems`, not only at teardown.
        page.goto(url, wait_until="networkidle")
        page.locator("#hero-value").filter(has_text="CO").wait_for()
        return page

    return open_
