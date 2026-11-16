import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from hydra_db import HydraDB as _SdkHydraDB
from rich.console import Console
from typer.testing import CliRunner

import hydradb_cli.output
from hydradb_cli.config import clear_config, get_database, save_config
from hydradb_cli.hydra import HydraDB
from hydradb_cli.hydra.client import _unwrap, _unwrap_payload
from hydradb_cli.main import app

runner = CliRunner()
WARNING = {"code": "FUTURE_WARNING", "message": "partial results", "collections": ["a", "b"], "extra": 1}
UNIFIED = {"chunks": [], "graph": [], "forceful_relations": [], "llm_prompt": ""}


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


def fake(layout="split"):
    save_config(api_key="synthetic", database="configured", collection="configured-col")
    w = MagicMock()
    w.databases.layout.return_value = layout
    w.context.query.return_value = {"chunks": [], "warnings": [WARNING]}
    w.context.query_unified.return_value = (UNIFIED, None)
    w.context.ingest.return_value = {"results": []}
    w.context.ingest_context.return_value = {"results": []}
    return w


@pytest.mark.parametrize("command", ["query", "ingest", "list", "delete", "relations", "subgraph"])
def test_type_alias_and_validation(command):
    args = {
        "query": ["x"],
        "ingest": ["--text", "x"],
        "list": [],
        "delete": ["id", "--yes"],
        "relations": ["id"],
        "subgraph": ["id"],
    }[command]
    w = fake()
    with patch("hydradb_cli.commands._impl.get_wrapper", return_value=w):
        canonical = runner.invoke(app, [command, *args, "--type", "memory"])
        assert canonical.exit_code == 0, canonical.output
        legacy = runner.invoke(app, [command, *args, "--kind", "memory"])
        assert legacy.exit_code == 0, legacy.output
        assert "deprecated" in legacy.stderr and "--type" in legacy.stderr
        for selectors in (
            ["--type", "invalid"],
            ["--kind", "invalid"],
            ["--type", "memory", "--kind", "knowledge"],
            ["--type", ""],
        ):
            result = runner.invoke(app, [command, *args, *selectors])
            assert result.exit_code != 0, result.output


def test_default_query_and_scope_switch_are_machine_readable():
    w = fake()
    with patch("hydradb_cli.commands._impl.get_wrapper", return_value=w):
        result = runner.invoke(app, ["-o", "json", "query", "x"])
        assert result.exit_code == 0, result.output
        body = json.loads(result.stdout)
        assert body["scope"] == {
            "database": "configured",
            "collection": "configured-col",
            "database_source": "default",
            "collection_source": "default",
        }
        assert body["warnings"] == [WARNING]
        assert w.context.query.call_args.kwargs["kind"] is None
        assert w.context.query.call_args.kwargs["recency_bias"] is None
        result = runner.invoke(app, ["-o", "json", "query", "x", "--database", "other", "--collection", "other-col"])
        assert "scope" not in json.loads(result.stdout)
        assert w.context.query.call_args.kwargs["database"] == "other"
    assert get_database() == "configured"


def test_unified_query_omission_does_not_erase_an_explicit_all_selector():
    w = fake("unified")
    with patch("hydradb_cli.commands._impl.get_wrapper", return_value=w):
        omitted = runner.invoke(app, ["query", "x"])
        assert omitted.exit_code == 0, omitted.output
        assert "kind" not in w.context.query_unified.call_args.kwargs
        assert "type" not in w.context.query_unified.call_args.kwargs
        w.context.query_unified.reset_mock()
        for selector in ("--type", "--kind"):
            explicit = runner.invoke(app, ["query", "x", selector, "all"])
            assert explicit.exit_code != 0, explicit.output
            assert "unified" in explicit.output
        w.context.query_unified.assert_not_called()
        w.context.query.assert_not_called()


