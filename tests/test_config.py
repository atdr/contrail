"""Tests for config resolution: CLI flags > env vars > config file > defaults."""

import json

import pytest

from contrail.config import DEFAULT_CSV_PATH, ConfigError, load_config


@pytest.fixture(autouse=True)
def away_from_the_developers_config(monkeypatch, tmp_path):
    """`load_config` defaults `directory` to the working directory, and this
    repository's root holds a real gitignored `config.json` with a real API key.
    A test that forgets both `env=` and `directory=` would read it, and a
    failure message would print it."""
    monkeypatch.chdir(tmp_path)


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


def test_flat_config_keys_still_work(tmp_path):
    """Pre-v0.1.0 config.json used flat keys. Upgrading shouldn't break them."""
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


# -- the sections, one per package -------------------------------------------


def test_every_section_resolves(tmp_path):
    write_config(
        tmp_path,
        {
            "importers": [{"type": "tripit_ical", "url": "https://example.invalid/f.ics"}],
            "emissions": {"type": "tim", "api_key": "key"},
            "storage": {
                "flights": {"type": "local_csv", "path": "log.csv"},
                "raw_log": {"type": "jsonl", "path": "log.raw.jsonl", "enabled": False},
            },
            "passport": {"output_path": "dash.html"},
        },
    )
    config = load_config(env={}, directory=str(tmp_path))

    assert config.importers[0]["type"] == "tripit_ical"
    assert config.provider_name == "tim"
    assert config.api_key == "key"
    assert (config.storage_type, config.csv_path) == ("local_csv", "log.csv")
    assert (config.raw_log_type, config.raw_path) == ("jsonl", "log.raw.jsonl")
    assert config.raw_log_enabled is False
    assert config.passport_output == "dash.html"


def test_importers_keep_the_order_they_were_written_in(tmp_path):
    """Order is the documented semantics, not an accident: the first importer
    listed owns a flight two of them report. See docs/resync.md."""
    write_config(
        tmp_path,
        {
            "importers": [
                {"type": "flighty_csv", "path": "flighty/"},
                {"type": "tripit_ical", "url": "https://example.invalid/f.ics"},
            ]
        },
    )
    config = load_config(env={}, directory=str(tmp_path))

    assert [entry["type"] for entry in config.importers] == ["flighty_csv", "tripit_ical"]


def test_an_absent_raw_log_section_is_not_a_disabled_one(tmp_path):
    write_config(tmp_path, {"storage": {"flights": {"path": "log.csv"}}})
    config = load_config(env={}, directory=str(tmp_path))

    assert config.raw_log_enabled is True
    assert config.raw_path is None  # so the CLI derives it from the CSV


def test_an_empty_enabled_leaves_the_raw_log_on(tmp_path):
    """`enabled:` with nothing after it is a key saying nothing, which has to
    mean the default like every other empty value in the file. Reading it as
    false is the one mistake here that cannot be undone: TIM refuses to price a
    departed flight a second time, so provenance skipped is provenance gone."""
    write_config(tmp_path, {"storage": {"raw_log": {"enabled": None}}})

    assert load_config(env={}, directory=str(tmp_path)).raw_log_enabled is True


def test_a_storage_role_left_empty_is_not_a_crash(tmp_path):
    """A role with every key commented out parses as None, which is the shape
    the shipped YAML example teaches by commenting out `path:`. The env layer
    writes into these, so an unnormalized None reached the user as a
    `TypeError` traceback rather than anything about configuration."""
    write_config(tmp_path, {"storage": {"flights": None, "raw_log": None}})
    config = load_config(
        env={"CSV_PATH": "env.csv", "RAW_PATH": "env.jsonl"}, directory=str(tmp_path)
    )

    assert config.csv_path == "env.csv"
    assert config.raw_path == "env.jsonl"


def test_a_storage_role_that_is_not_a_mapping_is_a_configuration_error(tmp_path):
    write_config(tmp_path, {"storage": {"flights": "log.csv"}})

    with pytest.raises(ConfigError, match="'storage.flights' must be a mapping"):
        load_config(env={}, directory=str(tmp_path))


def test_storage_as_a_list_is_a_configuration_error(tmp_path):
    """It reads as a list to anyone who just wrote `importers:` as one, and a
    list of backends has no answer to which one `load()` reads from."""
    write_config(tmp_path, {"storage": [{"type": "local_csv"}]})

    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(env={}, directory=str(tmp_path))


def test_an_unknown_storage_role_is_a_configuration_error(tmp_path):
    write_config(tmp_path, {"storage": {"flght": {"path": "log.csv"}}})

    with pytest.raises(ConfigError, match="flights, raw_log"):
        load_config(env={}, directory=str(tmp_path))


