# Configuration

`config.json` or `config.yaml`, read from the directory you run contrail in, or
named with `--config`. Both are gitignored: both hold secrets.

Everything here is optional. Two environment variables are enough to run
contrail, and that is how the GitHub Actions template does it.

## The shape

One section per package under `src/contrail`. A section that names a protocol
seam carries a `type`, because a registry resolves it to a class.

```yaml
importers:
  - type: tripit_ical
    url: https://www.tripit.com/feed/ical/private/YOUR-FEED-ID/tripit.ics
    airline_lookup: true
  - type: flighty_csv
    path: flighty/

emissions:
  type: tim
  api_key: YOUR-GOOGLE-CLOUD-API-KEY

storage:
  flights:
    type: local_csv
    path: flight_emissions.csv
  raw_log:
    type: jsonl
    enabled: true

passport:
  output_path: passport.html
```

`config.example.json` and `config.example.yaml` are the same configuration in
both formats, and a test asserts they stay that way.

## Resolution order

Highest priority first:

1. CLI flags: `--csv-path`, `--output`
2. Environment variables
3. `config.json` / `config.yaml` / `config.yml`, first found, in that order
4. Built-in defaults

Environment variables beat the file because that is what every deployment target
injects. GitHub Actions secrets, a cron environment and Lambda variables all
arrive that way, and none of them wants to write a file first.

| Variable             | Sets                            |
| -------------------- | ------------------------------- |
| `TRIPIT_ICAL_URL`    | a `tripit_ical` importer's URL  |
| `FLIGHTY_CSV_PATH`   | a `flighty_csv` importer's path |
| `TIM_API_KEY`        | `emissions.api_key`             |
| `EMISSIONS_PROVIDER` | `emissions.type`                |
| `CSV_PATH`           | `storage.flights.path`          |
| `RAW_LOG`            | `storage.raw_log.enabled`       |
| `RAW_PATH`           | `storage.raw_log.path`          |
| `PASSPORT_OUTPUT`    | `passport.output_path`          |

`EMISSIONS_PROVIDER` sets a key now called `type`. The name is kept because
these variables are the contract with the Actions template and every repository
created from it, and renaming one would break a scheduled run for a tidier
spelling.

Setting `TRIPIT_ICAL_URL` or `FLIGHTY_CSV_PATH` with no file at all synthesizes
an importer entry, so a bare pair of variables is a complete configuration. With
a file, the variable updates the matching entry rather than adding a second one:
two entries of one type would import every flight twice.

## Why `importers` is a list

Because order decides ownership. Where two importers report the same flight, the
first one listed keeps the row and the second fills in the blanks it can, such
as the cabin that only a Flighty export knows. See
[resync.md](resync.md#one-flight-two-sources).

A mapping keyed by type would read more tidily and would quietly change that,
and would also make two feeds of one type impossible to express.

## Why `storage` is keyed by role

`storage/` holds two classes, and they are not interchangeable. `LocalCSVStorage`
implements the `Storage` protocol, which is `load()` and `save(rows)`.
`JSONLRawLog` deliberately does not: it appends what the emissions API returned
and is never read back as rows. See the Seams section of
[storage.md](storage.md).

So the key is the role and the `type` is the implementation. A list would
promise the two are peers and ordered, neither of which is true, and would leave
`[{type: local_csv}, {type: local_csv}]` expressible with no answer to which one
`load()` should read. Anything other than `flights` and `raw_log` is refused.

`storage.raw_log.path` defaults to the flights path with a `.raw.jsonl` suffix,
so `flight_emissions.csv` gets `flight_emissions.raw.jsonl` beside it. An absent
`raw_log` section is not a disabled one: only `enabled: false` switches it off.

## Why `passport` has no `type`

The log is state. TIM will not price a flight that has departed, so a stored row
is the only copy of a figure that can ever exist, and the raw log is kept for the
same reason. Passport is neither. It is a view, rebuilt from the CSV whenever you
ask, which is why `contrail passport` needs no API key, no feed and no network.

A `type` there would be an invented choice: there is no protocol and no registry
behind it. The rule the file follows is that a section naming a seam carries a
type because something resolves it, and a section naming a package with one
implementation carries only that implementation's options.

## What contrail does not check

Only `type` is reserved. Everything else in an entry belongs to the
implementation, and contrail never inspects it: `config.py` does not know what a
`url` is or which importer takes a `path`. That is what lets a new importer
define its own shape, including one that needs OAuth credentials rather than a
URL, without the schema having to anticipate it. See
`src/contrail/importers/base.py`.

The consequence is that a misspelled key inside an entry is silently ignored,
and the importer reports the field it wanted as missing. Unknown keys at the top
level are named, because that set is closed.

## Keys replaced in 0.5.0

All of these still work. Each prints one line to stderr naming its replacement,
and where a file carries both spellings the new one wins, so a config can be
migrated a key at a time.

| Was                                       | Now                       |
| ----------------------------------------- | ------------------------- |
| `sources`                                 | `importers`               |
| `csv_path`, `CSV_PATH`                    | `storage.flights.path`    |
| `raw_log: true`                           | `storage.raw_log.enabled` |
| `raw_path`                                | `storage.raw_log.path`    |
| `emissions.provider`                      | `emissions.type`          |
| `TIM_API_KEY` as a top-level file key     | `emissions.api_key`       |
| `TRIPIT_ICAL_URL` as a top-level file key | an `importers` entry      |

The last two date from before v0.1.0 and were accepted in silence for four minor
versions. **They are all removed at 1.0.** The warning is what makes that fair
notice, and the whole compatibility layer is one block at the foot of
`config.py` so removing it is a single edit.

Environment variables are not deprecated and do not warn.
