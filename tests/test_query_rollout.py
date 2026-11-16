import inspect
import json
from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest
from hydra_db import HydraDB as _SdkHydraDB
from typer.testing import CliRunner

from hydradb_cli.commands import _impl
from hydradb_cli.config import save_config
from hydradb_cli.hydra import HydraDB
from hydradb_cli.main import app

runner = CliRunner()
WARNING = {"code": "rollout_notice", "message": "Server notice."}


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
    save_config(api_key="synthetic", database="db", collection="col")


def body_for(shape):
    body = {
        "chunks": [
            {
                "id": "source",
                "chunk_uuid": "chunk",
                "chunk_content": "Pricing is $29/mo",
                "source_title": "Pricing",
                "relevancy_score": 0.92,
            }
        ],
        "sources": [],
        "tabular_data": None,
        "forceful_relations": {"declared": [], "inferred": []},
        "additional_graph_context": {},
        "additional_context": {},
        "llm_prompt": "# Query results\nPricing is $29/mo\n",
    }
    if shape == "v2-graph-on":
        body.update(graph={"paths": []}, graph_context={"query_paths": [], "chunk_relations": []})
    if shape == "unified":
        body = {
            "chunks": [{"context_id": "source", "chunk_id": "chunk", "content": "Pricing is $29/mo", "score": 0.92}],
            "graph": [],
            "forceful_relations": [],
            "llm_prompt": body["llm_prompt"],
        }
    return body


