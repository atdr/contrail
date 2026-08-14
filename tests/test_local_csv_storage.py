"""Tests for CSV storage, the cumulative recompute, and the legacy schema migration."""

import csv

from contrail.storage import recompute_cumulative
from contrail.storage.local_csv import (
    CSV_FIELDS,
    LocalCSVStorage,
    actual_kg,
    is_legacy_row,
    row_key,
)


def row(source_id, flight_date, economy="", actual=None, **extra):
    data = {field: "" for field in CSV_FIELDS}
    data.update(
        source="tripit_ical",
        source_id=source_id,
        flight_date=flight_date,
        emissions_kg_economy=economy,
        emissions_kg_actual=economy if actual is None else actual,
    )
    data.update(extra)
    return data


def test_round_trip(tmp_path):
    path = tmp_path / "flight_emissions.csv"
    storage = LocalCSVStorage(str(path))
    assert storage.load() == []  # missing file is empty, not an error

    storage.save([row("uid-1", "2026-03-04", "100.0")])
    loaded = storage.load()

    assert len(loaded) == 1
    assert loaded[0]["source_id"] == "uid-1"
    assert row_key(loaded[0]) == "tripit_ical:uid-1"

    with open(path) as f:
        assert next(csv.reader(f)) == CSV_FIELDS


def test_save_creates_missing_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "log.csv"
    LocalCSVStorage(str(path)).save([row("uid-1", "2026-03-04", "10")])
    assert path.exists()


def test_cumulative_is_recomputed_in_date_order():
    rows = [
        row("uid-1", "2026-03-04", "100"),
        row("uid-2", "2026-06-10", "50"),
    ]
    result = recompute_cumulative(rows)
    assert [r["cumulative_kg_actual"] for r in result] == [100.0, 150.0]


def test_inserting_an_older_flight_reshuffles_the_running_total():
    """A backfilled flight lands in date order and every later total shifts up."""
    rows = [
        row("uid-1", "2026-03-04", "100"),
        row("uid-2", "2026-06-10", "50"),
    ]
    recompute_cumulative(rows)
    rows.append(row("uid-3", "2026-01-01", "25"))
    result = recompute_cumulative(rows)

    assert [r["source_id"] for r in result] == ["uid-3", "uid-1", "uid-2"]
    assert [r["cumulative_kg_actual"] for r in result] == [25.0, 125.0, 175.0]


def test_unparsed_rows_contribute_nothing_but_keep_their_place():
    """A partial date keeps an unparsed row in sequence instead of pinning it to the top."""
    rows = [
        row("uid-1", "2026-01-01", "100"),
        row("uid-2", "2026-06-01", "", emissions_source="unparsed"),
        row("uid-3", "2026-12-01", "50"),
    ]
    result = recompute_cumulative(rows)

    assert [r["source_id"] for r in result] == ["uid-1", "uid-2", "uid-3"]
    assert [r["cumulative_kg_actual"] for r in result] == [100.0, 100.0, 150.0]


def test_rows_with_no_date_sort_first():
    rows = [row("uid-1", "2026-01-01", "100"), row("uid-2", "", "")]
    result = recompute_cumulative(rows)
    assert result[0]["source_id"] == "uid-2"


def test_actual_kg_prefers_the_known_cabin():
    data = row(
        "uid-1",
        "2026-03-04",
        economy="100",
        cabin_class_known="business",
        emissions_kg_business="300",
    )
    assert actual_kg(data) == "300"


def test_actual_kg_falls_back_to_economy():
    assert actual_kg(row("uid-1", "2026-03-04", economy="100")) == "100"
    # An unrecognised or blank cabin must not silently produce nothing.
    assert actual_kg(row("uid-1", "2026-03-04", economy="100", cabin_class_known="couch")) == "100"


LEGACY_HEADER = [
    "sync_timestamp",
    "tripit_uid",
    "flight_date",
    "carrier_code",
    "flight_number",
    "origin",
    "destination",
    "emissions_source",
    "model_version",
    "emissions_kg_first",
    "emissions_kg_business",
    "emissions_kg_premium_economy",
    "emissions_kg_economy",
    "cumulative_kg_economy",
    "raw_summary",
]


def write_legacy_csv(path):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEGACY_HEADER)
        writer.writeheader()
        writer.writerow(
            {
                "sync_timestamp": "2026-01-01T00:00:00+00:00",
                "tripit_uid": "item-legacy@tripit.com",
                "flight_date": "2025-05-05",
                "carrier_code": "BA",
                "flight_number": "896",
                "origin": "LHR",
                "destination": "PFO",
                "emissions_source": "exact",
                "model_version": "1",
                "emissions_kg_first": "800",
                "emissions_kg_business": "600",
                "emissions_kg_premium_economy": "400",
                "emissions_kg_economy": "200",
                "cumulative_kg_economy": "200",
                "raw_summary": "BA896 LHR to PFO",
            }
        )


def test_legacy_prototype_csv_is_migrated_on_read(tmp_path):
    """A prototype CSV must be recognised, not re-imported and re-priced from scratch."""
    path = tmp_path / "flight_emissions.csv"
    write_legacy_csv(path)

    loaded = LocalCSVStorage(str(path)).load()

    assert len(loaded) == 1
    migrated = loaded[0]
    assert migrated["source"] == "tripit_ical"
    assert migrated["source_id"] == "item-legacy@tripit.com"
    assert row_key(migrated) == "tripit_ical:item-legacy@tripit.com"
    assert migrated["emissions_kg_actual"] == "200"  # carried over from economy
    assert migrated["carrier_code"] == "BA"
    assert set(migrated) == set(CSV_FIELDS)


def test_migrated_rows_survive_a_save_and_reload(tmp_path):
    path = tmp_path / "flight_emissions.csv"
    write_legacy_csv(path)
    storage = LocalCSVStorage(str(path))

    rows = recompute_cumulative(storage.load())
    storage.save(rows)
    reloaded = storage.load()

    assert not is_legacy_row(reloaded[0])
    assert reloaded[0]["cumulative_kg_actual"] == "200.0"
    assert reloaded[0]["source_id"] == "item-legacy@tripit.com"
