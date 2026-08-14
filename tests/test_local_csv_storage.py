"""Tests for CSV storage, row normalization, and the legacy schema migration."""

import csv

import pytest

from contrail.storage import normalize_rows, total_kg
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


def test_no_cumulative_column_is_stored():
    """The CSV is a record of flights. Totalling it is the reader's job, so no
    derived aggregate is written that could fall out of date."""
    assert not [f for f in CSV_FIELDS if f.startswith("cumulative")]


def test_rows_are_sorted_by_flight_date():
    rows = [row("uid-2", "2026-06-10", "50"), row("uid-1", "2026-03-04", "100")]
    result = normalize_rows(rows)
    assert [r["source_id"] for r in result] == ["uid-1", "uid-2"]
    assert total_kg(result) == 150.0


def test_backfilled_flight_sorts_into_place_without_touching_other_rows():
    """Inserting an older flight reorders the file but changes no other row's
    values — nothing downstream of it has to be rewritten."""
    rows = [row("uid-1", "2026-03-04", "100"), row("uid-2", "2026-06-10", "50")]
    normalize_rows(rows)
    snapshot = {r["source_id"]: dict(r) for r in rows}

    rows.append(row("uid-3", "2026-01-01", "25"))
    result = normalize_rows(rows)

    assert [r["source_id"] for r in result] == ["uid-3", "uid-1", "uid-2"]
    for r in result:
        if r["source_id"] in snapshot:
            assert r == snapshot[r["source_id"]]
    assert total_kg(result) == 175.0


def test_unparsed_rows_contribute_nothing_but_keep_their_place():
    """A partial date keeps an unparsed row in sequence instead of pinning it to the top."""
    rows = [
        row("uid-1", "2026-01-01", "100"),
        row("uid-2", "2026-06-01", "", emissions_source="unparsed"),
        row("uid-3", "2026-12-01", "50"),
    ]
    result = normalize_rows(rows)

    assert [r["source_id"] for r in result] == ["uid-1", "uid-2", "uid-3"]
    assert total_kg(result) == 150.0


def test_hand_filled_emissions_are_picked_up():
    """The README tells people to fill unparsed rows in by hand and says the
    figure counts from the next sync. It has to actually do that."""
    rows = [
        row("uid-1", "2026-01-01", "200.0"),
        row("uid-2", "2026-02-01", "", emissions_source="unparsed"),
    ]
    normalize_rows(rows)
    assert total_kg(rows) == 200.0

    rows[1]["emissions_kg_economy"] = "250.0"  # user edits the CSV
    result = normalize_rows(rows)

    assert result[1]["emissions_kg_actual"] == "250.0"
    assert total_kg(result) == 450.0


def test_correcting_the_cabin_class_changes_the_actual_figure():
    """emissions_kg_actual is re-derived every pass, not frozen at write time."""
    rows = [row("uid-1", "2026-01-01", economy="100", emissions_kg_business="300")]
    normalize_rows(rows)
    assert total_kg(rows) == 100.0

    rows[0]["cabin_class_known"] = "business"  # user corrects it
    result = normalize_rows(rows)

    assert result[0]["emissions_kg_actual"] == "300"
    assert total_kg(result) == 300.0


def test_a_stale_cumulative_column_is_dropped_on_read(tmp_path):
    """Earlier versions wrote cumulative_kg_actual. It must not linger as though
    it were a column the user added by hand."""
    path = tmp_path / "flight_emissions.csv"
    header = [*CSV_FIELDS, "cumulative_kg_actual"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerow({**row("uid-1", "2026-03-04", "100"), "cumulative_kg_actual": "100.0"})

    storage = LocalCSVStorage(str(path))
    loaded = storage.load()
    assert "cumulative_kg_actual" not in loaded[0]

    storage.save(normalize_rows(loaded))
    with open(path) as f:
        assert next(csv.reader(f)) == CSV_FIELDS


def test_user_added_columns_survive_a_save(tmp_path):
    """Hand-editing is documented, so a column someone added isn't silently wiped."""
    path = tmp_path / "flight_emissions.csv"
    storage = LocalCSVStorage(str(path))
    data = row("uid-1", "2026-03-04", "100")
    data["notes"] = "work trip, paid offset separately"

    storage.save([data])
    reloaded = storage.load()

    assert reloaded[0]["notes"] == "work trip, paid offset separately"
    with open(path) as f:
        assert "notes" in next(csv.reader(f))


def test_save_does_not_destroy_the_previous_file_if_writing_fails(tmp_path):
    """The CSV is the whole product and TripIt can't re-supply old history, so a
    failed write must leave the previous version intact."""
    path = tmp_path / "flight_emissions.csv"
    storage = LocalCSVStorage(str(path))
    storage.save([row("uid-1", "2026-03-04", "100")])
    original = path.read_text()

    class Exploding(dict):
        def get(self, *args, **kwargs):
            raise OSError("disk full")

    with pytest.raises(OSError):
        storage.save([Exploding()])

    assert path.read_text() == original
    # and no temp files left lying around
    assert [p.name for p in tmp_path.iterdir()] == ["flight_emissions.csv"]


def test_rows_with_no_date_sort_first():
    rows = [row("uid-1", "2026-01-01", "100"), row("uid-2", "", "")]
    result = normalize_rows(rows)
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

    rows = normalize_rows(storage.load())
    storage.save(rows)
    reloaded = storage.load()

    assert not is_legacy_row(reloaded[0])
    assert reloaded[0]["emissions_kg_actual"] == "200"
    assert reloaded[0]["source_id"] == "item-legacy@tripit.com"
    assert "cumulative_kg_economy" not in reloaded[0]
