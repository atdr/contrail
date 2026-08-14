# contrail — working notes

See `STATUS.md` for what's currently in progress.

## Commands

```bash
python3.12 -m venv venv && ./venv/bin/pip install -e ".[dev]"
./venv/bin/pytest -q
./venv/bin/ruff check . && ./venv/bin/ruff format .
TRIPIT_ICAL_URL=tests/fixtures/sample_feed.ics ./venv/bin/contrail sync --dry-run
```

Python 3.10+ is required (`str | None` annotations). On this machine the default
`python3` is 3.7 — use `/usr/local/bin/python3.12` explicitly.

## Architecture

Three protocol seams, each with one implementation today:

| Seam | Protocol | Implementation |
|---|---|---|
| `importers/` | `Importer.fetch(config) -> Iterable[FlightRecord \| UnparsedEvent]` | `tripit_ical` |
| `emissions/` | `EmissionsProvider.compute(flights) -> dict[key, EmissionsResult]` | `tim` |
| `storage/` | `Storage.load() -> list[dict]`, `save(rows)` | `local_csv` |

`cli.py` owns the flow: load config → load storage → collect from every source →
drop anything already stored → price → build rows → `recompute_cumulative` → save.

The dedup key is `f"{source}:{source_id}"` everywhere. Namespacing by source is
what lets two importers write to one CSV without their IDs colliding.

`normalize_rows` lives in `storage/__init__.py`, not on the protocol, so a future
S3/GCS backend inherits the sort-and-derive invariants for free.

## Conventions

- Conventional commits. release-please owns versions, tags, and `CHANGELOG.md` —
  never hand-edit the changelog or the version in `pyproject.toml`.
- `__version__` is read from installed package metadata, so `pyproject.toml` is the
  single source of truth and release-please only edits one file.
- No test may make a real network call. Mock `requests` in both directions.

## Gotchas

- **The regexes in `importers/tripit_ical.py` are ported verbatim from a prototype
  validated against a real TripIt feed.** Don't tidy them without a failing test.
- **`FROM_TO_RE` is compiled with `re.IGNORECASE`**, so its `[A-Z]{3}` groups match
  lowercase too. That's intentional (real feeds are inconsistent) but surprising.
- **TIM returns exact emissions only for flights that haven't departed *and* that
  it knows about.** Verified against a real feed on 2026-08-14: two upcoming
  Iberia flights three weeks out still came back empty and fell back to the route
  average, while two BA long-hauls seven weeks out priced exactly. Not having
  departed is necessary, not sufficient. The fallback isn't a workaround for a
  bug, it's the shape of the API. Don't "fix" it.
- **The TIM key goes in the `x-goog-api-key` header, never the query string.**
  `requests` embeds the full URL in every `HTTPError` it raises, and the README
  suggests piping cron output to a log file.
- **`normalize_rows` re-derives `emissions_kg_actual` on every pass**, which is
  what makes hand-editing the CSV work. `cmd_sync` therefore normalizes and saves
  even when it finds no new flights — but only writes if something actually
  changed, or contrail-gh would produce an empty commit every day.
- **The CSV stores no running total, deliberately.** It's a record of flights,
  not an analysis of them. `total_kg()` computes one for display. Don't add a
  cumulative column back: it goes stale against hand edits, and one backfilled
  flight rewrites every row after it.
- **Every value in a row dict is a string**, so a freshly built row compares equal
  to the same row loaded back from the CSV.
- **`LocalCSVStorage.save` writes to a temp file and `os.replace`s it.** TripIt's
  feed only exposes recent and upcoming trips, so a half-written CSV would lose
  history that cannot be re-fetched.
- **`tripit_ical` accepts a local path or `file://` URL**, which is what lets CI
  run `--dry-run` against the fixture with no network and no mocking.
- **`--dry-run` deliberately doesn't require `TIM_API_KEY`**, for the same reason.
- The default `./flight_emissions.csv` output is gitignored here. This repo is
  public; never commit a real CSV or `config.json`.

## Related repos

- `atdr/contrail-gh` — public GitHub Actions template. Pins a contrail release tag.
- `atdr/my-contrail` — private, created from that template. Real data, real secrets.