def run_query(payload, transport, output="human", *, graph=False):
    requests = []
    captured = {}

    def handler(request):
        requests.append(request)
        if request.url.path == "/databases":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {"databases": ["db"], "details": [{"database": "db", "type": "unified"}]},
                    "meta": {},
                },
            )
        assert request.url.path == "/query"
        return httpx.Response(
            200, json={"success": True, "data": payload, "meta": {"request_id": "req-rollout", "warnings": [WARNING]}}
        )

    wrapper = HydraDB(token="synthetic", base_url="http://test.local", database="db", collection="col")
    wrapper._sdk = _SdkHydraDB(
        token="synthetic",
        base_url="http://test.local",
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    sdk_signature = inspect.signature(wrapper._sdk.query)
    if transport == "raw":
        sdk_signature = sdk_signature.replace(
            parameters=[
                parameter for name, parameter in sdk_signature.parameters.items() if name != "follow_forceful_relations"
            ]
        )
    query = wrapper.context.query_unified

    def capture_body(**kwargs):
        outcome = query(**kwargs)
        captured.update(body=outcome[0], snapshot=deepcopy(outcome[0]))
        return outcome

    argv = ["query", "pricing", "--graph-context" if graph else "--no-graph-context"]
    if transport == "raw":
        argv.append("--no-follow-forceful-relations")
    if output == "json":
        argv = ["-o", "json", *argv]
    elif output == "llm":
        argv.append("--llm")
    with (
        patch("hydradb_cli.commands._impl.get_wrapper", return_value=wrapper),
        patch.object(wrapper.context, "query_unified", side_effect=capture_body),
        patch.object(wrapper._sdk, "query", wraps=wrapper._sdk.query) as sdk_query,
        patch(
            "hydradb_cli.hydra.client.httpx.post", side_effect=httpx.Client(transport=httpx.MockTransport(handler)).post
        ) as raw,
    ):
        sdk_query.__signature__ = sdk_signature
        result = runner.invoke(app, argv, env={"COLUMNS": "160", "NO_COLOR": "1", "TERM": "dumb"})
    queries = [request for request in requests if request.url.path == "/query"]
    assert len(queries) == 1
    sent = json.loads(queries[0].content)
    assert sent["database"] == "db" and sent["collection"] == "col"
    assert sent["graph_context"] is graph and "type" not in sent
    assert queries[0].headers["API-Version"] == "2"
    assert raw.call_count == (1 if transport == "raw" else 0)
    assert sdk_query.call_count == (0 if transport == "raw" else 1)
    if transport == "raw":
        assert sent["follow_forceful_relations"] is False
    if captured:
        assert captured["body"] == captured["snapshot"]
    return result


@pytest.mark.parametrize("shape", ["v2-graph-off", "v2-graph-on", "unified"])
@pytest.mark.parametrize("transport", ["sdk", "raw"])
@pytest.mark.parametrize("output", ["human", "json", "llm"])
def test_physical_unified_database_renders_actual_response_shape_once(shape, transport, output):
    payload = body_for(shape)
    payload.update(
        scope={"server_owned": True},
        cli_scope={"server_fallback": True},
        data={"original": True},
        warnings=[{"code": "payload-warning"}],
        response_warnings=[{"code": "payload-fallback"}],
    )
    result = run_query(payload, transport, output, graph=shape == "v2-graph-on")
    assert result.exit_code == 0, result.output
    assert "rollout_notice" in result.stderr
    if output == "json":
        parsed = json.loads(result.stdout)
        assert parsed == {
            "data": {
                **payload,
                "request_id": "req-rollout",
                "response_warnings": [*payload["response_warnings"], WARNING],
            },
            "cli_scope": {
                "database": "db",
                "collection": "col",
                "database_source": "default",
                "collection_source": "default",
            },
        }
    elif output == "llm":
        assert result.stdout == payload["llm_prompt"] + "\n"
        assert "req-rollout" in result.stderr and "scope:" in result.stderr
    else:
        assert "Pricing is $29/mo" in result.stdout
        assert "92%" in result.stdout and "req-rollout" in result.stdout
        assert "scope:" in result.stderr


@pytest.mark.parametrize("shape", ["v2-graph-off", "v2-graph-on", "unified"])
@pytest.mark.parametrize("transport", ["sdk", "raw"])
@pytest.mark.parametrize("output", ["human", "llm"])
@pytest.mark.parametrize("owned_id", ["payload-id", None, ""], ids=["different-id", "owned-null", "owned-empty"])
@pytest.mark.parametrize("empty", [False, True], ids=["chunks", "empty"])
def test_feedback_hint_uses_envelope_id_without_mutating_colliding_payload(shape, transport, output, owned_id, empty):
    payload = body_for(shape)
    payload.update(
        request_id=owned_id,
        scope={"server_owned": True},
        cli_scope={"server_fallback": True},
        data={"original": True},
        warnings=[{"code": "payload-warning"}],
        response_warnings=[{"code": "payload-fallback"}],
    )
    if empty:
        payload["chunks"] = []
    original = deepcopy(payload)
    result = run_query(payload, transport, output, graph=shape == "v2-graph-on")
    assert result.exit_code == 0, result.output
    hint_output = result.stderr if output == "llm" else result.stdout
    assert "hydradb feedback req-rollout" in hint_output
    assert "hydradb feedback payload-id" not in hint_output
    assert "scope:" in result.stderr and "rollout_notice" in result.stderr
    if output == "llm":
        assert result.stdout == payload["llm_prompt"] + "\n"
    elif empty:
        assert "No relevant results found." in result.stdout
    else:
        assert "Pricing is $29/mo" in result.stdout and "92%" in result.stdout
    assert payload == original


def test_shared_llm_prompt_alone_does_not_select_unified_renderer():
    assert not _impl._is_unified_query_body({"chunks": [], "llm_prompt": "shared prompt"})
    assert _impl._is_unified_query_body(
        {"chunks": [{"context_id": "c", "content": "text"}], "llm_prompt": "shared prompt"}
    )


@pytest.mark.parametrize(
    "payload",
    [
        "unexpected markdown body",
        {},
        {"chunks": "wrong", "graph": [], "llm_prompt": "prompt"},
        {"chunks": [None], "graph": [], "llm_prompt": "prompt"},
        {"chunks": [{"context_id": "c", "content": {}}], "graph": [], "llm_prompt": "prompt"},
        {"chunks": [], "graph": "wrong", "llm_prompt": "prompt"},
        {"chunks": [], "forceful_relations": True, "llm_prompt": "prompt"},
        {"chunks": [], "graph": [], "llm_prompt": {}},
        {"chunks": [{"chunk_content": "V2 text"}], "graph": [], "forceful_relations": {}, "llm_prompt": "prompt"},
    ],
)
@pytest.mark.parametrize("transport", ["sdk", "raw"])
def test_malformed_rollout_response_is_a_cli_error_without_retry(payload, transport):
    result = run_query(payload, transport, "json")
    assert result.exit_code != 0, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert "Malformed query response" in json.loads(result.stdout)["error"]
    assert "Traceback" not in result.output


@pytest.mark.parametrize("prompt", [None, "absent"])
@pytest.mark.parametrize("transport", ["sdk", "raw"])
def test_llm_requires_an_actual_string_prompt(prompt, transport):
    payload = body_for("v2-graph-off")
    if prompt == "absent":
        payload.pop("llm_prompt")
    else:
        payload["llm_prompt"] = prompt
    result = run_query(payload, transport, "llm")
    assert result.exit_code != 0, result.output
    assert "llm_prompt" in result.stderr and "string" in result.stderr
