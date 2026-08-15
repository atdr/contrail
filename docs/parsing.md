# Reading a TripIt feed

## The regexes are ported, not written

`AIRPORT_CODE_RE`, `FROM_TO_RE`, `CODE_PAIR_RE` and `FLIGHT_NO_RE` came verbatim
from a prototype validated against a real TripIt feed. **Don't tidy them without
a failing test.**

`FROM_TO_RE` is compiled with `re.IGNORECASE`, so its `[A-Z]{3}` groups match
lowercase too. Intentional — real feeds are inconsistent — but surprising.

Events are found by TripIt's `[Flight]` marker in DESCRIPTION, with a
regex fallback for other calendar tools. `SUMMARY` is tried alone first (least
chance of a false match) before the noisier SUMMARY + DESCRIPTION + LOCATION blob.

## Dates are local to the origin

`flight_date` is the local date **at the origin airport**, because that is what
TIM's `departureDate` is documented to mean. TripIt states every time in UTC, so
`airports.py` converts using an IATA→IANA mapping (`airportsdata`).

A wrong date costs twice: the row is misdated *and* the exact lookup misses,
silently downgrading to a route average. This is why `parse()` extracts the
origin **before** computing the date.

`departure_time` is stored alongside, as an instant in the origin's own zone.
All-day events have none; a naive time is RFC 5545 floating, i.e. already local,
so it is left as-is rather than shifted.

## Codeshares, and why parse() is two-pass

TripIt's DESCRIPTION names the *operating* flight even when SUMMARY shows only
the marketing one:

    SUMMARY:      IB3643 LHR to MAD
    DESCRIPTION:  ... British Airways 458, Terminal TERMINAL 5, Gate ...

TIM's field is `operatingCarrierCode` and it will only price the operating
flight, so a codeshare priced as booked falls back to a route average — which
overstated the two real Iberia flights by ~18% (118.269 against 97.125 kg).

For a non-codeshare the description simply repeats the marketing flight, so **a
flight-number mismatch is itself the codeshare signal**. No guessing.

Turning "British Airways" into `BA` needs a name→code mapping, and no maintained
PyPI dataset offers one. Two sources, cheapest first:

1. **The feed itself.** A direct segment gives a (name → code) pair for free.
   Ten direct BA segments yield `{"British Airways": "BA"}`, which resolves both
   Iberia codeshares with no network call at all. This is what makes `parse()`
   two-pass: the whole feed has to be read before any record is built.
2. **Wikidata property `P229`.** Only for names the feed never taught. Searching
   "Iberia" surfaces the Iberian Peninsula first; filtering on the presence of a
   P229 is what disambiguates. Failure is always soft — the flight stays priced
   as booked. `airline_lookup: false` disables it.

## Identity

The dedup key is `f"{source}:{source_id}"` everywhere, namespaced so two
importers can write to one CSV without their IDs colliding. TripIt always sets a
UID; other exporters don't, so a UID-less event falls back to a content hash of
DTSTART + SUMMARY. Without it every such event would share the key
`tripit_ical:` and mask all the others, permanently.

A key that parsed is never also queued as unparsed — that would write two rows
under one key and discard the priced flight.

## The test fixture's airport codes are real

JFK, LHR, CDG and FRA, deliberately, so CI exercises the timezone conversion —
plus QQQ/ZZZ which are genuinely unassigned so the fallback is covered too. An
earlier fixture used AAA/BBB/CCC believing them fake; they are Anaa, Benson and
Jardines del Rey, and adopting real timezone handling silently shifted its dates.

`tripit_ical` accepts a local path or `file://` URL, which is what lets CI run
`--dry-run` against the fixture with no network and no mocking. `--dry-run`
deliberately doesn't require `TIM_API_KEY`, for the same reason.
