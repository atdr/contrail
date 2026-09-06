# Keeping contrail-gh in step

[atdr/contrail-gh](https://github.com/atdr/contrail-gh) is a public **template
repository** holding no logic: a workflow, a header-only CSV, and setup docs. It
installs contrail from a pinned release tag. Users create their own private repo
from it, so the template is the thing every instance starts from.

Nothing in this repo can enforce that, because it's a separate repository. The
coupling is enforced from the other side by `check-template.yml` there, and this
page is the list of things that break it.

An instance gets a second check, `check-instance.yml`, which dry-runs the sync on
every pull request against that repo's own log. It's the first place a contrail
release meets someone's real data before it is committed to, so a change here that
breaks a real feed or export surfaces there rather than in a scheduled run. It
installs the tag `sync.yml` pins and runs `--dry-run`, so it never reaches the
emissions API — which keeps it to a few seconds and means it says nothing about
whether an instance's TIM key still works.

## Changes here that require a change there

| Change in contrail                                   | What contrail-gh needs                                                                                                                                              |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CSV_FIELDS` gains, loses or reorders a column       | Regenerate `flight_emissions.csv` (header row only)                                                                                                                 |
| `contrail sync` writes a new durable output file     | Add it to the `git add` line in `sync.yml`                                                                                                                          |
| contrail gains an importer that reads a **file**     | A directory for it, the env var in _both_ `sync.yml` and `check-instance.yml`, and a guard in `check-template.yml` that the public template never carries one       |
| A new column or behaviour users should know about    | Update the template's README                                                                                                                                        |
| contrail generates a private derived artifact        | Gitignore the default path and document that it must not be committed                                                                                               |
| The config **file** schema changes                   | Nothing, as long as the environment variable names hold. The template ships no config file and configures contrail entirely by env, so those names are the contract |
| A release is cut                                     | Nothing here — Dependabot opens the version-bump PR in the instance                                                                                                 |
| `requires-python` rises above the workflow's version | Raise `python-version` in `sync.yml`                                                                                                                                |

The release row used to be the one release-please couldn't take off your
hands: it rewrites the version pins in this repo's own README, because
they're listed under `extra-files`, but it had no reach into another
repository, so `sync.yml` stayed a hand edit after every release. Publishing
to PyPI closes that gap indirectly — contrail-gh pins a plain version in
`requirements.txt` rather than a git tag in a shell command, so Dependabot
can see and bump it. contrail's only obligation is that the release actually
reaches PyPI, which the `publish` job in `release-please.yml` now does on
its own. Someone still has to review and merge the Dependabot PR — see
contrail-gh's own README for that side of it.

The `flighty_csv` importer is the first of the file-reading kind, and it is the
reason that third row exists. An export is a manual file rather than a feed URL,
so an instance commits its exports to `flighty/` and points `FLIGHTY_CSV_PATH` at
the directory. An empty directory yields nothing rather than erroring, which is
what keeps the setting inert in the template itself.

Passport is the first private derived artifact. `contrail passport` writes a
self-contained `passport.html` with the itinerary embedded in it. The template
must gitignore that default path and explain that a custom output path is private
too. It must **not** add the file to `sync.yml`: Passport is generated locally on
demand, and committing it would duplicate sensitive flight data in a directly
viewable form.

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

A derived repo runs whatever version it pins. Tracking `main` would push every
commit here straight into people's private repos unannounced, including the ones
that turn out to be wrong. Bumping is still deliberate — reviewing and merging a
Dependabot pull request rather than hand-editing a tag — and the template's
README explains how to re-apply a personal pin after pulling template updates.

This also means **a schema change reaches users only when they bump.** Their CSV
may sit on an older schema for a while; contrail handles that — a column a row
doesn't have is treated as back-fill, not as a changed flight
(see [resync.md](resync.md)).

## What must never appear in the template

Real flight data. `flight_emissions.csv` is the header row and nothing else,
`flight_emissions.raw.jsonl` must not exist there at all, and `flighty/` must
hold no CSV. All three are guarded by CI in that repo.

Passport adds a fourth sensitive artifact: `passport.html` must not exist in the
public template either. The matching template update must gitignore it and make
`check-template.yml` reject it before a release pin exposes the command there.

`flighty/` is the sharpest of the three: a Flighty export is someone's entire
flight history in one file, and unlike the log it is a file a user puts there by
hand, so nothing else would catch it.

## The Markdown config is duplicated, not shared

`.markdownlint-cli2.yaml`, `.prettierrc.json` and `.prettierignore` exist in this
repo, in the template, and in every instance created from it. They are copies:
three separate repositories can't share a config file, and neither tool reads one
from a package. Change a rule here and the same edit has to be made in the
template, or the two repos start disagreeing about what correct Markdown is.

The template's copies ship to instances through "Use this template", so its
`README.md` names them in the recipe for pulling template updates — the workflow
that runs markdownlint and the config it reads have to arrive together.

## Three repos, briefly

- **atdr/contrail** (public) — this one. All the logic, no data, no secrets.
- **atdr/contrail-gh** (public) — the template. Scaffolding only.
- **`octocat/my-contrail`** (private) — an instance created from the template.
  Real itineraries, real secrets. Created _through_ GitHub's "Use this template"
  so that path stays exercised, rather than by copying files. Instances are
  personal: don't name a real one in public docs.
