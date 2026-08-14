"""Core data model shared by importers, emissions providers, and storage backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Cabin classes contrail knows about, in the order TIM reports them.
CABIN_CLASSES = ("first", "business", "premium_economy", "economy")


@dataclass
class FlightRecord:
    """A single flight, fully parsed and ready to price."""

    source: str  # importer id, e.g. "tripit_ical", "flighty_csv"
    source_id: str  # importer-specific unique id (dedup key, namespaced by source)
    flight_date: date
    carrier_code: str
    flight_number: str
    origin: str  # IATA code
    destination: str  # IATA code
    cabin_class: str | None = None  # set only if the source knows what was actually flown
    raw: dict = field(default_factory=dict)  # original source data, for debugging

    @property
    def key(self) -> str:
        """Namespaced dedup key. IDs from different sources can never collide."""
        return f"{self.source}:{self.source_id}"


@dataclass
class UnparsedEvent:
    """Something that looked like a flight but couldn't be fully parsed.

    ``partial`` carries whatever fields *were* recovered. It matters most for
    ``flight_date``: storage sorts rows chronologically to build the cumulative
    total, and a row with no date sorts to the very top and stays there. Keeping
    a partial date means the row sits in its real place, so filling the emissions
    in by hand later just works.
    """

    source: str
    source_id: str
    raw_text: str  # for manual review, mirrors the prototype's "unparsed" rows
    partial: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass
class EmissionsResult:
    """Per-passenger CO2e for one flight, in grams, as returned by the provider."""

    method: str  # "exact" | "typical_route_average" | "no_data"
    model_version: str | None = None
    grams_first: int | None = None
    grams_business: int | None = None
    grams_premium_economy: int | None = None
    grams_economy: int | None = None

    def grams_for(self, cabin_class: str) -> int | None:
        """Grams for a named cabin class, or None if unknown/unavailable."""
        return getattr(self, f"grams_{cabin_class}", None)
