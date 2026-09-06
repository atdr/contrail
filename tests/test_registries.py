"""The three seams' registries, and how they refuse a name they don't know.

A config `type:` is resolved against one of these. What matters is that all of
them refuse the same way — as a *configuration* error, since a name contrail
doesn't recognise is a fault in the user's file rather than a bug in the run.
"""

import pytest

from contrail.config import ConfigError
from contrail.emissions import PROVIDERS, get_provider
from contrail.importers import IMPORTERS, get_importer
from contrail.storage import RAW_LOGS, STORAGES, get_raw_log, get_storage
from contrail.storage.local_csv import LocalCSVStorage
from contrail.storage.raw_log import JSONLRawLog

LOOKUPS = [
    (get_importer, IMPORTERS, "tripit_ical"),
    (get_provider, PROVIDERS, "tim"),
    (get_storage, STORAGES, "local_csv"),
    (get_raw_log, RAW_LOGS, "jsonl"),
]


@pytest.mark.parametrize(("lookup", "registry", "known"), LOOKUPS)
def test_a_registry_resolves_the_names_it_advertises(lookup, registry, known):
    assert lookup(known) is registry[known]


@pytest.mark.parametrize(("lookup", "registry", "known"), LOOKUPS)
def test_an_unknown_type_is_a_configuration_error(lookup, registry, known):
    """`main` prints ConfigError as "Configuration error" and ValueError as
    "Error". A `type:` contrail doesn't know is the former, and used to be
    reported as the latter — while a *missing* type was already the former."""
    with pytest.raises(ConfigError) as excinfo:
        lookup("nope")

    assert "nope" in str(excinfo.value)
    assert known in str(excinfo.value)  # and it names what is available


@pytest.mark.parametrize(("lookup", "registry", "known"), LOOKUPS)
def test_every_entry_is_keyed_by_its_own_id(lookup, registry, known):
    """The key is the class's `id`, not a string repeated beside it, so a
    registry cannot advertise a name the class doesn't answer to."""
    assert all(name == cls.id for name, cls in registry.items())


def test_a_raw_log_is_not_a_storage_backend():
    """Separate registries on purpose: a raw log appends provider answers and
    has no load/save pair, so the two are not substitutable. See the Seams
    section of docs/storage.md."""
    assert JSONLRawLog.id not in STORAGES
    assert LocalCSVStorage.id not in RAW_LOGS

    with pytest.raises(ConfigError):
        get_storage(JSONLRawLog.id)
    with pytest.raises(ConfigError):
        get_raw_log(LocalCSVStorage.id)
