"""End-to-end tests for the CLI, with the emissions provider mocked out."""

import csv
import json
import pathlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from contrail.cli import main
from contrail.models import EmissionsResult
from contrail.storage import total_kg


class FakeProvider:
    """Stands in for TIM: prices everything as exact, and counts what it was asked."""

    id = "fake"
    seen: list = []

    def __init__(self, api_key):
        self.api_key = api_key

    def compute(self, flights, now=None):
        FakeProvider.seen = list(flights)
        return {
            f.key: EmissionsResult(
                method="exact",
                model_version="1",
                grams_first=400000,
                grams_business=300000,
                grams_premium_economy=200000,
                grams_economy=100000,
            )
            for f in flights
        }

    RAW = {"stub": True}


@pytest.fixture(autouse=True)
def reset_provider():
    FakeProvider.seen = []


# Pinned so the suite doesn't quietly start failing once the fixture's dates
# fall into the past: the open/frozen boundary is a comparison against now.
FROZEN_NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr("contrail.cli._now", lambda: FROZEN_NOW)


@pytest.fixture
def env(tmp_path, sample_feed_path, monkeypatch):
    """A working configuration pointing at the fixture feed."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TRIPIT_ICAL_URL", str(sample_feed_path))
    monkeypatch.setenv("TIM_API_KEY", "test-key")
    return tmp_path


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def run_sync(args):
    with patch("contrail.cli.get_provider", return_value=FakeProvider):
        return main(args)


def test_sync_writes_the_default_csv_in_the_cwd(env):
    """Unconfigured beyond the two secrets, `contrail sync` must just work."""
    assert run_sync(["sync"]) == 0

    rows = read_csv(env / "flight_emissions.csv")
    assert len(rows) == 6  # 5 parsed flights + 1 unparsed event
    assert rows[0]["flight_date"] <= rows[-1]["flight_date"]  # sorted by date


def test_sync_prices_flights_and_reports_a_total(env):
    run_sync(["sync", "--csv-path", "out.csv"])
    rows = read_csv(env / "out.csv")

    priced = [r for r in rows if r["emissions_source"] == "exact"]
    assert len(priced) == 5
    assert all(r["emissions_kg_economy"] == "100.0" for r in priced)
    assert all(r["emissions_kg_actual"] == "100.0" for r in priced)  # economy fallback
    assert total_kg(rows) == 500.0
    # The total is reported, never stored.
    assert "cumulative_kg_actual" not in rows[0]


def test_codeshare_columns_are_recorded(env):
    """The row records both what was booked and what was actually priced."""
    run_sync(["sync", "--csv-path", "out.csv"])
    rows = read_csv(env / "out.csv")

    codeshare = next(r for r in rows if r["flight_number"] == "999")
    assert codeshare["carrier_code"] == "YY"  # as booked
    assert codeshare["operating_carrier_code"] == "XX"
    assert codeshare["operating_flight_number"] == "456"

    direct = next(r for r in rows if r["flight_number"] == "123")
    assert direct["operating_carrier_code"] == "XX"


def test_unparsed_rows_are_written_with_their_raw_text(env):
    run_sync(["sync", "--csv-path", "out.csv"])
    rows = read_csv(env / "out.csv")

    unparsed = next(r for r in rows if r["emissions_source"] == "unparsed")
    assert unparsed["flight_date"] == "2026-07-22"  # partial date kept it in sequence
    assert unparsed["emissions_kg_actual"] == ""
    assert "QWERTY" in unparsed["raw_summary"]


def test_second_run_adds_nothing(env, capsys):
    run_sync(["sync", "--csv-path", "out.csv"])
    before = (env / "out.csv").read_text()

    assert run_sync(["sync", "--csv-path", "out.csv"]) == 0
    assert "already up to date" in capsys.readouterr().out
    assert (env / "out.csv").read_text() == before  # byte-identical


def test_hand_edits_are_picked_up_on_a_later_run(env):
    """The README promises a hand-filled figure reaches the total on the next
    sync. That run finds no new flights, so it must still recompute and save."""
    run_sync(["sync", "--csv-path", "out.csv"])

    rows = read_csv(env / "out.csv")
    unparsed_index = next(i for i, r in enumerate(rows) if r["emissions_source"] == "unparsed")
    rows[unparsed_index]["emissions_kg_economy"] = "250.0"
    with open(env / "out.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    assert run_sync(["sync", "--csv-path", "out.csv"]) == 0

    updated = read_csv(env / "out.csv")
    assert updated[unparsed_index]["emissions_kg_actual"] == "250.0"
    assert total_kg(updated) == 750.0  # 500 priced + 250 by hand


def test_an_untouched_rerun_leaves_the_file_alone(env, capsys):
    """Recomputing on every run must not mean rewriting on every run — that
    would produce a daily empty commit in contrail-gh."""
    run_sync(["sync", "--csv-path", "out.csv"])
    before = (env / "out.csv").stat().st_mtime_ns

    run_sync(["sync", "--csv-path", "out.csv"])

    assert (env / "out.csv").stat().st_mtime_ns == before
    assert "already up to date" in capsys.readouterr().out


def test_dry_run_writes_nothing_and_prices_nothing(env, capsys):
    assert run_sync(["sync", "--csv-path", "out.csv", "--dry-run"]) == 0

    assert not (env / "out.csv").exists()
    assert FakeProvider.seen == []
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "JFK -> LHR" in out


def test_dry_run_does_not_need_an_api_key(env, monkeypatch, capsys):
    """CI runs this against the fixture with no secret available."""
    monkeypatch.delenv("TIM_API_KEY")
    assert main(["sync", "--csv-path", "out.csv", "--dry-run"]) == 0
    assert "Dry run" in capsys.readouterr().out


def test_sync_without_an_api_key_fails_with_a_useful_message(env, monkeypatch, capsys):
    monkeypatch.delenv("TIM_API_KEY")
    assert main(["sync", "--csv-path", "out.csv"]) == 1
    assert "TIM_API_KEY" in capsys.readouterr().err


def test_sync_without_a_source_fails_with_a_useful_message(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRIPIT_ICAL_URL", raising=False)
    monkeypatch.setenv("TIM_API_KEY", "test-key")
    assert main(["sync"]) == 1
    assert "TRIPIT_ICAL_URL" in capsys.readouterr().err


def test_multiple_sources_run_in_one_invocation(tmp_path, sample_feed_path, monkeypatch):
    """Two sources feeding one CSV: the same UID from each must not collide or dedup away."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRIPIT_ICAL_URL", raising=False)
    monkeypatch.setenv("TIM_API_KEY", "test-key")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "sources": [
                    {"type": "tripit_ical", "url": str(sample_feed_path)},
                    {"type": "tripit_ical", "url": str(sample_feed_path)},
                ]
            }
        )
    )

    run_sync(["sync", "--csv-path", "out.csv"])
    rows = read_csv(tmp_path / "out.csv")
    # Both sources use the same importer id, so the second pass is deduped away.
    assert len(rows) == 6


