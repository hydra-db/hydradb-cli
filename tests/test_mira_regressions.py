import json
from copy import deepcopy
from pathlib import Path
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


def real_wrapper(handler):
    wrapper = HydraDB(token="synthetic", base_url="http://test.local", database="db", collection="col")
    wrapper._sdk = _SdkHydraDB(
        token="synthetic",
        base_url="http://test.local",
        httpx_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return wrapper


def database_response():
    return httpx.Response(
        200,
        json={
            "success": True,
            "data": {"databases": ["db"], "details": [{"database": "db", "type": "unified"}]},
            "meta": {},
        },
    )


@pytest.mark.parametrize(
    "owned_id",
    ["absent", "payload-id", None, "", "envelope-id", 0, False],
    ids=["missing-id", "owned-id", "owned-null", "owned-empty", "equal-id", "owned-zero", "owned-false"],
)
@pytest.mark.parametrize("scope_keys", [0, 1, 2], ids=["normal-scope", "backend-scope", "both-scope-keys"])
@pytest.mark.parametrize("envelope_id", ["envelope-id", None], ids=["envelope-id", "no-envelope-id"])
def test_unified_json_request_id_survives_to_feedback_without_clobbering(owned_id, scope_keys, envelope_id):
    payload = {
        "chunks": [],
        "graph": [],
        "forceful_relations": [],
        "llm_prompt": "",
        "warnings": [{"code": "payload-warning"}],
        "response_warnings": [{"code": "payload-fallback"}],
        "data": {"original": True},
    }
    if owned_id != "absent":
        payload["request_id"] = owned_id
    if scope_keys:
        payload["scope"] = {"server_owned": True}
    if scope_keys == 2:
        payload["cli_scope"] = {"server_fallback": True}
    original_payload = deepcopy(payload)
    warning = {"code": "server-warning", "message": "Server notice."}
    meta = {"warnings": [warning]}
    if envelope_id is not None:
        meta["request_id"] = envelope_id
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/databases":
            return database_response()
        if request.url.path == "/query":
            return httpx.Response(
                200,
                json={"success": True, "data": payload, "meta": meta},
            )
        assert request.url.path == "/feedback"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"recorded": True, "request_id": json.loads(request.content)["request_id"]},
                "meta": {},
            },
        )

    wrapper = real_wrapper(handler)
    query = wrapper.context.query_unified
    before_print = {}

    def capture_body(**kwargs):
        outcome = query(**kwargs)
        before_print.update(body=outcome[0], snapshot=deepcopy(outcome[0]))
        return outcome

    with (
        patch("hydradb_cli.commands._impl.get_wrapper", return_value=wrapper),
        patch.object(wrapper.context, "query_unified", side_effect=capture_body),
        patch(
            "hydradb_cli.hydra.client.httpx.post", side_effect=httpx.Client(transport=httpx.MockTransport(handler)).post
        ),
    ):
        result = runner.invoke(app, ["-o", "json", "query", "question"])
        assert result.exit_code == 0, result.output
        output = json.loads(result.stdout)
        returned_body = {**payload, "response_warnings": [*payload["response_warnings"], warning]}
        expected_body = dict(returned_body)
        collision = envelope_id is not None and owned_id != "absent" and owned_id != envelope_id
        if envelope_id is not None and owned_id == "absent":
            expected_body["request_id"] = envelope_id
        notice = {
            "database": "db",
            "collection": "col",
            "database_source": "default",
            "collection_source": "default",
        }
        if collision:
            assert output == {"data": expected_body, "request_id": envelope_id, "scope": notice}
        elif scope_keys == 2:
            assert output == {"data": expected_body, "cli_scope": notice}
        else:
            assert output == {**expected_body, "scope" if scope_keys == 0 else "cli_scope": notice}
        data = output["data"] if collision or scope_keys == 2 else output
        if owned_id != "absent":
            assert data["request_id"] == owned_id
            assert type(data["request_id"]) is type(owned_id)
        elif envelope_id is not None:
            assert data["request_id"] == envelope_id
        else:
            assert "request_id" not in data
        assert data["warnings"] == payload["warnings"]
        assert data["response_warnings"] == [*payload["response_warnings"], warning]
        assert data["data"] == payload["data"]
        assert before_print["body"] == before_print["snapshot"] == returned_body
        assert payload == original_payload
        if scope_keys:
            assert data["scope"] == payload["scope"]
        if scope_keys == 2:
            assert data["cli_scope"] == payload["cli_scope"]
        if envelope_id is not None:
            feedback_id = output.get("request_id") or output["data"]["request_id"]
            assert feedback_id == envelope_id
            feedback = runner.invoke(app, ["-o", "json", "feedback", feedback_id, "--feedback", "useful"])
            assert feedback.exit_code == 0, feedback.output
            assert json.loads(feedback.stdout)["recorded"] is True

    query_requests = [r for r in requests if r.url.path == "/query"]
    assert len(query_requests) == 1
    sent = json.loads(query_requests[0].content)
    assert "type" not in sent and sent["database"] == "db" and sent["collection"] == "col"
    assert query_requests[0].headers["API-Version"] == "2"
    feedback_requests = [r for r in requests if r.url.path == "/feedback"]
    assert len(feedback_requests) == (0 if envelope_id is None else 1)
    if feedback_requests:
        assert json.loads(feedback_requests[0].content)["request_id"] == envelope_id


