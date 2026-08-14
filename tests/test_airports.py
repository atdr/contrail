"""Tests for airport timezone lookup and local departure-date resolution."""

from datetime import date, datetime, timedelta, timezone

from contrail.airports import departure_date, timezone_for


def utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_known_airports_resolve():
    assert str(timezone_for("LHR")) == "Europe/London"
    assert str(timezone_for("HND")) == "Asia/Tokyo"
    assert str(timezone_for("jfk")) == "America/New_York"  # case-insensitive


def test_unknown_or_missing_airports_return_none():
    assert timezone_for("QQQ") is None
    assert timezone_for("") is None
    assert timezone_for(None) is None


def test_evening_departure_west_of_utc_keeps_its_own_date():
    """The classic case: an evening departure from the US is already tomorrow
    in UTC, so the naive reading records it a day late."""
    dt = utc(2026, 7, 5, 1, 30)  # 21:30 on 4 July, EDT
    assert dt.date() == date(2026, 7, 5)  # what the UTC date would have said
    assert departure_date(dt, "JFK") == date(2026, 7, 4)


def test_early_departure_far_east_of_utc_keeps_its_own_date():
    """The mirror image: an early Tokyo departure is still yesterday in UTC."""
    dt = utc(2026, 7, 5, 16, 0)  # 01:00 on 6 July, JST
    assert dt.date() == date(2026, 7, 5)
    assert departure_date(dt, "HND") == date(2026, 7, 6)


def test_conversion_respects_daylight_saving():
    """A fixed UTC offset would get one of these wrong: New York is -5 in March
    and -4 in July."""
    assert departure_date(utc(2026, 3, 5, 1, 30), "JFK") == date(2026, 3, 4)  # EST
    assert departure_date(utc(2026, 7, 5, 1, 30), "JFK") == date(2026, 7, 4)  # EDT
    # 04:30Z is the previous day only under EDT (-4), not EST (-5).
    assert departure_date(utc(2026, 3, 5, 4, 30), "JFK") == date(2026, 3, 4)
    assert departure_date(utc(2026, 7, 5, 4, 30), "JFK") == date(2026, 7, 5)


def test_midday_departures_are_unaffected():
    assert departure_date(utc(2026, 6, 12, 12, 0), "LHR") == date(2026, 6, 12)
    assert departure_date(utc(2026, 6, 12, 12, 0), "JFK") == date(2026, 6, 12)


def test_unknown_airport_falls_back_to_the_date_as_given():
    dt = utc(2026, 7, 5, 1, 30)
    assert departure_date(dt, "QQQ") == date(2026, 7, 5)
    assert departure_date(dt, None) == date(2026, 7, 5)


def test_plain_dates_pass_through():
    """An all-day event has no time to convert."""
    assert departure_date(date(2026, 6, 10), "JFK") == date(2026, 6, 10)


def test_naive_datetimes_are_treated_as_already_local():
    """RFC 5545 floating time is a local wall clock, so converting it would be
    wrong — it is already the date the traveller would say."""
    naive = datetime(2026, 7, 4, 21, 30)
    assert departure_date(naive, "JFK") == date(2026, 7, 4)


def test_non_utc_offsets_are_honoured():
    """A feed that states a real offset should be trusted for the instant, then
    converted to the origin's own zone."""
    dt = datetime(2026, 7, 4, 21, 30, tzinfo=timezone(timedelta(hours=-4)))
    assert departure_date(dt, "JFK") == date(2026, 7, 4)
    # Same instant, but departing Tokyo, would be the next day there.
    assert departure_date(dt, "HND") == date(2026, 7, 5)


def test_none_dtstart():
    assert departure_date(None, "LHR") is None
