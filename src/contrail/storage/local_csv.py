"""CSV storage backend.

Ported from the prototype, with one deliberate schema change: the headline
cumulative column is now ``cumulative_kg_actual``, a running total of
``emissions_kg_actual`` (the cabin actually flown, where the source knows it,
falling back to economy). The per-cabin reference columns are all still there.

That rename is what lets a future cabin-aware importer improve the headline
number rather than leaving the CSV with two competing "cumulative" concepts.
"""

from __future__ import annotations

import contextlib
import csv
import os
import tempfile
from collections.abc import Sequence

from contrail.models import CABIN_CLASSES

CSV_FIELDS = [
    "sync_timestamp",  # when the row was added (UTC ISO)
    "source",  # importer id, e.g. "tripit_ical"
    "source_id",  # importer-specific id
    "flight_date",  # YYYY-MM-DD departure date
    "carrier_code",  # e.g. "UA"
    "flight_number",  # e.g. "523"
    "origin",  # IATA code
    "destination",  # IATA code
    "cabin_class_known",  # cabin the source reported, else blank
    "emissions_source",  # exact | typical_route_average | unparsed | no_data
    "model_version",  # TIM model version, only set for `exact` rows
    "emissions_kg_first",
    "emissions_kg_business",
    "emissions_kg_premium_economy",
    "emissions_kg_economy",
    "emissions_kg_actual",  # the cabin flown if known, else economy
    "cumulative_kg_actual",  # running total of the above, sorted by flight_date
    "raw_summary",  # original source text, for unparsed rows
]

# Columns that identify the prototype's pre-v0.1.0 CSV.
LEGACY_ID_FIELD = "tripit_uid"
LEGACY_CUMULATIVE_FIELD = "cumulative_kg_economy"


def is_legacy_row(row: dict) -> bool:
    """True if this row came from the single-file prototype's schema."""
    return LEGACY_ID_FIELD in row and "source_id" not in row


def migrate_legacy_row(row: dict) -> dict:
    """Bring a prototype row up to the current schema, in memory.

    Without this, pointing contrail at an existing prototype CSV would fail to
    recognise any of its rows, re-import every flight, and re-price the lot.
    """
    migrated = {field: row.get(field, "") for field in CSV_FIELDS}
    migrated["source"] = "tripit_ical"
    migrated["source_id"] = row.get(LEGACY_ID_FIELD, "")
    migrated["cabin_class_known"] = ""
    # The prototype had no notion of an "actual" cabin, so economy is the
    # honest carry-over; cumulative gets recomputed from scratch anyway.
    migrated["emissions_kg_actual"] = row.get("emissions_kg_economy", "")
    migrated["cumulative_kg_actual"] = ""
    return migrated


def row_key(row: dict) -> str:
    """Namespaced dedup key for a stored row."""
    return f"{row.get('source', '')}:{row.get('source_id', '')}"


def extra_fields(rows: Sequence[dict]) -> list[str]:
    """Columns present in the data that contrail doesn't own.

    Hand-editing the CSV is a documented workflow, so a column someone added
    themselves (``notes``, say) is preserved rather than silently dropped on the
    next sync.
    """
    known = set(CSV_FIELDS)
    extra: list[str] = []
    for row in rows:
        for field in row:
            if field not in known:
                known.add(field)
                extra.append(field)
    return extra


def actual_kg(row: dict) -> str:
    """Pick the per-cabin column matching ``cabin_class_known``, else economy."""
    cabin = (row.get("cabin_class_known") or "").strip().lower()
    if cabin in CABIN_CLASSES:
        value = row.get(f"emissions_kg_{cabin}", "")
        if value not in (None, ""):
            return value
    return row.get("emissions_kg_economy", "")


class LocalCSVStorage:
    """Reads and writes the flight log as a CSV file on local disk."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, newline="") as f:
            rows = list(csv.DictReader(f))
        return [migrate_legacy_row(r) if is_legacy_row(r) else r for r in rows]

    def save(self, rows: Sequence[dict]) -> None:
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)

        fieldnames = CSV_FIELDS + extra_fields(rows)

        # Write to a temp file in the same directory, then rename over the
        # target. Opening the CSV directly with "w" truncates it before a single
        # row is written, so a crash, a cron timeout, or a full disk mid-write
        # would leave nothing behind — and a TripIt feed only exposes recent and
        # upcoming trips, so older history could not be re-fetched.
        fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".contrail-", suffix=".csv")
        try:
            with os.fdopen(fd, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    writer.writerow({field: row.get(field, "") for field in fieldnames})
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
