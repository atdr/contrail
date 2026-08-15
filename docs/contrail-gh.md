# Keeping contrail-gh in step

[atdr/contrail-gh](https://github.com/atdr/contrail-gh) is a public **template
repository** holding no logic: a workflow, a header-only CSV, and setup docs. It
installs contrail from a pinned release tag. Users create their own private repo
from it, so the template is the thing every instance starts from.

Nothing in this repo can enforce that, because it's a separate repository. The
coupling is enforced from the other side by `check-template.yml` there, and this
page is the list of things that break it.

## Changes here that require a change there

| Change in contrail | What contrail-gh needs |
|---|---|
| `CSV_FIELDS` gains, loses or reorders a column | Regenerate `flight_emissions.csv` (header row only) |
| contrail writes a new output file | Add it to the `git add` line in `sync.yml` |
| A new column or behaviour users should know about | Update the template's README |
| A release is cut | Bump the `@vX.Y.Z` pin in `sync.yml` |
| `requires-python` rises above the workflow's version | Raise `python-version` in `sync.yml` |

Regenerating the header, from a checkout of contrail:

```bash
./venv/bin/python -c 'from contrail.storage.local_csv import CSV_FIELDS; print(",".join(CSV_FIELDS))' \
  > ../contrail-gh/flight_emissions.csv
```

The header must match the **pinned** version's schema, not `main`'s. If a schema
change hasn't been released yet, regenerate the header in the same change that
bumps the pin — not before, or the template ships a header no released contrail
writes.

## Why the pin is never `main`

A derived repo runs whatever tag it pins. Tracking `main` would push every commit
here straight into people's private repos unannounced, including the ones that
turn out to be wrong. Bumping is a deliberate one-line edit, and the template's
README explains how to re-apply a personal pin after pulling template updates.

This also means **a schema change reaches users only when they bump.** Their CSV
may sit on an older schema for a while; contrail handles that — a column a row
doesn't have is treated as back-fill, not as a changed flight
(see [resync.md](resync.md)).

## What must never appear in the template

Real flight data. `flight_emissions.csv` is the header row and nothing else, and
`flight_emissions.raw.jsonl` must not exist there at all. Both are gitignored in
*this* repo and guarded by CI in that one.

## Three repos, briefly

- **atdr/contrail** (public) — this one. All the logic, no data, no secrets.
- **atdr/contrail-gh** (public) — the template. Scaffolding only.
- **atdr/my-contrail** (private) — an instance created from the template. Real
  itineraries, real secrets. Created *through* GitHub's "Use this template" so
  that path stays exercised, not by copying files.
