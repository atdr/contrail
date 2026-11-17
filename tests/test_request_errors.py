"""A failed request is described without its URL, which may be a credential."""

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NameResolutionError

from contrail.cli import main
from contrail.importers.tripit_ical import fetch_ical
from contrail.request_errors import RedactedRequestError, describe, origin

TOKEN = "0123456789abcdef0123456789abcdef"
FEED = f"https://user:pw@www.example.invalid/feed/ical/private/{TOKEN}/tripit.ics?k={TOKEN}"


def _http_error(status=404, reason="Not Found"):
    """What raise_for_status raises: the whole URL is in the message."""
    response = requests.Response()
    response.status_code = status
    response.reason = reason
    response.url = FEED
    return requests.HTTPError(f"{status} Client Error: {reason} for url: {FEED}", response=response)


def _connection_error():
    """What requests raises for an unreachable host: host and path apart, so
    masking the whole URL in a log does not catch the token."""
    reason = NameResolutionError("www.example.invalid", None, "nodename nor servname provided")
    inner = MaxRetryError(None, f"/feed/ical/private/{TOKEN}/tripit.ics", reason)
    return requests.ConnectionError(inner)


@pytest.mark.parametrize(
    "exc, expected",
    [
        (_http_error(), "HTTPError (404 Not Found) from https://www.example.invalid"),
        (
            _connection_error(),
            "ConnectionError (NameResolutionError) from https://www.example.invalid",
        ),
        (requests.Timeout("read timed out"), "Timeout from https://www.example.invalid"),
    ],
)
def test_describe_names_only_the_host_and_the_failure(exc, expected):
    assert describe(exc, FEED) == expected


@pytest.mark.parametrize("make", [_http_error, _connection_error])
def test_the_fixtures_reproduce_the_leak(make):
    """Guards the test doubles: if requests stopped repeating the URL, the
    redaction tests below would pass for the wrong reason."""
    assert TOKEN in str(make())


def test_describe_falls_back_to_the_request_on_the_exception():
    exc = requests.HTTPError("boom", request=requests.Request("GET", FEED).prepare())
    assert describe(exc) == "HTTPError from https://www.example.invalid"


def test_describe_with_no_url_at_all():
    assert describe(requests.ConnectionError("x")) == "ConnectionError from an unknown host"


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.example.invalid:8443/a/b?c=d", "https://www.example.invalid:8443"),
        ("not a url", "an unparseable URL"),
        ("https://[::1/x", "an unparseable URL"),
        ("", "an unknown host"),
    ],
)
def test_origin_drops_everything_but_scheme_host_and_port(url, expected):
    assert origin(url) == expected


@pytest.mark.parametrize("make", [_http_error, _connection_error])
def test_a_failed_feed_fetch_does_not_carry_the_url(monkeypatch, make):
    def get(url, timeout):
        raise make()

    monkeypatch.setattr("contrail.importers.tripit_ical.requests.get", get)
    with pytest.raises(RedactedRequestError) as info:
        fetch_ical(FEED)

    assert TOKEN not in str(info.value)
    assert "www.example.invalid" in str(info.value)
    # Chained from None, so no traceback can print the original either.
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_a_non_2xx_status_is_redacted_too(monkeypatch):
    response = requests.Response()
    response.status_code = 403
    response.reason = "Forbidden"
    response.url = FEED
    monkeypatch.setattr(
        "contrail.importers.tripit_ical.requests.get", lambda url, timeout: response
    )
    with pytest.raises(RedactedRequestError, match=r"403 Forbidden\) from https://www"):
        fetch_ical(FEED)


@pytest.mark.parametrize("make", [_http_error, _connection_error])
def test_sync_never_prints_the_feed_url(tmp_path, monkeypatch, capsys, make):
    """End to end: the line a cron log or an Actions log would keep."""

    def get(url, timeout):
        raise make()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIPIT_ICAL_URL", FEED)
    monkeypatch.setenv("TIM_API_KEY", "not-a-real-key")
    monkeypatch.delenv("FLIGHTY_CSV_PATH", raising=False)
    monkeypatch.setattr("contrail.importers.tripit_ical.requests.get", get)

    assert main(["sync", "--dry-run"]) == 1
    out, err = capsys.readouterr()
    assert TOKEN not in out + err
    assert "Network error talking to an API: could not fetch the calendar feed:" in err
    assert "www.example.invalid" in err


def test_main_redacts_a_request_error_from_anywhere(monkeypatch, capsys):
    """Defence in depth: a requests error nothing wrapped still prints no path."""

    def boom(args):
        raise _connection_error()

    monkeypatch.setattr("contrail.cli.cmd_sync", boom)

    assert main(["sync"]) == 1
    err = capsys.readouterr().err
    assert TOKEN not in err
    assert "ConnectionError (NameResolutionError) from an unknown host" in err
