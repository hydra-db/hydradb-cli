import json
from unittest.mock import patch

import httpx
import pytest
from hydra_db import HydraDB as _SdkHydraDB
from typer.testing import CliRunner

import hydradb_cli.output
from hydradb_cli.config import save_config
from hydradb_cli.hydra import HydraDB
from hydradb_cli.main import app

runner = CliRunner()
ENV = {"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"}


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr("hydradb_cli.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("hydradb_cli.config.CONFIG_FILE", tmp_path / "config.json")
    for key in (
        "HYDRADB_API_KEY",
        "HYDRA_DB_API_KEY",
        "HYDRA_OPENCLAW_API_KEY",
        "HYDRADB_DATABASE",
        "HYDRADB_COLLECTION",
        "HYDRA_DB_TENANT_ID",
        "HYDRA_DB_SUB_TENANT_ID",
        "HYDRADB_OUTPUT",
    ):
        monkeypatch.delenv(key, raising=False)
    hydradb_cli.output._warned_deprecations.clear()


def invoke(argv, *, layout="split", probe_status=200, query_data=None):
    """Run the real CLI against a mock API; returns (result, requests by path)."""
    seen: dict[str, list[httpx.Request]] = {}

    def handler(request):
        seen.setdefault(request.url.path, []).append(request)
        if request.url.path == "/databases":
            if probe_status != 200:
                return httpx.Response(probe_status, json={"success": False, "error": {"message": "refused"}})
            details = [{"database": "db", "type": layout}]
            return httpx.Response(
                200, json={"success": True, "data": {"databases": ["db"], "details": details}, "meta": {}}
            )
        if request.url.path == "/query":
            data = query_data if query_data is not None else {"chunks": []}
            if layout == "unified" and query_data is None:
                data = {"chunks": [], "graph": [], "forceful_relations": [], "llm_prompt": ""}
            return httpx.Response(200, json={"success": True, "data": data, "meta": {"request_id": "req-1"}})
        return httpx.Response(202, json={"success": True, "data": {"results": []}, "meta": {}})

    wrapper = HydraDB(token="synthetic", base_url="http://test.local", database="db", collection="col")
    wrapper._sdk = _SdkHydraDB(
        token="synthetic",
        base_url="http://test.local",
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with (
        patch("hydradb_cli.commands._impl.get_wrapper", return_value=wrapper),
        patch(
            "hydradb_cli.hydra.client.httpx.post", side_effect=httpx.Client(transport=httpx.MockTransport(handler)).post
        ),
        patch(
            "hydradb_cli.hydra.client.httpx.get", side_effect=httpx.Client(transport=httpx.MockTransport(handler)).get
        ),
    ):
        result = runner.invoke(app, argv, env=ENV)
    return result, seen


def flat(text: str) -> str:
    return " ".join(text.split())


# D3: --markdown on knowledge text has no field to travel in, so it is refused, never dropped.


@pytest.mark.parametrize("probe_status", [200, 503], ids=["split", "layout-unknown"])
def test_markdown_knowledge_text_is_refused_instead_of_silently_dropped(probe_status):
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["ingest", "--type", "knowledge", "--markdown", "--text", "# md"], probe_status=probe_status)
    assert result.exit_code == 1, result.output
    assert "/context/ingest" not in seen
    message = flat(result.stderr)
    assert "--markdown" in message and ".md file" in message and "hydradb ingest notes.md" in message


def test_markdown_on_unified_database_is_refused_with_what_to_do_instead():
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["ingest", "--markdown", "--text", "# md"], layout="unified")
    assert result.exit_code == 1, result.output
    assert "/context/ingest" not in seen
    assert "drop --markdown" in flat(result.stderr)


def test_markdown_memory_text_still_sends_the_flag():
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["ingest", "--markdown", "--text", "# md"])
    assert result.exit_code == 0, result.output
    body = seen["/context/ingest"][0].content.decode()
    assert '"is_markdown": true' in body


# Canonical /query names travel with the legacy twin that pre-rename servers read.


@pytest.mark.parametrize("layout", ["split", "unified"])
def test_canonical_query_names_are_sent_with_their_legacy_twins(layout):
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(
        ["query", "x", "--additional_graph_context", "hint", "--no-query-explicit-relations"], layout=layout
    )
    assert result.exit_code == 0, result.output
    sent = json.loads(seen["/query"][0].content)
    assert sent["additional_graph_context"] == sent["additional_context"] == "hint"
    assert sent["query_explicit_relations"] is False and sent["follow_forceful_relations"] is False


