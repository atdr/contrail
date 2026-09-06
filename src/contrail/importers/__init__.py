"""Importer registry: maps a config ``type:`` string to an Importer class."""

from __future__ import annotations

from contrail.config import lookup_type
from contrail.importers.base import Importer
from contrail.importers.flighty_csv import FlightyCSVImporter
from contrail.importers.tripit_ical import TripItICalImporter

# Adding an importer = one new module + one line here.
IMPORTERS: dict[str, type[Importer]] = {
    TripItICalImporter.id: TripItICalImporter,
    FlightyCSVImporter.id: FlightyCSVImporter,
}


def get_importer(type_name: str) -> type[Importer]:
    """Look up an importer class by its config ``type:`` string."""
    return lookup_type(IMPORTERS, type_name, "importer type", "importers")


__all__ = [
    "IMPORTERS",
    "FlightyCSVImporter",
    "Importer",
    "TripItICalImporter",
    "get_importer",
]
