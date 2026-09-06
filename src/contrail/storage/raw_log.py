"""An append-only record of everything the emissions API ever said.

The CSV holds the figures worth reading day to day. This holds the rest — the
well-to-tank/tank-to-wake split, load factors, cargo mass fraction, seat-area
ratios, source versions, the calculator permalink — because **TIM cannot be
asked twice**. It will not price a flight that has departed, so anything not
captured at the time is gone for good.

Append-only, deliberately. A flight is re-priced on every sync until it goes, so
the file accumulates the sequence of answers TIM gave over time: the record of
*when* a figure changed, not merely what it ended up as. Nothing is ever
rewritten, so no earlier capture can be lost.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path


def default_path(csv_path: str) -> str:
    """The sidecar that belongs beside a given CSV: ``x.csv`` -> ``x.raw.jsonl``."""
    return str(Path(csv_path).with_suffix(".raw.jsonl"))


class JSONLRawLog:
    """Appends one JSON object per priced flight."""

    id = "jsonl"

    def __init__(self, path: str, enabled: bool = True):
        self.path = path
        self.enabled = enabled

    def append(
        self,
        entries: Sequence[dict],
        captured_at: str | None = None,
        skip_unchanged: bool = True,
    ) -> int:
        """Record what the provider returned. Returns how many lines were written.

        Unchanged answers are skipped by default. An upcoming flight is re-priced
        on every run, so appending unconditionally would add identical lines
        daily — growth without information, and a daily commit in contrail-gh
        for a file nothing had actually changed. What is worth keeping is each
        *distinct* answer and when it arrived.
        """
        if not self.enabled or not entries:
            return 0

        if skip_unchanged:
            latest = self.latest_by_key()
            unseen = object()  # a key never captured before is always written
            entries = [
                entry
                for entry in entries
                if entry.get("response") != latest.get(entry.get("key"), unseen)
            ]
            if not entries:
                return 0

        captured_at = captured_at or datetime.now(UTC).isoformat()
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)

        written = 0
        with open(self.path, "a", encoding="utf-8") as handle:
            for entry in entries:
                payload = {"captured_at": captured_at, **entry}
                # Never let an unserializable corner of a response abort a sync
                # that has already succeeded in every other respect.
                handle.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
                written += 1
            handle.flush()
            os.fsync(handle.fileno())
        return written

    def latest_by_key(self) -> dict:
        """The most recently captured response for each flight."""
        latest: dict = {}
        for entry in self.read():
            if entry.get("key"):
                latest[entry["key"]] = entry.get("response")
        return latest

    def read(self) -> list[dict]:
        """Every captured entry, oldest first. For inspection and tests.

        A line that won't parse is skipped rather than raised. This is a plain
        append, so a crash or a full disk mid-write can leave a partial line —
        and since every sync reads the file back before appending, raising here
        would fail every future sync until someone hand-edited the file.
        """
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries
