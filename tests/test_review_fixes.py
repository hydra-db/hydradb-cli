import email
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


# --markdown is accepted on every ingest path and only travels where a split memory has the flag.

MAIN_SPLIT_KNOWLEDGE_TEXT = {
    "database": "db",
    "collection": "col",
    "type": "knowledge",
    "app_knowledge": [{"id": "", "content": {"text": "# md"}}],
}


@pytest.mark.parametrize("probe_status", [200, 503], ids=["split", "layout-unknown"])
def test_markdown_knowledge_text_is_accepted_and_sends_the_main_body(probe_status):
    save_config(api_key="synthetic", database="db")
    for extra in ([], ["--markdown"]):
        result, seen = invoke(["ingest", "--type", "knowledge", *extra, "--text", "# md"], probe_status=probe_status)
        assert result.exit_code == 0, result.output
        assert "markdown" not in result.stderr
        fields = form_fields(seen["/context/ingest"][0])
        fields["app_knowledge"] = json.loads(fields["app_knowledge"])
        assert fields == MAIN_SPLIT_KNOWLEDGE_TEXT, extra


def test_markdown_knowledge_file_is_accepted_and_sends_the_same_upload(tmp_path):
    save_config(api_key="synthetic", database="db")
    note = tmp_path / "a.txt"
    note.write_text("hi")
    for extra in ([], ["--markdown"]):
        result, seen = invoke(["ingest", *extra, str(note)])
        assert result.exit_code == 0, result.output
        assert "markdown" not in result.stderr
        assert form_fields(seen["/context/ingest"][0]) == {
            "database": "db",
            "collection": "col",
            "type": "knowledge",
            "upsert": "true",
            "documents": "hi",
        }, extra


def test_markdown_on_unified_database_is_accepted_and_left_out_of_the_item():
    save_config(api_key="synthetic", database="db")
    bodies = []
    for extra in ([], ["--markdown"]):
        result, seen = invoke(["ingest", *extra, "--text", "# md"], layout="unified")
        assert result.exit_code == 0, result.output
        assert "markdown" not in result.stderr
        bodies.append(json.loads(seen["/context/ingest"][0].content))
    assert bodies[0] == bodies[1]
    assert bodies[1]["context"] == [{"text": "# md", "enrich": True, "upsert": True}]


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
    assert sent["query_explicit_relations"] is False and sent["query_forceful_relations"] is False
    assert "follow_forceful_relations" not in sent


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
    assert json.loads(seen["/query"][0].content) == MAIN_SPLIT_QUERY


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


# Existing-user rule: on a split database an untyped call sends exactly what main sends.

MAIN_SPLIT_QUERY = {"query": "x", "database": "db", "collection": "col", "max_results": 10}
MAIN_SPLIT_INGEST = {
    "database": "db",
    "collection": "col",
    "type": "memory",
    "upsert": "true",
    "memories": [{"text": "note one", "infer": True, "is_markdown": False}],
}


def form_fields(request: httpx.Request) -> dict:
    message = email.message_from_bytes(
        b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
    )
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(decode=True).decode()
        for part in message.get_payload()
    }


def test_untyped_split_query_sends_the_main_body():
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["query", "x"])
    assert result.exit_code == 0, result.output
    sent = json.loads(seen["/query"][0].content)
    assert sent == MAIN_SPLIT_QUERY
    assert "type" not in sent and "recency_bias" not in sent


def test_untyped_split_ingest_sends_the_main_body():
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["ingest", "--text", "note one"])
    assert result.exit_code == 0, result.output
    fields = form_fields(seen["/context/ingest"][0])
    fields["memories"] = json.loads(fields["memories"])
    assert fields == MAIN_SPLIT_INGEST


def test_split_markdown_memory_sends_the_main_body():
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["ingest", "--markdown", "--text", "note one"])
    assert result.exit_code == 0, result.output
    assert "markdown" not in result.stderr
    fields = form_fields(seen["/context/ingest"][0])
    fields["memories"] = json.loads(fields["memories"])
    assert fields == {**MAIN_SPLIT_INGEST, "memories": [{"text": "note one", "infer": True, "is_markdown": True}]}


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["--type", "all"], {"type": "all"}),
        (["--recency-bias", "0.6"], {"recency_bias": 0.6}),
        (["--recency-bias", "0"], {"recency_bias": 0}),
    ],
)
def test_split_query_sends_explicit_type_and_recency_as_given(argv, expected):
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["query", "x", *argv])
    assert result.exit_code == 0, result.output
    assert json.loads(seen["/query"][0].content) == {**MAIN_SPLIT_QUERY, **expected}


@pytest.mark.parametrize("argv, expected", [([], {}), (["--recency-bias", "0.2"], {"recency_bias": 0.2})])
def test_unified_query_leaves_recency_to_the_server_unless_passed(argv, expected):
    save_config(api_key="synthetic", database="db")
    result, seen = invoke(["query", "x", *argv], layout="unified")
    assert result.exit_code == 0, result.output
    assert json.loads(seen["/query"][0].content) == {**MAIN_SPLIT_QUERY, **expected}
