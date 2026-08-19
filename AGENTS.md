# contrail

Estimates the CO2e of flights taken or booked and keeps a log. Reads a TripIt
iCal feed and Flighty CSV exports, prices each flight with Google's Travel Impact
Model, writes a CSV.

## Commands

```bash
python3.12 -m venv venv && ./venv/bin/pip install -e ".[dev]"
./venv/bin/pytest -q
./venv/bin/ruff check . && ./venv/bin/ruff format .
TRIPIT_ICAL_URL=tests/fixtures/sample_feed.ics \
  FLIGHTY_CSV_PATH=tests/fixtures/sample_flighty.csv ./venv/bin/contrail sync --dry-run
./venv/bin/python scripts/refresh_airline_codes.py   # needs network; run by hand
```

Python 3.11+. The default `python3` on this machine is
3.7 — use `/usr/local/bin/python3.12` explicitly.

## Architecture

Three protocol seams:

| Seam         | Protocol                                                                | Implementations              |
| ------------ | ----------------------------------------------------------------------- | ---------------------------- |
| `importers/` | `Importer.fetch(config) -> Iterable[FlightRecord \| UnparsedEvent]`     | `tripit_ical`, `flighty_csv` |
| `emissions/` | `EmissionsProvider.compute(flights, now) -> dict[key, EmissionsResult]` | `tim`                        |
| `storage/`   | `Storage.load() -> list[dict]`, `save(rows)`                            | `local_csv`                  |

`cli.py` owns the flow: load config → load storage → collect from every source →
collapse records that are the same flight → reconcile against what's stored →
price → build rows → normalize → save.

Two keys, and the difference matters:

- **`f"{source}:{source_id}"`** identifies a _record_. It is the dedup key and
  what `also_seen_as` stores.
- **`(flight_date, origin, destination)`** identifies a _flight_, across sources.
  `resync.identity()`. Never the flight number — see the gotchas.

## Conventions

- Conventional commits. release-please owns versions, tags and `CHANGELOG.md` —
  never hand-edit the changelog or the version in `pyproject.toml`.
- `__version__` is read from installed package metadata, so `pyproject.toml` is
  the single source of truth. Code that needs the version imports `__version__`
  rather than spelling it out — see the gotcha on version pins for the two
  literals that remain.
- **No test may make a real network call.** Mock `requests` in both directions.
- **Markdown is formatted, not hand-aligned.** Prettier owns table padding and
  whitespace; markdownlint-cli2 owns line length and the rest. Run
  `npx prettier@3.9.6 --write "**/*.md"` rather than lining a table up by
  hand. Prose wraps at 80, except `README.md`, which wraps at 100 and says so
  in a `markdownlint-configure-file` comment at its foot.
- This repo is public. Never commit a real CSV, a raw log, or `config.json` —
  all are gitignored.
- **When updating docs at the end of a change, skim the open issues**
  (`gh issue list`) for any the change touched. Cheap, and it catches both
  directions: an issue quietly fixed, and one made easier to hit.

  **Findings about your own change belong in the same PR** — that is not two
  things in one PR, it is one change described accurately. A doc line your change
  just made wrong, or an issue whose shape it altered, is part of that change; a
  reviewer needs it in front of them, and splitting it out means `main` is briefly
  wrong on purpose. Only a finding that stands entirely apart from what you built
  earns its own PR; note it on the issue either way.

  The one exception is the release PR: `chore(main): release X.Y.Z` is generated
  by release-please from commit subjects, so anything hand-added there is
  clobbered on the next regeneration.

## Depth

- [docs/parsing.md](docs/parsing.md) — the ported regexes, local dates,
  codeshares, why `parse()` is two-pass, reading a Flighty export
- [docs/emissions.md](docs/emissions.md) — exact vs route average, the past-date
  400, what gets kept, and the open question about timing
