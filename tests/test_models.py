"""Focused tests for behavior owned by the shared data models."""

from datetime import UTC, date, datetime

from contrail.models import EmissionsResult, FlightRecord


def flight(**extra):
    values = {
        "source": "test",
        "source_id": "one",
        "flight_date": date(2026, 9, 11),
        "carrier_code": "BA",
        "flight_number": "1",
        "origin": "LHR",
        "destination": "JFK",
    }
    values.update(extra)
    return FlightRecord(**values)


def test_an_aware_departure_uses_the_exact_instant():
    departure = datetime(2026, 9, 11, 12, tzinfo=UTC)

    assert flight(departure_time=departure).has_departed(departure)
    assert not flight(departure_time=departure).has_departed(
        datetime(2026, 9, 11, 11, 59, tzinfo=UTC)
    )


def test_emissions_are_selected_by_cabin_name():
    result = EmissionsResult(method="exact", grams_business=123)

    assert result.grams_for("business") == 123
    assert result.grams_for("unknown") is None
