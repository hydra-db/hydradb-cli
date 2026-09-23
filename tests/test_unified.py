"""Unified databases (PRO-1618).

A database is ``split`` (knowledge + memory corpora, ``type`` on every call)
or ``unified`` (one corpus; ``type`` is never sent). The CLI reads the layout
once per database from ``GET /databases`` ``details[].type`` and branches on
THAT, never on a request flag. Everything here is about the unified side, and
about the split side staying exactly as it was.
"""

import io
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import typer
from hydra_db import HydraDB as _SdkHydraDB
from rich.console import Console
from typer.testing import CliRunner

import hydradb_cli.config
import hydradb_cli.output
from hydradb_cli.commands import _impl
from hydradb_cli.config import save_config
from hydradb_cli.hydra import HydraDB, HydraDBClientError
from hydradb_cli.main import app

runner = CliRunner()
GOLDEN = Path(__file__).parent / "golden"
# A real unified /query envelope, rendered by the server's own handler test
# (hydradb-application PRO-1618). Its ``meta.request_id`` is not used: each
# test hands the CLI its own request_id through the wrapper or the envelope.
UNIFIED_ENVELOPE = json.loads((GOLDEN / "query_unified.json").read_text())
UNIFIED_BODY = UNIFIED_ENVELOPE["data"]
SPLIT_BODY = json.loads((GOLDEN / "query.json").read_text())

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_WIDE = {"COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"}
_HYDRA_ENV_VARS = (
    "HYDRADB_API_KEY",
    "HYDRADB_DATABASE",
    "HYDRADB_COLLECTION",
    "HYDRADB_BASE_URL",
    "HYDRADB_OUTPUT",
    "HYDRADB_TENANT_ID",
    "HYDRADB_SUB_TENANT_ID",
    "HYDRADB_API_URL",
    "HYDRA_DB_API_KEY",
    "HYDRA_DB_TENANT_ID",
    "HYDRA_DB_SUB_TENANT_ID",
    "HYDRA_DB_BASE_URL",
    "HYDRA_OPENCLAW_API_KEY",
    "HYDRA_OPENCLAW_TENANT_ID",
)

# One ``context[]`` item carrying every documented field, in the contract's
# exact names. The CLI test and the wrapper test both pin against it.
FULL_ITEM = {
    "context_id": "policy-1",
    "title": "Refund policy",
    "text": "Refund policy: 30-day window.",
    "enrich": True,
    "upsert": True,
    "instructions": "keep the window",
    "happened_at": "2026-07-29",
    "attributes": {"team": "support"},
    "custom_attributes": {"source_app": "wiki"},
    "context_category": "business_knowledge",
    "forceful_relations": {"context_ids": ["chat-w1"]},
    "acl": ["user_email:a@x.com", "domain:acme.com"],
}

INGEST_202 = {
    "success": True,
    "message": "queued",
    "results": [
        {
            "source_id": "policy-1",
            "title": "Refund policy",
            "status": "queued",
            "infer": True,
            "error": None,
            "error_code": None,
        }
    ],
    "success_count": 1,
    "failed_count": 0,
}

EMPTY_BODY = {"chunks": [], "graph": [], "forceful_relations": [], "llm_prompt": ""}


@pytest.fixture(autouse=True)
def clean_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".hydradb"
    monkeypatch.setattr("hydradb_cli.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("hydradb_cli.config.CONFIG_FILE", config_dir / "config.json")
    for var in _HYDRA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    hydradb_cli.output._warned_deprecations.clear()
    hydradb_cli.config._warned_env_aliases.clear()
    yield


def _auth():
    save_config(api_key="test-key-abcdef1234567890", tenant_id="t1")


def _mock(layout: str, **returns):
    """A MagicMock wrapper whose layout probe answers ``layout``."""
    w = MagicMock()
    w.databases.layout.return_value = layout
    for dotted, value in returns.items():
        resource, method = dotted.split(".")
        getattr(getattr(w, resource), method).return_value = value
    return w


def _patch(w):
    return patch("hydradb_cli.commands._impl.get_wrapper", return_value=w)


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", _ANSI_RE.sub("", text))


def _lines(result) -> list[str]:
    return [_ANSI_RE.sub("", line) for line in result.output.splitlines()]


def _real_wrapper(sdk_handler, database="db_test", collection="col_test") -> HydraDB:
    """The real wrapper: the SDK on a mock transport, the raw path untouched."""
    w = HydraDB(token="x", base_url="http://test.local", database=database, collection=collection)
    w._sdk = _SdkHydraDB(
        token="x",
        base_url="http://test.local",
        httpx_client=httpx.Client(transport=httpx.MockTransport(sdk_handler)),
    )
    return w


def _databases_envelope(details: list[dict]) -> dict:
    return {
        "success": True,
        "meta": {},
        "data": {"databases": [d["database"] for d in details], "details": details},
    }


def _sdk_500(request):
    return httpx.Response(500, json={"success": False, "error": {"message": "the SDK path must not be used"}})


def _sdk_server(routes: dict, seen: list):
    """An SDK transport answering ``routes[path] -> (status, json)`` and
    recording every request as ``(method, path, json body)``."""

    def handler(request):
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        status, payload = routes[request.url.path]
        return httpx.Response(status, json=payload)

    return handler


class _Capture:
    """Stands in for ``httpx.post`` on the raw path and records every call."""

    def __init__(self, status: int = 200, body: dict | None = None):
        self.status = status
        self.body = body if body is not None else {"success": True, "data": {}, "meta": {}}
        self.calls: list[dict] = []

    def __call__(self, url, *, headers=None, json=None, timeout=None, **_ignored):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(self.status, json=self.body, request=httpx.Request("POST", url))


def _capture_post(monkeypatch, status: int = 200, body: dict | None = None) -> _Capture:
    capture = _Capture(status, body)
    monkeypatch.setattr("hydradb_cli.hydra.client.httpx.post", capture)
    return capture


def _path(origin: str | None, chunk_id: str, source: str, target: str, summary: str) -> dict:
    """One ``graph[]`` path of a single hop extracted from ``chunk_id``."""
    path: dict = {
        "triplets": [
            {
                "source": {"entity_id": f"ent_{source}", "name": source},
                "relation": {
                    "predicate": "links",
                    "context": f"{source} links {target}.",
                    "relationship_id": f"rel_{source}",
                    "chunk_id": chunk_id,
                },
                "target": {"entity_id": f"ent_{target}", "name": target},
            }
        ],
        "path_summary": summary,
    }
    if origin is not None:
        path["origin"] = origin
    return path


# ── the layout probe ─────────────────────────────────────────────────────────


class TestLayoutProbe:
    def test_layout_reads_details_and_memoises(self):
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path))
            return httpx.Response(
                200,
                json=_databases_envelope([{"database": "a", "type": "unified"}, {"database": "b", "type": "split"}]),
            )

        w = _real_wrapper(handler)
        assert w.databases.layout("a") == "unified"
        assert w.databases.layout("b") == "split"
        assert w.databases.layout("missing") == "split"
        assert seen == [("GET", "/databases")], "one probe per wrapper"

    def test_no_details_means_every_database_is_split(self):
        w = _real_wrapper(
            lambda r: httpx.Response(200, json={"success": True, "meta": {}, "data": {"databases": ["a"]}})
        )
        assert w.databases.layout("a") == "split"

    def test_a_failed_probe_raises_and_is_not_memoised(self):
        failing = {"on": True}

        def handler(request):
            if failing["on"]:
                return httpx.Response(401, json={"success": False, "error": {"message": "bad key"}})
            return httpx.Response(200, json=_databases_envelope([{"database": "a", "type": "unified"}]))

        w = _real_wrapper(handler)
        # A failed probe is an error, not a guess: answering split here would
        # send the split request shape to a database that may be unified.
        with pytest.raises(HydraDBClientError):
            w.databases.layout("a")
        failing["on"] = False
        assert w.databases.layout("a") == "unified", "a failed probe must not be memoised"

    def test_a_failed_probe_surfaces_as_a_cli_error(self):
        _auth()
        w = _mock("split")
        w.databases.layout.side_effect = HydraDBClientError(401, "bad key")
        with _patch(w):
            result = runner.invoke(app, ["list"], env=_WIDE)
        assert result.exit_code != 0
        assert "Authentication failed" in result.output

    def test_a_mocked_wrapper_reads_as_split(self):
        # Compared by value: a MagicMock layout is not "unified", so every
        # existing test that mocks the wrapper keeps exercising the split path.
        assert _impl._is_unified(MagicMock(), "anything") is False

    def test_database_layout_resolves_the_scope_first(self):
        with _patch(_mock("unified")), pytest.raises(typer.Exit):
            _impl.database_layout(None)
        _auth()
        with _patch(_mock("unified")):
            assert _impl.database_layout(None) == ("t1", "unified")
        with _patch(_mock("split")):
            assert _impl.database_layout("other") == ("other", "split")


# ── wrapper: unified query ───────────────────────────────────────────────────


class TestUnifiedQueryWrapper:
    def test_sends_no_type_through_the_sdk_and_returns_the_body_verbatim(self):
        seen = []
        envelope = {"success": True, "data": UNIFIED_BODY, "meta": {"request_id": "req-1", "latency_ms": 12}}
        w = _real_wrapper(_sdk_server({"/query": (200, envelope)}, seen))
        body, request_id = w.context.query_unified(
            query="pro plan",
            operator="and",
            query_by="text",
            max_results=5,
            mode="fast",
            titles=["Q3"],
            acl=["a@x.com"],
            follow_forceful_relations=False,
        )
        assert body == UNIFIED_BODY, "the four keys, nothing added, nothing dropped"
        assert request_id == "req-1"
        method, path, sent = seen[0]
        assert (method, path) == ("POST", "/query")
        assert sent == {
            "database": "db_test",
            "collection": "col_test",
            "query": "pro plan",
            "operator": "and",
            "max_results": 5,
            "mode": "fast",
            "query_by": "text",
            "titles": ["Q3"],
            "acl": ["a@x.com"],
            "follow_forceful_relations": False,
        }

    def test_unset_fields_are_omitted_not_sent_as_null(self):
        seen = []
        w = _real_wrapper(_sdk_server({"/query": (200, {"success": True, "data": EMPTY_BODY, "meta": {}})}, seen))
        body, request_id = w.context.query_unified(query="q")
        assert seen[0][2] == {"database": "db_test", "collection": "col_test", "query": "q"}
        assert body == EMPTY_BODY
        assert request_id is None

    def test_a_refusal_is_a_client_error(self):
        refusal = {"success": False, "error": {"message": "knowledge is not valid"}}
        w = _real_wrapper(_sdk_server({"/query": (400, refusal)}, []))
        with pytest.raises(HydraDBClientError) as excinfo:
            w.context.query_unified(query="q")
        assert excinfo.value.status_code == 400
        assert "knowledge is not valid" in str(excinfo.value.detail)


# ── wrapper: unified ingest ──────────────────────────────────────────────────


class TestUnifiedIngestWrapper:
    def test_posts_the_exact_json_body(self, monkeypatch):
        capture = _capture_post(monkeypatch, status=202, body={"success": True, "data": INGEST_202, "meta": {}})
        out = _real_wrapper(_sdk_500).context.ingest_context([FULL_ITEM])
        call = capture.calls[0]
        assert call["url"] == "http://test.local/context/ingest"
        assert call["headers"]["API-Version"] == "2"
        assert call["json"] == {"database": "db_test", "collection": "col_test", "context": [FULL_ITEM]}
        for forbidden in ("type", "items", "contexts", "memories", "app_knowledge", "documents"):
            assert forbidden not in call["json"], forbidden
        # The 202: results[].source_id is the context id.
        assert out["results"][0]["source_id"] == "policy-1"
        assert out["success_count"] == 1 and out["failed_count"] == 0

    def test_request_level_defaults_travel_only_when_set(self, monkeypatch):
        capture = _capture_post(monkeypatch, status=202, body={"success": True, "data": INGEST_202, "meta": {}})
        w = _real_wrapper(_sdk_500, collection=None)
        w.context.ingest_context([{"text": "x"}])
        assert capture.calls[0]["json"] == {"database": "db_test", "context": [{"text": "x"}]}
        w.context.ingest_context([{"text": "x"}], enrich=False, upsert=False, instructions="be brief")
        assert capture.calls[1]["json"] == {
            "database": "db_test",
            "context": [{"text": "x"}],
            "enrich": False,
            "upsert": False,
            "instructions": "be brief",
        }

    def test_a_conversation_item_is_sent_as_given(self, monkeypatch):
        capture = _capture_post(monkeypatch, status=202, body={"success": True, "data": INGEST_202, "meta": {}})
        turns = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        item = {"context_id": "chat-w1", "user_name": "soham", "conversation": turns}
        _real_wrapper(_sdk_500).context.ingest_context([item])
        assert capture.calls[0]["json"]["context"] == [item]

    def test_refuses_an_item_with_both_or_neither_shape(self):
        w = _real_wrapper(_sdk_500)
        with pytest.raises(ValueError, match="exactly one"):
            w.context.ingest_context([{"text": "a", "conversation": []}])
        with pytest.raises(ValueError, match=r"context\[1\]"):
            w.context.ingest_context([{"text": "a"}, {"title": "no body"}])
        with pytest.raises(ValueError, match="at least one"):
            w.context.ingest_context([])

    def test_refuses_more_than_100_items(self):
        with pytest.raises(ValueError, match="100"):
            _real_wrapper(_sdk_500).context.ingest_context([{"text": "x"}] * 101)

    def test_a_refusal_is_a_client_error(self, monkeypatch):
        _capture_post(monkeypatch, status=400, body={"success": False, "error": {"message": "context[0]: too large"}})
        with pytest.raises(HydraDBClientError) as excinfo:
            _real_wrapper(_sdk_500).context.ingest_context([{"text": "x"}])
        assert excinfo.value.status_code == 400


# ── hydradb query ────────────────────────────────────────────────────────────


class TestUnifiedQueryCommand:
    def test_goes_over_query_unified_and_never_sends_a_kind(self):
        _auth()
        w = _mock("unified", **{"context.query_unified": (UNIFIED_BODY, "req-1")})
        with _patch(w):
            result = runner.invoke(
                app,
                [
                    "query",
                    "pro plan",
                    "--no-follow-forceful-relations",
                    "--title",
                    "Q3",
                    "--operator",
                    "and",
                    "-n",
                    "5",
                ],
            )
        assert result.exit_code == 0, result.output
        w.context.query.assert_not_called()
        kwargs = w.context.query_unified.call_args.kwargs
        assert "kind" not in kwargs and "type" not in kwargs
        assert kwargs["follow_forceful_relations"] is False
        assert kwargs["titles"] == ["Q3"]
        assert kwargs["operator"] == "and" and kwargs["query_by"] == "text"
        assert kwargs["max_results"] == 5
        assert kwargs["database"] == "t1"

    def test_the_follow_switch_is_omitted_unless_given(self):
        _auth()
        w = _mock("unified", **{"context.query_unified": (UNIFIED_BODY, "req-1")})
        with _patch(w):
            runner.invoke(app, ["query", "pro plan"])
        assert w.context.query_unified.call_args.kwargs["follow_forceful_relations"] is None
        with _patch(w):
            runner.invoke(app, ["query", "pro plan", "--follow-forceful-relations"])
        assert w.context.query_unified.call_args.kwargs["follow_forceful_relations"] is True

    def test_human_output_renders_chunks_graph_and_forceful_relations(self):
        _auth()
        w = _mock("unified", **{"context.query_unified": (UNIFIED_BODY, "req-1")})
        with _patch(w):
            result = runner.invoke(app, ["query", "pro plan"], env=_WIDE)
        assert result.exit_code == 0, result.output
        out = _plain(result.output)
        assert "Found 2 result(s)" in out
        # chunks[]: context_id, score, content, enrichment (a string) with
        # enrichment_kind beside it, temporal facts
        assert "refund-policy" in out and "91%" in out
        assert "Refunds are processed within 30 days of purchase by the Finance Department." in out
        assert "enrichment (business_knowledge): Refund window is 30 days; Finance owns refund processing." in out
        assert "chat-2026-07-29" in out and "84%" in out and "Keep refund answers short please" in out
        assert "enrichment (user_preference): User prefers short answers about refunds." in out
        # a temporal fact with only a start date prints only that side
        assert "temporal: Refund policy effective_from June 2026. Start: 2026-06-01 [2026-06-01]" in out
        # graph[]: grouped by origin, path_summary + triplets. A query-path hop
        # cites the returned chunk it came from; a chunk relation is listed
        # under the chunk it hangs under.
        assert "/// Graph: 1 query path(s)" in out
        assert "Refund processing is managed by the Finance Department." in out
        assert "Refund Processing -> managed by -> Finance Department [1]" in out
        assert "/// Graph: 1 chunk relation path(s)" in out
        assert "[2] chat-2026-07-29" in out
        assert "The user prefers short answers about refunds." in out
        assert "User -> prefers -> short answers" in out
        assert out.index("/// Graph: 1 query path(s)") < out.index("/// Graph: 1 chunk relation path(s)")
        # forceful_relations[]: R label, via from/to and the pulled-in chunk
        assert "/// Forceful relations: 1 chunk(s)" in out
        assert "R1" in out and "refund-faq" in out
        assert "refunds to a card take 5 to 7 business days" in out and "0%" in out
        assert "Related" not in out
        # the markdown prompt is not dumped into the structured view
        assert "# Query results" not in out and "**Enrichment:**" not in out
        # feedback stays reachable
        assert "hydradb feedback req-1" in out

    def test_json_prints_the_body_verbatim(self):
        _auth()
        w = _mock("unified", **{"context.query_unified": (UNIFIED_BODY, "req-1")})
        with _patch(w):
            result = runner.invoke(app, ["--output", "json", "query", "pro plan"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == UNIFIED_BODY

    def test_llm_prints_the_prompt_verbatim_on_stdout(self):
        _auth()
        w = _mock("unified", **{"context.query_unified": (UNIFIED_BODY, "req-1")})
        with _patch(w):
            result = runner.invoke(app, ["query", "pro plan", "--llm"])
        assert result.exit_code == 0, result.output
        assert result.stdout == UNIFIED_BODY["llm_prompt"] + "\n"
        assert "req-1" in result.stderr, "the feedback hint goes to stderr so the prompt can be piped"

    def test_llm_yields_to_json_output(self):
        _auth()
        w = _mock("unified", **{"context.query_unified": (UNIFIED_BODY, "req-1")})
        with _patch(w):
            result = runner.invoke(app, ["--output", "json", "query", "pro plan", "--llm"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == UNIFIED_BODY

    def test_an_empty_result_still_prints_the_request_id(self):
        _auth()
        w = _mock("unified", **{"context.query_unified": (EMPTY_BODY, "req-1")})
        with _patch(w):
            result = runner.invoke(app, ["query", "nothing"])
        assert result.exit_code == 0, result.output
        assert "No relevant results found." in result.output
        assert "req-1" in result.output

    def test_kind_is_refused_on_a_unified_database(self):
        _auth()
        w = _mock("unified")
        with _patch(w):
            result = runner.invoke(app, ["query", "x", "--kind", "memory"])
        assert result.exit_code != 0
        assert "unified" in result.output and "--kind" in result.output
        w.context.query.assert_not_called()
        w.context.query_unified.assert_not_called()

    def test_deprecated_recall_aliases_search_the_one_corpus_on_a_unified_database(self):
        # These aliases picked their kind themselves; the user never typed one,
        # so on a unified database it is dropped rather than refused.
        _auth()
        for argv in (["recall", "full", "x"], ["recall", "preferences", "x"]):
            w = _mock("unified", **{"context.query_unified": ({"chunks": [], "graph": []}, None)})
            with _patch(w):
                result = runner.invoke(app, argv)
            assert result.exit_code == 0, (argv, result.output)
            w.context.query.assert_not_called()
            w.context.query_unified.assert_called_once()

    def test_an_explicit_kind_is_still_refused_on_a_unified_database(self):
        _auth()
        w = _mock("unified")
        with _patch(w):
            result = runner.invoke(app, ["query", "x", "--kind", "memory"])
        assert result.exit_code != 0 and "Re-run without --kind" in _plain(result.output)
        w.context.query_unified.assert_not_called()

    def test_llm_and_follow_flags_are_refused_on_a_split_database(self):
        _auth()
        w = _mock("split", **{"context.query": {"chunks": []}})
        for extra in (["--llm"], ["--no-follow-forceful-relations"], ["--follow-forceful-relations"]):
            with _patch(w):
                result = runner.invoke(app, ["query", "x", *extra])
            assert result.exit_code != 0, extra
            assert "unified databases only" in result.output
        w.context.query.assert_not_called()

    def test_split_query_call_is_unchanged(self):
        _auth()
        w = _mock(
            "split", **{"context.query": {"chunks": [{"chunk_content": "Pricing is $29", "relevancy_score": 0.9}]}}
        )
        with _patch(w):
            result = runner.invoke(app, ["query", "pricing", "--kind", "knowledge"])
        assert result.exit_code == 0, result.output
        assert "Pricing" in result.output
        kwargs = w.context.query.call_args.kwargs
        assert kwargs["kind"] == "knowledge"
        assert "follow_forceful_relations" not in kwargs and "llm" not in kwargs
        w.context.query_unified.assert_not_called()

    def test_the_request_id_reaches_every_output_mode(self, monkeypatch):
        """A unified /query ``meta`` has no ``tenant_id``, ``sub_tenant_id`` or
        ``source_type`` (PRO-1618); ``request_id`` is the one key the unified
        path needs, in every output mode."""
        monkeypatch.setenv("HYDRADB_API_KEY", "x")
        monkeypatch.setenv("HYDRADB_DATABASE", "db_test")
        meta = {"request_id": "req-7", "api_version": "2", "latency_ms": 9, "database": "db_test", "collection": "c"}
        routes = {
            "/databases": (200, _databases_envelope([{"database": "db_test", "type": "unified"}])),
            "/query": (200, {"success": True, "data": UNIFIED_BODY, "meta": meta}),
        }
        outputs = {}
        for mode, argv in (
            ("human", ["query", "pro plan"]),
            ("llm", ["query", "pro plan", "--llm"]),
            ("json", ["--output", "json", "query", "pro plan"]),
        ):
            wrapper = _real_wrapper(_sdk_server(routes, []))
            with patch("hydradb_cli.commands._impl.get_wrapper", return_value=wrapper):
                result = runner.invoke(app, argv, env=_WIDE)
            assert result.exit_code == 0, (mode, result.output)
            outputs[mode] = result
        assert "hydradb feedback req-7" in outputs["human"].output
        assert "req-7" in outputs["llm"].stderr
        assert json.loads(outputs["json"].stdout) == UNIFIED_BODY

    def test_end_to_end_json_is_the_server_body_verbatim(self, monkeypatch):
        """Real wrapper and SDK: the probe, then the type-less query."""
        monkeypatch.setenv("HYDRADB_API_KEY", "x")
        monkeypatch.setenv("HYDRADB_DATABASE", "db_test")
        monkeypatch.setenv("HYDRADB_COLLECTION", "col_test")
        seen = []
        routes = {
            "/databases": (200, _databases_envelope([{"database": "db_test", "type": "unified"}])),
            "/query": (200, {"success": True, "data": UNIFIED_BODY, "meta": {"request_id": "req-9"}}),
        }
        with patch("hydradb_cli.commands._impl.get_wrapper", return_value=_real_wrapper(_sdk_server(routes, seen))):
            result = runner.invoke(app, ["--output", "json", "query", "pro plan"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == UNIFIED_BODY
        assert [path for _, path, _ in seen] == ["/databases", "/query"]
        assert "type" not in seen[1][2]


# ── the renderer ─────────────────────────────────────────────────────────────


class TestQueryRenderer:
    @staticmethod
    def _render(renderable, width: int = 120) -> str:
        buffer = io.StringIO()
        Console(file=buffer, width=width, force_terminal=False, no_color=True).print(renderable)
        return buffer.getvalue()

    def test_shape_detection_is_by_body_not_by_layout(self):
        assert _impl._is_unified_query_body(UNIFIED_BODY)
        assert _impl._is_unified_query_body(EMPTY_BODY)
        assert not _impl._is_unified_query_body(SPLIT_BODY)
        assert not _impl._is_unified_query_body({"chunks": []})
        assert not _impl._is_unified_query_body({"chunks": [], "graph_context": {"query_paths": []}})

    def test_a_split_body_carrying_graph_and_forceful_relations_objects_is_split(self):
        # A split body carries `graph` ({paths}) and `forceful_relations`
        # ({declared, inferred}) beside graph_context: the unified keys, as
        # objects. Shape detection goes by type, never by the key being there.
        split = {
            **SPLIT_BODY,
            "graph_context": {"query_paths": [], "chunk_relations": []},
            "graph": {"paths": []},
            "forceful_relations": {"declared": [], "inferred": []},
        }
        assert not _impl._is_unified_query_body(split)
        assert _impl._is_unified_query_body({"chunks": [], "forceful_relations": []})
        out = self._render(_impl._format_query_result(split))
        assert "Pricing is $29/mo" in out
        assert "/// Forceful relations" not in out and "/// Graph" not in out

    def test_unified_content_enrichment_and_forceful_rows_are_never_trimmed(self):
        # The unified answer is not compacted anywhere: long content,
        # enrichment and a forceful chunk's content all reach the screen whole.
        long = " ".join(["word"] * 150)
        chunk = {"chunk_id": "c1", "context_id": "doc-1", "score": 0.5}
        body = {
            "chunks": [{**chunk, "content": long + " CONTENTTAIL", "enrichment": long + " ENRICHTAIL"}],
            "graph": [],
            "forceful_relations": [
                {"via": {"from": "doc-1", "to": "doc-2"}, "chunk": {**chunk, "content": long + " FORCEFULTAIL"}}
            ],
            "llm_prompt": "",
        }
        out = self._render(_impl._format_query_result(body))
        for tail in ("CONTENTTAIL", "ENRICHTAIL", "FORCEFULTAIL"):
            assert tail in out, tail
        assert "..." not in out

    def test_the_split_golden_renders_through_the_split_renderer_unchanged(self):
        out = self._render(_impl._format_query_result(SPLIT_BODY))
        assert "Found 1 result(s)" in out
        assert "92%" in out and "Pricing Doc" in out and "Pricing is $29/mo" in out
        for marker in ("enrichment", "/// Graph", "/// Forceful relations", "context_id"):
            assert marker not in out, marker

    def test_a_unified_body_through_the_shared_entry_point_renders_unified(self):
        # A server that does not list a database's layout still answers a
        # type-less query on a unified one in the unified shape, and the
        # renderer must still read it.
        out = self._render(_impl._format_query_result({**UNIFIED_BODY, "request_id": "req-2"}), width=200)
        assert "Refund Processing -> managed by -> Finance Department" in out
        assert "enrichment (user_preference)" in out
        assert "req-2" in out

    def test_the_forceful_relations_section_is_hidden_when_empty(self):
        out = self._render(_impl._format_unified_query_result({**UNIFIED_BODY, "forceful_relations": []}))
        assert "Found 2 result(s)" in out and "/// Graph" in out
        assert "Forceful relations" not in out and "refund-faq" not in out

    def test_the_old_relations_key_is_not_read(self):
        # `relations` was renamed `forceful_relations`; there is no fallback.
        old = {key: value for key, value in UNIFIED_BODY.items() if key != "forceful_relations"}
        old["relations"] = UNIFIED_BODY["forceful_relations"]
        out = self._render(_impl._format_unified_query_result(old))
        assert "Found 2 result(s)" in out
        assert "Forceful relations" not in out and "business days" not in out
        only_old = {"chunks": [], "graph": [], "relations": UNIFIED_BODY["forceful_relations"], "llm_prompt": ""}
        assert "No relevant results found." in self._render(_impl._format_unified_query_result(only_old))

    def test_graph_is_grouped_by_origin_with_positional_labels(self):
        body = {
            **UNIFIED_BODY,
            "graph": [
                _path("chunk_relation", "ck_faq_1", "Comment", "Fix", "The comment names the fix."),
                _path("query_path", "ck_not_returned", "Alpha", "Beta", "Alpha links Beta."),
                _path(None, "ck_policy_3", "Gamma", "Delta", "Gamma links Delta."),
            ],
        }
        out = _plain(self._render(_impl._format_unified_query_result(body), width=200))
        # One group per origin, query paths first. P labels are positions in
        # graph[], which is how llm_prompt numbers paths.
        assert "/// Graph: 1 query path(s)" in out
        assert re.search(r"P2\W+Alpha links Beta\.", out)
        # a hop whose chunk is not in the result cites nothing
        assert "Alpha -> links -> Beta" in out and "Alpha -> links -> Beta [" not in out
        # a chunk relation hangs under the chunk its hops came from, here a
        # forceful-relation chunk, by its R label
        assert "/// Graph: 1 chunk relation path(s)" in out
        assert re.search(r"P1\W+\[R1\] refund-faq\W+The comment names the fix\.", out)
        # a path with no origin is still shown, in its own group, not guessed into one
        assert "/// Graph: 1 path(s) with no known origin" in out
        assert re.search(r"P3\W+Gamma links Delta\.", out) and "Gamma -> links -> Delta [1]" in out
        assert out.index("query path(s)") < out.index("chunk relation path(s)") < out.index("no known origin")

    def test_enrichment_is_a_string_with_its_kind_beside_it(self):
        chunk = {"context_id": "c1", "score": 0.5, "content": "x"}
        cases = (
            # enrichment_kind is present even when there is no enrichment
            ({"enrichment_kind": "decision_trace"}, "enrichment (decision_trace)", "enrichment (decision_trace):"),
            ({"enrichment": "Owns refunds."}, "enrichment: Owns refunds.", "enrichment ("),
            (
                {"enrichment": "Owns refunds.", "enrichment_kind": "business_knowledge"},
                "enrichment (business_knowledge): Owns refunds.",
                None,
            ),
        )
        for fields, present, absent in cases:
            body = {**EMPTY_BODY, "chunks": [{**chunk, **fields}]}
            out = self._render(_impl._format_unified_query_result(body))
            assert present in out, fields
            if absent:
                assert absent not in out, fields

    def test_no_enrichment_line_without_enrichment_or_kind(self):
        body = {**EMPTY_BODY, "chunks": [{"context_id": "c1", "score": 0.5, "content": "x"}]}
        assert "enrichment" not in self._render(_impl._format_unified_query_result(body))

    def test_the_old_enrichment_object_is_not_read(self):
        # `enrichment` was `{text, kind}`; it is a string now, with the kind in
        # `enrichment_kind`. An object is not rendered, and does not crash.
        body = {
            **EMPTY_BODY,
            "chunks": [
                {"context_id": "c1", "content": "x", "enrichment": {"text": "stale text", "kind": "decision_trace"}}
            ],
        }
        out = self._render(_impl._format_unified_query_result(body))
        assert "Found 1 result(s)" in out
        assert "enrichment" not in out and "stale text" not in out


# ── hydradb ingest ───────────────────────────────────────────────────────────


class TestUnifiedIngestCommand:
    def test_text_posts_one_context_item_with_every_field(self):
        _auth()
        w = _mock("unified", **{"context.ingest_context": INGEST_202})
        with _patch(w):
            result = runner.invoke(
                app,
                [
                    "ingest",
                    "--text",
                    "Refund policy: 30-day window.",
                    "--context-id",
                    "policy-1",
                    "--title",
                    "Refund policy",
                    "--instructions",
                    "keep the window",
                    "--happened-at",
                    "2026-07-29",
                    "--attributes",
                    '{"team": "support"}',
                    "--custom-attributes",
                    '{"source_app": "wiki"}',
                    "--category",
                    "business_knowledge",
                    "--forceful-relation",
                    "chat-w1",
                    "--acl",
                    "user_email:a@x.com",
                    "--acl",
                    "domain:acme.com",
                ],
            )
        assert result.exit_code == 0, result.output
        w.context.ingest.assert_not_called()
        w.context.ingest_many.assert_not_called()
        args, kwargs = w.context.ingest_context.call_args
        assert args == ([FULL_ITEM],)
        assert kwargs == {"database": "t1", "collection": None}
        out = _plain(result.output)
        assert "Context queued (1 success, 0 failed)" in out
        assert "Context ID: policy-1 (queued)" in out

    def test_conversation_file(self, tmp_path):
        _auth()
        turns = [
            {"role": "user", "content": "Keep answers short please"},
            {"role": "assistant", "content": "Got it."},
            {"role": "system", "content": "Never store account numbers"},
        ]
        f = tmp_path / "conv.json"
        f.write_text(json.dumps(turns))
        w = _mock("unified", **{"context.ingest_context": INGEST_202})
        with _patch(w):
            result = runner.invoke(
                app, ["ingest", "--conversation-file", str(f), "--context-id", "chat-w1", "--user-name", "soham"]
            )
        assert result.exit_code == 0, result.output
        item = w.context.ingest_context.call_args.args[0][0]
        # The speaker is the item's user_name (hydradb-application#1653): a
        # turn is exactly {role, content}.
        assert item == {
            "context_id": "chat-w1",
            "conversation": turns,
            "user_name": "soham",
            "enrich": True,
            "upsert": True,
        }
        assert "conversation, 3 turn(s)" in _plain(result.output)

    def test_defaults_are_explicit_and_nothing_else_is_sent(self):
        _auth()
        w = _mock("unified", **{"context.ingest_context": INGEST_202})
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "a note"])
        assert result.exit_code == 0, result.output
        assert w.context.ingest_context.call_args.args[0] == [{"text": "a note", "enrich": True, "upsert": True}]

    def test_no_infer_no_enrich_and_no_upsert(self):
        _auth()
        for flag, field in (("--no-infer", "enrich"), ("--no-enrich", "enrich"), ("--no-upsert", "upsert")):
            w = _mock("unified", **{"context.ingest_context": INGEST_202})
            with _patch(w):
                result = runner.invoke(app, ["ingest", "--text", "a note", flag])
            assert result.exit_code == 0, result.output
            assert w.context.ingest_context.call_args.args[0][0][field] is False, flag

    def test_source_id_is_the_context_id_on_a_unified_database(self):
        _auth()
        w = _mock("unified", **{"context.ingest_context": INGEST_202})
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "x", "--source-id", "abc"])
        assert result.exit_code == 0, result.output
        assert w.context.ingest_context.call_args.args[0][0]["context_id"] == "abc"
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "x", "--source-id", "abc", "--context-id", "abc"])
        assert result.exit_code == 0, result.output
        w = _mock("unified")
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "x", "--source-id", "abc", "--context-id", "def"])
        assert result.exit_code != 0
        w.context.ingest_context.assert_not_called()

    def test_json_prints_the_202_verbatim(self):
        _auth()
        w = _mock("unified", **{"context.ingest_context": INGEST_202})
        with _patch(w):
            result = runner.invoke(app, ["--output", "json", "ingest", "--text", "a note"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == INGEST_202

    def test_a_failed_item_is_reported(self):
        _auth()
        failed = {
            **INGEST_202,
            "results": [
                {"source_id": "big-1", "status": "failed", "error": "too large", "error_code": "TEXT_TOO_LARGE"}
            ],
            "success_count": 0,
            "failed_count": 1,
        }
        w = _mock("unified", **{"context.ingest_context": failed})
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "a note"], env=_WIDE)
        # The server's own error text is still shown, and the exit code now
        # says the ingest did not succeed.
        assert result.exit_code != 0, result.output
        out = _plain(result.output)
        assert "0 success, 1 failed" in out
        assert "Context ID: big-1 (failed)" in out
        assert "Error: too large (TEXT_TOO_LARGE)" in out

    def test_a_failed_row_under_a_zeroed_count_still_exits_nonzero(self):
        _auth()
        lying = {
            **INGEST_202,
            "results": [
                {"source_id": "big-1", "status": "failed", "error": "too large", "error_code": "TEXT_TOO_LARGE"}
            ],
            "success_count": 1,
            "failed_count": 0,
        }
        w = _mock("unified", **{"context.ingest_context": lying})
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "a note"], env=_WIDE)
        assert result.exit_code != 0, result.output
        assert "Error: too large" in _plain(result.output)

    def test_files_are_refused(self, tmp_path):
        _auth()
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        w = _mock("unified")
        with _patch(w):
            result = runner.invoke(app, ["ingest", str(f)])
        assert result.exit_code != 0
        assert "unified" in result.output and "--text" in result.output
        w.context.ingest_many.assert_not_called()
        w.context.ingest_context.assert_not_called()

    def test_kind_and_markdown_are_refused(self):
        _auth()
        for extra in (["--kind", "memory"], ["--kind", "knowledge"], ["--markdown"]):
            w = _mock("unified")
            with _patch(w):
                result = runner.invoke(app, ["ingest", "--text", "x", *extra])
            assert result.exit_code != 0, extra
            assert "unified" in result.output
            w.context.ingest.assert_not_called()
            w.context.ingest_context.assert_not_called()

    def test_text_and_conversation_together_are_refused(self, tmp_path):
        _auth()
        f = tmp_path / "conv.json"
        f.write_text(json.dumps([{"role": "user", "content": "hi"}]))
        w = _mock("unified")
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "x", "--conversation-file", str(f)])
        assert result.exit_code != 0
        assert "exactly one" in result.output
        w.context.ingest_context.assert_not_called()

    def test_a_bad_conversation_is_named_by_turn(self, tmp_path):
        _auth()
        cases = [
            ([{"role": "bot", "content": "x"}], "conversation[0].role"),
            ([{"role": "user", "content": "x"}, {"role": "user", "content": ""}], "conversation[1].content"),
            ([{"role": "user", "content": "x", "extra": 1}], "unknown field"),
            ([{"role": "user", "content": "x", "name": "soham"}], "conversation[0] has a name"),
            (["not an object"], "conversation[0] must be an object"),
            ([], "non-empty JSON list"),
            ({"role": "user"}, "non-empty JSON list"),
        ]
        for turns, message in cases:
            f = tmp_path / "conv.json"
            f.write_text(json.dumps(turns))
            w = _mock("unified")
            with _patch(w):
                result = runner.invoke(app, ["ingest", "--conversation-file", str(f)])
            assert result.exit_code != 0, turns
            assert message in result.output, (turns, result.output)
            w.context.ingest_context.assert_not_called()
        f.write_text("not json")
        with _patch(_mock("unified")):
            result = runner.invoke(app, ["ingest", "--conversation-file", str(f)])
        assert result.exit_code != 0 and "JSON list" in result.output
        with _patch(_mock("unified")):
            result = runner.invoke(app, ["ingest", "--conversation-file", str(tmp_path / "missing.json")])
        assert result.exit_code != 0 and "not found" in result.output

    def test_a_bad_happened_at_is_refused(self):
        _auth()
        for value in ("2026-7-1", "2026-13-40", "yesterday", "2026-07-29T10:00:00Z"):
            w = _mock("unified")
            with _patch(w):
                result = runner.invoke(app, ["ingest", "--text", "x", "--happened-at", value])
            assert result.exit_code != 0, value
            assert "YYYY-MM-DD" in result.output
            w.context.ingest_context.assert_not_called()

    def test_attributes_must_be_json_objects(self):
        _auth()
        for extra in (["--attributes", "[1]"], ["--attributes", "not json"], ["--custom-attributes", '"str"']):
            w = _mock("unified")
            with _patch(w):
                result = runner.invoke(app, ["ingest", "--text", "x", *extra])
            assert result.exit_code != 0, extra
            assert "JSON object" in result.output
            w.context.ingest_context.assert_not_called()

    def test_a_bad_category_is_refused(self):
        _auth()
        w = _mock("unified")
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "x", "--category", "wisdom"])
        assert result.exit_code != 0
        assert "--category must be one of" in result.output
        w.context.ingest_context.assert_not_called()

    def test_forceful_relation_ids_are_trimmed_and_deduplicated(self):
        _auth()
        w = _mock("unified", **{"context.ingest_context": INGEST_202})
        with _patch(w):
            result = runner.invoke(
                app,
                [
                    "ingest",
                    "--text",
                    "x",
                    "--forceful-relation",
                    " a ",
                    "--forceful-relation",
                    "b",
                    "--forceful-relation",
                    "a",
                ],
            )
        assert result.exit_code == 0, result.output
        assert w.context.ingest_context.call_args.args[0][0]["forceful_relations"] == {"context_ids": ["a", "b"]}
        w = _mock("unified")
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "x", "--forceful-relation", "  "])
        assert result.exit_code != 0
        w.context.ingest_context.assert_not_called()

    def test_deprecated_write_aliases_are_refused_on_a_unified_database(self, tmp_path):
        _auth()
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        for argv in (
            ["memories", "add", "--text", "x"],
            ["knowledge", "upload-text", "--text", "x"],
            ["knowledge", "upload", str(f)],
        ):
            w = _mock("unified")
            with _patch(w):
                result = runner.invoke(app, argv)
            assert result.exit_code != 0, argv
            assert "unified" in result.output
            w.context.ingest.assert_not_called()
            w.context.ingest_many.assert_not_called()

    def test_unified_only_options_are_refused_on_a_split_database(self, tmp_path):
        _auth()
        f = tmp_path / "conv.json"
        f.write_text(json.dumps([{"role": "user", "content": "hi"}]))
        for extra in (
            ["--conversation-file", str(f)],
            ["--context-id", "c"],
            ["--no-enrich"],
            ["--instructions", "i"],
            ["--happened-at", "2026-07-29"],
            ["--attributes", "{}"],
            ["--custom-attributes", "{}"],
            ["--category", "auto"],
            ["--forceful-relation", "x"],
            ["--acl", "a@x.com"],
        ):
            w = _mock("split")
            with _patch(w):
                result = runner.invoke(app, ["ingest", "--text", "x", *extra])
            assert result.exit_code != 0, extra
            assert "unified databases only" in result.output
            assert extra[0] in result.output
            w.context.ingest.assert_not_called()
            w.context.ingest_context.assert_not_called()

    def test_split_ingest_is_unchanged(self):
        _auth()
        w = _mock(
            "split",
            **{"context.ingest": {"success_count": 1, "failed_count": 0, "results": [{"id": "src_1", "status": "ok"}]}},
        )
        with _patch(w):
            result = runner.invoke(
                app,
                [
                    "ingest",
                    "--text",
                    "User prefers dark mode",
                    "--title",
                    "T",
                    "--source-id",
                    "s1",
                    "--user-name",
                    "ada",
                    "--markdown",
                    "--no-infer",
                    "--no-upsert",
                ],
            )
        assert result.exit_code == 0, result.output
        assert w.context.ingest.call_args.kwargs == {
            "kind": "memory",
            "text": "User prefers dark mode",
            "title": "T",
            "source_id": "s1",
            "user_name": "ada",
            "infer": False,
            "is_markdown": True,
            "upsert": False,
            "database": "t1",
            "collection": None,
        }
        w.context.ingest_context.assert_not_called()
        # and knowledge text
        w = _mock("split", **{"context.ingest": {"results": [{"id": "k1"}]}})
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--kind", "knowledge", "--text", "notes"])
        assert result.exit_code == 0, result.output
        assert w.context.ingest.call_args.kwargs["kind"] == "knowledge"