- [docs/resync.md](docs/resync.md) — what a sync may change, the freeze boundary,
  cancellation, matching one flight across two sources
- [docs/storage.md](docs/storage.md) — CSV invariants and why there's no total
- [docs/tripit-api.md](docs/tripit-api.md) — investigated, not used, and why
- [docs/contrail-gh.md](docs/contrail-gh.md) — **what a change here obliges in the
  template repo**

## Gotchas most likely to bite

- **The regexes in `importers/tripit_ical.py` are validated against real TripIt
  feeds.** They look untidy because real feeds are. Don't rewrite one without a
  failing test that proves the current form is wrong.
- **Mutation is confined to flights that haven't departed.** Past rows are never
  touched — that's what makes "absent from the feed" unambiguous. One exception:
  `resync.backfill()` fills a _blank_ cabin, aircraft or reason on a row of any
  age, because a Flighty export is almost entirely past flights and it re-prices
  nothing.
- **A flight is identified by route and date, never by flight number.** `BA16` is
  SYD-SIN-LHR on one day: two legs, two cabins, two rows. Any "dedup by flight
  number" idea silently merges them and loses a figure.
- **The file is written only when content actually changed**, or contrail-gh
  commits every day for nothing.
- **Every value in a row dict is a string**, so a fresh row compares equal to one
  loaded from the CSV. Every row builder must set every column.
- **TIM cannot be asked twice.** It won't price a departed flight, so anything not
  captured while it was upcoming is gone permanently.
- **A runtime dependency bump must be `fix(deps):`, not `chore(deps):`.**
  release-please hides `chore` by default and counts only `feat` and breaking
  changes toward a bump, so a `chore(deps)` bump of a runtime dependency changes
  what users install with nothing in the changelog and no release. Dev
  dependencies stay `chore(deps-dev):`.
- **The two install pins in `README.md` are rewritten by release-please**, not by
  hand. They sit inside `<!-- x-release-please-start-version -->` /
  `<!-- x-release-please-end -->` comments, and `README.md` is listed under
  `extra-files` in `release-please-config.json` — both halves are needed. Delete
  a comment and the pin quietly rots to whatever release it was written against,
  which is exactly how it drifted before. A _new_ literal version anywhere else
  is a bug: import `__version__`.
- **release-please needs the repo setting "Allow GitHub Actions to create and
  approve pull requests"** (Settings → Actions → General). `permissions:
pull-requests: write` in the workflow is _not_ sufficient on its own, and the
  API can report the flag as enabled while it is still blocked. Without it the
  release job fails with "GitHub Actions is not permitted to create or approve
  pull requests".
- **`CHANGELOG.md` and `CLAUDE.md` are excluded from both Markdown tools.**
  release-please regenerates the changelog from commit subjects, so a
  reformat there is undone on the next release and can desync the manifest;
  `CLAUDE.md` is a symlink to `AGENTS.md`, so linting it reports every line
  twice and formatting it writes the same file twice. Both are named in
  `.markdownlint-cli2.yaml` and `.prettierignore`.
- **`cli._now()` exists to be monkeypatched.** Tests that use the real clock rot
  once the fixture's dates fall into the past.
- **`src/contrail/data/airline_codes.csv` is generated, never hand-edited.** Fix
  it upstream in Wikidata and re-run the script, or the next refresh reverts you.
  It must be written with LF: `.gitattributes` normalizes, and the csv module
  defaults to CRLF.
- **`tests/fixtures/sample_flighty.csv` is synthetic.** This repo is public and
  a real export is someone's entire travel history.

## Related repos

- `atdr/contrail-gh` — public GitHub Actions template. Pins a contrail release
  tag and ships a header-only CSV that has to match it. **A schema or output
  change here needs a matching change there** — see
  [docs/contrail-gh.md](docs/contrail-gh.md).
- A **private instance** created from that template (`octocat/my-contrail` in the
  docs). Real data and secrets — never referenced by name in anything public.
