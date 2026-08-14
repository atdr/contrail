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

import requests

from contrail.models import EmissionsResult, FlightRecord

TIM_BASE_URL = "https://travelimpactmodel.googleapis.com/v1"
BATCH_SIZE = 200  # flights per TIM API request
REQUEST_TIMEOUT = 30


class TIMEmissionsProvider:
    """Prices flights using Google's Travel Impact Model API."""

    id = "tim"

    def __init__(self, api_key: str, base_url: str = TIM_BASE_URL):
        if not api_key:
            raise ValueError("TIMEmissionsProvider needs an API key (TIM_API_KEY).")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    # -- public API ---------------------------------------------------------

    def compute(self, flights: Sequence[FlightRecord]) -> dict[str, EmissionsResult]:
        flights = list(flights)
        if not flights:
            return {}

        exact = self._compute_exact(flights)

        needs_fallback = [f for f in flights if not exact.get(f.key, (None, None))[0]]
        typical = self._compute_typical(needs_fallback) if needs_fallback else {}

        results: dict[str, EmissionsResult] = {}
        for flight in flights:
            grams, model_version = exact.get(flight.key, (None, None))
            method = "exact"
            if not grams:
                grams = typical.get(flight.key)
                method = "typical_route_average"
                model_version = None
            if not grams:
                results[flight.key] = EmissionsResult(method="no_data")
                continue
            results[flight.key] = EmissionsResult(
                method=method,
                model_version=model_version,
                grams_first=grams.get("first"),
                grams_business=grams.get("business"),
                grams_premium_economy=grams.get("premiumEconomy"),
                grams_economy=grams.get("economy"),
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

    def _compute_exact(self, flights: Sequence[FlightRecord]) -> dict[str, tuple]:
        """Returns key -> (emissions dict or None, model_version)."""
        results: dict[str, tuple] = {}
        for i in range(0, len(flights), BATCH_SIZE):
            chunk = flights[i : i + BATCH_SIZE]
            body = {
                "flights": [
                    {
                        "origin": f.origin,
                        "destination": f.destination,
                        # The field means what it says: on a codeshare, TIM only
                        # prices the operating flight, not the marketing one.
                        "operatingCarrierCode": f.pricing_carrier_code,
                        "flightNumber": int(f.pricing_flight_number),
                        "departureDate": {
                            "year": f.flight_date.year,
                            "month": f.flight_date.month,
                            "day": f.flight_date.day,
                        },
                    }
                    for f in chunk
                ]
            }
            data = self._post("computeFlightEmissions", body)
            # `major` is an int, so a legitimate 0 must not fall through to the
            # serialized-object fallback.
            major = data.get("modelVersion", {}).get("major")
            model_version = (
                str(major) if major is not None else json.dumps(data.get("modelVersion", {}))
            )
            for f, fe in zip(chunk, data.get("flightEmissions", []), strict=False):
                results[f.key] = (fe.get("emissionsGramsPerPax"), model_version)
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
                emissions = entry.get("emissionsGramsPerPax")
                for key in market_to_keys[market]:
                    results[key] = emissions
        return results
