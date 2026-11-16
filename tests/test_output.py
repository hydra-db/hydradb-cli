"""Tests for hydradb_cli.output module."""

import json
from copy import deepcopy

import pytest

from hydradb_cli.output import (
    get_output_format,
    print_error,
    print_json,
    print_result,
    print_success,
    print_table,
    set_output_format,
    set_scope_notice,
)


@pytest.fixture(autouse=True)
def reset_output_format():
    """Reset output format to human after each test."""
    set_output_format("human")
    yield
    set_output_format("human")


class TestSetGetOutputFormat:
    def test_default_is_human(self):
        set_output_format("human")
        assert get_output_format() == "human"

    def test_set_json(self):
        set_output_format("json")
        assert get_output_format() == "json"


class TestPrintJson:
    def test_prints_valid_json(self, capsys):
        print_json({"key": "value", "num": 42})
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert parsed == {"key": "value", "num": 42}

    def test_handles_nested_data(self, capsys):
        print_json({"a": {"b": [1, 2, 3]}})
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert parsed["a"]["b"] == [1, 2, 3]

    @pytest.mark.parametrize(
        "payload, placement",
        [
            ({"chunks": []}, "scope"),
            ({"cli_scope": {"server_fallback": True}, "data": {"original": True}}, "scope"),
            ({"scope": {"server_owned": True}}, "cli_scope"),
            ({"scope": None}, "cli_scope"),
            (
                {
                    "scope": {"server_owned": True},
                    "cli_scope": {"server_fallback": True},
                    "cli_scope_1": {"another_fallback": True},
                    "data": {"original": True},
                    "warnings": [{"code": "server_warning"}],
                },
                "envelope",
            ),
            ({"scope": None, "cli_scope": None, "data": "server data"}, "envelope"),
        ],
        ids=["normal", "backend-fallback-only", "backend-scope", "null-scope", "both-owned", "both-null"],
    )
    def test_scope_notice_preserves_backend_fields_and_input(self, payload, placement, capsys):
        original = deepcopy(payload)
        notice = {
            "database": "db_test",
            "collection": "col_test",
            "database_source": "explicit",
            "collection_source": "default",
        }
        set_output_format("json")
        set_scope_notice("db_test", "col_test", database_source="explicit", collection_source="default")

        print_json(payload)

        captured = capsys.readouterr()
        expected = (
            {"data": original, "cli_scope": notice} if placement == "envelope" else {**original, placement: notice}
        )
        assert json.loads(captured.out) == expected
        assert captured.err == ""
        assert payload == original

    def test_backend_scope_fields_are_unchanged_without_a_client_notice(self, capsys):
        payload = {"scope": {"server_owned": True}, "cli_scope": {"server_fallback": True}, "data": [1]}
        original = deepcopy(payload)

        print_json(payload)

        captured = capsys.readouterr()
        assert json.loads(captured.out) == original
        assert captured.err == ""
        assert payload == original


class TestPrintSuccess:
    def test_human_mode_prints_message(self, capsys):
        set_output_format("human")
        print_success("All good")
        assert "All good" in capsys.readouterr().out

    def test_json_mode_is_noop(self, capsys):
        set_output_format("json")
        print_success("All good")
        assert capsys.readouterr().out == ""


class TestPrintError:
    def test_human_mode_prints_to_stderr(self, capsys):
        set_output_format("human")
        with pytest.raises((SystemExit, Exception)):
            print_error("Something broke")
        captured = capsys.readouterr()
        assert "Something broke" in captured.err

    def test_json_mode_prints_json(self, capsys):
        set_output_format("json")
        with pytest.raises((SystemExit, Exception)):
            print_error("Something broke")
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert parsed["success"] is False
        assert "Something broke" in parsed["error"]


class TestPrintResult:
    def test_json_mode_outputs_raw_data(self, capsys):
        set_output_format("json")
        print_result({"chunks": [], "total": 0})
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert parsed == {"chunks": [], "total": 0}

    def test_human_mode_uses_formatter(self, capsys):
        set_output_format("human")
        print_result({"count": 5}, lambda r: f"Found {r['count']} items")
        assert "Found 5 items" in capsys.readouterr().out

    def test_human_mode_fallback_to_json(self, capsys):
        set_output_format("human")
        print_result({"key": "val"})
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert parsed == {"key": "val"}


class TestPrintTable:
    def test_json_mode_outputs_array(self, capsys):
        set_output_format("json")
        print_table(["id", "name"], [["1", "Alice"], ["2", "Bob"]])
        output = capsys.readouterr().out
        parsed = json.loads(output)
        assert len(parsed) == 2
        assert parsed[0] == {"id": "1", "name": "Alice"}

    def test_human_mode_prints_table(self, capsys):
        set_output_format("human")
        print_table(["ID", "Name"], [["1", "Alice"]])
        output = capsys.readouterr().out
        assert "ID" in output
        assert "Alice" in output

    def test_empty_rows(self, capsys):
        set_output_format("human")
        print_table(["ID"], [])
        assert "No results" in capsys.readouterr().out
