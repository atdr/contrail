# What a sync may change

**A flight that hasn't departed is contrail's to correct. One that has is left
alone.** (`resync.py`)

That is not only caution. TripIt's feed carries only recent and upcoming trips,
so "absent from the feed" is ambiguous between *cancelled* and *aged out of the
window*. Confining every change to future flights removes the ambiguity
outright: a future flight cannot have aged out.

## The freeze boundary

The departure instant, from `departure_time` when the source states one. Falling
back, in order:

1. Stored `departure_time` — exact to the minute.
2. The local date **at the origin** (`airports.today_at`).
3. The UTC date, only when the origin can't be resolved.

Never the UTC date alone: it is wrong for part of every day by the origin's
offset, in both directions. At 08:00 in Tokyo the UTC date is still yesterday, so
a flown flight would stay cancellable; at 18:00 in Los Angeles it is already
tomorrow, so a flight departing that evening would freeze before it left.

## While a flight is open

- Date, route and flight numbers are corrected from the feed.
- It is re-priced on every run — see [emissions.md](emissions.md) for why even an
  `exact` figure is worth re-asking.
- `cabin_class_known` is never overwritten. No importer can supply it, so
  overwriting would destroy the only copy; a warning names the row when a
  re-synced flight had one set.
- Disappearing from the feed marks it `cancelled` rather than deleting it. The
  per-cabin figures stay because TIM will never price a past flight again; only
  `emissions_kg_actual` is cleared, which is the single point that drops it out
  of every total. Reappearing restores it.

## Guards worth knowing

- **A column the row doesn't have is back-fill, not a feed change.**
  `differences()` skips absent fields. Gaining a column would otherwise mark
  every stored row changed on the first sync after an upgrade — and `changed` is
  what bypasses the no-downgrade guard, which would replace a whole file of exact
  figures with route averages and then freeze them that way.
- **Only a source that returned something may cancel its own rows.** With several
  sources configured, one silently empty feed would otherwise cancel every flight
  it owns while the others kept a global guard happy. Granularity is the importer
  id, so two feeds of the same *type* still cannot be told apart — known, not
  solved; it needs a per-source identity in the row.
- **A feed yielding no flights at all refuses to cancel anything** and exits
  non-zero. `--dry-run` is exempt: it can neither write nor cancel.
- **The file is written only when content actually changed.** Re-pricing runs
  unconditionally, so `_merge_row` keeps the original `sync_timestamp` on a no-op
  and `cmd_sync` compares against the rows as loaded. Otherwise contrail-gh would
  commit every single day for nothing.

## Open: does TripIt reuse the calendar UID?

Unknown, and it decides which path a changed itinerary takes. If the UID is
reused, a change is an update. If TripIt mints a new one, the old row looks
cancelled and the new one looks new — a cancelled row plus a fresh row rather
than an edit.

Both are handled and neither loses data, so no decision is needed. It wants a
real before/after `.ics` capture the next time a trip actually moves. The cases
to design against: a same-day time change, a whole trip cancelled, a destination
changed within the week on the same PNR.
