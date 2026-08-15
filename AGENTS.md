# contrail

Estimates the CO2e of flights taken or booked and keeps a log. Reads a TripIt
iCal feed, prices each flight with Google's Travel Impact Model, writes a CSV.

## Commands

```bash
python3.12 -m venv venv && ./venv/bin/pip install -e ".[dev]"
./venv/bin/pytest -q
./venv/bin/ruff check . && ./venv/bin/ruff format .
TRIPIT_ICAL_URL=tests/fixtures/sample_feed.ics ./venv/bin/contrail sync --dry-run
```

Python 3.10+ (`str | None` annotations). The default `python3` on this machine is
3.7 — use `/usr/local/bin/python3.12` explicitly.

## Architecture

Three protocol seams, one implementation each today:

| Seam | Protocol | Implementation |
|---|---|---|
| `importers/` | `Importer.fetch(config) -> Iterable[FlightRecord \| UnparsedEvent]` | `tripit_ical` |
| `emissions/` | `EmissionsProvider.compute(flights, now) -> dict[key, EmissionsResult]` | `tim` |
| `storage/` | `Storage.load() -> list[dict]`, `save(rows)` | `local_csv` |

`cli.py` owns the flow: load config → load storage → collect from every source →
reconcile against what's stored → price → build rows → normalize → save.

The dedup key is `f"{source}:{source_id}"` everywhere.

## Conventions

- Conventional commits. release-please owns versions, tags and `CHANGELOG.md` —
  never hand-edit the changelog or the version in `pyproject.toml`.
- `__version__` is read from installed package metadata, so `pyproject.toml` is
  the single source of truth and release-please edits one file.
- **No test may make a real network call.** Mock `requests` in both directions.
- This repo is public. Never commit a real CSV, a raw log, or `config.json` —
  all are gitignored.

## Depth

- [docs/parsing.md](docs/parsing.md) — the ported regexes, local dates,
  codeshares, why `parse()` is two-pass
- [docs/emissions.md](docs/emissions.md) — exact vs route average, the past-date
  400, what gets kept, and the open question about timing
- [docs/resync.md](docs/resync.md) — what a sync may change, the freeze boundary,
  cancellation
- [docs/storage.md](docs/storage.md) — CSV invariants and why there's no total
- [docs/tripit-api.md](docs/tripit-api.md) — investigated, not used, and why
- [docs/contrail-gh.md](docs/contrail-gh.md) — **what a change here obliges in the
  template repo**

## Gotchas most likely to bite

- **The regexes in `importers/tripit_ical.py` are validated against real TripIt
  feeds.** They look untidy because real feeds are. Don't rewrite one without a
  failing test that proves the current form is wrong.
- **Mutation is confined to flights that haven't departed.** Past rows are never
  touched — that's what makes "absent from the feed" unambiguous.
- **The file is written only when content actually changed**, or contrail-gh
  commits every day for nothing.
- **Every value in a row dict is a string**, so a fresh row compares equal to one
  loaded from the CSV. Every row builder must set every column.
- **TIM cannot be asked twice.** It won't price a departed flight, so anything not
  captured while it was upcoming is gone permanently.
- **`cli._now()` exists to be monkeypatched.** Tests that use the real clock rot
  once the fixture's dates fall into the past.

## Related repos

- `atdr/contrail-gh` — public GitHub Actions template. Pins a contrail release
  tag and ships a header-only CSV that has to match it. **A schema or output
  change here needs a matching change there** — see
  [docs/contrail-gh.md](docs/contrail-gh.md).
- A **private instance** created from that template (`octocat/my-contrail` in the
  docs). Real data and secrets — never referenced by name in anything public.
