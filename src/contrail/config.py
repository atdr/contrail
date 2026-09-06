"""Configuration loading and resolution.

Resolution order, highest priority first:

    CLI flags -> environment variables -> config.json/config.yaml -> defaults

Environment variables come before the file because they're what every
production target actually injects: GitHub Actions secrets, a cron environment,
and Lambda environment variables all arrive that way.

The file mirrors the packages under ``src/contrail``. A section that names a
protocol seam carries a ``type:``, because a registry resolves it:

    importers:   a list, because order decides which source owns a flight
    emissions:   one provider
    storage:     one entry per role, because the flight log and the raw log are
                 different kinds of thing (see storage/__init__.py)
    passport:    a view over the log, so no type: there is nothing to select

The 0.4.x shape is still read, and says so once on stderr. See docs/config.md.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CSV_PATH = "flight_emissions.csv"
DEFAULT_EMISSIONS_PROVIDER = "tim"
DEFAULT_STORAGE = "local_csv"
DEFAULT_RAW_LOG = "jsonl"
DEFAULT_PASSPORT_OUTPUT = "passport.html"
CONFIG_BASENAMES = ("config.json", "config.yaml", "config.yml")

# The sections a config file may carry, the roles `storage:` may name, and the
# keys `passport:` may carry. Closed sets, each checked in `_check_keys`.
SECTIONS = ("importers", "emissions", "storage", "passport")
STORAGE_ROLES = ("flights", "raw_log")
PASSPORT_KEYS = ("output_path",)


class ConfigError(Exception):
    """Raised when configuration is missing or malformed."""


@dataclass
class Config:
    """One section per package, resolved from flags, environment and file."""

    importers: list[dict] = field(default_factory=list)
    emissions: dict = field(default_factory=dict)
    storage: dict = field(default_factory=dict)
    passport: dict = field(default_factory=dict)

    def _role(self, name: str) -> dict:
        role = self.storage.get(name)
        return role if isinstance(role, dict) else {}

    @property
    def sources(self) -> list[dict]:
        """The importers, under the name the 0.4.x config file used."""
        return self.importers

    @property
    def provider_name(self) -> str:
        emissions = self.emissions
        return emissions.get("type") or emissions.get("provider") or DEFAULT_EMISSIONS_PROVIDER

    @property
    def api_key(self) -> str | None:
        return self.emissions.get("api_key")

    @property
    def storage_type(self) -> str:
        return self._role("flights").get("type") or DEFAULT_STORAGE

    @property
    def csv_path(self) -> str:
        return self._role("flights").get("path") or DEFAULT_CSV_PATH

    @property
    def raw_log_type(self) -> str:
        return self._role("raw_log").get("type") or DEFAULT_RAW_LOG

    @property
    def raw_path(self) -> str | None:
        return csv_path_or_none(self._role("raw_log").get("path"))

    @property
    def raw_log_enabled(self) -> bool:
        """On unless switched off. An absent section is not a disabled one: a
        file that says nothing about the raw log gets the same one it always
        had, beside the CSV.

        An `enabled:` with nothing after it says nothing either, so it has to
        mean the default too. Every other key here reads an empty value that
        way, via `or DEFAULT_*`, and this is the one where guessing wrong
        cannot be undone: TIM will not price a departed flight twice, so a
        provenance record skipped is gone rather than deferred.
        """
        enabled = self._role("raw_log").get("enabled")
        return True if enabled is None else bool(enabled)

    @property
    def passport_output(self) -> str:
        return self.passport.get("output_path") or DEFAULT_PASSPORT_OUTPUT

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                "Missing TIM_API_KEY.\n"
                "Set it as an environment variable, or add it to config.json as:\n"
                '  {"emissions": {"type": "tim", "api_key": "..."}}\n'
                "Get a free key by enabling the Travel Impact Model API in a Google Cloud "
                "project: https://console.cloud.google.com\n"
                "(Not needed for --dry-run, which never calls the emissions API.)"
            )
        return self.api_key

    def require_importers(self) -> list[dict]:
        if not self.importers:
            raise ConfigError(
                "No flight sources configured.\n"
                "Set TRIPIT_ICAL_URL as an environment variable, or add to config.json:\n"
                '  {"importers": [{"type": "tripit_ical", "url": "https://..."}]}\n'
                "Find your feed URL in TripIt under Settings -> Calendar Feed. "
                "Treat it as a secret: anyone holding it can see your itineraries.\n"
                "\n"
                "Or point FLIGHTY_CSV_PATH at a Flighty export, which is the only "
                "source that knows which cabin you actually flew:\n"
                '  {"importers": [{"type": "flighty_csv", "path": "flighty/"}]}'
            )
        return self.importers

    # The name `cli.collect` used before the section was renamed.
    require_sources = require_importers


def lookup_type(registry: dict, type_name: str, noun: str, plural: str):
    """Resolve a config ``type:`` string against one of the seam registries.

    A `ConfigError` rather than a `ValueError`: an unrecognised ``type:`` is a
    fault in the user's config file, and `main` prints those as "Configuration
    error". Raising `ValueError` here would report the same mistake two ways —
    `cli.collect` already raises `ConfigError` for a *missing* type.
    """
    try:
        return registry[type_name]
    except KeyError:
        available = ", ".join(sorted(registry)) or "(none)"
        raise ConfigError(
            f"Unknown {noun} {type_name!r}. Available {plural}: {available}"
        ) from None


def csv_path_or_none(value):
    return str(value) if value else None


def warn(message: str) -> None:
    """Advisories go to stderr, never stdout.

    stdout is the sync report: contrail-gh commits around it, `--dry-run` users
    diff it, and the bug report template asks people to paste it.
    """
    print(message, file=sys.stderr)


def warn_superseded(source: str, old: str, new: str, superseded_by: str | None = None) -> None:
    """Name a key the 0.4.x schema used, and what replaced it.

    A plain print rather than `warnings.warn`: DeprecationWarning is suppressed
    by default outside __main__ and invisible under cron and GitHub Actions,
    which is exactly where an old config file is most likely to be sitting.

    `superseded_by` names whatever is beating the old key on this run, which is
    not always its replacement: an environment variable or a flag overrides
    both. Saying "still honoured" about a value the run is not using would send
    someone to edit a line that was never the cause.
    """
    if superseded_by is None:
        warn(f"{source}: '{old}' was replaced in 0.5.0 and is still honoured. Use {new}.")
    elif superseded_by == new:
        warn(f"{source}: '{old}' was replaced in 0.5.0 and {new} is already set. Drop '{old}'.")
    else:
        warn(
            f"{source}: '{old}' was replaced in 0.5.0, and {superseded_by} is overriding it "
            f"on this run. Use {new}."
        )


def _mapping(value, name: str, source: str) -> dict:
    """One mapping, rejecting a shape that cannot mean anything.

    `storage:` in particular reads as a list to anyone who has just written
    `importers:` as one, and a list of storage backends has no answer to "which
    one does `load()` read from".

    `None` is not that: a block with every key commented out parses as one, and
    that is a file saying nothing rather than a file saying something wrong.
    Returning `{}` also keeps every caller safe to write into, which the env
    layer below relies on.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(
            f"{source}: '{name}' must be a mapping, not a {type(value).__name__}. "
            f"Only 'importers' is a list, because its order decides which source "
            f"owns a flight two of them report."
        )
    return dict(value)