def test_importers_as_a_mapping_is_a_configuration_error(tmp_path):
    """The mirror of the storage mistake, and the one the storage error message
    invites by saying only `importers` is a list. Keyed by type it reads well,
    yields a list of bare strings, and used to fail as an AttributeError from
    inside an importer, a long way from the line that caused it."""
    write_config(tmp_path, {"importers": {"tripit_ical": {"url": "u"}}})

    with pytest.raises(ConfigError, match="'importers' must be a list"):
        load_config(env={"TRIPIT_ICAL_URL": "u"}, directory=str(tmp_path))


def test_an_importer_entry_that_is_not_a_mapping_is_a_configuration_error(tmp_path):
    """Reported by position and type, never by value. The likely way to write a
    bare entry is to paste the feed URL as one, and that URL is a credential:
    anyone holding it can read the itineraries. The bug report template asks
    people to paste what contrail printed, so an echoed value travels."""
    secret = "https://www.tripit.com/feed/ical/private/NOT-A-REAL-FEED-ID/tripit.ics"
    write_config(tmp_path, {"importers": [secret]})

    with pytest.raises(ConfigError, match="'importers' entry 1 must be a mapping") as caught:
        load_config(env={}, directory=str(tmp_path))

    assert secret not in str(caught.value)
    assert "NOT-A-REAL-FEED-ID" not in str(caught.value)


def test_an_unknown_passport_key_is_named_rather_than_ignored_in_silence(tmp_path, capsys):
    """`passport` carries no `type`, so no registry and no implementation owns
    its leftovers and the key set is as closed as the section names. It is also
    where a typo hides best: the flag is spelled `--output`, so `output:` is
    the natural thing to write, and the dashboard lands at the default path."""
    write_config(tmp_path, {"passport": {"output": "dash.html"}})
    config = load_config(env={}, directory=str(tmp_path))

    assert "'passport.output' is not a key" in capsys.readouterr().err
    assert config.passport_output == "passport.html"


def test_an_unknown_section_is_named_rather_than_ignored_in_silence(tmp_path, capsys):
    """A typo used to surface as "No flight sources configured", which points
    at the wrong problem entirely."""
    write_config(tmp_path, {"importer": [{"type": "tripit_ical"}]})
    load_config(env={}, directory=str(tmp_path))

    assert "'importer' is not a section" in capsys.readouterr().err


@pytest.mark.parametrize(
    "data",
    [
        {"importers": [{"type": "tripit_ical", "url": "u", "something_new": 1}]},
        {"storage": {"flights": {"type": "local_csv", "path": "l.csv", "bucket": "b"}}},
    ],
    ids=["importer", "storage_role"],
)
def test_keys_inside_an_entry_are_never_second_guessed(tmp_path, capsys, data):
    """An entry carrying a `type` has an implementation behind it, and that
    implementation owns every other key: config.py does not know what a `url`
    or a `bucket` is and must not start guessing. That is what lets a new
    backend define its own shape, which is the whole point of the registries.
    See importers/base.py."""
    write_config(tmp_path, data)
    load_config(env={}, directory=str(tmp_path))

    assert capsys.readouterr().err == ""


# -- the passport output path ------------------------------------------------


def test_passport_output_resolves_through_all_four_layers(tmp_path):
    write_config(tmp_path, {"passport": {"output_path": "file.html"}})
    where = {"directory": str(tmp_path)}

    assert load_config(env={}, **where).passport_output == "file.html"
    assert load_config(env={"PASSPORT_OUTPUT": "env.html"}, **where).passport_output == "env.html"
    assert (
        load_config(
            env={"PASSPORT_OUTPUT": "env.html"}, passport_output="flag.html", **where
        ).passport_output
        == "flag.html"
    )


def test_passport_output_falls_back_to_the_default(tmp_path):
    assert load_config(env={}, directory=str(tmp_path)).passport_output == "passport.html"


# -- the 0.4.x schema, still read ---------------------------------------------

SUPERSEDED = [
    ({"sources": [{"type": "tripit_ical", "url": "u"}]}, "'sources'", "importers"),
    ({"csv_path": "old.csv"}, "'csv_path'", "storage.flights.path"),
    ({"CSV_PATH": "old.csv"}, "'CSV_PATH'", "storage.flights.path"),
    ({"raw_path": "old.jsonl"}, "'raw_path'", "storage.raw_log.path"),
    ({"raw_log": False}, "'raw_log'", "storage.raw_log.enabled"),
    ({"emissions": {"provider": "tim"}}, "'emissions.provider'", "emissions.type"),
    ({"TIM_API_KEY": "k"}, "'TIM_API_KEY'", "emissions.api_key"),
    ({"TRIPIT_ICAL_URL": "u"}, "'TRIPIT_ICAL_URL'", "importers"),
]


