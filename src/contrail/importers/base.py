"""The Importer seam: anything that can produce flights from some source."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from contrail.models import FlightRecord, UnparsedEvent


@runtime_checkable
class Importer(Protocol):
    """Produces flights from one source.

    Adding a new source should mean: one new module implementing this protocol,
    one entry in the registry in ``importers/__init__.py``, and nothing else.
    """

    id: str  # stable string, used in FlightRecord.source and for CSV dedup

    def fetch(self, config: dict) -> Iterable[FlightRecord | UnparsedEvent]:
        """Yield everything this source knows about.

        ``config`` is the importer's own entry from the ``sources:`` list, so
        each importer is free to define its own shape (a URL, OAuth creds, a
        file path) without the config schema having to anticipate it.
        """
        ...
