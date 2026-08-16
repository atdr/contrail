"""CSV storage backend.

Three schema decisions worth knowing:

- ``emissions_kg_actual`` records the cabin actually flown where the source
  knows it, falling back to economy. The per-cabin reference columns are all
  still there beside it, so a cabin-aware importer improves this number
  without the file gaining a second, competing notion of "the" figure.
- ``also_seen_as`` carries the keys *other* sources use for the same flight, not
  the row's own — that is already in ``source``/``source_id``. It is there to be
  joined on: the row keeps only what contrail needs to price a flight, and the
  link is what reaches everything else the original export holds (seat, PNR, tail
  number). Space-separated rather than comma, so the column survives hand editing
  and the README's ``awk`` one-liner; sorted, so an unchanged row stays
  byte-identical between runs.
- There is **no** running-total column. This file is a record of flights;
  totalling it is the job of whatever reads it.
  A stored aggregate would go stale against hand edits and would rewrite every
  row after any backfilled flight.
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
    "also_seen_as",  # other sources' keys for this same flight, space-separated
    "flight_date",  # YYYY-MM-DD departure date
    "carrier_code",  # e.g. "UA" — as booked (the marketing carrier)
    "flight_number",  # e.g. "523"
    "operating_carrier_code",  # who actually flies it; differs on a codeshare
    "operating_flight_number",  # blank when the source doesn't say
    "origin",  # IATA code
    "destination",  # IATA code
    "departure_time",  # ISO 8601 in the origin's timezone; blank for all-day events
    "status",  # blank = flown or booked; "cancelled" = dropped from the feed
    "cabin_class_known",  # cabin the source reported, else blank
    "aircraft_type",  # airframe as the source names it; TIM never names one
    "flight_reason",  # business | leisure, if the source says
    "emissions_source",  # exact | typical_route_average | unparsed | no_data
    "model_version",  # full TIM version, e.g. 3.0.0+20260814
    "emissions_data_source",  # TIM | EASA (`source` is taken by the importer id)
    "contrails_impact",  # negligible | moderate | severe
    "distance_km",
    "aircraft_match",  # how well TIM matched an airframe; it never names one
    "emissions_kg_first",
    "emissions_kg_business",
    "emissions_kg_premium_economy",
    "emissions_kg_economy",
    "emissions_kg_actual",  # the cabin flown if known, else economy
    "raw_summary",  # original source text, for unparsed rows
]

# `status` values. Blank means an ordinary flight, booked or flown.
STATUS_CANCELLED = "cancelled"

# Columns that identify a pre-v0.1.0 CSV.
LEGACY_ID_FIELD = "tripit_uid"
LEGACY_CUMULATIVE_FIELD = "cumulative_kg_economy"

# Columns contrail used to write and no longer does. Dropped on read, so they
# don't survive indefinitely as if they were a column someone added by hand.
RETIRED_FIELDS = frozenset({LEGACY_CUMULATIVE_FIELD, "cumulative_kg_actual"})


def is_legacy_row(row: dict) -> bool:
    """True if this row uses the pre-v0.1.0 schema."""
    return LEGACY_ID_FIELD in row and "source_id" not in row


def migrate_legacy_row(row: dict) -> dict:
    """Bring a pre-v0.1.0 row up to the current schema, in memory.

    Without this, pointing contrail at such a CSV would fail to recognise any of
    its rows, re-import every flight, and re-price the lot.
    """
    migrated = {field: row.get(field, "") for field in CSV_FIELDS}
    migrated["source"] = "tripit_ical"
    migrated["source_id"] = row.get(LEGACY_ID_FIELD, "")
    migrated["cabin_class_known"] = ""
    # That schema had no notion of an "actual" cabin, so economy is the honest
    # carry-over.
    migrated["emissions_kg_actual"] = row.get("emissions_kg_economy", "")
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
    known = set(CSV_FIELDS) | RETIRED_FIELDS
    extra: list[str] = []
    for row in rows:
        for field in row:
            if field not in known:
                known.add(field)
                extra.append(field)
    return extra


def is_cancelled(row: dict) -> bool:
    return (row.get("status") or "").strip().lower() == STATUS_CANCELLED


def actual_kg(row: dict) -> str:
    """Pick the per-cabin column matching ``cabin_class_known``, else economy.

    A cancelled flight yields nothing, which is the whole mechanism for keeping
    it out of any total: ``normalize_rows`` stops re-deriving the column,
    ``total_kg`` stops summing it, and so does anything else reading the CSV.
    The per-cabin figures are deliberately left intact — once a flight is in the
    past TIM will never price it again, so a figure discarded on a mistaken
    cancellation could not be recovered.
    """
    if is_cancelled(row):
        return ""
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
        return [
            migrate_legacy_row(row)
            if is_legacy_row(row)
            else {k: v for k, v in row.items() if k not in RETIRED_FIELDS}
            for row in rows
        ]

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