# ── list / delete / relations / subgraph / inspect ───────────────────────────


class TestUnifiedReadAndDeleteCommands:
    def test_list_sends_no_kind(self):
        _auth()
        w = _mock("unified", **{"context.list": {"sources": [{"id": "s1", "title": "Report"}], "total": 1}})
        with _patch(w):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0, result.output
        assert w.context.list.call_args.kwargs["kind"] is None
        with _patch(w):
            result = runner.invoke(app, ["list", "--kind", "memory"])
        assert result.exit_code != 0 and "unified" in result.output

    def test_delete_sends_no_kind(self):
        _auth()
        w = _mock("unified", **{"context.delete": {"success": True, "deleted_count": 1}})
        with _patch(w):
            result = runner.invoke(app, ["delete", "item-1", "--yes"])
        assert result.exit_code == 0, result.output
        assert w.context.delete.call_args.kwargs["kind"] is None
        assert "item(s)" in result.output
        w = _mock("unified")
        with _patch(w):
            result = runner.invoke(app, ["delete", "item-1", "--kind", "knowledge", "--yes"])
        assert result.exit_code != 0 and "unified" in result.output
        w.context.delete.assert_not_called()

    def test_delete_keeps_the_split_default(self):
        _auth()
        w = _mock("split", **{"context.delete": {"success": True, "deleted_count": 1}})
        with _patch(w):
            result = runner.invoke(app, ["delete", "src_9", "--yes"])
        assert result.exit_code == 0, result.output
        assert w.context.delete.call_args.kwargs["kind"] == "knowledge"
        assert "knowledge source(s)" in result.output
        with _patch(w):
            runner.invoke(app, ["delete", "mem_1", "--kind", "memory", "--yes"])
        assert w.context.delete.call_args.kwargs["kind"] == "memory"

    def test_relations_sends_no_kind(self):
        _auth()
        w = _mock("unified", **{"context.relations": {"relations": []}})
        with _patch(w):
            result = runner.invoke(app, ["relations", "src_1"])
        assert result.exit_code == 0, result.output
        assert w.context.relations.call_args.kwargs["kind"] is None
        with _patch(w):
            result = runner.invoke(app, ["relations", "src_1", "--kind", "knowledge"])
        assert result.exit_code != 0 and "unified" in result.output

    def test_subgraph_sends_no_kind(self):
        _auth()
        w = _mock("unified", **{"context.subgraph": {"sources": []}})
        with _patch(w):
            result = runner.invoke(app, ["subgraph", "src_1"])
        assert result.exit_code == 0, result.output
        assert w.context.subgraph.call_args.kwargs["kind"] is None
        with _patch(w):
            result = runner.invoke(app, ["subgraph", "src_1", "--kind", "memory"])
        assert result.exit_code != 0 and "unified" in result.output

    def test_inspect_sends_no_kind(self):
        _auth()
        w = _mock("unified", **{"context.inspect": {"content": "Full text", "content_type": "text/plain"}})
        with _patch(w):
            result = runner.invoke(app, ["inspect", "src_1"])
        assert result.exit_code == 0, result.output
        kwargs = w.context.inspect.call_args.kwargs
        assert "kind" not in kwargs and "type" not in kwargs

    def test_deprecated_memories_list_lists_the_one_corpus_on_a_unified_database(self):
        _auth()
        w = _mock("unified", **{"context.list": {"sources": [{"id": "a", "title": "A", "type": "memory"}]}})
        with _patch(w):
            result = runner.invoke(app, ["memories", "list"])
        assert result.exit_code == 0, result.output
        assert w.context.list.call_args.kwargs["kind"] is None
        # One corpus: no per-item kind column that would only mislead.
        assert "Type" not in result.output and "│ memory" not in result.output


