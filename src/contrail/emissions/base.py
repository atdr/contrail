"""The EmissionsProvider seam: anything that can price a batch of flights."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from contrail.models import EmissionsResult, FlightRecord


@runtime_checkable
class EmissionsProvider(Protocol):
    """Prices flights in bulk.

    Keeping this behind a protocol keeps HTTP and API details out of the CLI and
    storage layers, so both are trivial to unit test with mocks, and leaves room
    for an alternative model later.
    """

    id: str

    def compute(self, flights: Sequence[FlightRecord]) -> dict[str, EmissionsResult]:
        """Return results keyed by ``FlightRecord.key``."""
        ...