def assert_invalid_conversation_is_local(case, tmp_path, *, cached_layout):
    role = [] if case == "role-list" else {} if case == "role-object" else "user"
    conversation = tmp_path / "conversation.json"
    conversation.write_text(json.dumps([{"role": role, "content": "hello"}]))
    requests = []

    def handler(request):
        requests.append(request)
        if not cached_layout and request.method == "GET" and request.url.path == "/databases":
            return database_response()
        raise AssertionError("Invalid local input must not make ingest/write requests")

    wrapper = real_wrapper(handler)
    if cached_layout:
        wrapper._layouts = {"db": "unified"}
    argv = ["-o", "json", "ingest", "--conversation-file", str(conversation)]
    if case in ("empty-text", "nonempty-text"):
        argv.extend(["--text", "" if case == "empty-text" else "other text"])
    with (
        patch("hydradb_cli.commands._impl.get_wrapper", return_value=wrapper),
        patch(
            "hydradb_cli.hydra.client.httpx.post", side_effect=httpx.Client(transport=httpx.MockTransport(handler)).post
        ),
    ):
        result = runner.invoke(app, argv)
    assert result.exit_code != 0, result.output
    assert [(request.method, request.url.path) for request in requests] == (
        [] if cached_layout else [("GET", "/databases")]
    )
    assert isinstance(result.exception, SystemExit), result.exception
    error = json.loads(result.stdout)["error"]
    assert (
        "conversation[0].role" if case.startswith("role-") else "Pass exactly one of --text or --conversation-file"
    ) in error
    assert "Traceback" not in result.output


@pytest.mark.parametrize("case", ["role-list", "role-object", "empty-text", "nonempty-text"])
def test_invalid_conversation_is_a_local_cli_error_with_zero_http_when_layout_known(case, tmp_path):
    assert_invalid_conversation_is_local(case, tmp_path, cached_layout=True)


@pytest.mark.parametrize("case", ["role-list", "role-object", "empty-text", "nonempty-text"])
def test_invalid_conversation_does_not_write_after_cold_database_probe(case, tmp_path):
    assert_invalid_conversation_is_local(case, tmp_path, cached_layout=False)


def test_valid_conversation_is_parsed_once_and_keeps_cold_layout_negotiation(tmp_path):
    conversation = tmp_path / "conversation.json"
    turns = [{"role": "user", "content": "hello"}]
    conversation.write_text(json.dumps(turns))
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/databases":
            return database_response()
        assert request.url.path == "/context/ingest"
        assert json.loads(request.content)["context"][0]["conversation"] == turns
        return httpx.Response(
            202,
            json={
                "success": True,
                "data": {"results": [{"id": "accepted-id", "status": "queued"}], "success_count": 1},
                "meta": {},
            },
        )

    with (
        patch("hydradb_cli.commands._impl.get_wrapper", return_value=real_wrapper(handler)),
        patch.object(_impl, "_load_conversation", wraps=_impl._load_conversation) as load,
        patch(
            "hydradb_cli.hydra.client.httpx.post", side_effect=httpx.Client(transport=httpx.MockTransport(handler)).post
        ),
    ):
        result = runner.invoke(app, ["-o", "json", "ingest", "--conversation-file", str(conversation)])
    assert result.exit_code == 0, result.output
    assert load.call_count == 1
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/databases"),
        ("POST", "/context/ingest"),
    ]


def test_changelog_describes_additive_json_and_collision_envelope():
    changelog = (Path(__file__).parents[1] / "CHANGELOG.md").read_text()
    assert "body verbatim, nothing added" not in changelog
    assert "entire original response" in changelog
    assert "cli_scope" in changelog and "request_id" in changelog
    assert "current servers make it unified" not in changelog
    assert "leaves the default to the server/configuration" in changelog
    assert "before an ingest request" in changelog
    assert "request-ID collision" in changelog
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    assert "request-ID collision" in readme and "scope-only" in readme
    assert "jq -r '.request_id // .data.request_id'" in readme