# ── hydradb database create --type / list ────────────────────────────────────


class TestDatabaseLayoutCommands:
    def test_create_with_type_unified(self):
        _auth()
        w = _mock("split", **{"databases.create": {"success": True}})
        with _patch(w):
            result = runner.invoke(app, ["database", "create", "new-db", "--type", "unified"])
        assert result.exit_code == 0, result.output
        assert w.databases.create.call_args.kwargs == {"database": "new-db", "layout": "unified"}
        assert "unified" in result.output

    def test_create_without_type_sends_no_layout(self):
        _auth()
        w = _mock("split", **{"databases.create": {"success": True}})
        with _patch(w):
            result = runner.invoke(app, ["database", "create", "new-db"])
        assert result.exit_code == 0, result.output
        assert w.databases.create.call_args.kwargs["layout"] is None

    def test_create_rejects_an_unknown_type(self):
        _auth()
        w = _mock("split")
        with _patch(w):
            result = runner.invoke(app, ["database", "create", "new-db", "--type", "hybrid"])
        assert result.exit_code != 0
        assert "split" in result.output and "unified" in result.output
        w.databases.create.assert_not_called()

    def test_wrapper_create_with_a_layout_sends_type_through_the_sdk(self):
        seen = []
        accepted = {"success": True, "data": {"status": "accepted"}, "meta": {}}
        out = _real_wrapper(_sdk_server({"/databases": (200, accepted)}, seen)).databases.create(
            database="new", layout="unified"
        )
        assert out.get("status") == "accepted"
        assert seen == [("POST", "/databases", {"database": "new", "type": "unified"})]

    def test_wrapper_create_without_a_layout_is_the_sdk_call(self):
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"success": True, "data": {"status": "accepted"}, "meta": {}})

        _real_wrapper(handler).databases.create(database="new")
        assert seen["path"] == "/databases"
        assert "type" not in seen["body"]

    def test_wrapper_create_rejects_an_unknown_layout(self):
        with pytest.raises(ValueError):
            _real_wrapper(_sdk_500).databases.create(database="new", layout="hybrid")

    def test_list_shows_each_layout(self):
        _auth()
        w = _mock(
            "split",
            **{
                "databases.list": {
                    "databases": ["a", "b"],
                    "details": [{"database": "a", "type": "unified"}, {"database": "b", "type": "split"}],
                }
            },
        )
        with _patch(w):
            result = runner.invoke(app, ["database", "list"], env=_WIDE)
        assert result.exit_code == 0, result.output
        lines = _lines(result)
        assert any("Type" in line for line in lines)
        assert any("a" in line.split("│") or " a " in line for line in lines if "unified" in line), lines
        assert any(" b " in line for line in lines if "split" in line), lines

    def test_list_without_details_shows_split(self):
        _auth()
        w = _mock("split", **{"databases.list": {"databases": ["a"]}})
        with _patch(w):
            result = runner.invoke(app, ["database", "list"], env=_WIDE)
        assert result.exit_code == 0, result.output
        assert any(" a " in line and "split" in line for line in _lines(result))


