"""Storage backends, plus the row invariants every backend shares."""

from __future__ import annotations

from datetime import datetime

from contrail.config import lookup_type
from contrail.storage.base import Storage
from contrail.storage.local_csv import (
    CSV_FIELDS,
    STATUS_CANCELLED,
    LocalCSVStorage,
    actual_kg,
    is_cancelled,
)
from contrail.storage.raw_log import JSONLRawLog, default_path

# The flight log itself: what `Storage.load()` and `.save()` act on. Adding a
# backend = one new module + one line here. See issue #9 for the S3 one.
STORAGES: dict[str, type[Storage]] = {
    LocalCSVStorage.id: LocalCSVStorage,
}

# Raw logs are a separate registry, not more entries in STORAGES. A raw log is
# deliberately not a `Storage` — it appends provider answers rather than loading
# and saving rows, and the CLI drives both (see the Seams section of
# docs/storage.md). Keeping them apart is what stops a config naming one where
# the other belongs.
RAW_LOGS: dict[str, type] = {
    JSONLRawLog.id: JSONLRawLog,
}


def get_storage(type_name: str) -> type[Storage]:
    """Look up a flight-log backend by its config ``type:`` string."""
    return lookup_type(STORAGES, type_name, "storage type", "storage backends")


def get_raw_log(type_name: str) -> type:
    """Look up a raw-log backend by its config ``type:`` string."""
    return lookup_type(RAW_LOGS, type_name, "raw log type", "raw logs")


def _sort_key(row: dict):
    try:
        return datetime.strptime(row["flight_date"], "%Y-%m-%d")
    except (ValueError, KeyError, TypeError):
        return datetime.min


def kg_value(row: dict) -> float:
    """A row's actual-cabin emissions as a number, treating anything unusable as 0."""
    kg = row.get("emissions_kg_actual")
    try:
        return float(kg) if kg not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def total_kg(rows: list[dict]) -> float:
    """Sum of the actual-cabin emissions across rows.

    Computed on demand for display. Deliberately not stored: the CSV is a record
    of flights, and a running total is an analysis of it — see the note on
    ``normalize_rows``.
    """
    return round(sum(kg_value(row) for row in rows), 3)


def normalize_rows(rows: list[dict]) -> list[dict]:
    """Sort by flight date ascending and re-derive the actual-cabin column.

    ``emissions_kg_actual`` is re-derived from the per-cabin columns on every
    pass, not just when a row is first written. That's what makes the documented
    repair workflows work: filling in a figure by hand on an `unparsed` row, or
    correcting ``cabin_class_known`` on an existing one, is picked up on the next
    sync.

    No running total is stored. The CSV is the database; totalling it is the job
    of whatever reads it, so there is no derived aggregate to fall out of date or
    to reshuffle every later row when one flight is backfilled out of order.

    Lives outside the Storage protocol so every backend inherits the same
    invariants. Called by the CLI before ``storage.save()``.
    """
    rows.sort(key=_sort_key)
    for row in rows:
        row["emissions_kg_actual"] = actual_kg(row)
    return rows


__all__ = [
    "CSV_FIELDS",
    "JSONLRawLog",
    "RAW_LOGS",
    "STORAGES",
    "default_path",
    "STATUS_CANCELLED",
    "LocalCSVStorage",
    "Storage",
    "get_raw_log",
    "get_storage",
    "is_cancelled",
    "kg_value",
    "normalize_rows",
    "total_kg",
]
