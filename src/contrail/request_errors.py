"""Describe a failed HTTP request without repeating its URL.

``str()`` of a ``requests`` exception embeds the URL it was asked for: the whole
URL for an ``HTTPError``, and the host and path as separate fragments for a
``ConnectionError``. A TripIt feed URL is a credential, the token is its path,
and contrail's error output goes to cron logs, Actions logs and bug reports. So
nothing contrail prints about a failed request may come from ``str(exc)``. It
names the scheme and host, the status, and the class of the underlying failure,
none of which can carry a token.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import requests


class RedactedRequestError(requests.RequestException):
    """A request failure whose message has already been made safe to print.

    It subclasses ``RequestException`` so every caller that handled the original
    exception handles this one the same way.
    """


def origin(url: str | None) -> str:
    """``scheme://host[:port]`` of a URL, with any userinfo, path and query dropped."""
    if not url:
        return "an unknown host"
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "an unparseable URL"
    if not host:
        return "an unparseable URL"
    netloc = f"{host}:{port}" if port else host
    return f"{parts.scheme}://{netloc}" if parts.scheme else netloc


def _underlying(exc: requests.RequestException) -> str | None:
    """The class name of the urllib3 failure inside a connection error.

    ``MaxRetryError.reason`` holds it, as a ``NameResolutionError`` or
    ``NewConnectionError`` say. Only the class name is used: its message repeats
    the URL too.
    """
    for arg in exc.args:
        reason = getattr(arg, "reason", None)
        if isinstance(reason, BaseException):
            return type(reason).__name__
    return None


def describe(exc: requests.RequestException, url: str | None = None) -> str:
    """A one line account of ``exc`` that names no path, query or credential.

    ``url`` is the URL the caller asked for. Without it, the request recorded on
    the exception is used, when there is one.
    """
    if isinstance(exc, RedactedRequestError):
        return str(exc)
    if url is None:
        request = getattr(exc, "request", None)
        url = getattr(request, "url", None)

    what = type(exc).__name__
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        status = f"{response.status_code} {response.reason or ''}".strip()
        what = f"{what} ({status})"
    else:
        cause = _underlying(exc)
        if cause:
            what = f"{what} ({cause})"
    return f"{what} from {origin(url)}"
