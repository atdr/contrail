"""Storage backends, plus the ordering/total invariant every backend shares."""

from __future__ import annotations

from datetime import datetime

from contrail.storage.base import Storage
from contrail.storage.local_csv import CSV_FIELDS, LocalCSVStorage


def _sort_key(row: dict):
    try:
        return datetime.strptime(row["flight_date"], "%Y-%m-%d")
    except (ValueError, KeyError, TypeError):
        return datetime.min


def recompute_cumulative(rows: list[dict]) -> list[dict]:
    """Sort by flight date ascending and recompute the running actual-cabin total.

    Lives outside the Storage protocol so every backend inherits the same
    invariant. Called by the CLI before ``storage.save()``.
    """
    rows.sort(key=_sort_key)
    running = 0.0
    for row in rows:
        kg = row.get("emissions_kg_actual")
        try:
            kg_val = float(kg) if kg not in (None, "") else 0.0
        except (TypeError, ValueError):
            kg_val = 0.0
        running += kg_val
        row["cumulative_kg_actual"] = round(running, 3)
    return rows


__all__ = ["CSV_FIELDS", "LocalCSVStorage", "Storage", "recompute_cumulative"]
