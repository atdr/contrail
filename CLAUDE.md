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

`recompute_cumulative` lives in `storage/__init__.py`, not on the protocol, so a
future S3/GCS backend inherits the sort-and-total invariant for free.

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
- **TIM returns exact emissions only for flights that haven't departed.** The
  route-average fallback isn't a workaround for a bug, it's the documented shape
  of the API. Don't "fix" it.
- **`tripit_ical` accepts a local path or `file://` URL**, which is what lets CI
  run `--dry-run` against the fixture with no network and no mocking.
- **`--dry-run` deliberately doesn't require `TIM_API_KEY`**, for the same reason.
- The default `./flight_emissions.csv` output is gitignored here. This repo is
  public; never commit a real CSV or `config.json`.

## Related repos

- `atdr/contrail-gh` — public GitHub Actions template. Pins a contrail release tag.
- `atdr/my-contrail` — private, created from that template. Real data, real secrets.
