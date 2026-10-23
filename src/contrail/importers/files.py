"""Finding the export files a file-reading importer was pointed at."""

from __future__ import annotations

import sys
from glob import glob
from pathlib import Path


def export_files(path: str, missing: str) -> list[Path]:
    """Every export the configured path names, newest first.

    A file, a directory of them, or a glob. Newest first because the CLI keeps
    the *first* record it sees for a key, so the most recent export wins wherever
    two of them disagree about the same flight.

    Ordering is by filename, not modification time: both apps that export
    date-stamp the name (``FlightyExport-YYYY-MM-DD.csv``,
    ``openflights-backup-YYYY-MM-DD-HHMM.csv``), so the name sorts correctly,
    and a repo checkout gives every file the same mtime anyway.

    **Finding nothing is a warning, not an error**, and an empty directory and a
    missing one behave alike. An export arrives by hand, so "not there yet" is an
    ordinary state — the contrail-gh template ships an empty ``flighty/`` for
    exactly that reason. Raising would let one unconfigured source take down a
    sync that had a perfectly good TripIt feed alongside it. Nothing else breaks:
    a source that returns nothing already can't cancel its own rows, and a run
    where *every* source is empty is refused separately.

    ``missing`` is the warning's first line, naming the source and what the run
    goes without.
    """
    target = Path(path).expanduser()

    if target.is_dir():
        matches = list(target.glob("*.csv"))
    elif target.exists():
        matches = [target]
    else:
        matches = [Path(p) for p in glob(str(target))]

    if not matches:
        print(
            f"{missing}\n  'path' takes an exported CSV, a directory of them, or a glob.",
            file=sys.stderr,
        )

    return sorted(matches, key=lambda p: p.name, reverse=True)
