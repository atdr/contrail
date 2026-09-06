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

# The sections a config file may carry, and the roles `storage:` may name.
SECTIONS = ("importers", "emissions", "storage", "passport")
STORAGE_ROLES = ("flights", "raw_log")


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
        had, beside the CSV."""
        return bool(self._role("raw_log").get("enabled", True))

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


def warn_superseded(source: str, old: str, new: str, ignored: bool = False) -> None:
    """Name a key the 0.4.x schema used, and what replaced it.

    A plain print rather than `warnings.warn`: DeprecationWarning is suppressed
    by default outside __main__ and invisible under cron and GitHub Actions,
    which is exactly where an old config file is most likely to be sitting.
    """
    fate = "being ignored in favour of" if ignored else "still honoured. Use"
    warn(f"{source}: '{old}' was replaced in 0.5.0 and is {fate} {new}.")


def _section(file_data: dict, name: str, source: str) -> dict:
    """One mapping section, rejecting a shape that cannot mean anything.

    `storage:` in particular reads as a list to anyone who has just written
    `importers:` as one, and a list of storage backends has no answer to "which
    one does `load()` read from".
    """
    value = file_data.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(
            f"{source}: '{name}' must be a mapping, not a {type(value).__name__}. "
            f"Only 'importers' is a list, because its order decides which source "
            f"owns a flight two of them report."
        )
    return dict(value)


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

    importers = list(file_data.get("importers") or [])
    emissions = _section(file_data, "emissions", source)
    storage = _section(file_data, "storage", source)
    passport = _section(file_data, "passport", source)

    _read_superseded(file_data, source, importers, emissions, storage)
    _check_keys(file_data, storage, source)

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
    file_data: dict, source: str, importers: list[dict], emissions: dict, storage: dict
) -> None:
    """Fold the keys a 0.4.x file uses into the sections that replaced them.

    A file carrying both spellings keeps the new one. Saying otherwise would
    mean a config could not be migrated a key at a time.
    """
    if file_data.get("sources"):
        warn_superseded(source, "sources", "importers", ignored=bool(importers))
        if not importers:
            importers.extend(file_data["sources"])

    if emissions.get("provider"):
        warn_superseded(
            source, "emissions.provider", "emissions.type", ignored=bool(emissions.get("type"))
        )

    flights = storage.setdefault("flights", {})
    raw_log = storage.setdefault("raw_log", {})

    for old, role, key in (
        ("csv_path", flights, "path"),
        ("CSV_PATH", flights, "path"),
        ("raw_path", raw_log, "path"),
    ):
        if file_data.get(old):
            new = f"storage.{'flights' if role is flights else 'raw_log'}.{key}"
            warn_superseded(source, old, new, ignored=key in role)
            role.setdefault(key, file_data[old])

    if "raw_log" in file_data and not isinstance(file_data["raw_log"], dict):
        warn_superseded(source, "raw_log", "storage.raw_log.enabled", ignored="enabled" in raw_log)
        raw_log.setdefault("enabled", bool(file_data["raw_log"]))

    # Flat keys, as written by pre-v0.1.0 config.json files. Silently accepted
    # for four minor versions; they join the warning rather than outliving it.
    if file_data.get("TIM_API_KEY"):
        warn_superseded(
            source, "TIM_API_KEY", "emissions.api_key", ignored=bool(emissions.get("api_key"))
        )
        emissions.setdefault("api_key", file_data["TIM_API_KEY"])
    if file_data.get("TRIPIT_ICAL_URL"):
        warn_superseded(source, "TRIPIT_ICAL_URL", "importers", ignored=bool(importers))
        if not importers:
            importers.append({"type": "tripit_ical", "url": file_data["TRIPIT_ICAL_URL"]})


SUPERSEDED_KEYS = frozenset(
    {"sources", "csv_path", "CSV_PATH", "raw_path", "raw_log", "TIM_API_KEY", "TRIPIT_ICAL_URL"}
)


def _check_keys(file_data: dict, storage: dict, source: str) -> None:
    """Name a key contrail will do nothing with.

    Only at the top level and among storage roles, where the set of names is
    closed. Never inside an entry: an importer defines its own shape, and
    `config.py` deliberately does not know what a `url` is.
    """
    for key in file_data:
        if key not in SECTIONS and key not in SUPERSEDED_KEYS:
            warn(f"{source}: '{key}' is not a section contrail reads. Ignoring it.")

    for role in storage:
        if role not in STORAGE_ROLES:
            raise ConfigError(
                f"{source}: 'storage.{role}' is not a role contrail writes. "
                f"Expected one of: {', '.join(STORAGE_ROLES)}."
            )
