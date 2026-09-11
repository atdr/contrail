"""The offline generator for the airline lookup table."""

import csv
import runpy
from unittest.mock import Mock, mock_open

import pytest

from scripts import refresh_airline_codes as refresh


def binding(item, iata, icao, name="", aliases="", dissolved=""):
    values = {
        "item": item,
        "iata": iata,
        "icao": icao,
        "name": name,
        "aliases": aliases,
        "dissolved": dissolved,
    }
    return {key: {"value": value} for key, value in values.items() if value}


def test_normalize_and_value_match_runtime_lookup_keys():
    assert refresh.normalize("  British   AIRWAYS ") == "british airways"
    assert refresh.value({"name": {"value": "  Example Air  "}}, "name") == "Example Air"
    assert refresh.value({}, "name") == ""


def test_fetch_uses_the_wikidata_query_contract(monkeypatch):
    response = Mock()
    response.json.return_value = {"results": {"bindings": [{"item": {"value": "one"}}]}}
    get = Mock(return_value=response)
    monkeypatch.setattr(refresh.requests, "get", get)

    assert refresh.fetch() == [{"item": {"value": "one"}}]
    get.assert_called_once_with(
        refresh.SPARQL_URL,
        params={"query": refresh.QUERY, "format": "json"},
        headers={
            "User-Agent": refresh.USER_AGENT,
            "Accept": "application/sparql-results+json",
        },
        timeout=refresh.REQUEST_TIMEOUT,
    )
    response.raise_for_status.assert_called_once_with()


def test_build_rows_rejects_bad_and_ambiguous_designators():
    bindings = [
        binding("bad-iata", "ABC", "BAD"),
        binding("bad-icao", "AB", "LONG"),
        binding("many-iata", "AA", "MUL"),
        binding("many-iata", "BB", "MUL"),
        binding("many-icao", "CC", "ONE"),
        binding("many-icao", "CC", "TWO"),
        binding("ambiguous-a", "DD", "DUP", "First Air"),
        binding("ambiguous-b", "EE", "DUP", "Second Air"),
    ]

    rows, dropped = refresh.build_rows(bindings)

    assert rows == []
    assert dropped == ["ICAO DUP: DD, EE"]


def test_build_rows_prefers_live_carriers_and_filters_aliases():
    aliases = "Active Air|AA|ACT|Shared|Shared|One|Two|Three|Four|Five|Six|Seven"
    bindings = [
        binding("active", "AA", "ACT", "Active Air", aliases),
        binding("former", "AA", "ACT", "Former Air", "Former Alias", "2020-01-01"),
        binding("unnamed", "AA", "ACT", dissolved="2020-01-01"),
        binding("old-code", "BB", "NOW", "Old Name", dissolved="2020-01-01"),
        binding("live-code", "CC", "NOW", "Current Name"),
    ]

    rows, dropped = refresh.build_rows(bindings)

    assert dropped == []
    assert rows == [
        {
            "iata": "AA",
            "icao": "ACT",
            "name": "Active Air",
            "aliases": "Shared|One|Two|Three|Four|Five",
        },
        {"iata": "CC", "icao": "NOW", "name": "Current Name", "aliases": ""},
    ]


def test_ambiguous_names_are_removed_without_touching_unique_ones():
    rows = [
        {"iata": "AA", "name": " Shared Name ", "aliases": "Unique A|Clash||"},
        {"iata": "BB", "name": "Other", "aliases": "shared name|Clash|Unique B"},
    ]

    assert refresh.drop_ambiguous_names(rows) == ["clash", "shared name"]
    assert rows == [
        {"iata": "AA", "name": "", "aliases": "Unique A"},
        {"iata": "BB", "name": "Other", "aliases": "Unique B"},
    ]

    unique = [{"iata": "CC", "name": "Solo", "aliases": "Only"}]
    assert refresh.drop_ambiguous_names(unique) == []
    assert unique[0]["name"] == "Solo"


def test_main_writes_lf_csv_and_reports_every_kind_of_ambiguity(tmp_path, monkeypatch, capsys):
    output = tmp_path / "nested" / "airline_codes.csv"
    rows = [
        {"iata": "AA", "icao": "AAA", "name": "Alpha", "aliases": "A"},
        {"iata": "BB", "icao": "BBB", "name": "", "aliases": ""},
    ]
    dropped_names = [f"ambiguous {number}" for number in range(21)]
    monkeypatch.setattr(refresh, "OUTPUT", output)
    monkeypatch.setattr(refresh, "fetch", lambda: [{"binding": True}])
    monkeypatch.setattr(refresh, "build_rows", lambda _bindings: (rows, ["ICAO DUP: AA, BB"]))
    monkeypatch.setattr(refresh, "drop_ambiguous_names", lambda _rows: dropped_names)

    assert refresh.main() == 0

    with output.open(newline="") as handle:
        assert list(csv.DictReader(handle)) == rows
    assert b"\r\n" not in output.read_bytes()
    report = capsys.readouterr().out
    assert "1 binding(s) returned" in report
    assert "1 carry a usable name, 1 are ICAO-only" in report
    assert "Dropped 1 ambiguous ICAO code(s)" in report
    assert "Blanked 21 name(s)" in report
    assert "... and 1 more" in report

    monkeypatch.setattr(refresh, "drop_ambiguous_names", lambda _rows: dropped_names[:1])
    assert refresh.main() == 0
    assert "... and" not in capsys.readouterr().out


def test_main_has_no_ambiguity_report_when_nothing_was_dropped(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(refresh, "OUTPUT", tmp_path / "airline_codes.csv")
    monkeypatch.setattr(refresh, "fetch", lambda: [])
    monkeypatch.setattr(refresh, "build_rows", lambda _bindings: ([], []))
    monkeypatch.setattr(refresh, "drop_ambiguous_names", lambda _rows: [])

    assert refresh.main() == 0

    report = capsys.readouterr().out
    assert "Dropped" not in report
    assert "Blanked" not in report


def test_script_entrypoint_exits_with_main_result(monkeypatch):
    response = Mock()
    response.json.return_value = {"results": {"bindings": []}}
    monkeypatch.setattr(refresh.requests, "get", Mock(return_value=response))
    monkeypatch.setattr("builtins.open", mock_open())

    with pytest.raises(SystemExit) as caught:
        runpy.run_path(refresh.__file__, run_name="__main__")

    assert caught.value.code == 0