# ── PRO-2196: probe fallback, strict item contract, caps ─────────────────────

_REFUSED_AS_UNIFIED = HydraDBClientError(
    400,
    '{"success": false, "error": {"code": "CORPUS_TYPE_UNSUPPORTED", '
    '"message": "type \\"memory\\" is not valid on a unified database"}}',
)


def _unknown(**returns):
    """A mocked wrapper whose layout probe fails the way a busy server does."""
    w = _mock("split", **returns)
    w.databases.layout.side_effect = HydraDBClientError(503, "unavailable")
    return w


class TestLayoutProbeFallback:
    def test_the_probe_has_a_short_budget_and_no_retries(self):
        seen = []

        def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, json=_databases_envelope([{"database": "a", "type": "unified"}]))

        w = _real_wrapper(handler)
        original = w._sdk.databases.list
        calls = []

        def spy(**kwargs):
            calls.append(kwargs)
            return original(**kwargs)

        w._sdk.databases.list = spy
        assert w.databases.layout("a") == "unified"
        assert calls == [{"request_options": {"timeout_in_seconds": 5, "max_retries": 0}}]

    def test_a_transient_probe_failure_sends_the_split_request_with_a_warning(self):
        _auth()
        w = _unknown(**{"context.query": {"chunks": []}})
        with _patch(w):
            result = runner.invoke(app, ["query", "x"])
        assert result.exit_code == 0, result.output
        w.context.query.assert_called_once()
        w.context.query_unified.assert_not_called()
        assert "Could not check whether 't1' is split or unified (HTTP 503)" in _plain(result.stderr)

    def test_an_auth_failure_on_the_probe_is_still_reported(self):
        _auth()
        w = _mock("split")
        w.databases.layout.side_effect = HydraDBClientError(401, "bad key")
        with _patch(w):
            result = runner.invoke(app, ["query", "x"])
        assert result.exit_code != 0 and "Authentication failed" in result.output
        w.context.query.assert_not_called()

    def test_an_ingest_refused_as_unified_is_redone_as_one_context_item(self):
        _auth()
        w = _unknown(**{"context.ingest_context": INGEST_202})
        w.context.ingest.side_effect = _REFUSED_AS_UNIFIED
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "a note", "--user-name", "ada", "--source-id", "n1"])
        assert result.exit_code == 0, result.output
        assert w.context.ingest.call_args.kwargs["kind"] == "memory"
        item = w.context.ingest_context.call_args.args[0][0]
        assert item == {"context_id": "n1", "text": "a note", "user_name": "ada", "enrich": True, "upsert": True}
        assert "redoing the request in the unified shape" in _plain(result.stderr)

    def test_the_sdk_paths_message_only_refusal_is_recognised(self):
        # The SDK path keeps the server's message and drops its code.
        _auth()
        w = _unknown(**{"context.ingest_context": INGEST_202})
        w.context.ingest.side_effect = HydraDBClientError(
            400, 'type "memory" is not valid on a unified database: knowledge and memory are one corpus'
        )
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "a note"])
        assert result.exit_code == 0, result.output
        w.context.ingest_context.assert_called_once()

    def test_a_unified_body_the_sdk_cannot_parse_is_redone_as_a_unified_query(self):
        _auth()
        w = _unknown(**{"context.query_unified": ({"chunks": [], "llm_prompt": "# Query results"}, "req-1")})
        w.context.query.side_effect = HydraDBClientError(200, "{'chunks': [], 'llm_prompt': '# Query results'}")
        with _patch(w):
            result = runner.invoke(app, ["query", "x"])
        assert result.exit_code == 0, result.output
        w.context.query_unified.assert_called_once()

    def test_an_explicit_kind_refused_as_unified_is_explained_not_redone(self):
        _auth()
        w = _unknown()
        w.context.ingest.side_effect = _REFUSED_AS_UNIFIED
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "a note", "--kind", "knowledge"])
        assert result.exit_code != 0
        assert "--kind does not apply" in _plain(result.output)
        w.context.ingest_context.assert_not_called()

    def test_a_deprecated_write_alias_refused_as_unified_points_at_ingest(self):
        _auth()
        w = _unknown()
        w.context.ingest.side_effect = _REFUSED_AS_UNIFIED
        with _patch(w):
            result = runner.invoke(app, ["memories", "add", "--text", "a note"])
        assert result.exit_code != 0
        assert "hydradb ingest --text" in _plain(result.output)
        w.context.ingest_context.assert_not_called()

    def test_a_delete_refused_as_unified_is_redone_without_a_kind(self):
        _auth()
        w = _unknown()
        w.context.delete.side_effect = [_REFUSED_AS_UNIFIED, {"success": True, "deleted_count": 1}]
        with _patch(w):
            result = runner.invoke(app, ["delete", "a1", "--yes"])
        assert result.exit_code == 0, result.output
        kinds = [c.kwargs["kind"] for c in w.context.delete.call_args_list]
        assert kinds == ["knowledge", None]
        assert "Deleted 1 item(s)" in _plain(result.output)

    def test_unified_only_flags_need_a_known_layout(self):
        _auth()
        w = _unknown()
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "a note", "--context-id", "n1"])
        assert result.exit_code != 0
        assert "could not be checked just now" in _plain(result.output)
        w.context.ingest.assert_not_called()
        w.context.ingest_context.assert_not_called()

    def test_llm_with_an_unknown_layout_goes_out_unified(self):
        _auth()
        w = _unknown(**{"context.query_unified": ({"chunks": [], "llm_prompt": "# Query results"}, None)})
        with _patch(w):
            result = runner.invoke(app, ["query", "x", "--llm"])
        assert result.exit_code == 0, result.output
        w.context.query.assert_not_called()
        assert "# Query results" in result.stdout