def test_unknown_importer_type_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRIPIT_ICAL_URL", raising=False)
    monkeypatch.setenv("TIM_API_KEY", "test-key")
    (tmp_path / "config.json").write_text(json.dumps({"sources": [{"type": "nope"}]}))

    assert main(["sync"]) == 1
    err = capsys.readouterr().err
    assert "Unknown importer type" in err
    assert "tripit_ical" in err  # tells you what is available


def test_sources_command_marks_what_is_configured(env, capsys):
    assert main(["sources"]) == 0
    out = capsys.readouterr().out
    assert "tripit_ical" in out
    assert "not configured" not in out


# -- re-sync at the CLI level -------------------------------------------------

FIXTURE_FEED = pathlib.Path(__file__).parent / "fixtures" / "sample_feed.ics"


def doctored_feed(tmp_path, drop_uid=None, drop_all=False):
    """The fixture with one VEVENT removed, or every one of them."""
    kept, block = [], None
    for line in FIXTURE_FEED.read_text().splitlines(keepends=True):
        if line.startswith("BEGIN:VEVENT"):
            block = [line]
            continue
        if block is not None:
            block.append(line)
            if line.startswith("END:VEVENT"):
                text = "".join(block)
                if not drop_all and (drop_uid is None or drop_uid not in text):
                    kept.append(text)
                block = None
            continue
        kept.append(line)  # calendar header/footer

    path = tmp_path / "doctored.ics"
    path.write_text("".join(kept))
    return path


# The fixture's YY999 LHR->CDG departs 2026-09-20, i.e. after "today" (2026-08-14).
UPCOMING_UID = "item-88888888-hhhh@example.invalid"


