"""Golden ``--output json`` shape tests.

``--output json`` is a documented agent contract (the README pipes
``hydradb -o json query … | jq '.chunks[0].chunk_content'``). The wrapper unwraps
the SDK's ``HandlerEnvelope`` and ``model_dump``s the payload back to a plain
dict, so the JSON shape of each command stays stable across SDK patch bumps.

Each case drives a representative v2 response through the **real** wrapper (SDK on
a mock transport) and the real CLI, then compares stdout to a committed golden
file. Regenerate the goldens deliberately with ``HYDRADB_UPDATE_GOLDEN=1`` when a
shape change is intended (e.g. an intentional SDK bump).
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from hydra_db import HydraDB as _SdkHydraDB
from typer.testing import CliRunner

from hydradb_cli.hydra import HydraDB
from hydradb_cli.main import app

runner = CliRunner()
GOLDEN_DIR = Path(__file__).parent / "golden"

# key -> (argv, representative envelope the server would return)
CASES = {
    "query": (
        ["query", "pricing", "--kind", "knowledge"],
        {
            "success": True,
            "meta": {},
            "data": {
                "chunks": [
                    {"chunk_content": "Pricing is $29/mo", "relevancy_score": 0.92, "source_title": "Pricing Doc"}
                ]
            },
        },
    ),
    "ingest": (
        ["ingest", "--text", "User prefers dark mode"],
        {
            "success": True,
            "meta": {},
            "data": {"success_count": 1, "failed_count": 0, "results": [{"id": "src_1", "status": "completed"}]},
        },
    ),
    "list": (
        ["list", "--kind", "knowledge"],
        {
            "success": True,
            "meta": {},
            "data": {
                "inner": {
                    "sources": [{"id": "s1", "title": "Report", "type": "knowledge"}],
                    "total": 1,
                    "success": True,
                }
            },
        },
    ),
    "inspect": (
        ["inspect", "src_1"],
        {
            "success": True,
            "meta": {},
            "data": {"content": "Full document text", "content_type": "text/plain", "size_bytes": 18},
        },
    ),
    "database_readiness": (
        ["database", "readiness", "db_test"],
        {
            "success": True,
            "meta": {},
            "data": {"database": "db_test", "infra": {"ready_for_ingestion": True, "graph_status": True}},
        },
    ),
}


def _wrapper_returning(envelope):
    def handler(request):
        return httpx.Response(200, json=envelope)

    w = HydraDB(token="x", base_url="http://test.local", database="db_test", collection="col_test")
    w._sdk = _SdkHydraDB(
        token="x", base_url="http://test.local", httpx_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    return w


def _run(argv, envelope, monkeypatch):
    monkeypatch.setenv("HYDRADB_API_KEY", "x")
    monkeypatch.setenv("HYDRADB_DATABASE", "db_test")
    monkeypatch.setenv("HYDRADB_COLLECTION", "col_test")
    with patch("hydradb_cli.commands._impl.get_wrapper", return_value=_wrapper_returning(envelope)):
        result = runner.invoke(app, ["--output", "json", *argv])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("key", list(CASES))
def test_json_shape_matches_golden(key, monkeypatch, tmp_path):
    argv, envelope = CASES[key]
    monkeypatch.setattr("hydradb_cli.config.CONFIG_FILE", tmp_path / "cfg.json")
    output = _run(argv, envelope, monkeypatch)

    golden_path = GOLDEN_DIR / f"{key}.json"
    if os.environ.get("HYDRADB_UPDATE_GOLDEN"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        golden_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")

    expected = json.loads(golden_path.read_text())
    if key != "database_readiness":
        expected = {
            **expected,
            "scope": {
                "database": "db_test",
                "collection": "col_test",
                "database_source": "default",
                "collection_source": "default",
            },
        }
    assert output == expected


def test_query_json_honours_jq_contract(monkeypatch, tmp_path):
    """The documented `jq '.chunks[0].chunk_content'` path must resolve."""
    argv, envelope = CASES["query"]
    monkeypatch.setattr("hydradb_cli.config.CONFIG_FILE", tmp_path / "cfg.json")
    output = _run(argv, envelope, monkeypatch)
    assert output["chunks"][0]["chunk_content"] == "Pricing is $29/mo"
