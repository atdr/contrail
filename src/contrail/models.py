"""Core data model shared by importers, emissions providers, and storage backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

# Cabin classes contrail knows about, in the order TIM reports them.
CABIN_CLASSES = ("first", "business", "premium_economy", "economy")

# Why the trip was taken, where a source says. Not every source does, and no
# figure depends on it — it is there so business travel can be split out of a
# total by whatever reads the CSV.
FLIGHT_REASONS = ("business", "leisure")


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
    # The departure as an instant, in the origin's own timezone, when the source
    # states a time. flight_date stays the local calendar date, because that is
    # what TIM asks for and what the log sorts on.
    departure_time: datetime | None = None
    cabin_class: str | None = None  # set only if the source knows what was actually flown
    # On a codeshare the ticket shows a marketing flight (IB3643) that is really
    # operated by someone else (BA458). TIM's field is `operatingCarrierCode`,
    # and it only prices the operating flight, so keep both: carrier_code and
    # flight_number stay as booked, and these carry who actually flies it.
    operating_carrier_code: str | None = None
    operating_flight_number: str | None = None
    aircraft_type: str | None = None  # as the source names it; TIM never names one
    flight_reason: str | None = None  # "business" | "leisure", if the source says
    # Stated by the source, as opposed to inferred from a flight disappearing.
    cancelled: bool = False
    # Keys of other entries found to be this same flight. Populated when two
    # sources both report it, or when one source lists it twice.
    also_seen: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # original source data, for debugging

    @property
    def key(self) -> str:
        """Namespaced dedup key. IDs from different sources can never collide."""
        return f"{self.source}:{self.source_id}"

    @property
    def identity(self) -> tuple[str, str, str] | None:
        """What makes this the *same flight* regardless of which source found it.

        Deliberately not the flight number. ``BA16`` can be two legs on one day —
        SYD-SIN-LHR, flown in different cabins — and they are two flights, two
        emissions figures, and two rows. Route and date separate them; a flight
        number would collapse them and lose one.

        None when a field is missing, which keeps a half-parsed record from
        matching anything.
        """
        if not (self.flight_date and self.origin and self.destination):
            return None
        # Upper-cased to agree with ``resync.identity``, which normalizes what it
        # reads from the file. TripIt's FROM_TO_RE is IGNORECASE, so a feed
        # phrasing like "from ... (sfo) to ... (jfk)" really does yield lower
        # case, and an unnormalized comparison would silently miss the match and
        # write a second row for the same flight.
        return (self.flight_date.isoformat(), self.origin.upper(), self.destination.upper())

    @property
    def pricing_carrier_code(self) -> str:
        """The carrier to price against: whoever actually operates the flight."""
        return self.operating_carrier_code or self.carrier_code

    @property
    def pricing_flight_number(self) -> str:
        return self.operating_flight_number or self.flight_number

    def has_departed(self, now: datetime) -> bool:
        """Whether the flight has already gone, as precisely as the source allows.

        Matters to more than the freeze boundary: TIM's detailed endpoint
        *rejects* a past departure date outright, so asking about a flown flight
        fails the whole batch rather than simply returning nothing.
        """
        from contrail.airports import today_at

        if self.departure_time is not None and self.departure_time.tzinfo is not None:
            return now >= self.departure_time
        return self.flight_date < today_at(self.origin, now)

    @property
    def is_codeshare(self) -> bool:
        return bool(self.operating_flight_number) and (
            self.operating_carrier_code,
            self.operating_flight_number,
        ) != (self.carrier_code, self.flight_number)


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
    raw_text: str  # the source text, for manual review
    partial: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


@dataclass
class EmissionsResult:
    """Per-passenger CO2e for one flight, in grams, as returned by the provider.

    Carries more than the four figures because TIM cannot be asked again: once a
    flight has departed it will not price it, so whatever was captured at the
    time is all there will ever be. ``raw`` holds the untouched response for the
    same reason.
    """

    method: str  # "exact" | "typical_route_average" | "no_data"
    model_version: str | None = None
    grams_first: int | None = None
    grams_business: int | None = None
    grams_premium_economy: int | None = None
    grams_economy: int | None = None
    data_source: str | None = None  # TIM | EASA
    contrails_impact: str | None = None  # negligible | moderate | severe
    distance_km: int | None = None
    aircraft_match: str | None = None  # e.g. AIRCRAFT_MAPPING_EXACT
    raw: dict = field(default_factory=dict)

    def grams_for(self, cabin_class: str) -> int | None:
        """Grams for a named cabin class, or None if unknown/unavailable."""
        return getattr(self, f"grams_{cabin_class}", None)
