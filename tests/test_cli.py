"""End-to-end tests for the CLI, with the emissions provider mocked out."""

import csv
import json
import pathlib
from datetime import UTC, datetime
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
FROZEN_NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


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
    assert "tripit_ical      configured" in out
    assert "flighty_csv      not configured" in out


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


# -- two sources describing the same flights ---------------------------------

FIXTURE_FLIGHTY = pathlib.Path(__file__).parent / "fixtures" / "sample_flighty.csv"


@pytest.fixture
def both_sources(env, monkeypatch):
    """The iCal feed and the Flighty export, in that order.

    They overlap on one flight: the feed's `XX123 JFK to LHR` departs
    2026-03-05T01:30Z, which is the evening of 2026-03-04 in New York, and the
    export lists a JFK-LHR on that date. The carrier and number differ between
    them on purpose — identity is route and date, not how a source labels it.
    """
    monkeypatch.setenv("FLIGHTY_CSV_PATH", str(FIXTURE_FLIGHTY))
    return env


def test_the_same_flight_from_two_sources_is_one_row(both_sources, capsys):
    assert run_sync(["sync"]) == 0
    rows = read_csv(both_sources / "flight_emissions.csv")

    jfk = [r for r in rows if (r["origin"], r["destination"]) == ("JFK", "LHR")]
    assert len(jfk) == 1
    assert jfk[0]["source"] == "tripit_ical"  # the first source listed owns it
    assert jfk[0]["also_seen_as"] == "flighty_csv:00000000-0000-4000-8000-000000000009"


def test_a_duplicate_backfills_the_owning_row(both_sources):
    """The feed cannot report a cabin; the export can. The row is frozen — the
    flight is months past — and this is the one thing allowed to change on it."""
    run_sync(["sync"])
    rows = read_csv(both_sources / "flight_emissions.csv")
    jfk = next(r for r in rows if r["origin"] == "JFK")

    assert jfk["cabin_class_known"] == "business"
    assert jfk["aircraft_type"] == "Boeing 777-300 ER"
    assert jfk["flight_reason"] == "business"
    # The per-cabin figures were already stored; the cabin decides which counts.
    assert jfk["emissions_kg_actual"] == jfk["emissions_kg_business"] == "300.0"


def test_a_duplicate_is_never_priced_again(both_sources):
    run_sync(["sync"])
    assert not [f for f in FakeProvider.seen if f.origin == "JFK" and f.source == "flighty_csv"]


def test_a_codeshare_listed_twice_becomes_one_row(both_sources, capsys):
    """The export has 2023-04-02 LHR-MAD as both IB3643 and BA458 — one flight,
    entered twice. Two rows would count it twice."""
    run_sync(["sync"])
    rows = read_csv(both_sources / "flight_emissions.csv")

    mad = [r for r in rows if r["destination"] == "MAD"]
    assert len(mad) == 1
    assert mad[0]["also_seen_as"] == "flighty_csv:00000000-0000-4000-8000-000000000008"
    # The row kept is the first listed, and it takes what the second knew.
    assert (mad[0]["carrier_code"], mad[0]["flight_number"]) == ("IB", "3643")
    assert mad[0]["cabin_class_known"] == "business"
    assert "matched a flight another source had already reported" in capsys.readouterr().out


def test_legs_of_one_flight_number_stay_two_rows(both_sources):
    """BA16 SYD-SIN-LHR, flown in different cabins. Collapsing them would lose a
    first-class leg and its emissions figure."""
    run_sync(["sync"])
    rows = read_csv(both_sources / "flight_emissions.csv")

    legs = [r for r in rows if r["flight_number"] == "16"]
    assert [(r["origin"], r["destination"]) for r in legs] == [("SYD", "SIN"), ("SIN", "LHR")]
    assert {r["cabin_class_known"] for r in legs} == {"business", "first"}
    assert [r["emissions_kg_actual"] for r in legs] == ["300.0", "400.0"]


def test_a_cancelled_flight_the_source_reports_counts_for_nothing(both_sources):
    run_sync(["sync"])
    rows = read_csv(both_sources / "flight_emissions.csv")

    cancelled = next(r for r in rows if r["destination"] == "EDI")
    assert cancelled["status"] == "cancelled"
    assert cancelled["emissions_kg_actual"] == ""
    assert cancelled["emissions_kg_economy"] == "100.0"  # kept: TIM won't say again


def test_a_second_sync_changes_nothing(both_sources, capsys):
    """Two sources reporting the same flight must not make the file churn."""
    run_sync(["sync"])
    before = (both_sources / "flight_emissions.csv").read_text()

    capsys.readouterr()
    run_sync(["sync"])
    assert (both_sources / "flight_emissions.csv").read_text() == before
    assert "already up to date" in capsys.readouterr().out


