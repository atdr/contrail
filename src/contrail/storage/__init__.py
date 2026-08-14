"""Storage backends, plus the ordering/total invariant every backend shares."""

from __future__ import annotations

from datetime import datetime

from contrail.storage.base import Storage
from contrail.storage.local_csv import CSV_FIELDS, LocalCSVStorage, actual_kg


def _sort_key(row: dict):
    try:
        return datetime.strptime(row["flight_date"], "%Y-%m-%d")
    except (ValueError, KeyError, TypeError):
        return datetime.min


def recompute_cumulative(rows: list[dict]) -> list[dict]:
    """Sort by flight date ascending and recompute the running actual-cabin total.

    ``emissions_kg_actual`` is re-derived from the per-cabin columns on every
    pass, not just when a row is first written. That's what makes the documented
    repair workflows actually work: filling in a figure by hand on an `unparsed`
    row, or correcting ``cabin_class_known`` on an existing one, is picked up on
    the next sync.

    Lives outside the Storage protocol so every backend inherits the same
    invariant. Called by the CLI before ``storage.save()``.
    """
    rows.sort(key=_sort_key)
    running = 0.0
    for row in rows:
        row["emissions_kg_actual"] = actual_kg(row)
        kg = row.get("emissions_kg_actual")
        try:
            kg_val = float(kg) if kg not in (None, "") else 0.0
        except (TypeError, ValueError):
            kg_val = 0.0
        running += kg_val
        # String, like every other value in a row, so a recomputed row compares
        # equal to the same row loaded back from the CSV.
        row["cumulative_kg_actual"] = str(round(running, 3))
    return rows


__all__ = ["CSV_FIELDS", "LocalCSVStorage", "Storage", "recompute_cumulative"]
