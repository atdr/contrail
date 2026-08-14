"""Tests for config resolution: CLI flags > env vars > config file > defaults."""

import json

import pytest

from contrail.config import DEFAULT_CSV_PATH, ConfigError, load_config


def write_config(directory, data, name="config.json"):
    path = directory / name
    path.write_text(json.dumps(data))
    return path


def test_defaults_with_no_config_at_all(tmp_path):
    config = load_config(env={}, directory=str(tmp_path))
    assert config.csv_path == DEFAULT_CSV_PATH
    assert config.sources == []
    assert config.provider_name == "tim"


def test_env_var_alone_synthesizes_a_source(tmp_path):
    """The common case: TRIPIT_ICAL_URL set, no config file anywhere."""
    config = load_config(
        env={"TRIPIT_ICAL_URL": "https://example.invalid/feed.ics", "TIM_API_KEY": "k"},
        directory=str(tmp_path),
    )
    assert config.sources == [{"type": "tripit_ical", "url": "https://example.invalid/feed.ics"}]
    assert config.api_key == "k"


def test_config_file_is_read(tmp_path):
    write_config(
        tmp_path,
        {
            "csv_path": "custom.csv",
            "sources": [{"type": "tripit_ical", "url": "https://from-file.invalid/f.ics"}],
            "emissions": {"api_key": "file-key"},
        },
    )
    config = load_config(env={}, directory=str(tmp_path))
    assert config.csv_path == "custom.csv"
    assert config.api_key == "file-key"
    assert len(config.sources) == 1


def test_env_beats_the_config_file(tmp_path):
    write_config(
        tmp_path,
        {
            "csv_path": "from-file.csv",
            "sources": [{"type": "tripit_ical", "url": "https://from-file.invalid/f.ics"}],
            "emissions": {"api_key": "file-key"},
        },
    )
    config = load_config(
        env={
            "TRIPIT_ICAL_URL": "https://from-env.invalid/f.ics",
            "TIM_API_KEY": "env-key",
            "CSV_PATH": "from-env.csv",
        },
        directory=str(tmp_path),
    )
    assert config.csv_path == "from-env.csv"
    assert config.api_key == "env-key"
    # The env URL updates the existing source rather than adding a duplicate.
    assert config.sources == [{"type": "tripit_ical", "url": "https://from-env.invalid/f.ics"}]


def test_flag_beats_env(tmp_path):
    config = load_config(
        csv_path="from-flag.csv",
        env={"CSV_PATH": "from-env.csv"},
        directory=str(tmp_path),
    )
    assert config.csv_path == "from-flag.csv"


def test_multiple_sources_are_preserved(tmp_path):
    write_config(
        tmp_path,
        {
            "sources": [
                {"type": "tripit_ical", "url": "https://one.invalid/f.ics"},
                {"type": "tripit_ical", "url": "https://two.invalid/f.ics"},
            ]
        },
    )
    config = load_config(env={}, directory=str(tmp_path))
    assert len(config.sources) == 2


def test_prototype_style_flat_config_still_works(tmp_path):
    """The single-file prototype's config.json shape shouldn't break on upgrade."""
    write_config(
        tmp_path,
        {"TRIPIT_ICAL_URL": "https://legacy.invalid/f.ics", "TIM_API_KEY": "legacy-key"},
    )
    config = load_config(env={}, directory=str(tmp_path))
    assert config.sources == [{"type": "tripit_ical", "url": "https://legacy.invalid/f.ics"}]
    assert config.api_key == "legacy-key"


def test_yaml_config(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "config.yaml").write_text(
        "csv_path: y.csv\nsources:\n  - type: tripit_ical\n    url: https://y.invalid/f.ics\n"
    )
    config = load_config(env={}, directory=str(tmp_path))
    assert config.csv_path == "y.csv"
    assert config.sources[0]["url"] == "https://y.invalid/f.ics"


def test_missing_explicit_config_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(config_path=str(tmp_path / "nope.json"), env={})


def test_require_sources_explains_itself(tmp_path):
    config = load_config(env={}, directory=str(tmp_path))
    with pytest.raises(ConfigError, match="TRIPIT_ICAL_URL"):
        config.require_sources()


def test_require_api_key_explains_itself(tmp_path):
    config = load_config(env={}, directory=str(tmp_path))
    with pytest.raises(ConfigError, match="TIM_API_KEY"):
        config.require_api_key()
