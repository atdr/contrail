# Reading a source

Two importers, and most of what follows is about the messier one. The Flighty
section near the end is short because its format is fixed columns rather than
free text.

## TripIt: the regexes look untidy because real feeds are

`AIRPORT_CODE_RE`, `FROM_TO_RE`, `CODE_PAIR_RE` and `FLIGHT_NO_RE` are validated
against real TripIt feeds and handle their inconsistencies. **Don't rewrite one
without a failing test that proves the current form is wrong** — every oddity in
them is load-bearing for some real event.

`FROM_TO_RE` is compiled with `re.IGNORECASE`, so its `[A-Z]{3}` groups match
lowercase too. Intentional — real feeds are inconsistent — but surprising.

Events are found by TripIt's `[Flight]` marker in DESCRIPTION, with a
regex fallback for other calendar tools. `SUMMARY` is tried alone first (least
chance of a false match) before the noisier SUMMARY + DESCRIPTION + LOCATION blob.

## Dates are local to the origin

`flight_date` is the local date **at the origin airport**, because that is what
TIM's `departureDate` is documented to mean. TripIt states every time in UTC, so
`airports.py` converts using an IATA→IANA mapping (`airportsdata`).

A wrong date costs twice: the row is misdated _and_ the exact lookup misses,
silently downgrading to a route average. This is why `parse()` extracts the
origin **before** computing the date.

`departure_time` is stored alongside, as an instant in the origin's own zone.
All-day events have none; a naive time is RFC 5545 floating, i.e. already local,
so it is left as-is rather than shifted.

`arrival_time` follows the same rule at the destination. TripIt's `DTEND` and
Flighty's scheduled arrival feed it when available. Keeping both instants lets
Passport calculate scheduled gate-to-gate duration across timezones and date
boundaries. A source that states neither arrival nor departure leaves duration
unknown; contrail does not infer it from route distance.

## Codeshares, and why parse() is two-pass

TripIt's DESCRIPTION names the _operating_ flight even when SUMMARY shows only
the marketing one:

```text
SUMMARY:      IB3643 LHR to MAD
DESCRIPTION:  ... British Airways 458, Terminal TERMINAL 5, Gate ...
```

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
2. **The bundled table** (`src/contrail/data/airline_codes.csv`), generated from
   Wikidata by `scripts/refresh_airline_codes.py` and shipped in the wheel. It
   carries `iata,icao,name,aliases`, so the same file answers the ICAO lookup
   Flighty needs.
3. **Wikidata live, property `P229`.** Only for names neither the feed nor the
   table knows. Searching "Iberia" surfaces the Iberian Peninsula first;
   filtering on the presence of a P229 is what disambiguates. Failure is always
   soft — the flight stays priced as booked. `airline_lookup: false` disables
   this step and only this step: it exists to opt out of the network, and the
   table needs none.

The generator drops ambiguity rather than guessing. A name that maps to two IATA
codes — "easyJet" covers U2, EC and DS — is blanked, because resolving it to
whichever sorted first would price a flight confidently against the wrong airline,
which is worse than not resolving it at all.

## Reading a Flighty export

Fixed columns, no free text, so there is nothing to regex. Three things still
need care, all verified against a real export rather than assumed:

- **`Airline` is ICAO** (`BAW`), while TIM wants IATA (`BA`). Hence the shared
  table above. An unresolved code is left as-is and costs an exact figure, not
  the row: the route-average fallback is market-based and needs no carrier code.
- **`Date` is already the local date at the origin**, matching the scheduled gate
  departure on every row of a 261-flight export. No conversion is applied to it.
- **Times are naive local wall-clock** (`2026-09-02T07:50`), with no offset.
  `departure_datetime` attaches the origin's zone, which is what lets the freeze
  boundary be exact to the minute rather than a date comparison. Departure may
  fall back to takeoff or actual time for that boundary. Passport duration is
  stricter: arrival uses only the scheduled gate time, in the destination's
  zone, so it never mixes gate, airborne, scheduled or actual endpoints.

`PRIVATE` appears as a cabin class and is deliberately not mapped. TIM's
per-cabin figures describe a seat on a scheduled airliner and say nothing useful
about a charter, so the honest answer is that the cabin is unknown.

An export is a full-history snapshot rather than a rolling window, so a
re-export repeats everything. Flighty's own UUID keeps each flight's key stable
across exports, which is what makes a re-import idempotent. Multiple export files
are read newest first, by filename, because the CLI keeps the first record it
sees for a key — Flighty names them `FlightyExport-YYYY-MM-DD.csv`, and a repo
checkout gives every file the same mtime.

## One flight number, two legs

`BA16` flies SYD–SIN–LHR: two legs on one day, under one number, and they can be
two different cabins. Flighty exports them as two rows, which is the only
representation that can express that, and contrail keeps them as two rows.

This is why identity is `(flight_date, origin, destination)` and **not** the
flight number. Matching on the number would fold the legs into one row and lose
an emissions figure — and, in the case that motivated the check, a first-class
leg. Two flights on the same route on the same calendar day is a thing one person
cannot do, so route and date are safe.

The reverse case is not resolvable and so is not resolved: a source that reports
the published route SYD–LHR as a single segment describes the same journey as the
legs, and nothing matches them up. contrail detects the chain, warns that the
journey is counted about twice, and changes nothing — see
[resync.md](resync.md).

## Identity

The dedup key is `f"{source}:{source_id}"` everywhere, namespaced so two
importers can write to one CSV without their IDs colliding. TripIt always sets a
UID; other exporters don't, so a UID-less event falls back to a content hash of
DTSTART + SUMMARY. Without it every such event would share the key
`tripit_ical:` and mask all the others, permanently.

A key that parsed is never also queued as unparsed — that would write two rows
under one key and discard the priced flight.

Flighty's `Flight Flighty ID` is a UUID, present and unique on every row of a
real export, so it needs no fallback in practice — but there is one, for the same
reason TripIt has one. It is also the join key: `also_seen_as` carries it onto
whichever row ends up owning the flight, so a contrail row can be joined back to
the seat, PNR and tail number that only the export holds. See the README.

## The test fixture's airport codes are real

JFK, LHR, CDG and FRA, deliberately, so CI exercises the timezone conversion —
plus QQQ/ZZZ which are genuinely unassigned so the fallback is covered too. An
earlier fixture used AAA/BBB/CCC believing them fake; they are Anaa, Benson and
Jardines del Rey, and adopting real timezone handling silently shifted its dates.

`tripit_ical` accepts a local path or `file://` URL, which is what lets CI run
`--dry-run` against the fixture with no network and no mocking. `--dry-run`
deliberately doesn't require `TIM_API_KEY`, for the same reason.
