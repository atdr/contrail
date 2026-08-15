# How emissions are computed

## Exact, or a route average

Every flight that hasn't departed goes to `computeDetailedFlightEmissions`.
Anything that comes back without figures falls to `computeTypicalFlightEmissions`,
a route/market average that works for any date. `emissions_source` records which
answered.

**"Hasn't departed" is necessary but not sufficient.** Verified against a real
feed: two Iberia flights three weeks out came back empty while two BA long-hauls
seven weeks out priced exactly. TIM has to recognise the specific flight. The
fallback is the shape of the API, not a bug to fix.

## The detailed endpoint rejects past dates

`computeDetailedFlightEmissions` enforces the documented "must be a date in the
present or future" with a **400**, where the plain endpoint merely returns an
empty result — and one past flight fails the entire batch. So `compute()` never
asks about a flight that has already departed, and if a batch is refused anyway
it bisects to find the offender, dropping only that one flight to the plain
endpoint. Falling a whole batch back would lose the provenance for every good
flight in it, and provenance cannot be re-fetched later.

## Does timing a call help? Probably not, but it isn't settled

Responses carry a dataset stamp in `model_version` — the `+dated` suffix of
`3.0.0+20260814`. Google documents these as "model datasets recreated with
refreshed input data".

**What is actually observed:** `dated` read `20260814` on both 14 and 15 August,
so the stamp does not move daily.

**What is not:** whether a flight-level input — the assigned aircraft above all —
can change *within* a dataset stamp. If the aircraft is baked in at rebuild, then
a call an hour before departure returns exactly what the morning's call returned
and timing a sync is pointless. If TIM refreshes schedule data more often than it
bumps `dated`, a late run could catch a swap. **This has not been tested**, and
it's the question that decides whether a pre-departure run is worth scheduling.

The raw sidecar is the instrument for settling it: it records each *distinct*
answer per flight, so a figure that moves while `dated` holds steady is exactly
what would show up. Worth checking after a few upcoming flights have been through
several syncs.

Two things hold regardless. Re-pricing on every run until departure already
captures whatever the last pre-departure sync can see. And GitHub's `schedule:`
is static and best-effort — routinely delayed, occasionally dropped — so
minute-precision isn't available there whatever TIM does.

## Open: does TIM price a leg of a multi-leg flight number?

`BA16` flies SYD–SIN–LHR under one number. contrail asks about each leg
separately — `(SYD, SIN, BA, 16, date)` — and whether TIM answers depends on
whether its schedule data is per-leg or keyed on the published route `SYD–LHR`.
**Not yet tested.**

The downside is bounded either way. `computeTypicalFlightEmissions` is
market-based, so a leg always prices to a route average at worst; the question is
only whether an *exact* figure is available for an upcoming one.

To settle it, needs an API key and a future date, since the detailed endpoint
refuses past ones. BA15 (LHR–SIN–SYD) runs daily, so ask for all three of
`(LHR, SIN, BA, 15)`, `(SIN, SYD, BA, 15)` and `(LHR, SYD, BA, 15)` a few weeks
out and see which come back with figures.

## TIM never names the aircraft

Not even from the detailed endpoint. The closest signal is
`fuelBurnEeaStrategy` (stored as `aircraft_match`), which says how well TIM
matched an airframe without saying which. An equipment swap can therefore only
ever be *inferred* from a changed figure.

This is why open rows are re-priced on **every** run, including ones already
priced `exact`: the figure depends on the aircraft, and short-haul equipment
changes right up to departure (A319/A320/A321, ceo against neo).

`is_better()` stops a transient blank from downgrading a good figure, and a
response carrying no figures at all never overwrites one that has them — TIM
returns nothing for a flight it cannot price, and that is exactly the row users
are told to fill in by hand.

A Flighty export *does* name the airframe, and it is stored in `aircraft_type`.
That is a record of what was flown, not an input to any figure: TIM is never told
about it, and it cannot be, since the API takes no aircraft parameter. Its use is
the other direction — a stored airframe beside a moving `exact` figure is
evidence about the equipment-swap question above.

## Keeping everything it returned

TIM refuses to price a departed flight, so whatever is captured while a flight is
upcoming is all there will ever be. Five fields get CSV columns
(`model_version`, `emissions_data_source`, `contrails_impact`, `distance_km`,
`aircraft_match`); the whole response goes to `flight_emissions.raw.jsonl` —
the well-to-tank/tank-to-wake split, load factors, cargo mass fraction,
seat-area ratios, source versions, the calculator permalink.

The sidecar is append-only and records an answer only when it differs from the
last one for that flight. Appending unconditionally would grow the file daily
and commit for no new information. Corrupt lines are skipped on read: it is a
plain append, and every sync reads it back before appending, so raising would
fail every future run.

`raw_log` and `raw_path` are **top-level** config keys, not per-source.

## The API key

Goes in the `x-goog-api-key` header, never the query string. `requests` embeds
the full URL in every `HTTPError` it raises, and the README suggests piping cron
output to a log file.
