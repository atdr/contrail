# contrail

Estimate the CO2e emissions of flights you've taken or booked, and keep a running log with a
cumulative total.

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
   numbers **only for flights that haven't departed yet**. This is a property of Google's API,
   not a limitation of contrail.
2. Anything that comes back empty (i.e. the flight already happened) falls back to
   `computeTypicalFlightEmissions`, a route/market average that works for any date.
3. The `emissions_source` column records which method produced each row.

**So run it regularly.** A daily sync locks in the exact figure for each flight while it's still
upcoming. Flights first discovered *after* they've flown — an initial backfill of your history,
say — get the route average instead, permanently.

## Configuration

Resolution order, highest priority first:

1. CLI flags (`--csv-path`, `--config`)
2. Environment variables (`TRIPIT_ICAL_URL`, `TIM_API_KEY`, `CSV_PATH`)
3. `config.json` or `config.yaml` in the current directory
4. Built-in defaults

Environment variables come before the file because that's what every deployment target injects:
GitHub Actions secrets, a cron environment, and Lambda environment variables all arrive that way.

A config file lets you run several sources in one sync:

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
| `carrier_code` / `flight_number` | e.g. `BA` / `896` |
| `origin` / `destination` | IATA airport codes |
| `cabin_class_known` | The cabin flown, if the source reported one, else blank |
| `emissions_source` | `exact`, `typical_route_average`, `unparsed`, or `no_data` |
| `model_version` | TIM model version (only set on `exact` rows) |
| `emissions_kg_first` / `_business` / `_premium_economy` / `_economy` | Per-passenger CO2e by cabin, in kg |
| `emissions_kg_actual` | The cabin actually flown if known, else economy |
| `cumulative_kg_actual` | Running total of the above, sorted by flight date — **the headline number** |
| `raw_summary` | Original source text, useful for `unparsed` rows |

`unparsed` rows are events that looked like a flight but couldn't be confidently parsed. They're
rare. Check `raw_summary`, and fill the emissions in by hand if you want them counted — the
cumulative total picks them up on the next sync.

The cumulative total is based on `emissions_kg_actual`, which falls back to economy whenever the
source doesn't know the cabin. TripIt's feed never does; a future Flighty importer will.

## Importers

v1 ships one:

- **`tripit_ical`** — reads a TripIt calendar feed. Finds events tagged `[Flight]` in the
  description (TripIt's own marker), with a regex fallback for other calendar tools. Extracts the
  carrier, flight number, and airports from `SUMMARY` first, then `DESCRIPTION` + `LOCATION`.

Adding another means writing one module implementing the `Importer` protocol
(`src/contrail/importers/base.py`) and adding one line to the registry in
`src/contrail/importers/__init__.py`. Nothing else changes.

Two are anticipated but not built:

- **`tripit_api`** — TripIt's official API instead of the calendar feed. Needs OAuth, which the
  per-source config dict can already express.
- **`flighty_csv`** — a CSV exported from the Flighty app. Flighty exports include the cabin
  actually flown, so this one would populate `cabin_class` and make the headline cumulative total
  reflect reality instead of assuming economy.

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
- Departure date comes from the calendar event's start time. For flights leaving very close to
  local midnight, this can occasionally land a day either side of the true local date.
- Flight detection leans on TripIt's `[Flight]` description tag, with a regex fallback for other
  calendar tools. Anything it can't confidently parse is written as an `unparsed` row rather than
  guessed at.

## License

MIT — see [LICENSE](LICENSE).