class TestStrictItemContract:
    def test_user_name_is_sent_on_a_text_item(self):
        _auth()
        w = _mock("unified", **{"context.ingest_context": INGEST_202})
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "I prefer short answers", "--user-name", "Harsh"])
        assert result.exit_code == 0, result.output
        assert w.context.ingest_context.call_args.args[0][0]["user_name"] == "Harsh"

    def test_the_202_id_is_read_from_id(self):
        _auth()
        body = {"success": True, "success_count": 1, "failed_count": 0, "results": [{"id": "n1", "status": "queued"}]}
        w = _mock("unified", **{"context.ingest_context": body})
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "a note"])
        assert result.exit_code == 0, result.output
        assert "Context ID: n1 (queued)" in _plain(result.output)

    @pytest.mark.parametrize(
        ("extra", "message"),
        [
            (["--context-id", "a,b"], "no commas"),
            (["--context-id", "x" * 101], "at most 100 characters"),
            (["--title", "é" * 513], "at most 1024 bytes"),
            (["--instructions", "x" * 4001], "at most 4000 characters"),
        ],
    )
    def test_the_server_caps_are_checked_before_sending(self, extra, message):
        _auth()
        w = _mock("unified")
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "a note", *extra])
        assert result.exit_code != 0
        assert message in _plain(result.output)
        w.context.ingest_context.assert_not_called()

    def test_text_over_one_mebibyte_is_refused(self):
        _auth()
        w = _mock("unified")
        with _patch(w):
            result = runner.invoke(app, ["ingest", "--text", "x" * ((1 << 20) + 1)])
        assert result.exit_code != 0
        assert "Split it into several items" in _plain(result.output)
        w.context.ingest_context.assert_not_called()


class TestDatabaseCreateDefault:
    def test_create_without_type_says_the_server_picks(self):
        _auth()
        w = _mock("split", **{"databases.create": {"status": "accepted"}})
        with _patch(w):
            result = runner.invoke(app, ["database", "create", "new-db"])
        assert result.exit_code == 0, result.output
        assert w.databases.create.call_args.kwargs["layout"] is None
        assert "the server's default layout" in _plain(result.output)
