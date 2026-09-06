# contrail

Estimate the CO2e emissions of flights you've taken or booked, and keep a running log of them.

contrail pulls flights from one or more _sources_ (a TripIt calendar feed, a Flighty export),
prices each one using Google's [Travel Impact Model](https://travelimpactmodel.org/about-tim)
(TIM) API, and writes the result to a CSV.

- **This repo** is the installable package. All the logic, no secrets, no personal data.
- **[atdr/contrail-gh](https://github.com/atdr/contrail-gh)** is a template repo that runs
  contrail for you on a schedule via GitHub Actions, in your own private repo. If you don't want
  to manage a machine, start there.

## Install

<!-- x-release-please-start-version -->

```bash
pip install contrails==0.4.1
```

<!-- x-release-please-end -->

The distribution is `contrails` — the `contrail` name was already taken on PyPI by an
unrelated project — but the import name and the CLI command are still `contrail`.

Pin an exact version rather than a floor or `main`, so a change here can never surprise a
running instance. Python 3.11 or newer.

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

```text
contrail sync [--config PATH] [--csv-path PATH] [--dry-run]
contrail passport [--config PATH] [--csv-path PATH] [--output PATH] [--open]
contrail sources
```

`--dry-run` fetches and parses flights and prints what _would_ be written, without calling the
emissions API or touching the CSV. Use it when testing a parsing change, or when you'd rather not
spend API calls. It doesn't need `TIM_API_KEY` set.

`contrail sources` lists which importers exist and which are configured.

## Passport

`contrail passport` turns the flight log into a private, interactive emissions
dashboard:

```bash
contrail passport
```

It reads `./flight_emissions.csv` and writes `./passport.html` by default. Use
`--csv-path` to read another log, `--output` to choose another HTML path, and
`--open` to open the result in your default browser after writing it:

```bash
contrail passport --csv-path archive/flights.csv --output reports/passport.html --open
```

The result is one self-contained HTML file. It embeds the flight data and
styles, Chart.js 4.5.1 for the charts, Leaflet 1.9.4 for the map, and stripped
Natural Earth 1:110m country boundaries. It requests no external scripts or map
tiles, so it works offline and makes no network requests. That also means the
HTML contains your itinerary. Keep it private: `passport.html` is gitignored by
default, and a custom output path needs the same care.

Passport compares all time with individual years, and shows total CO2e, CO2e
per kilometre and CO2e per scheduled block hour alongside the flights and
routes that drove them. The annual trajectory and the month or weekday carbon
patterns can each be split by cabin or reason. Airport and country connection
counts appear in the overview, their CO2e contributions are ranked in the
patterns section, and the lightest and heaviest individual flights are compared
by total, per-kilometre and per-hour impact. Contributor rankings can use those
same three measures and can be broken down by cabin or reason. The offline route
map can be panned and zoomed.

Distance is calculated consistently for every flight as the Haversine
great-circle distance between the bundled airport coordinates. It is labelled
as an estimated route distance and does not replace or modify TIM's
`distance_km`. Scheduled block duration is calculated from the stored departure
and arrival instants where both are available. Flights without a reported cabin
are shown as Economy in charts. The estimate quality section discloses those
assumptions and partitions the completed flights by emissions source, distance,
duration, cabin and reason availability.

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
whether TIM can refresh a flight's aircraft _within_ a stamp hasn't been tested. Either way,
re-pricing on every run already captures whatever the last sync before departure can see.

**So run it regularly.** A daily sync gives each flight the most chances to be priced exactly
while it is still upcoming. Flights first discovered _after_ they've flown — an initial backfill
of your history, say — get the route average instead.

A row is re-priced on every run until it departs, so a flight TIM didn't recognise the day it was
first seen can still improve later. Once it has departed the figure is frozen, because TIM will not
price a past flight again. In practice a long-haul on a major carrier tends to resolve to `exact`;
a codeshare or a flight several weeks out may not.

## Configuration

Resolution order, highest priority first:

1. CLI flags (`--csv-path`, `--config`)
2. Environment variables (`TRIPIT_ICAL_URL`, `FLIGHTY_CSV_PATH`, `TIM_API_KEY`, `CSV_PATH`)
3. `config.json` or `config.yaml` in the current directory
4. Built-in defaults

Environment variables come before the file because that's what every deployment target injects:
GitHub Actions secrets, a cron environment, and Lambda environment variables all arrive that way.

Copy one of the shipped examples and edit it:

```bash
cp config.example.json config.json      # no extra dependencies
cp config.example.yaml config.yaml      # commented; needs pip install "contrails[yaml]"
```

Both are gitignored once renamed. A config file also lets you run several sources in one sync:

```json
{
  "csv_path": "flight_emissions.csv",
  "sources": [
    { "type": "tripit_ical", "url": "https://www.tripit.com/feed/ical/private/.../tripit.ics" },
    { "type": "flighty_csv", "path": "flighty/" }
  ],
  "emissions": { "provider": "tim", "api_key": "..." }
}
```

`config.yaml` works identically but needs PyYAML: `pip install "contrails[yaml]"`.

Each entry in `sources` is passed to its importer as-is, so importers are free to define whatever
shape they need (a URL, OAuth credentials, a file path) without the schema having to anticipate
it.

**Never commit `config.json`.** It's in this repo's `.gitignore` for that reason.

## CSV columns

| Column                                                               | Meaning                                                                                                    |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `sync_timestamp`                                                     | When the row was added                                                                                     |
| `source`                                                             | Importer id, e.g. `tripit_ical`                                                                            |
| `source_id`                                                          | Importer-specific id. `source:source_id` is the dedup key                                                  |
| `also_seen_as`                                                       | Other sources' keys for this same flight, space-separated. See "Joining to a Flighty export"               |
| `flight_date`                                                        | Departure date                                                                                             |
| `carrier_code` / `flight_number`                                     | e.g. `BA` / `896` — as booked (the marketing flight)                                                       |
| `operating_carrier_code` / `operating_flight_number`                 | Who actually flies it. Differs from the above on a codeshare, and is what gets priced                      |
| `origin` / `destination`                                             | IATA airport codes                                                                                         |
| `departure_time`                                                     | Departure as an instant, in the origin's own timezone. Blank for all-day events                            |
| `arrival_time`                                                       | Arrival as an instant, in the destination's own timezone. Blank when the source does not state one         |
| `status`                                                             | Blank normally; `cancelled` if an upcoming flight vanished from the feed                                   |
| `cabin_class_known`                                                  | The cabin flown, if a source reported one, else blank                                                      |
| `aircraft_type`                                                      | The airframe, as the source names it. TIM never names one                                                  |
| `flight_reason`                                                      | `business` or `leisure`, if the source says                                                                |
| `emissions_source`                                                   | `exact`, `typical_route_average`, `unparsed`, or `no_data`                                                 |
| `model_version`                                                      | Full TIM version, e.g. `3.0.0+20260814`. The `+dated` part identifies the dataset that produced the figure |
| `emissions_data_source`                                              | `TIM` or `EASA`                                                                                            |
| `contrails_impact`                                                   | TIM's contrails warming bucket: `negligible`, `moderate` or `severe`                                       |
| `distance_km`                                                        | Distance TIM used for its calculation; distinct from Passport's estimated great-circle route distance      |
| `aircraft_match`                                                     | How well TIM matched an airframe. It never names the aircraft                                              |
| `emissions_kg_first` / `_business` / `_premium_economy` / `_economy` | Per-passenger CO2e by cabin, in kg                                                                         |
| `emissions_kg_actual`                                                | The cabin actually flown if known, else economy — **the per-flight figure to sum**                         |
| `raw_summary`                                                        | Original source text, useful for `unparsed` rows                                                           |

### What a sync may change

**A flight that hasn't departed is contrail's to correct. One that has is left
alone.** Concretely, while `flight_date` is today or later:

- Its date, route, times and flight numbers are updated if TripIt now says
  something different — a retimed flight, or a destination changed on the same
  booking. A source that states no `arrival_time` leaves the stored one alone
  rather than blanking it, since it may have come from a source that does.
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
- `cabin_class_known`, `aircraft_type` and `flight_reason` are filled in when
  blank and never overwritten. A stored value is either your own edit or the one
  source that reports it, so it is the only copy.

From the day after departure a row is frozen, and only your own edits change it
— with one exception. If a _second_ source turns out to know a flight already in
the log, it may fill in a blank `arrival_time`, `cabin_class_known`,
`aircraft_type` or `flight_reason`, and record its own key in `also_seen_as`.
That re-prices nothing and re-fetches nothing: arrival only makes scheduled
duration available, while setting the cabin decides which of the per-cabin
figures the row already holds counts as its actual emissions. It is what lets a
log built from TripIt over months have its past rows corrected the day you first
point contrail at a Flighty export.
That boundary is also what makes cancellation safe to infer at all: TripIt's
feed only carries recent and upcoming trips, so a _past_ flight leaving it just
means it aged out, while a _future_ one leaving it genuinely means something.

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

`emissions_kg_actual` falls back to economy whenever no source knows the cabin. TripIt's feed
never does; a Flighty export does, which is what the `flighty_csv` importer is for.

## Importers

Two ship today:

- **`tripit_ical`** — reads a TripIt calendar feed. Finds events tagged `[Flight]` in the
  description (TripIt's own marker), with a regex fallback for other calendar tools. Extracts the
  carrier, flight number, and airports from `SUMMARY` first, then `DESCRIPTION` + `LOCATION`.
- **`flighty_csv`** — reads a CSV exported from the [Flighty](https://flighty.com) app. The only
  source that reports **the cabin you actually flew**, which on a long-haul business seat is
  roughly a fourfold difference against the economy assumption. It also carries your whole flight
  history, where TripIt's feed only exposes recent and upcoming trips.

### Using a Flighty export

Export from the app (Settings → export), which emails you a CSV. Then either:

```bash
# One-off: price the lot and write the log
FLIGHTY_CSV_PATH=~/Downloads/FlightyExport-2026-08-15.csv contrail sync

# Or keep exports in a directory and re-export whenever you like
FLIGHTY_CSV_PATH=flighty/ contrail sync
```

`path` takes a file, a directory, or a glob. A directory is the useful shape: drop each new export
in and the newest wins, since Flighty names them `FlightyExport-YYYY-MM-DD.csv` and contrail reads
them newest-first. Re-importing the same flight is free — Flighty's own id keeps its key stable, so
nothing is re-priced and nothing is rewritten.

**An export is your entire flight history in one file.** Keep it out of any public repository.

Two details worth knowing:

- Flighty names airlines by ICAO code (`BAW`); TIM wants IATA (`BA`). A table shipped with contrail
  does that offline, falling back to [Wikidata](https://www.wikidata.org) for anything it doesn't
  know. `"airline_lookup": false` disables only the network fallback.
- `PRIVATE` isn't a cabin contrail records. TIM's per-cabin figures describe a seat on a scheduled
  airliner and say nothing useful about a charter, so those rows are left blank and fall back to
  economy — worth correcting by hand if you have a better number.

### Running more than one source

Sources overlap. Flighty and TripIt will both know your upcoming trips, so contrail matches them
up rather than logging the flight twice.

- **A flight is identified by its date, origin and destination** — not its flight number. `BA16`
  can be SYD–SIN and SIN–LHR on the same day, flown in two different cabins, and those are two
  flights, two figures and two rows.
- **The first source listed owns the row.** A second source reporting the same flight never adds a
  row and is never priced; it fills in blanks and leaves its key in `also_seen_as`.
- If one source lists a flight twice — a codeshare entered under both its marketing and operating
  number, say — the same matching collapses it, and says so.
- If one source reports a journey as a single through segment while another reports its legs,
  contrail **can't** tell which is right, warns that the journey is being counted twice, and leaves
  both. Delete whichever you don't want.

### Joining to a Flighty export

contrail stores what it needs to price a flight and no more, so seat, PNR, tail number and
terminals stay in the export. `also_seen_as` is what joins the two back together — whichever
source ended up owning a row, its Flighty id is in either `source_id` or `also_seen_as`:

```sql
-- DuckDB: emissions by seat, cabin and airframe
SELECT c.flight_date, c.carrier_code || c.flight_number AS flight,
       c.cabin_class_known, c.emissions_kg_actual,
       f.Seat, f."Seat Type", f."Tail Number"
FROM 'flight_emissions.csv' c
JOIN 'FlightyExport-2026-08-15.csv' f
  ON f."Flight Flighty ID" = regexp_extract(
       c.source || ':' || c.source_id || ' ' || c.also_seen_as,
       'flighty_csv:([0-9a-f-]{36})', 1)
ORDER BY c.flight_date;
```

```python
# pandas, same idea
import pandas as pd

log = pd.read_csv("flight_emissions.csv", keep_default_na=False)
export = pd.read_csv("FlightyExport-2026-08-15.csv", keep_default_na=False)

keys = log["source"] + ":" + log["source_id"] + " " + log["also_seen_as"]
log["flighty_id"] = keys.str.extract(r"flighty_csv:([0-9a-f-]{36})")

joined = log.merge(export, left_on="flighty_id", right_on="Flight Flighty ID")
```

`BA16` is the example that shows why this is worth having: its two legs join to seat 13K in
business and seat 1A in first, against two separate emissions figures.

A collapsed codeshare puts one Flighty id in `source_id` and the other in `also_seen_as`. Both
describe the same physical flight, so either joins to the same seat and PNR — taking the first
match is correct.

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
prices that instead. Turning "British Airways" into `BA` uses three sources, cheapest first:

1. **The feed itself.** On a direct flight the description restates the flight already in the
   summary, which gives an airline-name-to-code pair for free. If you fly a carrier directly and
   also hold a codeshare it operates, this resolves with no network call at all.
2. **A table shipped with contrail** (`src/contrail/data/airline_codes.csv`), generated from
   [Wikidata](https://www.wikidata.org) and covering around 2,200 airlines by name, alias and ICAO
   code. Offline, so it costs nothing to consult. Regenerate it with
   `./venv/bin/python scripts/refresh_airline_codes.py`.
3. **Wikidata live** (property `P229`, the IATA airline designator), for names neither the feed nor
   the table knows. Set `"airline_lookup": false` on a source to switch **this step** off — the
   shipped table needs no network and keeps working. An unresolved airline just leaves the flight
   priced as booked, exactly as before.

The same table answers the ICAO codes a Flighty export uses, which is why it carries both.
Wikidata is CC0.

Adding another means writing one module implementing the `Importer` protocol
(`src/contrail/importers/base.py`) and adding one line to the registry in
`src/contrail/importers/__init__.py`. Nothing else changes.

One more is anticipated but not built:

- **`tripit_api`** — TripIt's official API instead of the calendar feed. Needs OAuth, which the
  per-source config dict can already express.

## Running it on a schedule

### GitHub Actions (recommended if you don't want to manage a machine)

Use the **[atdr/contrail-gh](https://github.com/atdr/contrail-gh)** template. Click "Use this
template", make your new repo **private**, add `TRIPIT_ICAL_URL` and `TIM_API_KEY` as Actions
secrets, and it syncs daily and commits the updated CSV back to your own repo.

### Raspberry Pi, VPS, or any host with cron

<!-- x-release-please-start-version -->

```bash
python3 -m venv ~/contrail-venv
~/contrail-venv/bin/pip install contrails==0.4.1
crontab -e
```

<!-- x-release-please-end -->

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow. Quick start:

```bash
./venv/bin/pytest              # no test makes a real network call
./venv/bin/ruff check .
./venv/bin/ruff format .
TRIPIT_ICAL_URL=tests/fixtures/sample_feed.ics \
FLIGHTY_CSV_PATH=tests/fixtures/sample_flighty.csv \
  ./venv/bin/contrail sync --dry-run
```

That last one is also run in CI: both importers accept a local file path, so a broken parser is
caught here before it can reach anyone's production instance through a version tag. Running both
also exercises the matching between them, which is the part with no single source of truth.

`./venv/bin/python scripts/refresh_airline_codes.py` regenerates the bundled airline table from
Wikidata. Run it from the dev venv — it imports contrail for its version. It needs network, so CI
never runs it — do it by hand when the table looks stale, and commit the result. Don't hand-edit the
CSV: fix it upstream in Wikidata and re-run, or the next refresh silently reverts you.

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
- A row that has departed is never re-fetched or re-priced, so historical figures never shift
  under you. Rows that haven't departed are corrected and re-priced on every run, since a flight
  can be retimed, rerouted, or flown by different equipment right up to departure.
- Flight detection leans on TripIt's `[Flight]` description tag, with a regex fallback for other
  calendar tools. Anything it can't confidently parse is written as an `unparsed` row rather than
  guessed at.
- Matching a flight across sources uses its date, origin and destination. Two flights on the same
  route on the same day would be treated as one — not something one person can do, but worth
  knowing it is the assumption.
- A journey reported as a single through segment by one source and as separate legs by another is
  counted twice. contrail says so and leaves both, because the legs may have been flown in
  different cabins, which one row can't express.
- Flighty's `PRIVATE` cabin is recorded as unknown, so those rows fall back to economy. TIM's
  per-cabin figures describe a seat on a scheduled airliner; a charter is a different question
  contrail can't answer.

## License

MIT — see [LICENSE](LICENSE).

<!-- This file is wrapped at 100, not the 80 the rest of the repo uses. -->
<!-- markdownlint-configure-file {
  "MD013": { "line_length": 100, "tables": false, "code_blocks": false }
} -->
