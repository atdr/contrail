"""The shipped example configs, checked against the code they describe.

Nothing used to load these. Pre-commit proves they parse and pre-commit is not
run in CI, so they could drift from `config.py` indefinitely, which is exactly
what happened: the flighty importer arrived in `config.example.yaml` and never
reached `config.example.json`, while the YAML told readers the JSON carried the
same fields.
"""

from pathlib import Path

import pytest

from contrail.config import load_config
from contrail.emissions import PROVIDERS
from contrail.importers import IMPORTERS
from contrail.storage import RAW_LOGS, STORAGES

ROOT = Path(__file__).resolve().parent.parent
JSON = ROOT / "config.example.json"
YAML = ROOT / "config.example.yaml"


def load(path: Path):
    if path.suffix == ".yaml":
        pytest.importorskip("yaml")
    # An explicit path and an empty environment: never the working directory,
    # which in a checkout holds the developer's real config.
    return load_config(config_path=str(path), env={})


@pytest.mark.parametrize("path", [JSON, YAML], ids=lambda p: p.name)
def test_every_type_resolves_against_a_registry(path):
    """The drift-killer. An example naming an importer, provider or backend
    that contrail cannot look up is a broken example."""
    config = load(path)

    assert {entry["type"] for entry in config.importers} == set(IMPORTERS)  # all of them shown
    assert config.provider_name in PROVIDERS
    assert config.storage_type in STORAGES
    assert config.raw_log_type in RAW_LOGS


@pytest.mark.parametrize("path", [JSON, YAML], ids=lambda p: p.name)
def test_an_example_lists_tripit_before_flighty(path):
    """Order is not decoration here: the first importer listed owns a flight
    both report, and the export is the one that fills in the cabin. Swapping
    them changes which source owns every row a new user ends up with."""
    assert [entry["type"] for entry in load(path).importers] == ["tripit_ical", "flighty_csv"]


@pytest.mark.parametrize("path", [JSON, YAML], ids=lambda p: p.name)
def test_an_example_is_written_in_the_current_schema(path, capsys):
    """No deprecation line, or the file we hand people teaches the old shape."""
    load(path)

    assert capsys.readouterr().err == ""


def test_the_two_examples_describe_the_same_configuration():
    """`config.example.yaml` tells readers the JSON has the "same fields, no
    comments". Field-for-field rather than a key comparison, since it is the
    values that drifted last time."""
    assert load(JSON) == load(YAML)


@pytest.mark.parametrize("path", [JSON, YAML], ids=lambda p: p.name)
def test_an_example_carries_no_real_secret(path):
    """Both files are committed, and both have a slot shaped like a credential."""
    config = load(path)

    assert config.api_key == "YOUR-GOOGLE-CLOUD-API-KEY"
    assert "YOUR-FEED-ID" in config.importers[0]["url"]
