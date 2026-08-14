"""The Storage seam: anything that can persist and reload the flight log."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Storage(Protocol):
    """Loads and saves rows of the flight log.

    Deliberately dumb: it does not sort, dedup, or compute totals. That keeps a
    future S3/GCS-backed backend (for a stateless Lambda or Cloud Function
    deployment) to just two methods, with none of the logic duplicated.
    """

    def load(self) -> list[dict]:
        """Return every row currently stored, as dicts keyed by column name."""
        ...

    def save(self, rows: Sequence[dict]) -> None:
        """Replace the stored contents with ``rows``."""
        ...
