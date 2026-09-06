"""Configuration loading and resolution.

Resolution order, highest priority first:

    CLI flags -> environment variables -> config.json/config.yaml -> defaults

Environment variables come before the file because they're what every
production target actually injects: GitHub Actions secrets, a cron environment,
and Lambda environment variables all arrive that way.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CSV_PATH = "flight_emissions.csv"
DEFAULT_EMISSIONS_PROVIDER = "tim"
DEFAULT_STORAGE = "local_csv"
DEFAULT_RAW_LOG = "jsonl"
CONFIG_BASENAMES = ("config.json", "config.yaml", "config.yml")


class ConfigError(Exception):
    """Raised when configuration is missing or malformed."""


@dataclass
class Config:
    csv_path: str = DEFAULT_CSV_PATH
    sources: list[dict] = field(default_factory=list)
    emissions: dict = field(default_factory=dict)
    raw_path: str | None = None
    raw_log: bool = True

    @property
    def provider_name(self) -> str:
        return self.emissions.get("provider") or DEFAULT_EMISSIONS_PROVIDER

    @property
    def api_key(self) -> str | None:
        return self.emissions.get("api_key")

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                "Missing TIM_API_KEY.\n"
                "Set it as an environment variable, or add it to config.json as:\n"
                '  {"emissions": {"api_key": "..."}}\n'
                "Get a free key by enabling the Travel Impact Model API in a Google Cloud "
                "project: https://console.cloud.google.com\n"
                "(Not needed for --dry-run, which never calls the emissions API.)"
            )
        return self.api_key

    def require_sources(self) -> list[dict]:
        if not self.sources:
            raise ConfigError(
                "No flight sources configured.\n"
                "Set TRIPIT_ICAL_URL as an environment variable, or add to config.json:\n"
                '  {"sources": [{"type": "tripit_ical", "url": "https://..."}]}\n'
                "Find your feed URL in TripIt under Settings -> Calendar Feed. "
                "Treat it as a secret: anyone holding it can see your itineraries.\n"
                "\n"
                "Or point FLIGHTY_CSV_PATH at a Flighty export, which is the only "
                "source that knows which cabin you actually flew:\n"
                '  {"sources": [{"type": "flighty_csv", "path": "flighty/"}]}'
            )
        return self.sources


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
) -> Config:
    """Build a Config from flags, environment, and an optional config file."""
    env = os.environ if env is None else env

    file_data: dict = {}
    found = find_config_file(config_path, directory)
    if found is not None:
        file_data = _load_file(found)
        if not isinstance(file_data, dict):
            raise ConfigError(f"{found} must contain a JSON/YAML object at the top level.")

    sources = list(file_data.get("sources") or [])
    emissions = dict(file_data.get("emissions") or {})

    # A bare TRIPIT_ICAL_URL is the common case (GitHub Actions, cron), so treat
    # it as an implicit single source rather than making people write JSON. The
    # same goes for a Flighty export, which in a scheduled setup is a path in the
    # repository rather than a URL.
    _env_source(sources, "tripit_ical", "url", env.get("TRIPIT_ICAL_URL"))
    _env_source(sources, "flighty_csv", "path", env.get("FLIGHTY_CSV_PATH"))

    if env.get("TIM_API_KEY"):
        emissions["api_key"] = env["TIM_API_KEY"]
    if env.get("EMISSIONS_PROVIDER"):
        emissions["provider"] = env["EMISSIONS_PROVIDER"]

    # Flat keys, as written by pre-v0.1.0 config.json files.
    if not emissions.get("api_key") and file_data.get("TIM_API_KEY"):
        emissions["api_key"] = file_data["TIM_API_KEY"]
    if not sources and file_data.get("TRIPIT_ICAL_URL"):
        sources.append({"type": "tripit_ical", "url": file_data["TRIPIT_ICAL_URL"]})

    resolved_csv = (
        csv_path
        or env.get("CSV_PATH")
        or file_data.get("csv_path")
        or file_data.get("CSV_PATH")
        or DEFAULT_CSV_PATH
    )

    raw_path = csv_path_or_none(env.get("RAW_PATH") or file_data.get("raw_path"))
    raw_log = file_data.get("raw_log", True)
    if env.get("RAW_LOG"):
        raw_log = env["RAW_LOG"].strip().lower() not in ("0", "false", "no", "off")

    return Config(
        csv_path=resolved_csv,
        sources=sources,
        emissions=emissions,
        raw_path=raw_path,
        raw_log=bool(raw_log),
    )