def _section(file_data: dict, name: str, source: str) -> dict:
    return _mapping(file_data.get(name), name, source)


def _entries(value, name: str, source: str) -> list[dict]:
    """The importers, as the list of mappings the rest of the loader assumes.

    The mirror of `_mapping`, and the mistake `_mapping`'s own error message
    invites: `importers:` keyed by type reads perfectly well, and yields a list
    of bare type names that fails much later and somewhere else, as an
    `AttributeError` from inside an importer.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(
            f"{source}: '{name}' must be a list, not a {type(value).__name__}. "
            f"Its order decides which source owns a flight two of them report, "
            f"and keying it by type would also make two feeds of one type "
            f"impossible to write down:\n"
            f'  {{"{name}": [{{"type": "tripit_ical", "url": "https://..."}}]}}'
        )
    for entry in value:
        if not isinstance(entry, dict):
            raise ConfigError(
                f"{source}: every '{name}' entry must be a mapping carrying a "
                f"'type', not a {type(entry).__name__}: {entry!r}"
            )
    # Copied, because `_env_source` updates an entry in place and the file data
    # is still read afterwards by the 0.4.x block.
    return [dict(entry) for entry in value]


def _env_source(sources: list[dict], type_name: str, field: str, value: str | None) -> None:
    """Point a source at what an environment variable says, adding it if absent.

    Updating in place rather than appending matters: a config file that already
    configures this source should have its other settings kept, and two entries
    of the same type would import the same flights twice.
    """
    if not value:
        return
    existing = next((s for s in sources if s.get("type") == type_name), None)
    if existing is not None:
        existing[field] = value
    else:
        sources.append({"type": type_name, field: value})


def _load_file(path: Path) -> dict:
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise ConfigError(
                f"{path} needs PyYAML to read. Install it with: pip install 'contrail[yaml]'\n"
                "(Or use config.json instead, which needs no extra dependency.)"
            ) from None
        with open(path) as f:
            return yaml.safe_load(f) or {}
    with open(path) as f:
        return json.load(f)


def find_config_file(explicit: str | None = None, directory: str | None = None) -> Path | None:
    """Locate a config file: an explicit path, else the first known name in ``directory``."""
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise ConfigError(f"Config file not found: {explicit}")
        return path

    base = Path(directory or os.getcwd())
    for name in CONFIG_BASENAMES:
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


def load_config(
    config_path: str | None = None,
    csv_path: str | None = None,
    env: dict | None = None,
    directory: str | None = None,
    passport_output: str | None = None,
) -> Config:
    """Build a Config from flags, environment, and an optional config file."""
    env = os.environ if env is None else env

    file_data: dict = {}
    found = find_config_file(config_path, directory)
    if found is not None:
        file_data = _load_file(found)
        if not isinstance(file_data, dict):
            raise ConfigError(f"{found} must contain a JSON/YAML object at the top level.")
    source = found.name if found is not None else "config"

    importers = _entries(file_data.get("importers"), "importers", source)
    emissions = _section(file_data, "emissions", source)
    storage = _section(file_data, "storage", source)
    passport = _section(file_data, "passport", source)

    # Each role too, not just the section: a role with every key commented out
    # is `None`, and everything below writes into these.
    for role, value in storage.items():
        storage[role] = _mapping(value, f"storage.{role}", source)

    # Which layer above the file will win, per superseded key, named so the
    # warning can point at it, or None where the file's own value is what the
    # run ends up using. Decided here although env and flags are applied below:
    # a warning that misnames the cause sends someone to fix the wrong line.
    superseding = {var: var if env.get(var) else None for var in SUPERSEDING_VARS}
    if csv_path:
        superseding["CSV_PATH"] = "--csv-path"
    _read_superseded(file_data, source, importers, emissions, storage, superseding)
    _check_keys(file_data, storage, passport, source)

    # A bare TRIPIT_ICAL_URL is the common case (GitHub Actions, cron), so treat
    # it as an implicit single source rather than making people write JSON. The
    # same goes for a Flighty export, which in a scheduled setup is a path in the
    # repository rather than a URL.
    _env_source(importers, "tripit_ical", "url", env.get("TRIPIT_ICAL_URL"))
    _env_source(importers, "flighty_csv", "path", env.get("FLIGHTY_CSV_PATH"))

    if env.get("TIM_API_KEY"):
        emissions["api_key"] = env["TIM_API_KEY"]
    if env.get("EMISSIONS_PROVIDER"):
        emissions["type"] = env["EMISSIONS_PROVIDER"]

    flights = storage.setdefault("flights", {})
    raw_log = storage.setdefault("raw_log", {})
    if env.get("CSV_PATH"):
        flights["path"] = env["CSV_PATH"]
    if env.get("RAW_PATH"):
        raw_log["path"] = env["RAW_PATH"]
    if env.get("RAW_LOG"):
        raw_log["enabled"] = env["RAW_LOG"].strip().lower() not in ("0", "false", "no", "off")
    if env.get("PASSPORT_OUTPUT"):
        passport["output_path"] = env["PASSPORT_OUTPUT"]

    # Flags last: they are the only layer the person typed just now.
    if csv_path:
        flights["path"] = csv_path
    if passport_output:
        passport["output_path"] = passport_output

    return Config(
        importers=importers,
        emissions=emissions,
        storage=storage,
        passport=passport,
    )


# --- The 0.4.x schema ------------------------------------------------------
#
# Everything below reads keys the sections above replaced. Each is still
# honoured and names its replacement once, on stderr. Removed at 1.0: deleting
# this block and its two call sites in `load_config` is the whole job.


def _read_superseded(
    file_data: dict,
    source: str,
    importers: list[dict],
    emissions: dict,
    storage: dict,
    superseding: dict,
) -> None:
    """Fold the keys a 0.4.x file uses into the sections that replaced them.

    A file carrying both spellings keeps the new one. Saying otherwise would
    mean a config could not be migrated a key at a time.

    `superseding` carries the flags and environment variables that will be
    applied after this runs. They are already decided even though nothing has
    written them yet, so a key one of them overwrites is reported as ignored:
    telling someone a value is "still honoured" when the run is using a
    different one sends them to fix the wrong thing.
    """
    if file_data.get("sources"):
        # Not superseded by TRIPIT_ICAL_URL: an env var updates the entry these
        # become rather than replacing them, so they are still doing work.
        warn_superseded(source, "sources", "importers", "importers" if importers else None)
        if not importers:
            importers.extend(_entries(file_data["sources"], "sources", source))

    if emissions.get("provider"):
        warn_superseded(
            source,
            "emissions.provider",
            "emissions.type",
            superseded_by=(
                "emissions.type" if emissions.get("type") else superseding["EMISSIONS_PROVIDER"]
            ),
        )

    flights = storage.setdefault("flights", {})
    raw_log = storage.setdefault("raw_log", {})

    for old, role, key, above in (
        ("csv_path", flights, "path", "CSV_PATH"),
        ("CSV_PATH", flights, "path", "CSV_PATH"),
        ("raw_path", raw_log, "path", "RAW_PATH"),
    ):
        if file_data.get(old):
            new = f"storage.{'flights' if role is flights else 'raw_log'}.{key}"
            warn_superseded(source, old, new, new if key in role else superseding[above])
            role.setdefault(key, file_data[old])

    if "raw_log" in file_data and not isinstance(file_data["raw_log"], dict):
        warn_superseded(
            source,
            "raw_log",
            "storage.raw_log.enabled",
            superseded_by=(
                "storage.raw_log.enabled" if "enabled" in raw_log else superseding["RAW_LOG"]
            ),
        )
        raw_log.setdefault("enabled", bool(file_data["raw_log"]))

    # Flat keys, as written by pre-v0.1.0 config.json files. Silently accepted
    # for four minor versions; they join the warning rather than outliving it.
    if file_data.get("TIM_API_KEY"):
        warn_superseded(
            source,
            "TIM_API_KEY",
            "emissions.api_key",
            superseded_by=(
                "emissions.api_key" if emissions.get("api_key") else superseding["TIM_API_KEY"]
            ),
        )
        emissions.setdefault("api_key", file_data["TIM_API_KEY"])
    if file_data.get("TRIPIT_ICAL_URL"):
        # This one does not survive the env var: it is the URL itself, and
        # `_env_source` overwrites exactly that field on the entry it makes.
        warn_superseded(
            source,
            "TRIPIT_ICAL_URL",
            "importers",
            superseded_by="importers" if importers else superseding["TRIPIT_ICAL_URL"],
        )
        if not importers:
            importers.append({"type": "tripit_ical", "url": file_data["TRIPIT_ICAL_URL"]})


# The environment variables that can override a key this block still reads.
SUPERSEDING_VARS = (
    "CSV_PATH",
    "RAW_PATH",
    "RAW_LOG",
    "TIM_API_KEY",
    "TRIPIT_ICAL_URL",
    "EMISSIONS_PROVIDER",
)

SUPERSEDED_KEYS = frozenset(
    {"sources", "csv_path", "CSV_PATH", "raw_path", "raw_log", "TIM_API_KEY", "TRIPIT_ICAL_URL"}
)


def _check_keys(file_data: dict, storage: dict, passport: dict, source: str) -> None:
    """Name a key contrail will do nothing with.

    Every closed set, and only the closed sets: the top level, the storage
    roles, and `passport`. Never inside an entry that carries a `type` — an
    importer or a storage backend defines its own shape, and `config.py`
    deliberately does not know what a `url` or a `bucket` is.

    `passport` is the exception among the sections because it has no `type`.
    There is no registry behind it and so no implementation to own the
    leftovers, which makes its keys as closed as the section names. It is also
    the section where a typo is most likely and least visible: the flag is
    spelled `--output`, so `output:` is the natural thing to write, and the
    dashboard would land at the default path in silence.
    """
    for key in file_data:
        if key not in SECTIONS and key not in SUPERSEDED_KEYS:
            warn(f"{source}: '{key}' is not a section contrail reads. Ignoring it.")

    for key in passport:
        if key not in PASSPORT_KEYS:
            warn(
                f"{source}: 'passport.{key}' is not a key contrail reads. "
                f"Ignoring it. Expected: {', '.join(PASSPORT_KEYS)}."
            )

    for role in storage:
        if role not in STORAGE_ROLES:
            raise ConfigError(
                f"{source}: 'storage.{role}' is not a role contrail writes. "
                f"Expected one of: {', '.join(STORAGE_ROLES)}."
            )