def test_a_vanished_upcoming_flight_is_cancelled(env, monkeypatch, capsys):
    run_sync(["sync", "--csv-path", "out.csv"])
    before_total = total_kg(read_csv(env / "out.csv"))

    monkeypatch.setenv("TRIPIT_ICAL_URL", str(doctored_feed(env, drop_uid=UPCOMING_UID)))
    assert run_sync(["sync", "--csv-path", "out.csv"]) == 0

    rows = read_csv(env / "out.csv")
    gone = next(r for r in rows if r["source_id"] == UPCOMING_UID)
    assert gone["status"] == "cancelled"
    assert gone["emissions_kg_actual"] == ""  # out of the total
    assert gone["emissions_kg_economy"] == "100.0"  # but recoverable
    assert total_kg(rows) == before_total - 100.0
    assert "marking cancelled" in capsys.readouterr().out


def test_a_restored_flight_returns_to_the_total(env, monkeypatch):
    run_sync(["sync", "--csv-path", "out.csv"])
    full_total = total_kg(read_csv(env / "out.csv"))

    monkeypatch.setenv("TRIPIT_ICAL_URL", str(doctored_feed(env, drop_uid=UPCOMING_UID)))
    run_sync(["sync", "--csv-path", "out.csv"])
    monkeypatch.setenv("TRIPIT_ICAL_URL", str(FIXTURE_FEED))  # back to the real feed
    assert run_sync(["sync", "--csv-path", "out.csv"]) == 0

    rows = read_csv(env / "out.csv")
    back = next(r for r in rows if r["source_id"] == UPCOMING_UID)
    assert back["status"] == ""
    assert total_kg(rows) == full_total


def test_a_departed_flight_leaving_the_feed_is_left_alone(env, monkeypatch):
    """The fixture's XX123 is in the past; TripIt drops old trips routinely."""
    run_sync(["sync", "--csv-path", "out.csv"])
    past_uid = "item-11111111-aaaa@example.invalid"

    monkeypatch.setenv("TRIPIT_ICAL_URL", str(doctored_feed(env, drop_uid=past_uid)))
    run_sync(["sync", "--csv-path", "out.csv"])

    rows = read_csv(env / "out.csv")
    still_there = next(r for r in rows if r["source_id"] == past_uid)
    assert still_there["status"] == ""
    assert still_there["emissions_kg_actual"] == "100.0"


def test_an_empty_feed_refuses_to_cancel_anything(env, monkeypatch, capsys):
    """A feed returning nothing is a broken feed, not a cancelled life."""
    run_sync(["sync", "--csv-path", "out.csv"])
    before = (env / "out.csv").read_text()

    monkeypatch.setenv("TRIPIT_ICAL_URL", str(doctored_feed(env, drop_all=True)))
    assert main(["sync", "--csv-path", "out.csv"]) == 1

    assert (env / "out.csv").read_text() == before
    assert "Refusing to cancel" in capsys.readouterr().err


def test_a_hand_edited_future_row_is_corrected_back(env):
    """Upcoming rows belong to contrail, so a wrong date is put right."""
    run_sync(["sync", "--csv-path", "out.csv"])
    rows = read_csv(env / "out.csv")
    target = next(i for i, r in enumerate(rows) if r["source_id"] == UPCOMING_UID)
    rows[target]["flight_date"] = "2026-10-01"
    with open(env / "out.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    run_sync(["sync", "--csv-path", "out.csv"])

    fixed = next(r for r in read_csv(env / "out.csv") if r["source_id"] == UPCOMING_UID)
    assert fixed["flight_date"] == "2026-09-20"


def test_a_hand_edited_past_row_is_respected(env):
    """Past rows are the user's. contrail must not argue with them."""
    run_sync(["sync", "--csv-path", "out.csv"])
    rows = read_csv(env / "out.csv")
    past_uid = "item-11111111-aaaa@example.invalid"
    target = next(i for i, r in enumerate(rows) if r["source_id"] == past_uid)
    rows[target]["flight_date"] = "2026-03-01"
    with open(env / "out.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    run_sync(["sync", "--csv-path", "out.csv"])

    kept = next(r for r in read_csv(env / "out.csv") if r["source_id"] == past_uid)
    assert kept["flight_date"] == "2026-03-01"


def test_dry_run_reports_cancellations_without_writing(env, monkeypatch, capsys):
    run_sync(["sync", "--csv-path", "out.csv"])
    before = (env / "out.csv").read_text()

    monkeypatch.setenv("TRIPIT_ICAL_URL", str(doctored_feed(env, drop_uid=UPCOMING_UID)))
    assert run_sync(["sync", "--csv-path", "out.csv", "--dry-run"]) == 0

    assert (env / "out.csv").read_text() == before
    assert "CANCEL" in capsys.readouterr().out
