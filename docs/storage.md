# The CSV, and what it is for

**It is a record of flights, not an analysis of them.**

## No running total

There is no `cumulative_*` column, and one shouldn't be added. A stored
aggregate goes stale the moment a row is hand-edited, and one backfilled flight
rewrites every row after it — noise in a file that lives in git. `total_kg()`
computes a total for display; readers sum the column themselves.

Never document that sum by column *number*. Adding the operating columns once
turned the README's `$16` into `emissions_kg_premium_economy`, a 23%
overstatement that looked entirely plausible. The documented one-liner looks the
column up by header name.

## Invariants

- **Every value in a row dict is a string**, so a freshly built row compares
  equal to the same row loaded back from the CSV. The no-op-write check depends
  on it, which is why every row builder must set every column.
- **`normalize_rows` re-derives `emissions_kg_actual` on every pass.** That is
  what makes hand-editing work: fill in a figure on an `unparsed` row, or correct
  `cabin_class_known`, and the next sync picks it up.
- **A cancelled row keeps its per-cabin figures**; only `emissions_kg_actual` is
  cleared, via `actual_kg`. That single point is what drops it from every total,
  including the README's one-liner, with no reader needing to know the `status`
  column exists.
- **Columns the user added are preserved.** Hand-editing is documented, so a
  `notes` column survives a sync. Columns contrail has retired are dropped on
  read so they don't linger as if hand-added.
- **`save` writes to a temp file and `os.replace`s it**, with an `fsync`. TripIt's
  feed only exposes recent and upcoming trips, so a half-written CSV would lose
  history that cannot be re-fetched.

## Older files

A CSV using the pre-v0.1.0 schema (`tripit_uid`, `cumulative_kg_economy`) is
migrated in memory on read. Without it, pointing contrail at one would fail to
recognise any row, re-import every flight and re-price the lot.

## Seams

`normalize_rows`, `kg_value` and `total_kg` live in `storage/__init__.py`, not on
the `Storage` protocol, so a future S3/GCS backend inherits the sort-and-derive
invariants for free. The protocol itself is deliberately two methods.

The raw log (`storage/raw_log.py`) is kept off that protocol: `Storage` is about
rows, and the CLI drives both.