def test_omitted_collection_stays_omitted_and_scope_does_not_leak():
    w = fake("unified")
    clear_config()
    save_config(api_key="synthetic", database="configured")
    with patch("hydradb_cli.commands._impl.get_wrapper", return_value=w):
        result = runner.invoke(app, ["-o", "json", "query", "x"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["scope"]["collection"] is None
        assert json.loads(result.stdout)["scope"]["collection_source"] == "server_default"
        assert w.context.query_unified.call_args.kwargs["collection"] is None
        w.databases.list.return_value = {"databases": []}
        result = runner.invoke(app, ["-o", "json", "database", "list"])
        assert "scope" not in json.loads(result.stdout)


def test_key_only_login_list_create_then_select_and_missing_database():
    with patch("hydradb_cli.commands.auth.build_wrapper") as build:
        result = runner.invoke(app, ["login", "--api-key", "synthetic"])
        assert result.exit_code == 0
        build.assert_not_called()
    assert get_database() is None
    w = MagicMock()
    w.databases.list.return_value = {"databases": []}
    w.databases.create.return_value = {"status": "accepted"}
    w.databases.layout.return_value = "split"
    w.context.query.return_value = {"chunks": []}
    with patch("hydradb_cli.commands._impl.get_wrapper", return_value=w):
        assert runner.invoke(app, ["database", "list"]).exit_code == 0
        assert runner.invoke(app, ["database", "create", "new"]).exit_code == 0
        result = runner.invoke(app, ["query", "x"])
        assert result.exit_code != 0
        assert "--database" in result.output and "config set database" in " ".join(result.output.split())
        assert runner.invoke(app, ["config", "set", "database", "new"]).exit_code == 0
        assert runner.invoke(app, ["query", "x"]).exit_code == 0
    assert w.context.query.call_args.kwargs["database"] == "new"


@pytest.mark.parametrize("layout", ["split", "unified"])
def test_automatic_ingest_routing_and_explicit_selectors(layout):
    w = fake(layout)
    with patch("hydradb_cli.commands._impl.get_wrapper", return_value=w):
        assert runner.invoke(app, ["ingest", "--text", "# Markdown text"]).exit_code == 0
        if layout == "unified":
            assert w.context.ingest_context.call_args.args[0][0]["text"] == "# Markdown text"
            w.context.ingest.assert_not_called()
            assert runner.invoke(app, ["ingest", "--text", "x", "--no-enrich"]).exit_code == 0
            assert w.context.ingest_context.call_args.args[0][0]["enrich"] is False
            assert runner.invoke(app, ["ingest", "--text", "x", "--type", "memory"]).exit_code != 0
        else:
            assert w.context.ingest.call_args.kwargs["kind"] == "memory"
            assert runner.invoke(app, ["ingest", "--text", "x", "--type", "knowledge"]).exit_code == 0
            assert w.context.ingest.call_args.kwargs["kind"] == "knowledge"


@pytest.mark.parametrize("layout", ["split", "unified"])
def test_canonical_query_controls_and_legacy_false(layout):
    w = fake(layout)
    with patch("hydradb_cli.commands._impl.get_wrapper", return_value=w):
        result = runner.invoke(
            app,
            [
                "query",
                "x",
                "--no-query-explicit-relations",
                "--follow-forceful-relations",
                "--additional-graph-context",
                "",
            ],
        )
        assert result.exit_code == 0, result.output
        method = w.context.query_unified if layout == "unified" else w.context.query
        assert method.call_args.kwargs["query_explicit_relations"] is False
        assert method.call_args.kwargs["additional_graph_context"] == ""
        result = runner.invoke(app, ["query", "x", "--no-query-forceful-relations", "--context", "old"])
        assert result.exit_code == 0, result.output
        assert method.call_args.kwargs["query_forceful_relations"] is False
        assert method.call_args.kwargs["additional_context"] == "old"


@pytest.mark.parametrize("unwrap", [_unwrap, _unwrap_payload])
def test_reuses_warning_metadata_without_losing_unknown_fields(unwrap, capsys):
    body = {"success": True, "data": {"chunks": [], "warnings": ["payload-owned"]}, "meta": {"warnings": [WARNING]}}
    envelope = SimpleNamespace(**body) if unwrap is _unwrap else body
    result = unwrap(envelope)
    assert result["warnings"] == ["payload-owned"]
    assert result["response_warnings"] == [WARNING]
    assert "FUTURE_WARNING" in capsys.readouterr().err


@pytest.mark.parametrize("unified", [False, True])
def test_wire_alias_precedence_and_unsupported_fields_use_raw(unified):
    w = HydraDB(token="synthetic", base_url="http://test.local", database="db")
    w._sdk = MagicMock()
    response = {"success": True, "data": UNIFIED if unified else {"chunks": []}, "meta": {"warnings": [WARNING]}}
    with patch("hydradb_cli.hydra.client.httpx.post", return_value=httpx.Response(200, json=response)) as post:
        method = w.context.query_unified if unified else w.context.query
        result = method(
            query="x",
            query_explicit_relations=False,
            follow_forceful_relations=True,
            query_forceful_relations=True,
            additional_graph_context="",
            additional_context="old",
            context="oldest",
        )
    sent = post.call_args.kwargs["json"]
    assert sent["query_explicit_relations"] is False
    assert sent["additional_graph_context"] == ""
    assert not {"follow_forceful_relations", "query_forceful_relations", "additional_context", "context"} & sent.keys()
    assert not {"type", "recency_bias"} & sent.keys()
    w._sdk.query.assert_not_called()
    assert (result[0] if unified else result)["warnings"] == [WARNING]


@pytest.mark.parametrize("extra", [{}, {"llm_prompt": "split prompt"}])
def test_response_linked_bucket_prefers_canonical_map_without_confusing_input(extra):
    from hydradb_cli.commands._impl import _format_query_result

    result = _format_query_result(
        {
            **extra,
            "chunks": [],
            "additional_graph_context": {"linked-id": [{"chunk_content": "linked text"}]},
            "additional_context": {"old-id": [{"chunk_content": "old text"}]},
        }
    )
    stream = StringIO()
    Console(file=stream, width=120).print(result)
    assert "linked text" in stream.getvalue()
    assert "old text" not in stream.getvalue()


@pytest.mark.parametrize("canonical", [None, {}, "request hints are not chunk maps"])
def test_response_linked_map_fallback_is_type_aware(canonical):
    from hydradb_cli.commands._impl import _format_query_result

    stream = StringIO()
    Console(file=stream).print(
        _format_query_result(
            {
                "chunks": [],
                "additional_graph_context": canonical,
                "additional_context": {"old-id": {"chunk_content": "legacy linked text"}},
            }
        )
    )
    assert ("legacy linked text" in stream.getvalue()) is (canonical != {})


@pytest.mark.parametrize(
    "args",
    [
        ["query", "x"],
        ["ingest", "--text", "x"],
        ["list"],
        ["inspect", "id"],
        ["delete", "id", "--yes"],
        ["relations", "id"],
        ["subgraph", "id"],
        ["verify", "id"],
    ],
)
def test_scoped_commands_without_database_are_actionable(args):
    save_config(api_key="synthetic")
    with patch("hydradb_cli.commands._impl.get_wrapper") as factory:
        result = runner.invoke(app, ["-o", "json", *args])
    assert result.exit_code != 0
    error = json.loads(result.stdout)["error"]
    assert "--database" in error and "config set database" in error
    factory.assert_not_called()


@pytest.mark.parametrize("warnings", [[], None, [None, {}, {"code": ""}, {"code": "bad", "message": 3}]])
def test_empty_or_invalid_warnings_do_not_invent_fields(warnings, capsys):
    assert _unwrap_payload({"success": True, "data": {"chunks": []}, "meta": {"warnings": warnings}}) == {"chunks": []}
    assert capsys.readouterr().err == ""


def test_warning_json_stdout_and_human_stderr_with_real_sdk():
    save_config(api_key="synthetic", database="db")

    def handler(request):
        body = {"chunks": []} if request.url.path == "/query" else {"details": [{"database": "db", "type": "split"}]}
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": body,
                "meta": {"warnings": [WARNING] if request.url.path == "/query" else []},
            },
        )

    w = HydraDB(token="synthetic", base_url="http://test.local", database="db")
    w._sdk = _SdkHydraDB(
        token="synthetic",
        base_url="http://test.local",
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with patch("hydradb_cli.commands._impl.get_wrapper", return_value=w):
        for options in (["-o", "json"], []):
            result = runner.invoke(app, [*options, "query", "x"])
            assert result.exit_code == 0, result.output
            assert "FUTURE_WARNING" in result.stderr
            if options:
                assert json.loads(result.stdout)["warnings"] == [WARNING]
            else:
                assert "scope:" in result.stderr


@pytest.mark.parametrize("layout", ["split", "unified"], ids=["sdk", "raw"])
@pytest.mark.parametrize("output", ["json", "human"])
@pytest.mark.parametrize("payload_owned_metadata", [False, True], ids=["additive", "payload-owned"])
def test_ingest_server_notices_survive_transport_to_cli(layout, output, payload_owned_metadata):
    save_config(api_key="synthetic", database="configured", collection="configured-col")
    warnings = [
        {"code": "inference_enabled_by_default", "message": "Server inference notice."},
        {"code": "enrichment_enabled_by_default", "message": "Server enrichment notice."},
        {"code": "acl_ignored_by_connector_enforcement", "message": "Server ACL notice.", "collections": ["a", "b"]},
    ]
    payload = {"results": [{"id": "accepted-id", "status": "queued"}], "success_count": 1, "failed_count": 0}
    if payload_owned_metadata:
        payload.update(
            {
                "request_id": "payload-request",
                "warnings": [{"code": "payload-warning"}],
                "response_warnings": [{"code": "payload-fallback-warning"}],
                "scope": {"server_owned": True},
                "cli_scope": {"server_fallback": True},
                "cli_scope_1": {"another_fallback": True},
            }
        )
    envelope = {"success": True, "data": payload, "meta": {"request_id": "server-request", "warnings": warnings}}
    ingests = []

    def handler(request):
        if request.url.path == "/databases":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"databases": ["switched"], "details": [{"database": "switched", "type": layout}]},
                    "meta": {},
                },
            )
        assert request.url.path == "/context/ingest"
        ingests.append(request)
        return httpx.Response(202, json=envelope)

    w = HydraDB(token="synthetic", base_url="http://test.local", database="configured", collection="configured-col")
    w._sdk = _SdkHydraDB(
        token="synthetic",
        base_url="http://test.local",
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with (
        patch("hydradb_cli.commands._impl.get_wrapper", return_value=w),
        patch(
            "hydradb_cli.hydra.client.httpx.post", side_effect=httpx.Client(transport=httpx.MockTransport(handler)).post
        ) as raw_post,
    ):
        result = runner.invoke(app, ["-o", output, "ingest", "--text", "ordinary text", "--database", "switched"])

    assert result.exit_code == 0, result.output
    assert len(ingests) == 1
    assert raw_post.call_count == (1 if layout == "unified" else 0)
    assert (
        ingests[0]
        .headers["content-type"]
        .startswith("application/json" if layout == "unified" else "multipart/form-data")
    )
    for warning in warnings:
        assert warning["code"] in result.stderr
        assert warning["message"] in " ".join(result.stderr.split())
    if output == "human":
        assert "scope:" in result.stderr and "switched" in result.stderr and "configured-col" in result.stderr
        assert all(warning["code"] not in result.stdout for warning in warnings)
        return
    body = json.loads(result.stdout)
    notice = body["cli_scope"] if payload_owned_metadata else body["scope"]
    assert notice == {
        "database": "switched",
        "collection": "configured-col",
        "database_source": "explicit",
        "collection_source": "default",
    }
    data = body["data"] if payload_owned_metadata else body
    assert data["request_id"] == ("payload-request" if payload_owned_metadata else "server-request")
    assert data["results"] == payload["results"]
    assert data["success_count"] == payload["success_count"]
    assert data["failed_count"] == payload["failed_count"]
    if payload_owned_metadata:
        assert data["warnings"] == payload["warnings"]
        assert data["response_warnings"] == [*payload["response_warnings"], *warnings]
        assert data["scope"] == payload["scope"]
        assert data["cli_scope"] == payload["cli_scope"]
        assert data["cli_scope_1"] == payload["cli_scope_1"]
    else:
        assert data["warnings"] == warnings


@pytest.mark.parametrize(
    "controls, expected",
    [
        ({"follow_forceful_relations": False, "query_forceful_relations": True}, {"query_explicit_relations": False}),
        ({"query_forceful_relations": False}, {"query_explicit_relations": False}),
        ({"additional_context": "", "context": "fallback"}, {"additional_graph_context": ""}),
        ({"additional_graph_context": None, "context": "old"}, {"additional_graph_context": "old"}),
    ],
)
def test_legacy_aliases_are_sent_under_the_new_name_keeping_false_and_empty_string(controls, expected):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "data": {"chunks": []}, "meta": {}})

    w = HydraDB(token="synthetic", base_url="http://test.local", database="db")
    w._sdk = _SdkHydraDB(
        token="synthetic",
        base_url="http://test.local",
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with patch(
        "hydradb_cli.hydra.client.httpx.post", side_effect=httpx.Client(transport=httpx.MockTransport(handler)).post
    ):
        w.context.query(query="x", **controls)
    assert seen == [{"query": "x", "database": "db", **expected}]


def test_missing_app_knowledge_sdk_field_uses_multipart_bridge_and_keeps_id():
    w = HydraDB(token="synthetic", base_url="http://test.local", database="db")

    def ingest(*, database, memories=None):
        raise AssertionError("unsupported text knowledge must not call the SDK")

    w._sdk = SimpleNamespace(context=SimpleNamespace(ingest=ingest))
    with patch(
        "hydradb_cli.hydra.client.httpx.post",
        return_value=httpx.Response(202, json={"success": True, "data": {"results": []}, "meta": {}}),
    ) as post:
        w.context.ingest(kind="knowledge", text="# Markdown", source_id="stable-id")
    files = post.call_args.kwargs["files"]
    assert json.loads(files["app_knowledge"][1]) == [{"id": "stable-id", "content": {"text": "# Markdown"}}]
    assert files["database"] == (None, "db")
    assert "collection" not in files


@pytest.mark.parametrize("status", [408, 409, 429, 503])
def test_raw_query_retries_reads_but_ingest_is_not_retried(status):
    w = HydraDB(token="synthetic", base_url="http://test.local", database="db")
    w._sdk = MagicMock()
    responses = [
        httpx.Response(status, json={"error": "retry"}),
        httpx.Response(200, json={"success": True, "data": {"chunks": []}, "meta": {}}),
    ]
    with (
        patch("hydradb_cli.hydra.client.httpx.post", side_effect=responses) as post,
        patch("hydradb_cli.hydra.client.time.sleep"),
    ):
        assert w.context.query(query="x", additional_graph_context="hint") == {"chunks": []}
        assert post.call_count == 2
    from hydradb_cli.hydra import HydraDBClientError

    with patch(
        "hydradb_cli.hydra.client.httpx.post", return_value=httpx.Response(status, json={"error": "no retry"})
    ) as post:
        with pytest.raises(HydraDBClientError):
            w.context.ingest_context([{"text": "x"}])
        assert post.call_count == 1
