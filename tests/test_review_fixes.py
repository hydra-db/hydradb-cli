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
