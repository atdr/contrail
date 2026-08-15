# contrail

Estimate the CO2e emissions of flights you've taken or booked, and keep a running log of them.

contrail pulls flights from one or more *sources* (a TripIt calendar feed today, more later),
prices each one using Google's [Travel Impact Model](https://travelimpactmodel.org/about-tim)
(TIM) API, and writes the result to a CSV.

- **This repo** is the installable package. All the logic, no secrets, no personal data.
- **[atdr/contrail-gh](https://github.com/atdr/contrail-gh)** is a template repo that runs
  contrail for you on a schedule via GitHub Actions, in your own private repo. If you don't want
  to manage a machine, start there.

## Install

```bash
pip install "contrail @ git+https://github.com/atdr/contrail.git@v0.1.0"
```

Pin a tag rather than tracking `main`, so a change here can never surprise a running instance.
Python 3.10 or newer.

For local development:

```bash
git clone https://github.com/atdr/contrail.git && cd contrail
python3 -m venv venv && ./venv/bin/pip install -e ".[dev]"
```

## Quickstart

You need two things:

1. **A TripIt calendar feed URL.** In TripIt: profile icon → Settings → enable Calendar Sync if
   needed → Calendar Feed → copy the URL. **Treat this as a secret** — anyone holding it can read
   your itineraries.
2. **A Travel Impact Model API key** (free, no billing required). At
   [console.cloud.google.com](https://console.cloud.google.com): create or pick a project →
   APIs & Services → Library → search "Travel Impact Model API" → Enable → Credentials → Create
   Credentials → API key. Restricting the key to just that API is recommended.

Then:

```bash
export TRIPIT_ICAL_URL="https://www.tripit.com/feed/ical/private/.../tripit.ics"
export TIM_API_KEY="..."
contrail sync
```

That writes `./flight_emissions.csv` in the current directory. Running it again only adds flights
it hasn't seen before — nothing is ever double-counted or re-priced.

```
contrail sync [--config PATH] [--csv-path PATH] [--dry-run]
contrail sources
```

`--dry-run` fetches and parses flights and prints what *would* be written, without calling the
emissions API or touching the CSV. Use it when testing a parsing change, or when you'd rather not
spend API calls. It doesn't need `TIM_API_KEY` set.

`contrail sources` lists which importers exist and which are configured.

## How emissions are computed

contrail uses a hybrid approach, because of how the TIM API behaves:

1. Every new flight goes into `computeFlightEmissions` first. That returns real, flight-specific
   numbers **only for flights that haven't departed yet, and only for flights TIM actually knows
   about**. Not having departed is necessary but not sufficient: an upcoming flight outside TIM's
   schedule coverage comes back empty too. This is a property of Google's API, not a limitation
   of contrail.
2. Anything that comes back empty falls back to `computeTypicalFlightEmissions`, a route/market
   average that works for any date.
3. The `emissions_source` column records which method produced each row.

Responses carry a dataset stamp in `model_version` (the `+dated` part). It doesn't move daily,
which suggests a call shortly before departure returns what the morning's call returned — though
whether TIM can refresh a flight's aircraft *within* a stamp hasn't been tested. Either way,
re-pricing on every run already captures whatever the last sync before departure can see.

**So run it regularly.** A daily sync gives each flight the most chances to be priced exactly
while it is still upcoming. Flights first discovered *after* they've flown — an initial backfill
of your history, say — get the route average instead.

Note that a row is priced once and never re-priced, so a flight that TIM didn't recognise on the
day it was first seen keeps its route average even if TIM would recognise it later. In practice a
long-haul on a major carrier tends to resolve to `exact`; a codeshare or a flight several weeks
out may not.

## Configuration

Resolution order, highest priority first:

1. CLI flags (`--csv-path`, `--config`)
2. Environment variables (`TRIPIT_ICAL_URL`, `TIM_API_KEY`, `CSV_PATH`)
3. `config.json` or `config.yaml` in the current directory
4. Built-in defaults

Environment variables come before the file because that's what every deployment target injects:
GitHub Actions secrets, a cron environment, and Lambda environment variables all arrive that way.

Copy one of the shipped examples and edit it:

```bash
cp config.example.json config.json      # no extra dependencies
cp config.example.yaml config.yaml      # commented; needs pip install "contrail[yaml]"
```

Both are gitignored once renamed. A config file also lets you run several sources in one sync:

```json
{
  "csv_path": "flight_emissions.csv",
  "sources": [
    { "type": "tripit_ical", "url": "https://www.tripit.com/feed/ical/private/.../tripit.ics" }
  ],
  "emissions": { "provider": "tim", "api_key": "..." }
}
```

`config.yaml` works identically but needs PyYAML: `pip install "contrail[yaml]"`.

Each entry in `sources` is passed to its importer as-is, so importers are free to define whatever
shape they need (a URL, OAuth credentials, a file path) without the schema having to anticipate
it.

**Never commit `config.json`.** It's in this repo's `.gitignore` for that reason.

## CSV columns

| Column | Meaning |
|---|---|
| `sync_timestamp` | When the row was added |
| `source` | Importer id, e.g. `tripit_ical` |
| `source_id` | Importer-specific id. `source:source_id` is the dedup key |
| `flight_date` | Departure date |
| `carrier_code` / `flight_number` | e.g. `BA` / `896` — as booked (the marketing flight) |
| `operating_carrier_code` / `operating_flight_number` | Who actually flies it. Differs from the above on a codeshare, and is what gets priced |
| `origin` / `destination` | IATA airport codes |
| `departure_time` | Departure as an instant, in the origin's own timezone. Blank for all-day events |
| `status` | Blank normally; `cancelled` if an upcoming flight vanished from the feed |
| `cabin_class_known` | The cabin flown, if the source reported one, else blank |
| `emissions_source` | `exact`, `typical_route_average`, `unparsed`, or `no_data` |
| `model_version` | Full TIM version, e.g. `3.0.0+20260814`. The `+dated` part identifies the dataset that produced the figure |
| `emissions_data_source` | `TIM` or `EASA` |
| `contrails_impact` | TIM's contrails warming bucket: `negligible`, `moderate` or `severe` |
| `distance_km` | Distance TIM used for the calculation |
| `aircraft_match` | How well TIM matched an airframe. It never names the aircraft |
| `emissions_kg_first` / `_business` / `_premium_economy` / `_economy` | Per-passenger CO2e by cabin, in kg |
| `emissions_kg_actual` | The cabin actually flown if known, else economy — **the per-flight figure to sum** |
| `raw_summary` | Original source text, useful for `unparsed` rows |

### What a sync may change

**A flight that hasn't departed is contrail's to correct. One that has is left
alone.** Concretely, while `flight_date` is today or later:

- Its date, route and flight numbers are updated if TripIt now says something
  different — a retimed flight, or a destination changed on the same booking.
- It is **re-priced on every run**, even if it already has an `exact` figure.
  That figure depends on the aircraft, and short-haul equipment changes right up
  to departure (A319/A320/A321, ceo against neo), so a number from three weeks
  ago can be stale on the day. A worse answer is never accepted for an unchanged
  flight, so a momentary API blank can't undo a good figure.
- If it disappears from the feed it is marked `cancelled` rather than deleted,
  keeping its per-cabin figures — TIM will never price a past flight again, so a
  mistaken cancellation would otherwise destroy them permanently. Only
  `emissions_kg_actual` is cleared, which is what drops it out of any total. If
  it reappears, it is restored automatically.
- `cabin_class_known` is never overwritten, because no source can supply it.

From the day after departure a row is frozen, and only your own edits change it.
That boundary is also what makes cancellation safe to infer at all: TripIt's
feed only carries recent and upcoming trips, so a *past* flight leaving it just
means it aged out, while a *future* one leaving it genuinely means something.

If the feed returns no flights whatsoever, contrail refuses to cancel anything
and exits non-zero, on the grounds that an empty feed is far more likely to be
broken than to mean every trip was called off.

**There is deliberately no cumulative column.** The CSV is a record of flights; totalling it is
the job of whatever reads it. `contrail sync` prints the current total, and one line of your tool
of choice gets it from the file:

```bash
awk -F, 'NR==1{for(i=1;i<=NF;i++)if($i=="emissions_kg_actual")c=i;next} $c!=""{t+=$c} END{printf "%.1f kg CO2e\n",t}' flight_emissions.csv
```

(It finds the column by name rather than by position, so it keeps working when
the schema gains a column.)

A stored running total would go stale the moment you hand-edited a row, and would rewrite every
later row whenever an older flight was backfilled — noisy in a file that lives in git.

`unparsed` rows are events that looked like a flight but couldn't be confidently parsed. They're
rare. Check `raw_summary`, and fill the emissions in by hand if you want them counted — the figure
counts from the next sync. The same goes for correcting `cabin_class_known` on a row:
`emissions_kg_actual` is re-derived from the per-cabin columns on every run, not frozen when the
row was first written.

Columns you add yourself (a `notes` column, say) are preserved across syncs. Rows are kept sorted
by flight date.

`emissions_kg_actual` falls back to economy whenever the source doesn't know the cabin. TripIt's
feed never does; a future Flighty importer will.

## Importers

v1 ships one:

- **`tripit_ical`** — reads a TripIt calendar feed. Finds events tagged `[Flight]` in the
  description (TripIt's own marker), with a regex fallback for other calendar tools. Extracts the
  carrier, flight number, and airports from `SUMMARY` first, then `DESCRIPTION` + `LOCATION`.

### Everything else TIM said

Alongside the CSV, contrail appends each provider response in full to
`flight_emissions.raw.jsonl` — the well-to-tank/tank-to-wake split, load factors,
cargo mass fraction, seat-area ratios, source versions, the calculator permalink.

This exists because **TIM cannot be asked twice.** It refuses to price a flight
that has departed, so anything not captured while the flight was upcoming is
gone permanently. The sidecar is append-only and records an answer only when it
differs from the last one for that flight, so it accumulates the history of what
changed rather than a copy per run.

Both are top-level config keys: `"raw_log": false` switches it off, `"raw_path"` moves
it. (`RAW_LOG` and `RAW_PATH` work as environment variables too.)

### Codeshares

A ticket often shows a marketing flight that someone else actually operates — `IB3643` really
being `BA458`. TIM's field is `operatingCarrierCode` and it will only price the operating flight,
so a codeshare priced as booked silently falls back to a route average, typically overstating it.

TripIt names the operating flight in the event description, so contrail reads it from there and
prices that instead. Turning "British Airways" into `BA` uses two sources, cheapest first:

1. **The feed itself.** On a direct flight the description restates the flight already in the
   summary, which gives an airline-name-to-code pair for free. If you fly a carrier directly and
   also hold a codeshare it operates, this resolves with no network call at all.
2. **[Wikidata](https://www.wikidata.org)** (property `P229`, the IATA airline designator), for
   names the feed never taught us. Set `"airline_lookup": false` on a source to switch this off;
   an unresolved airline just leaves the flight priced as booked, exactly as before.

Adding another means writing one module implementing the `Importer` protocol
(`src/contrail/importers/base.py`) and adding one line to the registry in
`src/contrail/importers/__init__.py`. Nothing else changes.

Two are anticipated but not built:

- **`tripit_api`** — TripIt's official API instead of the calendar feed. Needs OAuth, which the
  per-source config dict can already express.
- **`flighty_csv`** — a CSV exported from the Flighty app. Flighty exports include the cabin
  actually flown, so this one would populate `cabin_class` and make `emissions_kg_actual` reflect
  reality instead of assuming economy.

## Running it on a schedule

### GitHub Actions (recommended if you don't want to manage a machine)

Use the **[atdr/contrail-gh](https://github.com/atdr/contrail-gh)** template. Click "Use this
template", make your new repo **private**, add `TRIPIT_ICAL_URL` and `TIM_API_KEY` as Actions
secrets, and it syncs daily and commits the updated CSV back to your own repo.

### Raspberry Pi, VPS, or any host with cron

```bash
python3 -m venv ~/contrail-venv
~/contrail-venv/bin/pip install "contrail @ git+https://github.com/atdr/contrail.git@v0.1.0"
crontab -e
```

```cron
0 6 * * * TRIPIT_ICAL_URL="..." TIM_API_KEY="..." ~/contrail-venv/bin/contrail sync --csv-path ~/flight_emissions.csv >> ~/contrail.log 2>&1
```

A `config.json` sitting next to the CSV works instead of inline environment variables, and keeps
your secrets out of the crontab.

**Raspberry Pi OS (Bookworm and newer) blocks a bare `pip install`** under
[PEP 668](https://peps.python.org/pep-0668/). Use a venv as above, or
`pip install --break-system-packages` if you know what you're trading away.

Fly.io, Render, and similar cron jobs work exactly the same way: install the package, run it on a
schedule.

### AWS Lambda + EventBridge Scheduler — not built

The shape: package contrail as a Lambda with a handler calling the same sync path the CLI uses,
and point an EventBridge schedule at it. **The missing piece is storage.** Lambda is stateless,
so `LocalCSVStorage` has nothing durable to write to — this needs an S3-backed implementation of
the `Storage` protocol (`src/contrail/storage/base.py`, two methods: `load` and `save`). That
class doesn't exist yet. The seam is there deliberately, but treat this as a project, not a
supported deployment.

### Google Cloud Functions + Cloud Scheduler — not built

Same shape and the same missing piece, with GCS instead of S3. Worth mentioning because anyone
using contrail already has a Google Cloud project for the TIM API key.

## Development

```bash
./venv/bin/pytest              # no test makes a real network call
./venv/bin/ruff check .
./venv/bin/ruff format .
TRIPIT_ICAL_URL=tests/fixtures/sample_feed.ics ./venv/bin/contrail sync --dry-run
```

That last one is also run in CI: `tripit_ical` accepts a local file path or `file://` URL as well
as an http(s) URL, so a broken parser is caught here before it can reach anyone's production
instance through a version tag.

Commits follow [Conventional Commits](https://www.conventionalcommits.org/); releases and the
changelog are handled by release-please.

## Limitations

- The TIM API only returns exact emissions for flights that haven't departed yet. Hence the
  route-average fallback. See "How emissions are computed" above.
- Departure date is the **local date at the origin airport**, which is what TIM asks for. Feeds
  that state times in UTC (TripIt's does) are converted using the origin's timezone, so an evening
  departure from the US is recorded on the day the traveller would say, not the following one.
  Airports contrail can't identify fall back to the date exactly as the feed gave it. The same
  applies to the point a row freezes: it is the moment of departure in the origin's timezone,
  not a UTC date, which would be a day out for part of every day.
- An existing row is never re-fetched or re-priced, only skipped, so historical figures never
  shift under you. The flip side: if you rebook a trip, TripIt reuses the calendar UID and
  contrail keeps the original date, route, and emissions. Delete the row to have it re-imported.
- Flight detection leans on TripIt's `[Flight]` description tag, with a regex fallback for other
  calendar tools. Anything it can't confidently parse is written as an `unparsed` row rather than
  guessed at.

## License

MIT — see [LICENSE](LICENSE).