def test_legacy_query_names_alone_are_sent_unchanged():
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["query", "x", "--context", "hint", "--no-follow-forceful-relations"])
    assert result.exit_code == 0, result.output
    sent = json.loads(seen["/query"][0].content)
    assert sent["additional_context"] == "hint" and "additional_graph_context" not in sent
    assert sent["follow_forceful_relations"] is False and "query_explicit_relations" not in sent


# D5: only a rejected key ends the command; any other probe failure falls back to split.


@pytest.mark.parametrize("status", [403, 404])
def test_layout_probe_refusal_falls_back_to_split_instead_of_failing(status):
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["query", "x"], probe_status=status)
    assert result.exit_code == 0, result.output
    assert f"HTTP {status}" in result.stderr and "sending the split request" in flat(result.stderr)
    assert json.loads(seen["/query"][0].content)["type"] == "all"


def test_layout_probe_401_still_ends_the_command():
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["query", "x"], probe_status=401)
    assert result.exit_code == 1, result.output
    assert "Authentication failed" in result.stderr
    assert "/query" not in seen


# Scope notice only when the database or collection came from a default.


@pytest.mark.parametrize("output", ["human", "json"])
def test_scope_notice_is_silent_when_both_scopes_are_explicit(output):
    save_config(api_key="synthetic", database="configured", collection="configured-col")
    argv = ["query", "x", "--database", "db", "--collection", "col"]
    result, _ = invoke(["-o", "json", *argv] if output == "json" else argv)
    assert result.exit_code == 0, result.output
    assert "scope:" not in result.stderr
    if output == "json":
        body = json.loads(result.stdout)
        assert "scope" not in body and "cli_scope" not in body


@pytest.mark.parametrize(
    "scope_args, expected",
    [
        ([], {"database_source": "default", "collection_source": "default"}),
        (["--database", "db"], {"database_source": "explicit", "collection_source": "default"}),
        (["--collection", "col"], {"database_source": "default", "collection_source": "explicit"}),
    ],
)
def test_scope_notice_is_reported_when_a_scope_came_from_a_default(scope_args, expected):
    save_config(api_key="synthetic", database="db", collection="col")
    human, _ = invoke(["query", "x", *scope_args])
    assert human.exit_code == 0, human.output
    assert "scope:" in human.stderr
    machine, _ = invoke(["-o", "json", "query", "x", *scope_args])
    assert machine.exit_code == 0, machine.output
    body = json.loads(machine.stdout)
    assert body["scope"] == {"database": "db", "collection": "col", **expected}


# D6: a legacy hint that disagrees with --additional_graph_context is a conflict.


@pytest.mark.parametrize("legacy", ["--context", "--additional-context"])
def test_conflicting_query_hints_exit_with_a_conflict(legacy):
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["query", "x", legacy, "old", "--additional_graph_context", "new"])
    assert result.exit_code == 1, result.output
    assert "conflict" in result.stderr and "--additional-graph-context" in result.stderr
    assert "/query" not in seen


def test_matching_query_hints_are_accepted():
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["query", "x", "--context", "same", "--additional_graph_context", "same"])
    assert result.exit_code == 0, result.output
    assert json.loads(seen["/query"][0].content)["additional_graph_context"] == "same"


# D6: linked chunks are labelled as linked, not ranked among the results.


def test_split_query_labels_linked_chunks_apart_from_ranked_results():
    save_config(api_key="synthetic", database="db")
    data = {
        "chunks": [{"chunk_content": "ranked body", "relevancy_score": 0.9, "source_title": "Ranked"}],
        "additional_graph_context": {
            "uuid-1": {"chunk_content": "linked body", "relevancy_score": 0.1, "source_title": "Linked"}
        },
    }
    result, _ = invoke(["query", "x"], query_data=data)
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "Found 1 result(s)" in out
    titles = [line for line in out.splitlines() if "Ranked" in line or "Linked" in line]
    ranked, linked = (next(line for line in titles if word in line) for word in ("Ranked", "Linked"))
    assert "1 • 90%" in ranked
    assert "linked" in linked and "•" not in linked and "10%" not in linked
    assert "2 •" not in out


# Requirement 10: a key-only user is pointed at listing or creating a database.


@pytest.mark.parametrize("argv", [["query", "x"], ["ingest", "--text", "x"], ["list"]])
def test_missing_database_error_names_list_and_create(argv):
    save_config(api_key="synthetic")
    result = runner.invoke(app, argv, env=ENV)
    assert result.exit_code == 1, result.output
    message = flat(result.stderr)
    assert "hydradb database list" in message and "hydradb database create <name>" in message
