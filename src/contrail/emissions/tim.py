"""Google Travel Impact Model (TIM) emissions provider.

Ported from the prototype's ``compute_exact_emissions`` / ``compute_typical_emissions``.
The hybrid behaviour is deliberate and correct:

1. Batch every new flight into ``computeFlightEmissions``. This only returns real
   numbers for flights that have not departed yet — a property of Google's API,
   not a bug here.
2. Anything that comes back without emissions data (i.e. it already departed)
   goes into a batched ``computeTypicalFlightEmissions`` call, which gives a
   route/market average for any date.
3. Each flight records which method produced its number.

Practical implication: run the sync regularly, so upcoming flights are priced
exactly *before* they depart. Flights first discovered after they've flown get
the route average instead.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone

import requests

from contrail.models import EmissionsResult, FlightRecord

TIM_BASE_URL = "https://travelimpactmodel.googleapis.com/v1"
BATCH_SIZE = 200  # flights per TIM API request
REQUEST_TIMEOUT = 30


def _format_model_version(version: dict | None) -> str:
    """``3.0.0+20260814`` — the whole thing, not just the major.

    ``dated`` is the one that matters in practice: TIM rebuilds its dataset on
    that cadence, so two prices carrying the same ``dated`` cannot differ, and a
    figure is only ever as current as the dataset that produced it.
    """
    version = version or {}
    major, minor, patch = (version.get(k) for k in ("major", "minor", "patch"))
    if major is None:
        return json.dumps(version) if version else ""
    core = ".".join(str(part if part is not None else 0) for part in (major, minor, patch))
    dated = version.get("dated")
    return f"{core}+{dated}" if dated else core


def _as_detailed(data: dict) -> dict:
    """Reshape a plain ``computeFlightEmissions`` reply as a detailed one.

    Used only when the detailed endpoint has refused a batch: the figures are
    identical, so this keeps them at the cost of the provenance.
    """
    entries = [
        {
            "flight": entry.get("flight"),
            "flightEmissionsDetails": {
                key: entry[key]
                for key in ("emissionsGramsPerPax", "source", "contrailsImpactBucket")
                if key in entry
            },
        }
        for entry in data.get("flightEmissions", [])
    ]
    return {"modelVersion": data.get("modelVersion"), "flightsWithDetailedEmissions": entries}


def _identity(flight: dict | None) -> tuple:
    """A flight's identifiers, as the request and response both state them."""
    flight = flight or {}
    departure = flight.get("departureDate") or {}
    return (
        (flight.get("origin") or "").upper(),
        (flight.get("destination") or "").upper(),
        (flight.get("operatingCarrierCode") or "").upper(),
        flight.get("flightNumber"),
        departure.get("year"),
        departure.get("month"),
        departure.get("day"),
    )


def _grams_of(entry: dict | None) -> dict | None:
    if not entry:
        return None
    return (entry.get("flightEmissionsDetails") or {}).get("emissionsGramsPerPax")


def _provenance(entry: dict) -> dict:
    """Provenance entries keyed by type, e.g. FUEL_BURN, DISTANCE_ADJUSTMENT."""
    metadata = entry.get("emissionsMetadata") or {}
    entries = (metadata.get("emissionsProvenance") or {}).get("provenanceEntries") or []
    return {e.get("provenanceEntryType"): e for e in entries if e.get("provenanceEntryType")}


def _exact_result(entry: dict, grams: dict) -> EmissionsResult:
    details = entry.get("flightEmissionsDetails") or {}
    provenance = _provenance(entry)
    bucket = details.get("contrailsImpactBucket") or ""
    return EmissionsResult(
        method="exact",
        model_version=entry.get("modelVersion"),
        grams_first=grams.get("first"),
        grams_business=grams.get("business"),
        grams_premium_economy=grams.get("premiumEconomy"),
        grams_economy=grams.get("economy"),
        data_source=details.get("source"),
        # CONTRAILS_IMPACT_MODERATE -> moderate
        contrails_impact=bucket.removeprefix("CONTRAILS_IMPACT_").lower() or None,
        distance_km=(provenance.get("DISTANCE_ADJUSTMENT") or {}).get("estimatedFlightDistanceKm"),
        # The nearest thing TIM offers to naming the aircraft: it says how well
        # it matched an airframe, never which one.
        aircraft_match=(provenance.get("FUEL_BURN") or {}).get("fuelBurnEeaStrategy"),
        raw=entry,
    )