@pytest.mark.parametrize(("data", "old", "new"), SUPERSEDED)
def test_a_superseded_key_still_works_and_names_its_replacement(tmp_path, capsys, data, old, new):
    write_config(tmp_path, data)
    load_config(env={}, directory=str(tmp_path))

    err = capsys.readouterr().err
    assert old in err
    assert new in err


def test_a_whole_0_4_x_config_still_resolves(tmp_path):
    """The shape every existing config.json is written in."""
    write_config(
        tmp_path,
        {
            "csv_path": "old.csv",
            "sources": [{"type": "tripit_ical", "url": "https://example.invalid/f.ics"}],
            "emissions": {"provider": "tim", "api_key": "key"},
            "raw_log": False,
            "raw_path": "old.raw.jsonl",
        },
    )
    config = load_config(env={}, directory=str(tmp_path))

    assert config.csv_path == "old.csv"
    assert config.importers[0]["url"] == "https://example.invalid/f.ics"
    assert config.provider_name == "tim"
    assert config.api_key == "key"
    assert config.raw_log_enabled is False
    assert config.raw_path == "old.raw.jsonl"


def test_a_new_key_wins_over_the_one_it_replaced(tmp_path, capsys):
    """Otherwise a config could not be migrated one key at a time."""
    write_config(
        tmp_path,
        {
            "csv_path": "old.csv",
            "storage": {"flights": {"path": "new.csv"}},
            "sources": [{"type": "flighty_csv", "path": "old/"}],
            "importers": [{"type": "tripit_ical", "url": "u"}],
        },
    )
    config = load_config(env={}, directory=str(tmp_path))

    assert config.csv_path == "new.csv"
    assert [entry["type"] for entry in config.importers] == ["tripit_ical"]
    assert "storage.flights.path is already set. Drop 'csv_path'." in capsys.readouterr().err


SUPERSEDING_LAYERS = [
    ({"emissions": {"provider": "tim"}}, {"EMISSIONS_PROVIDER": "tim"}, "EMISSIONS_PROVIDER"),
    ({"csv_path": "old.csv"}, {"CSV_PATH": "env.csv"}, "CSV_PATH"),
    ({"raw_path": "old.jsonl"}, {"RAW_PATH": "env.jsonl"}, "RAW_PATH"),
    ({"raw_log": False}, {"RAW_LOG": "true"}, "RAW_LOG"),
    ({"TIM_API_KEY": "old"}, {"TIM_API_KEY": "env"}, "TIM_API_KEY"),
    ({"TRIPIT_ICAL_URL": "old"}, {"TRIPIT_ICAL_URL": "env"}, "TRIPIT_ICAL_URL"),
]


@pytest.mark.parametrize(("data", "env", "layer"), SUPERSEDING_LAYERS, ids=lambda v: str(v)[:24])
def test_a_superseded_key_beaten_by_the_environment_says_so(tmp_path, capsys, data, env, layer):
    """The warning is the entire migration path, so it has to name the thing
    actually winning. Every one of these resolves to the environment's value,
    and calling the file key "still honoured" would send someone to edit a line
    that was never the cause."""
    write_config(tmp_path, data)
    load_config(env=env, directory=str(tmp_path))

    assert f"{layer} is overriding it on this run" in capsys.readouterr().err


def test_a_superseded_key_beaten_by_a_flag_says_so(tmp_path, capsys):
    """Flags supersede the file too, and are the layer furthest from it."""
    write_config(tmp_path, {"csv_path": "old.csv"})
    config = load_config(csv_path="flag.csv", env={}, directory=str(tmp_path))

    assert config.csv_path == "flag.csv"
    assert "--csv-path is overriding it on this run" in capsys.readouterr().err


def test_an_environment_only_setup_warns_about_nothing(tmp_path, capsys):
    """The contrail-gh tripwire. The template and every instance created from it
    configure contrail through environment variables alone and ship no config
    file, so a deprecation line here would print on every scheduled run in every
    instance, for a file nobody has."""
    config = load_config(
        env={
            "TRIPIT_ICAL_URL": "https://example.invalid/f.ics",
            "FLIGHTY_CSV_PATH": "flighty/",
            "TIM_API_KEY": "key",
            "CSV_PATH": "flight_emissions.csv",
            "RAW_LOG": "false",
            "RAW_PATH": "raw.jsonl",
            "EMISSIONS_PROVIDER": "tim",
        },
        directory=str(tmp_path),
    )

    assert capsys.readouterr().err == ""
    assert [entry["type"] for entry in config.importers] == ["tripit_ical", "flighty_csv"]
    assert config.csv_path == "flight_emissions.csv"
    assert config.raw_log_enabled is False
    assert config.raw_path == "raw.jsonl"
    assert config.provider_name == "tim"