def test_a_through_flight_alongside_its_legs_is_reported(env, tmp_path, monkeypatch, capsys):
    """A source that reports BA16 as one SYD-LHR segment describes the same
    journey as the two legs, and nothing matches them up. contrail can't pick a
    winner — the legs were two different cabins, which one row can't express — so
    it says so loudly and keeps both."""
    rows = list(csv.DictReader(FIXTURE_FLIGHTY.open()))
    through = {**rows[4], "To": "LHR", "Flight Flighty ID": "through-1"}
    export = tmp_path / "with-through-flight.csv"
    with open(export, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows([*rows, through])
    monkeypatch.setenv("FLIGHTY_CSV_PATH", str(export))

    assert run_sync(["sync"]) == 0

    err = capsys.readouterr().err
    assert "BA16 on 2022-09-01" in err
    assert "SYD-SIN + SIN-LHR" in err
    assert "counted about twice" in err

    written = read_csv(env / "flight_emissions.csv")
    assert len([r for r in written if r["flight_number"] == "16"]) == 3  # nothing removed


def test_the_dry_run_lists_what_it_would_link(env, monkeypatch, capsys):
    """The link is only visible before it happens, so the dry run has to show it."""
    run_sync(["sync"])  # the feed alone, so the JFK row exists and is owned by it
    monkeypatch.setenv("FLIGHTY_CSV_PATH", str(FIXTURE_FLIGHTY))
    capsys.readouterr()

    run_sync(["sync", "--dry-run"])

    out = capsys.readouterr().out
    assert "LINKED" in out
    assert "already stored as tripit_ical" in out
    assert "fills also_seen_as, cabin_class_known, aircraft_type, flight_reason" in out


def test_adding_an_export_later_fills_in_frozen_rows(env, monkeypatch, capsys):
    """The upgrade path, and the whole point of the feature: a log built from
    TripIt over months has every past row assuming economy. Pointing contrail at
    a Flighty export has to correct them, even though they are frozen."""
    run_sync(["sync"])
    before = read_csv(env / "flight_emissions.csv")
    jfk_before = next(r for r in before if r["origin"] == "JFK")
    assert jfk_before["cabin_class_known"] == ""
    assert jfk_before["emissions_kg_actual"] == "100.0"  # economy, assumed

    monkeypatch.setenv("FLIGHTY_CSV_PATH", str(FIXTURE_FLIGHTY))
    assert run_sync(["sync"]) == 0

    after = read_csv(env / "flight_emissions.csv")
    jfk = next(r for r in after if r["origin"] == "JFK")
    assert jfk["source"] == "tripit_ical"  # still owned by the source that found it
    assert jfk["cabin_class_known"] == "business"
    assert jfk["emissions_kg_actual"] == "300.0"  # a figure it already had
    assert jfk["also_seen_as"] == "flighty_csv:00000000-0000-4000-8000-000000000009"


def test_a_frozen_row_is_not_repriced_when_it_is_filled_in(env, monkeypatch):
    """Filling a blank must not become an excuse to re-ask about a past flight —
    TIM refuses, and the stored figures are all there will ever be."""
    run_sync(["sync"])
    monkeypatch.setenv("FLIGHTY_CSV_PATH", str(FIXTURE_FLIGHTY))
    run_sync(["sync"])

    assert not [f for f in FakeProvider.seen if (f.origin, f.destination) == ("JFK", "LHR")]


def test_the_flighty_id_is_always_reachable_for_a_join(env, monkeypatch):
    """Whichever source owns a row, the Flighty id is in source_id or in
    also_seen_as — which is what the README's join recipe relies on."""
    monkeypatch.setenv("FLIGHTY_CSV_PATH", str(FIXTURE_FLIGHTY))
    run_sync(["sync"])

    rows = read_csv(env / "flight_emissions.csv")
    from_flighty = [
        r for r in rows if r["source"] == "flighty_csv" or "flighty_csv:" in r["also_seen_as"]
    ]
    assert len(from_flighty) == 10  # 9 rows of its own + the one it linked to

    for row in from_flighty:
        keys = f"{row['source']}:{row['source_id']} {row['also_seen_as']}"
        assert "flighty_csv:00000000-0000-4000-8000-" in keys


def test_a_stated_cancellation_survives_being_folded(env, tmp_path, monkeypatch):
    """Which record wins a collapse is decided by config order. That must not be
    what decides whether a called-off flight counts toward the total."""
    rows = list(csv.DictReader(FIXTURE_FLIGHTY.open()))
    # Same flight as the feed's XX123 JFK->LHR, but Flighty says it was cancelled.
    rows[8]["Canceled"] = "true"
    export = tmp_path / "cancelled.csv"
    with open(export, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setenv("FLIGHTY_CSV_PATH", str(export))

    run_sync(["sync"])

    jfk = next(r for r in read_csv(env / "flight_emissions.csv") if r["origin"] == "JFK")
    assert jfk["status"] == "cancelled"
    assert jfk["emissions_kg_actual"] == ""


def test_a_settled_rerun_says_nothing_it_did_not_do(both_sources, capsys):
    """Sources overlapping is the steady state, not news. A line per matched
    flight every run would bury whatever actually changed."""
    run_sync(["sync"])
    capsys.readouterr()

    run_sync(["sync"])

    out = capsys.readouterr().out
    assert "already up to date" in out
    assert "gained details" not in out
    assert "matched a flight" not in out
    assert "is the same flight as" not in out  # detail belongs to --dry-run


def test_the_link_is_recorded_on_an_upcoming_flight(env, monkeypatch):
    """The join has to work before departure, not only after. When the stored row
    is owned by a record that got folded in, the surviving record's own key is the
    only new information there is."""
    monkeypatch.delenv("TRIPIT_ICAL_URL")
    monkeypatch.setenv("FLIGHTY_CSV_PATH", str(FIXTURE_FLIGHTY))
    run_sync(["sync"])  # Flighty alone, so it owns every row

    # Now a feed that also reports the upcoming 2026-03-04 JFK->LHR. It is listed
    # first, so it wins the collapse while Flighty still owns the stored row.
    monkeypatch.setenv("TRIPIT_ICAL_URL", str(FIXTURE_FEED))
    run_sync(["sync"])

    rows = read_csv(env / "flight_emissions.csv")
    jfk = next(r for r in rows if (r["origin"], r["destination"]) == ("JFK", "LHR"))
    assert jfk["source"] == "flighty_csv"  # first to find it keeps it
    assert jfk["also_seen_as"] == "tripit_ical:item-11111111-aaaa@example.invalid"


def test_a_through_flight_conflict_survives_a_lower_case_feed(env, tmp_path, monkeypatch, capsys):
    """TripIt's FROM_TO_RE is IGNORECASE. Comparing raw codes against normalized
    rows meant the double-count warning silently never fired."""
    rows = list(csv.DictReader(FIXTURE_FLIGHTY.open()))
    for r in rows[4:6]:  # the two BA16 legs
        r["From"], r["To"] = r["From"].lower(), r["To"].lower()
    through = {**rows[4], "To": "lhr", "Flight Flighty ID": "through-1"}
    export = tmp_path / "lower.csv"
    with open(export, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows([*rows, through])
    monkeypatch.setenv("FLIGHTY_CSV_PATH", str(export))

    run_sync(["sync"])

    assert "counted about twice" in capsys.readouterr().err


# -- the passport command -----------------------------------------------------


def test_passport_writes_a_dashboard_from_the_log(env, capsys):
    run_sync(["sync"])
    capsys.readouterr()

    assert main(["passport"]) == 0

    document = (env / "passport.html").read_text(encoding="utf-8")
    assert "Contrail Passport" in document
    assert '"origin":"JFK"' in document  # the log, embedded rather than linked
    assert "Keep the HTML private" in capsys.readouterr().out


def test_passport_needs_no_source_and_no_api_key(env, monkeypatch):
    """It reads the CSV and nothing else. Requiring the sync configuration would
    stop anyone looking at a log they were handed."""
    run_sync(["sync"])
    monkeypatch.delenv("TRIPIT_ICAL_URL")
    monkeypatch.delenv("TIM_API_KEY")

    assert main(["passport", "--output", "elsewhere/passport.html"]) == 0
    assert (env / "elsewhere" / "passport.html").exists()


def test_passport_without_a_log_says_so(env, capsys):
    assert main(["passport"]) == 1
    assert "not found" in capsys.readouterr().err


def test_passport_of_an_empty_log_says_so(env, capsys):
    run_sync(["sync"])
    header = (env / "flight_emissions.csv").read_text().splitlines()[0]
    (env / "flight_emissions.csv").write_text(header + "\n")

    assert main(["passport"]) == 1
    assert "empty" in capsys.readouterr().err


def test_passport_refuses_to_write_over_the_log(env, capsys):
    """One mistyped --output would destroy years of figures TIM will not re-price."""
    run_sync(["sync"])
    before = read_csv(env / "flight_emissions.csv")

    assert main(["passport", "--output", "flight_emissions.csv"]) == 1

    assert "must not overwrite" in capsys.readouterr().err
    assert read_csv(env / "flight_emissions.csv") == before


def test_passport_opens_a_browser_only_when_asked(env):
    run_sync(["sync"])

    with patch("contrail.cli.webbrowser.open", return_value=True) as opener:
        assert main(["passport"]) == 0
        opener.assert_not_called()

        assert main(["passport", "--open"]) == 0
        opener.assert_called_once()
