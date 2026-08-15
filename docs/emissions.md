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

## Timing a call buys nothing

TIM serves a **pre-built dataset**, identified by the `+dated` suffix in
`model_version` (`3.0.0+20260814`). A call an hour before departure returns
exactly what a call that morning returns. Scheduling a run just before takeoff to
catch a late aircraft swap is pointless, and GitHub's scheduler is best-effort
anyway. What matters is that a sync lands while the flight is still upcoming.

Observed: `dated` did **not** change between 14 and 15 August, so the rebuild is
periodic rather than daily.

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
