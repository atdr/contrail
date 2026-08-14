"""End-to-end tests for the CLI, with the emissions provider mocked out."""

import csv
import json
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

    def compute(self, flights):
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


@pytest.fixture(autouse=True)
def reset_provider():
    FakeProvider.seen = []


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
    assert len(rows) == 5  # 4 parsed flights + 1 unparsed event
    assert rows[0]["flight_date"] <= rows[-1]["flight_date"]  # sorted by date


def test_sync_prices_flights_and_reports_a_total(env):
    run_sync(["sync", "--csv-path", "out.csv"])
    rows = read_csv(env / "out.csv")

    priced = [r for r in rows if r["emissions_source"] == "exact"]
    assert len(priced) == 4
    assert all(r["emissions_kg_economy"] == "100.0" for r in priced)
    assert all(r["emissions_kg_actual"] == "100.0" for r in priced)  # economy fallback
    assert total_kg(rows) == 400.0
    # The total is reported, never stored.
    assert "cumulative_kg_actual" not in rows[0]


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
    assert "No new flights" in capsys.readouterr().out
    assert (env / "out.csv").read_text() == before  # byte-identical


def test_hand_edits_are_picked_up_on_a_run_with_no_new_flights(env, capsys):
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
    assert total_kg(updated) == 650.0  # 400 priced + 250 by hand
    assert "hand-edited" in capsys.readouterr().out


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
    assert len(rows) == 5


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
