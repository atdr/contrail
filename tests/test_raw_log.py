"""The append-only sidecar that keeps everything the emissions API returned."""

import json

from contrail.storage.raw_log import JSONLRawLog, default_path


def test_default_path_sits_beside_the_csv():
    assert default_path("flight_emissions.csv") == "flight_emissions.raw.jsonl"
    assert default_path("/data/log.csv") == "/data/log.raw.jsonl"


def test_entries_round_trip(tmp_path):
    log = JSONLRawLog(str(tmp_path / "raw.jsonl"))
    log.append([{"key": "tripit_ical:a", "response": {"nested": {"deep": [1, 2]}}}])

    entries = log.read()
    assert len(entries) == 1
    assert entries[0]["response"]["nested"]["deep"] == [1, 2]
    assert entries[0]["captured_at"]


def test_appending_never_rewrites(tmp_path):
    """A flight is re-priced until it departs, so the file has to accumulate the
    sequence of answers rather than replace the last one."""
    log = JSONLRawLog(str(tmp_path / "raw.jsonl"))
    log.append([{"key": "a", "response": {"economy": 100}}], captured_at="FIRST")
    log.append([{"key": "a", "response": {"economy": 90}}], captured_at="SECOND")

    entries = log.read()
    assert len(entries) == 2
    assert [e["captured_at"] for e in entries] == ["FIRST", "SECOND"]
    assert [e["response"]["economy"] for e in entries] == [100, 90]


def test_an_unchanged_answer_is_not_recorded_again(tmp_path):
    """Upcoming flights are re-priced every run. Appending an identical answer
    daily would be growth without information — and a daily commit for it."""
    log = JSONLRawLog(str(tmp_path / "raw.jsonl"))
    payload = [{"key": "a", "response": {"economy": 100}}]

    assert log.append(payload) == 1
    assert log.append(payload) == 0
    assert log.append(payload) == 0
    assert len(log.read()) == 1

    # but a genuinely different answer still lands
    assert log.append([{"key": "a", "response": {"economy": 95}}]) == 1
    assert len(log.read()) == 2


def test_each_flight_is_tracked_separately(tmp_path):
    log = JSONLRawLog(str(tmp_path / "raw.jsonl"))
    log.append([{"key": "a", "response": {"x": 1}}, {"key": "b", "response": {"x": 2}}])

    # only b moved
    assert log.append([{"key": "a", "response": {"x": 1}}, {"key": "b", "response": {"x": 3}}]) == 1
    assert [e["key"] for e in log.read()] == ["a", "b", "b"]


def test_every_line_is_valid_json(tmp_path):
    path = tmp_path / "raw.jsonl"
    log = JSONLRawLog(str(path))
    log.append([{"key": f"k{i}", "response": {"i": i}} for i in range(3)])

    lines = path.read_text().splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["key"] for line in lines] == ["k0", "k1", "k2"]


def test_disabled_writes_nothing(tmp_path):
    path = tmp_path / "raw.jsonl"
    assert JSONLRawLog(str(path), enabled=False).append([{"key": "a"}]) == 0
    assert not path.exists()


def test_empty_input_creates_no_file(tmp_path):
    path = tmp_path / "raw.jsonl"
    assert JSONLRawLog(str(path)).append([]) == 0
    assert not path.exists()


def test_missing_file_reads_as_empty(tmp_path):
    assert JSONLRawLog(str(tmp_path / "absent.jsonl")).read() == []


def test_unserializable_values_do_not_abort_a_sync(tmp_path):
    """A sync that has already priced everything must not fail at the last step
    because one corner of a response wouldn't serialize."""
    from datetime import date

    log = JSONLRawLog(str(tmp_path / "raw.jsonl"))
    assert log.append([{"key": "a", "response": {"when": date(2026, 8, 14)}}]) == 1
    assert log.read()[0]["response"]["when"] == "2026-08-14"


def test_directories_are_created(tmp_path):
    log = JSONLRawLog(str(tmp_path / "nested" / "dir" / "raw.jsonl"))
    log.append([{"key": "a"}])
    assert log.read()
