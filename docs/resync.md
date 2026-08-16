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
- `cabin_class_known`, `aircraft_type` and `flight_reason` are only ever filled
  in, never overwritten. A stored value is either a hand edit or the one source
  that reports it, and in both cases it is the only copy; a warning names the row
  when a re-synced flight had a cabin set.
- Disappearing from the feed marks it `cancelled` rather than deleting it. The
  per-cabin figures stay because TIM will never price a past flight again; only
  `emissions_kg_actual` is cleared, which is the single point that drops it out
  of every total. Reappearing restores it.

## The one thing a past row will accept

`resync.backfill()` may fill a **blank** `cabin_class_known`, `aircraft_type` or
`flight_reason`, and add to `also_seen_as`, on a row of any age. Nothing else in
this module touches a departed flight.

It is safe because it isn't what the freeze protects against. The freeze exists
for two reasons — absence from a feed is ambiguous for a past flight, and TIM
will not re-price one — and neither is in play. A back-fill re-prices nothing and
re-fetches nothing. Setting a cabin only changes which of the per-cabin figures
the row *already holds* feeds `emissions_kg_actual`, which is precisely the hand
edit the README documents.

And refusing it would refuse the point. A Flighty export is typically two decades
of flights, essentially all in the past; if the correction only applied to
upcoming ones, adding an export to an existing log would fix nothing.

## One flight, two sources

Identity is `(flight_date, origin, destination)`. The first source to report a
flight owns its row; a second one reporting the same flight never creates a
second row.

- **Within a run**, two records with one identity are collapsed before
  reconciliation, keeping the first. The winner takes any field the loser knew
  and it didn't, and records the loser's key. This is what stops a Flighty export
  that lists a codeshare under both its marketing and operating number from
  counting the flight twice.
- **Against the file**, a record whose key is unknown but whose identity is
  already stored back-fills that row instead of adding one. It is never sent for
  pricing: the row already has its figure.
- **A cancelled row does not claim its identity.** If one source called a flight
  off and another says it flew, the flight that happened deserves a row rather
  than being filed against one that counts for nothing.
- **A row another source still reports is never cancelled**, even if the source
  that owns it went quiet.

Ordering decides ownership, so it is the order sources appear in `sources:`. That
is deterministic and stable, which matters more than which source is "better":
the row is the same flight either way, and `also_seen_as` reaches the rest.

**Not the flight number.** `BA16` can be SYD–SIN and SIN–LHR on one day, in two
different cabins. See [parsing.md](parsing.md).

## Open: a through flight alongside its legs

If a source reports `BA16` as one SYD–LHR segment while another reports the two
legs, nothing matches them and the journey is counted about twice. contrail
detects the chain and prints a warning naming both, then changes nothing.

Deliberately not resolved automatically. Which representation is right depends on
what actually happened, and the legs can carry two cabins that a single through
row cannot express — so an automatic answer would sometimes destroy the better
data. It needs a real capture to settle: **does TripIt emit a through flight as
one VEVENT or two?** Unknown. Until that is answered, a visible clash beats a
confident guess.

The legs are the better data whichever way that lands: TIM prices a leg and
returns nothing at all for a published through route
(see [emissions.md](emissions.md)), so a through row could never be priced
exactly. The cost of the clash is double counting, not a wrong figure.

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