class TIMEmissionsProvider:
    """Prices flights using Google's Travel Impact Model API."""

    id = "tim"

    def __init__(self, api_key: str, base_url: str = TIM_BASE_URL):
        if not api_key:
            raise ValueError("TIMEmissionsProvider needs an API key (TIM_API_KEY).")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    # -- public API ---------------------------------------------------------

    def compute(
        self, flights: Sequence[FlightRecord], now: datetime | None = None
    ) -> dict[str, EmissionsResult]:
        flights = list(flights)
        if not flights:
            return {}

        # Only flights that haven't departed can have an exact figure, and the
        # detailed endpoint doesn't merely return nothing for a past date — it
        # rejects the request with a 400, taking the whole batch down with it.
        # So never ask about a flight that has already gone. The caller passes
        # its own clock so one sync never disagrees with itself about "now".
        now = now or datetime.now(timezone.utc)
        upcoming = [f for f in flights if not f.has_departed(now)]

        exact = self._compute_exact(upcoming) if upcoming else {}

        needs_fallback = [f for f in flights if not _grams_of(exact.get(f.key))]
        typical = self._compute_typical(needs_fallback) if needs_fallback else {}

        results: dict[str, EmissionsResult] = {}
        for flight in flights:
            detail = exact.get(flight.key)
            grams = _grams_of(detail)
            if grams:
                results[flight.key] = _exact_result(detail, grams)
                continue

            fallback = typical.get(flight.key)
            grams = (fallback or {}).get("emissionsGramsPerPax")
            if not grams:
                results[flight.key] = EmissionsResult(method="no_data")
                continue
            results[flight.key] = EmissionsResult(
                method="typical_route_average",
                model_version=(fallback or {}).get("modelVersion"),
                grams_first=grams.get("first"),
                grams_business=grams.get("business"),
                grams_premium_economy=grams.get("premiumEconomy"),
                grams_economy=grams.get("economy"),
                raw=fallback or {},
            )
        return results

    # -- internals ----------------------------------------------------------

    def _post(self, endpoint: str, body: dict) -> dict:
        # The key goes in a header, never the query string. requests embeds the
        # full URL in the HTTPError it raises, and the README suggests piping
        # cron output to a log file — a query-string key would end up written
        # there in plaintext on any 403 or quota error.
        url = f"{self.base_url}/flights:{endpoint}"
        resp = requests.post(
            url,
            json=body,
            headers={"x-goog-api-key": self.api_key},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _post_detailed(self, requests_body: list[dict]) -> dict:
        """The detailed endpoint, narrowing in on whatever it refuses.

        It validates strictly and rejects an entire batch over one bad entry.
        Falling the whole batch back to the plain endpoint would keep the figures
        but lose the provenance for every good flight in it — and provenance
        cannot be re-fetched once a flight departs. So split and retry, and only
        drop to the plain endpoint for the single flight that is actually at
        fault.
        """
        try:
            return self._post("computeDetailedFlightEmissions", {"flights": requests_body})
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 400:
                raise
            if len(requests_body) == 1:
                return _as_detailed(
                    self._post("computeFlightEmissions", {"flights": requests_body})
                )

        half = len(requests_body) // 2
        left = self._post_detailed(requests_body[:half])
        right = self._post_detailed(requests_body[half:])
        return {
            "modelVersion": left.get("modelVersion") or right.get("modelVersion"),
            "flightsWithDetailedEmissions": (
                left.get("flightsWithDetailedEmissions", [])
                + right.get("flightsWithDetailedEmissions", [])
            ),
        }

    def _request_for(self, flight: FlightRecord) -> dict:
        return {
            "origin": flight.origin,
            "destination": flight.destination,
            # The field means what it says: on a codeshare, TIM only prices the
            # operating flight, not the marketing one.
            "operatingCarrierCode": flight.pricing_carrier_code,
            "flightNumber": int(flight.pricing_flight_number),
            "departureDate": {
                "year": flight.flight_date.year,
                "month": flight.flight_date.month,
                "day": flight.flight_date.day,
            },
        }

    def _compute_exact(self, flights: Sequence[FlightRecord]) -> dict[str, dict]:
        """Returns key -> the flight's whole entry from the detailed response.

        Uses ``computeDetailedFlightEmissions`` rather than the plain endpoint:
        same request, same batching, identical per-cabin figures, but it also
        carries the provenance — distance, load factors, aircraft-match quality,
        the well-to-tank/tank-to-wake split. TIM will never price a departed
        flight again, so anything not captured now is gone for good.
        """
        results: dict[str, dict] = {}
        for i in range(0, len(flights), BATCH_SIZE):
            chunk = flights[i : i + BATCH_SIZE]
            requests_by_key = {f.key: self._request_for(f) for f in chunk}
            data = self._post_detailed(list(requests_by_key.values()))
            model_version = _format_model_version(data.get("modelVersion"))
            entries = data.get("flightsWithDetailedEmissions", [])

            # The response echoes each flight's identifiers, so match on identity
            # rather than position — an omitted or reordered entry would
            # otherwise attribute one flight's emissions to another.
            by_identity = {_identity(e.get("flight")): e for e in entries}
            # Position is only trustworthy when identity tells us nothing at all.
            # Mixing the two would hand an unmatched flight an entry another
            # flight has already claimed — the very cross-attribution matching on
            # identity exists to prevent.
            positional = not any(
                _identity(requests_by_key[f.key]) in by_identity for f in chunk
            ) and len(entries) == len(chunk)

            for position, flight in enumerate(chunk):
                entry = by_identity.get(_identity(requests_by_key[flight.key]))
                if entry is None and positional:
                    entry = entries[position]
                if entry is None:
                    continue
                results[flight.key] = {
                    **entry,
                    "modelVersion": model_version,
                    "request": requests_by_key[flight.key],
                }
        return results

    def _compute_typical(self, flights: Sequence[FlightRecord]) -> dict[str, dict]:
        """Route-average fallback for flights with no exact data. Returns key -> emissions."""
        results: dict[str, dict] = {}

        # Dedup markets to minimize calls: many flights often share one route.
        market_to_keys: dict[tuple[str, str], list[str]] = {}
        for f in flights:
            market_to_keys.setdefault((f.origin, f.destination), []).append(f.key)

        markets = list(market_to_keys)
        for i in range(0, len(markets), BATCH_SIZE):
            chunk = markets[i : i + BATCH_SIZE]
            body = {"markets": [{"origin": o, "destination": d} for o, d in chunk]}
            data = self._post("computeTypicalFlightEmissions", body)
            model_version = _format_model_version(data.get("modelVersion"))
            entries = data.get("typicalFlightEmissions", [])
            for position, entry in enumerate(entries):
                # The response echoes the market back. Trust that over position:
                # matching positionally would attribute one route's emissions to
                # a different flight if the API ever reordered or dropped an entry.
                echoed = entry.get("market") or {}
                market = (echoed.get("origin"), echoed.get("destination"))
                if market not in market_to_keys:
                    if position >= len(chunk):
                        continue
                    market = chunk[position]
                for key in market_to_keys[market]:
                    # There is no detailed variant of this endpoint, so a
                    # route-average row gets the figures and the dataset version
                    # and nothing more.
                    results[key] = {
                        **entry,
                        "modelVersion": model_version,
                        "request": {"origin": market[0], "destination": market[1]},
                    }
        return results
