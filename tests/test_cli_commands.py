"""Integration tests for CLI commands against the mocked SDK wrapper.

Commands talk to the hand-owned :class:`hydradb_cli.hydra.HydraDB` wrapper via
``_impl.get_wrapper``; these tests patch that seam. Both the canonical commands
and the deprecated aliases are exercised, and every alias asserts its one-time
stderr deprecation warning.
"""

import json
import re
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import hydradb_cli.config
import hydradb_cli.output
from hydradb_cli.config import save_config
from hydradb_cli.main import app

runner = CliRunner()

# Every canonical + deprecated-alias HydraDB env var, cleared so the developer's
# own shell (which exports HYDRADB_TENANT_ID etc.) never leaks into tests.
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


@pytest.fixture(autouse=True)
def clean_config(tmp_path, monkeypatch):
    """Use temp config and a clean environment for all tests.

    Also resets the one-warning-per-process dedupe sets. Without this, two tests that
    exercise the *same* deprecated name in one pytest session would see the warning only
    in whichever ran first.
    """
    config_dir = tmp_path / ".hydradb"
    monkeypatch.setattr("hydradb_cli.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("hydradb_cli.config.CONFIG_FILE", config_dir / "config.json")
    for var in _HYDRA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    hydradb_cli.output._warned_deprecations.clear()
    hydradb_cli.config._warned_env_aliases.clear()
    yield


def _auth():
    """Configure credentials + default database so data commands have scope."""
    save_config(api_key="test-key-abcdef1234567890", tenant_id="t1")


def _wrapper(**returns):
    """Build a MagicMock wrapper; ``returns`` maps 'context.query' -> value etc."""
    w = MagicMock()
    for dotted, value in returns.items():
        resource, method = dotted.split(".")
        getattr(getattr(w, resource), method).return_value = value
    return w


def _patch_wrapper(w):
    return patch("hydradb_cli.commands._impl.get_wrapper", return_value=w)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _help_text(*argv: str) -> str:
    """`--help` output as flat, colourless text.

    Rich wraps to the terminal width and injects ANSI codes, so a bare substring check
    against ``result.output`` passes on a wide dev terminal and fails on CI's 80 columns.
    Force a wide, colourless render and strip what is left.
    """
    result = runner.invoke(
        app,
        [*argv, "--help"],
        env={"COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"},
    )
    assert result.exit_code == 0
    # Rich may still break a long line; collapse whitespace so flags stay contiguous.
    return re.sub(r"\s+", " ", _ANSI_RE.sub("", result.output))


# Render at a pinned width so panel/table layout assertions do not depend on the
# terminal the suite happens to run in.
_WIDE = {"COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"}


def _lines(result) -> list[str]:
    """Output split into colourless lines, for "this stayed on one line" assertions."""
    return [_ANSI_RE.sub("", line) for line in result.output.splitlines()]


def _kv_labels(result) -> list[str]:
    """The left-hand column of a rendered key/value table (which sits inside a panel)."""
    labels = []
    for line in _lines(result):
        cells = [cell.strip() for cell in line.split("│")]
        non_empty = [cell for cell in cells if cell]
        if len(non_empty) >= 2:
            labels.append(non_empty[0])
    return labels


class TestVersionHelp:
    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "hydradb-cli" in result.output

    def test_main_help_lists_canonical_and_aliases(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for name in ("query", "ingest", "list", "inspect", "delete", "relations", "subgraph", "database", "doctor"):
            assert name in result.output
        for alias in ("tenant", "memories", "recall", "knowledge", "fetch"):
            assert alias in result.output


class TestQuery:
    def test_query_human(self):
        _auth()
        w = _wrapper(**{"context.query": {"chunks": [{"chunk_content": "Pricing is $29", "relevancy_score": 0.9}]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["query", "pricing", "--kind", "knowledge"])
        assert result.exit_code == 0
        assert "1 result" in result.output
        assert "Pricing" in result.output
        assert w.context.query.call_args.kwargs["kind"] == "knowledge"

    def test_query_json_shape(self):
        _auth()
        w = _wrapper(**{"context.query": {"chunks": []}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["--output", "json", "query", "q"])
        assert result.exit_code == 0
        assert "chunks" in json.loads(result.output)

    def test_query_empty_fails(self):
        _auth()
        with _patch_wrapper(_wrapper()):
            result = runner.invoke(app, ["query", ""])
        assert result.exit_code != 0

    def test_query_invalid_alpha_fails(self):
        _auth()
        with _patch_wrapper(_wrapper()):
            result = runner.invoke(app, ["query", "x", "--alpha", "1.5"])
        assert result.exit_code != 0

    def test_query_invalid_mode_fails(self):
        _auth()
        with _patch_wrapper(_wrapper()):
            result = runner.invoke(app, ["query", "x", "--mode", "bogus"])
        assert result.exit_code != 0


class TestIngest:
    def test_ingest_memory(self):
        _auth()
        w = _wrapper(
            **{"context.ingest": {"success_count": 1, "failed_count": 0, "results": [{"id": "src_1", "status": "ok"}]}}
        )
        with _patch_wrapper(w):
            result = runner.invoke(app, ["ingest", "--text", "User prefers dark mode"])
        assert result.exit_code == 0
        assert "Memory added" in result.output
        # v2 returns `id`, not `source_id`: must not render "unknown".
        assert "src_1" in result.output
        assert "unknown" not in result.output
        assert w.context.ingest.call_args.kwargs["kind"] == "memory"

    def test_ingest_memory_json(self):
        _auth()
        w = _wrapper(**{"context.ingest": {"success_count": 1, "failed_count": 0}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["--output", "json", "ingest", "--text", "hi"])
        assert result.exit_code == 0
        assert json.loads(result.output)["success_count"] == 1

    def test_ingest_knowledge_text(self):
        _auth()
        w = _wrapper(**{"context.ingest": {"results": [{"id": "k1"}]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["ingest", "--kind", "knowledge", "--text", "notes"])
        assert result.exit_code == 0
        assert "uploaded" in result.output.lower()
        assert w.context.ingest.call_args.kwargs["kind"] == "knowledge"

    def test_ingest_files_loops(self, tmp_path):
        _auth()
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("aaa")
        f2.write_text("bbb")
        w = _wrapper(**{"context.ingest_many": {"success_count": 2, "failed_count": 0, "results": []}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["ingest", str(f1), str(f2)])
        assert result.exit_code == 0
        assert w.context.ingest_many.called
        assert len(w.context.ingest_many.call_args.kwargs["documents"]) == 2

    def test_ingest_empty_text_fails(self):
        _auth()
        with _patch_wrapper(_wrapper()):
            result = runner.invoke(app, ["ingest", "--text", "   "])
        assert result.exit_code != 0

    def test_ingest_files_with_kind_memory_fails(self, tmp_path):
        _auth()
        f = tmp_path / "a.txt"
        f.write_text("aaa")
        with _patch_wrapper(_wrapper()):
            result = runner.invoke(app, ["ingest", str(f), "--kind", "memory"])
        assert result.exit_code != 0

    def test_ingest_files_with_text_fails(self, tmp_path):
        _auth()
        f = tmp_path / "a.txt"
        f.write_text("aaa")
        with _patch_wrapper(_wrapper()):
            result = runner.invoke(app, ["ingest", str(f), "--text", "x"])
        assert result.exit_code != 0

    def test_ingest_files_with_markdown_fails(self, tmp_path):
        _auth()
        f = tmp_path / "a.txt"
        f.write_text("aaa")
        with _patch_wrapper(_wrapper()):
            result = runner.invoke(app, ["ingest", str(f), "--markdown"])
        assert result.exit_code != 0

    def test_ingest_files_with_no_infer_fails(self, tmp_path):
        _auth()
        f = tmp_path / "a.txt"
        f.write_text("aaa")
        with _patch_wrapper(_wrapper()):
            result = runner.invoke(app, ["ingest", str(f), "--no-infer"])
        assert result.exit_code != 0


class TestListInspectDeleteRelationsVerify:
    def test_list(self):
        _auth()
        w = _wrapper(**{"context.list": {"sources": [{"id": "s1", "title": "Report", "type": "pdf"}], "total": 1}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "Report" in result.output

    def test_inspect(self):
        _auth()
        w = _wrapper(**{"context.inspect": {"content": "Full text", "content_type": "text/plain"}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["inspect", "src_1"])
        assert result.exit_code == 0
        assert "Full text" in result.output

    def test_inspect_not_found(self):
        _auth()
        from hydradb_cli.hydra import HydraDBClientError

        w = MagicMock()
        w.context.inspect.side_effect = HydraDBClientError(404, "not found")
        with _patch_wrapper(w):
            result = runner.invoke(app, ["inspect", "bad"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()
        assert "collection" in result.output.lower()

    def test_delete(self):
        _auth()
        w = _wrapper(**{"context.delete": {"success": True, "deleted_count": 1}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["delete", "src_9", "--kind", "knowledge", "--yes"])
        assert result.exit_code == 0
        assert w.context.delete.call_args.kwargs["kind"] == "knowledge"

    def test_delete_no_match_fails(self):
        _auth()
        # v2 returns 200 with success:false when nothing matched — must not be
        # reported as success or exit 0.
        w = _wrapper(**{"context.delete": {"success": False, "deleted_count": 0}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["delete", "ghost", "--kind", "knowledge", "--yes"])
        assert result.exit_code != 0

    def test_delete_no_match_json_reports_failure(self):
        _auth()
        w = _wrapper(**{"context.delete": {"success": False, "deleted_count": 0}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["--output", "json", "delete", "ghost", "--kind", "knowledge", "--yes"])
        assert result.exit_code != 0
        assert json.loads(result.output)["success"] is False

    def test_relations(self):
        _auth()
        w = _wrapper(
            **{
                "context.relations": {
                    "relations": [
                        {
                            "source": {"name": "Acme"},
                            "target": {"name": "Bob"},
                            "relations": [{"canonical_predicate": "employs"}],
                        }
                    ]
                }
            }
        )
        with _patch_wrapper(w):
            result = runner.invoke(app, ["relations", "src_1"])
        assert result.exit_code == 0
        assert "employs" in result.output

    def test_relations_title_survives_a_long_source_id(self):
        """Same Table-title trap as `database collections`.

        The subject/predicate/object columns are narrow, so a Table title wrapped to the
        table's width breaks any ordinary source ID mid-token. On a panel it stays whole.
        """
        _auth()
        source_id = "cli-e2e-20260731-bridge-inspection-report"
        w = _wrapper(
            **{
                "context.relations": {
                    "relations": [
                        {
                            "source": {"name": "a"},
                            "target": {"name": "b"},
                            "relations": [{"canonical_predicate": "x"}],
                        }
                    ]
                }
            }
        )
        with _patch_wrapper(w):
            result = runner.invoke(app, ["relations", source_id], env=_WIDE)
        assert result.exit_code == 0
        assert any(f"/// Relations: {source_id}" in line for line in _lines(result))

    def test_subgraph(self):
        _auth()
        w = _wrapper(
            **{
                "context.subgraph": {
                    "seed_source_id": "src_1",
                    "sources": [
                        # discovered_via is the member it was reached FROM, not a mechanism.
                        {
                            "source_id": "reply_2",
                            "title": "re: budget",
                            "depth": 1,
                            "discovered_via": "src_1",
                            "discovered_relation": "same_thread",
                            "app_provider": "slack",
                        },
                        {"source_id": "src_1", "title": "Q3 budget", "depth": 0},
                    ],
                    "relations": [{}],
                    "auxiliary_relations": [{}, {}],
                    "is_truncated": False,
                    "auxiliary_truncated": False,
                    "max_depth_reached": 1,
                    "success": True,
                }
            }
        )
        with _patch_wrapper(w):
            result = runner.invoke(app, ["subgraph", "src_1", "--depth", "2", "--max-sources", "50"], env=_WIDE)
        assert result.exit_code == 0, result.output
        assert "2 items connected through 1 hop" in result.output
        assert "reply_2" in result.output and "re: budget" in result.output
        assert "same_thread ← src_1" in result.output
        assert "Reached by" in result.output
        assert any("/// Subgraph: src_1" in line for line in _lines(result))
        kw = w.context.subgraph.call_args.kwargs
        assert kw["id"] == "src_1" and kw["depth"] == 2 and kw["max_sources"] == 50

    def test_subgraph_json_is_the_payload(self):
        _auth()
        payload = {"seed_source_id": "src_1", "sources": [{"source_id": "src_1", "depth": 0}], "success": True}
        w = _wrapper(**{"context.subgraph": payload})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["--output", "json", "subgraph", "src_1"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == payload

    def test_subgraph_unknown_id_is_an_answer(self):
        _auth()
        w = _wrapper(**{"context.subgraph": {"seed_source_id": "nope", "sources": [], "success": True}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["subgraph", "nope"])
        assert result.exit_code == 0
        assert "no subgraph" in result.output.lower()

    def test_subgraph_reports_truncation(self):
        _auth()
        w = _wrapper(
            **{
                "context.subgraph": {
                    "sources": [{"source_id": "a", "depth": 0}, {"source_id": "b", "depth": 1}],
                    "is_truncated": True,
                    "max_depth_reached": 3,
                    "success": True,
                }
            }
        )
        with _patch_wrapper(w):
            result = runner.invoke(app, ["subgraph", "a"], env=_WIDE)
        assert result.exit_code == 0
        assert "clipped" in result.output

    def test_subgraph_validates_depth_before_the_network(self):
        _auth()
        w = _wrapper()
        with _patch_wrapper(w):
            result = runner.invoke(app, ["subgraph", "a", "--depth", "0"])
        assert result.exit_code != 0
        assert "depth" in result.output
        w.context.subgraph.assert_not_called()

    def test_verify(self):
        _auth()
        w = _wrapper(
            **{
                "context.ingestion_status": {
                    "statuses": [
                        {"id": "f1", "indexing_status": "completed"},
                        {"id": "f2", "indexing_status": "failed", "error_code": "FILE_NOT_FOUND"},
                    ]
                }
            }
        )
        with _patch_wrapper(w):
            result = runner.invoke(app, ["verify", "f1", "f2"])
        assert result.exit_code == 0
        assert "indexed" in result.output
        assert "not found" in result.output


class TestDatabase:
    def test_create(self):
        _auth()
        w = _wrapper(**{"databases.create": {"status": "accepted"}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["database", "create", "new-db"])
        assert result.exit_code == 0
        assert w.databases.create.call_args.kwargs["database"] == "new-db"

    def test_create_does_not_send_embeddings_fields(self):
        # is_embeddings_tenant provisions a raw-embeddings collection *instead of*
        # the knowledge and memory ones, leaving a database no other command can
        # use. Sending it at all is the bug, so assert the wire call stays clean.
        _auth()
        w = _wrapper(**{"databases.create": {"status": "accepted"}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["database", "create", "new-db"])
        assert result.exit_code == 0
        kwargs = w.databases.create.call_args.kwargs
        assert "is_embeddings_tenant" not in kwargs
        assert "embeddings_dimension" not in kwargs

    def test_create_rejects_removed_embeddings_flags(self):
        _auth()
        for flag in (["--embeddings"], ["--embeddings-dimension", "384"]):
            with _patch_wrapper(_wrapper()):
                result = runner.invoke(app, ["database", "create", "new-db", *flag])
            assert result.exit_code != 0, f"{flag} should no longer be accepted"

    def test_delete_requires_confirm(self):
        _auth()
        with _patch_wrapper(_wrapper()):
            result = runner.invoke(app, ["database", "delete", "db1"], input="n\n")
        assert result.exit_code != 0 or "Aborted" in result.output

    def test_list(self):
        _auth()
        w = _wrapper(**{"databases.list": {"databases": ["a", "b"]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["database", "list"])
        assert result.exit_code == 0
        assert "Database ID" in result.output
        assert "a" in result.output and "b" in result.output

    def test_collections(self):
        _auth()
        w = _wrapper(**{"databases.collections": {"collections": ["c1", "c2"]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["database", "collections", "t1"])
        assert result.exit_code == 0
        assert "c1" in result.output

    def test_collections_title_is_a_panel_title_like_its_siblings(self):
        """A Table title wraps to the *table's* width, mangling longer database names.

        The title belongs on the surrounding panel, the way `stats`/`readiness`/`monitor`
        render theirs, so it stays on one line whatever the database is called.
        """
        _auth()
        db = "cli-e2e-20260731"
        w = _wrapper(**{"databases.collections": {"collections": ["c1", "c2"]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["database", "collections", db], env=_WIDE)
        assert result.exit_code == 0
        assert any(f"/// Collections: {db}" in line for line in _lines(result))

    def test_readiness(self):
        _auth()
        w = _wrapper(**{"databases.readiness": {"infra": {"ready_for_ingestion": True}}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["database", "readiness", "t1"])
        assert result.exit_code == 0
        assert "ready" in result.output.lower()

    def test_monitor_merges_stats_and_readiness(self):
        _auth()
        w = MagicMock()
        w.databases.stats.return_value = {"knowledge_collection": {"row_count": 5}}
        w.databases.readiness.return_value = {"infra": {"ready_for_ingestion": True}}
        with _patch_wrapper(w):
            result = runner.invoke(app, ["database", "monitor", "t1"])
        assert result.exit_code == 0
        assert w.databases.stats.called
        assert w.databases.readiness.called


class TestDoctor:
    def test_doctor_no_config(self):
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Not configured" in result.output

    def test_doctor_reachable(self):
        _auth()
        w = _wrapper(**{"databases.readiness": {"infra": {"ready_for_ingestion": True}}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "yes" in result.output.lower()


class TestDeprecatedAliases:
    """Every legacy command still works and warns once, naming its replacement."""

    def test_recall_full(self):
        _auth()
        w = _wrapper(**{"context.query": {"chunks": []}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["recall", "full", "pricing"])
        assert result.exit_code == 0
        assert "deprecated" in result.stderr
        assert w.context.query.call_args.kwargs["kind"] == "knowledge"

    def test_recall_preferences(self):
        _auth()
        w = _wrapper(**{"context.query": {"chunks": []}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["recall", "preferences", "prefs"])
        assert result.exit_code == 0
        assert w.context.query.call_args.kwargs["kind"] == "memory"

    def test_recall_keyword(self):
        _auth()
        w = _wrapper(**{"context.query": {"chunks": []}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["recall", "keyword", "a AND b", "--operator", "and"])
        assert result.exit_code == 0
        assert w.context.query.call_args.kwargs["operator"] == "and"

    def test_tenant_create(self):
        _auth()
        w = _wrapper(**{"databases.create": {"status": "accepted"}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["tenant", "create", "new-t"])
        assert result.exit_code == 0
        assert "deprecated" in result.stderr
        assert w.databases.create.called

    def test_tenant_monitor(self):
        _auth()
        w = MagicMock()
        w.databases.stats.return_value = {"knowledge_collection": {"row_count": 1}}
        w.databases.readiness.return_value = {"infra": {"ready_for_ingestion": True}}
        with _patch_wrapper(w):
            result = runner.invoke(app, ["tenant", "monitor"])
        assert result.exit_code == 0

    def test_tenant_list_sub_tenants(self):
        _auth()
        w = _wrapper(**{"databases.collections": {"collections": ["c1"]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["tenant", "list-sub-tenants"])
        assert result.exit_code == 0
        assert w.databases.collections.called

    def test_tenant_delete_with_yes(self):
        _auth()
        w = _wrapper(**{"databases.delete": {"status": "deleted"}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["tenant", "delete", "t1", "--yes"])
        assert result.exit_code == 0

    def test_memories_add(self):
        _auth()
        w = _wrapper(**{"context.ingest": {"success_count": 1, "failed_count": 0, "results": [{"id": "m1"}]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["memories", "add", "--text", "dark mode"])
        assert result.exit_code == 0
        assert "deprecated" in result.stderr
        assert w.context.ingest.call_args.kwargs["kind"] == "memory"

    def test_memories_list(self):
        _auth()
        w = _wrapper(**{"context.list": {"sources": [{"id": "m1", "content": "likes dark mode"}]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["memories", "list"])
        assert result.exit_code == 0
        assert "m1" in result.output

    def test_memories_delete(self):
        _auth()
        w = _wrapper(**{"context.delete": {"success": True, "user_memory_deleted": 1}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["memories", "delete", "m1", "--yes"])
        assert result.exit_code == 0
        assert w.context.delete.call_args.kwargs["kind"] == "memory"

    def test_knowledge_upload_text(self):
        _auth()
        w = _wrapper(**{"context.ingest": {"results": [{"id": "k1"}]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["knowledge", "upload-text", "--text", "notes"])
        assert result.exit_code == 0
        assert "deprecated" in result.stderr
        assert w.context.ingest.call_args.kwargs["kind"] == "knowledge"

    def test_knowledge_verify(self):
        _auth()
        w = _wrapper(**{"context.ingestion_status": {"statuses": [{"id": "f1", "indexing_status": "completed"}]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["knowledge", "verify", "f1"])
        assert result.exit_code == 0
        assert "indexed" in result.output

    def test_knowledge_delete(self):
        _auth()
        w = _wrapper(**{"context.delete": {"success": True, "deleted_count": 2}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["knowledge", "delete", "d1", "d2", "--yes"])
        assert result.exit_code == 0
        assert w.context.delete.call_args.kwargs["kind"] == "knowledge"

    def test_fetch_content(self):
        _auth()
        w = _wrapper(**{"context.inspect": {"content": "text here"}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["fetch", "content", "src_1"])
        assert result.exit_code == 0
        assert "text here" in result.output

    def test_fetch_sources(self):
        _auth()
        w = _wrapper(**{"context.list": {"sources": [{"id": "s1", "title": "Report", "type": "pdf"}], "total": 1}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["fetch", "sources"])
        assert result.exit_code == 0
        assert "Report" in result.output

    def test_fetch_sources_legacy_kind_memories(self):
        _auth()
        # The deprecated `--kind memories` must still work (maps to canonical `memory`).
        w = _wrapper(**{"context.list": {"sources": [{"id": "m1", "content": "hi"}]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["fetch", "sources", "--kind", "memories"])
        assert result.exit_code == 0
        assert w.context.list.call_args.kwargs["kind"] == "memory"

    def test_fetch_relations(self):
        _auth()
        w = _wrapper(**{"context.relations": {"relations": []}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["fetch", "relations", "src_1"])
        assert result.exit_code == 0
        assert w.context.relations.called


class TestNoAuth:
    def test_query_no_scope_fails(self):
        result = runner.invoke(app, ["query", "test"])
        assert result.exit_code != 0

    def test_ingest_no_scope_fails(self):
        result = runner.invoke(app, ["ingest", "--text", "test"])
        assert result.exit_code != 0


class TestAuthAndConfig:
    def test_login_saves_config(self):
        w = _wrapper(**{"databases.readiness": {"infra": {"ready_for_ingestion": True}}})
        with patch("hydradb_cli.commands.auth.build_wrapper", return_value=w):
            result = runner.invoke(app, ["login", "--api-key", "test-key-abcdef1234567890", "--tenant-id", "t1"])
        assert result.exit_code == 0
        assert "Logged in" in result.output
        result2 = runner.invoke(app, ["whoami"])
        assert "test-key" in result2.output
        assert "t1" in result2.output

    def test_login_json(self):
        w = _wrapper(**{"databases.readiness": {}})
        with patch("hydradb_cli.commands.auth.build_wrapper", return_value=w):
            result = runner.invoke(app, ["--output", "json", "login", "--api-key", "k-abc", "--tenant-id", "t1"])
        assert result.exit_code == 0
        # `--tenant-id` warns on stderr; stdout must stay pure JSON.
        assert json.loads(result.stdout)["success"] is True

    def test_login_invalid_key_warns(self):
        from hydradb_cli.hydra import HydraDBClientError

        w = MagicMock()
        w.databases.readiness.side_effect = HydraDBClientError(403, "Forbidden")
        with patch("hydradb_cli.commands.auth.build_wrapper", return_value=w):
            result = runner.invoke(app, ["login", "--api-key", "bad-key", "--tenant-id", "t1"])
        assert result.exit_code == 0
        assert "rejected" in result.output

    def test_login_empty_key_fails(self):
        result = runner.invoke(app, ["login", "--api-key", ""])
        assert result.exit_code != 0

    def test_logout(self):
        _auth()
        result = runner.invoke(app, ["logout"])
        assert result.exit_code == 0
        assert "Logged out" in result.output

    def test_config_set_and_show(self):
        result = runner.invoke(app, ["config", "set", "tenant_id", "my-t"])
        assert result.exit_code == 0
        result2 = runner.invoke(app, ["config", "show"])
        assert "my-t" in result2.output

    def test_config_set_canonical_database_key(self):
        result = runner.invoke(app, ["config", "set", "database", "canon-db"])
        assert result.exit_code == 0
        result2 = runner.invoke(app, ["config", "show"])
        assert "canon-db" in result2.output

    def test_config_set_invalid_key(self):
        result = runner.invoke(app, ["config", "set", "bogus", "v"])
        assert result.exit_code != 0

    def test_config_set_tenant_id_switches_scope_after_database_was_set(self):
        """Switching scope with the deprecated key must actually switch it.

        `config set` reports success either way, so a write that does not take
        effect leaves later commands — including `delete` — pointed at the
        previous database.
        """
        assert runner.invoke(app, ["config", "set", "database", "prod"]).exit_code == 0
        assert runner.invoke(app, ["config", "set", "tenant_id", "staging"]).exit_code == 0
        assert hydradb_cli.config.get_database() == "staging"
        assert "staging" in runner.invoke(app, ["config", "show"]).output

    def test_config_set_sub_tenant_id_switches_scope_after_collection_was_set(self):
        assert runner.invoke(app, ["config", "set", "collection", "col-a"]).exit_code == 0
        assert runner.invoke(app, ["config", "set", "sub_tenant_id", "col-b"]).exit_code == 0
        assert hydradb_cli.config.get_collection() == "col-b"
        assert "col-b" in runner.invoke(app, ["config", "show"]).output

    def test_login_database_overrides_stale_deprecated_key(self):
        """A config carrying only the old `tenant_id` must be re-scoped by login."""
        save_config(api_key="k", tenant_id="prod")
        w = _wrapper(**{"databases.readiness": {"infra": {"ready_for_ingestion": True}}})
        with patch("hydradb_cli.commands.auth.build_wrapper", return_value=w):
            result = runner.invoke(app, ["login", "--api-key", "k", "--database", "staging"])
        assert result.exit_code == 0
        assert hydradb_cli.config.get_database() == "staging"

    def test_config_show_json(self):
        result = runner.invoke(app, ["--output", "json", "config", "show"])
        assert result.exit_code == 0
        assert "base_url" in json.loads(result.output)

    def test_config_show_labels_are_canonical(self):
        """`config show` labels the scope rows with the canonical vocabulary (CONTRACT §1).

        Users set `database`/`collection`; showing them back as `tenant_id`/`sub_tenant_id`
        gives no hint the two are the same thing.
        """
        assert runner.invoke(app, ["config", "set", "database", "canon-db"]).exit_code == 0
        assert runner.invoke(app, ["config", "set", "collection", "canon-coll"]).exit_code == 0
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        labels = _kv_labels(result)
        assert "database" in labels and "collection" in labels
        assert "tenant_id" not in labels and "sub_tenant_id" not in labels
        assert "canon-db" in result.output and "canon-coll" in result.output

    def test_config_show_reads_legacy_file_keys(self):
        """Config files still holding the old keys keep working, under the new labels."""
        save_config(tenant_id="legacy-db", sub_tenant_id="legacy-coll")
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        labels = _kv_labels(result)
        assert "database" in labels and "collection" in labels
        assert "legacy-db" in result.output and "legacy-coll" in result.output

    def test_config_show_json_keys_unchanged(self):
        """`--output json` is a documented jq contract — only the human labels moved."""
        assert runner.invoke(app, ["config", "set", "database", "canon-db"]).exit_code == 0
        result = runner.invoke(app, ["--output", "json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tenant_id"] == "canon-db"
        assert "sub_tenant_id" in data


class TestScopeFlags:
    """Canonical --database/--collection (CONTRACT §1); --tenant-id/--sub-tenant-id warn.

    The scope flags are declared identically on every canonical command and collapsed by
    one helper, so these cover the helper's behaviour once and its wiring per command.
    """

    # (argv, wrapper mock spec, resource, method) for every command taking scope flags.
    SCOPED = [
        (["query", "q"], {"context.query": {"chunks": []}}, "context", "query"),
        (["ingest", "--text", "t"], {"context.ingest": {"success_count": 1}}, "context", "ingest"),
        (["list"], {"context.list": {"sources": []}}, "context", "list"),
        (["inspect", "s1"], {"context.inspect": {"content": {}}}, "context", "inspect"),
        (["delete", "s1", "--yes"], {"context.delete": {"success": True}}, "context", "delete"),
        (["relations", "s1"], {"context.relations": {"relations": []}}, "context", "relations"),
        (["verify", "s1"], {"context.ingestion_status": {"statuses": []}}, "context", "ingestion_status"),
    ]

    @pytest.mark.parametrize("argv,spec,resource,method", SCOPED, ids=[a[0] for a, _, _, _ in SCOPED])
    def test_canonical_database_flag_reaches_wrapper(self, argv, spec, resource, method):
        _auth()
        w = _wrapper(**spec)
        with _patch_wrapper(w):
            result = runner.invoke(app, [*argv, "--database", "canon-db"])
        assert result.exit_code == 0, result.output
        assert getattr(getattr(w, resource), method).call_args.kwargs["database"] == "canon-db"
        assert "deprecated" not in result.stderr

    @pytest.mark.parametrize("argv,spec,resource,method", SCOPED, ids=[a[0] for a, _, _, _ in SCOPED])
    def test_legacy_tenant_id_flag_still_works_and_warns(self, argv, spec, resource, method):
        _auth()
        w = _wrapper(**spec)
        with _patch_wrapper(w):
            result = runner.invoke(app, [*argv, "--tenant-id", "legacy-db"])
        assert result.exit_code == 0, result.output
        assert getattr(getattr(w, resource), method).call_args.kwargs["database"] == "legacy-db"
        assert "'--tenant-id' is deprecated; use '--database'" in result.stderr

    def test_canonical_collection_flag(self):
        _auth()
        w = _wrapper(**{"context.query": {"chunks": []}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["query", "q", "--database", "d", "--collection", "c1"])
        assert result.exit_code == 0
        assert w.context.query.call_args.kwargs["collection"] == "c1"
        assert "deprecated" not in result.stderr

    def test_legacy_sub_tenant_id_warns(self):
        _auth()
        w = _wrapper(**{"context.query": {"chunks": []}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["query", "q", "--sub-tenant-id", "c1"])
        assert result.exit_code == 0
        assert w.context.query.call_args.kwargs["collection"] == "c1"
        assert "'--sub-tenant-id' is deprecated; use '--collection'" in result.stderr

    def test_canonical_wins_when_both_given_without_warning(self):
        _auth()
        w = _wrapper(**{"context.query": {"chunks": []}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["query", "q", "--database", "canon", "--tenant-id", "legacy"])
        assert result.exit_code == 0
        assert w.context.query.call_args.kwargs["database"] == "canon"
        # No warning: the caller already uses the canonical spelling.
        assert "deprecated" not in result.stderr

    def test_deprecation_warning_keeps_json_stdout_parseable(self):
        """The warning must go to stderr only, or it corrupts the documented jq contract."""
        _auth()
        w = _wrapper(**{"context.query": {"chunks": [{"chunk_content": "hit"}]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["--output", "json", "query", "q", "--tenant-id", "legacy-db"])
        assert result.exit_code == 0
        # `.output` merges both streams; `.stdout` is what a `jq` pipeline actually reads.
        assert json.loads(result.stdout)["chunks"][0]["chunk_content"] == "hit"
        assert "deprecated" in result.stderr

    def test_short_d_flag(self):
        _auth()
        w = _wrapper(**{"context.list": {"sources": []}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["list", "-d", "short-db"])
        assert result.exit_code == 0
        assert w.context.list.call_args.kwargs["database"] == "short-db"

    def test_legacy_flags_hidden_from_help(self):
        help_text = _help_text("query")
        assert "--database" in help_text
        assert "--collection" in help_text
        assert "--tenant-id" not in help_text
        assert "--sub-tenant-id" not in help_text

    def test_database_group_canonical_flag_warns_on_legacy(self):
        _auth()
        w = _wrapper(**{"databases.collections": {"collections": ["c1"]}})
        with _patch_wrapper(w):
            result = runner.invoke(app, ["database", "collections", "--tenant-id", "legacy-db"])
        assert result.exit_code == 0
        assert "'--tenant-id' is deprecated; use '--database'" in result.stderr

    def test_login_canonical_database_flag(self):
        w = _wrapper(**{"databases.readiness": {"infra": {"ready_for_ingestion": True}}})
        with patch("hydradb_cli.commands.auth.build_wrapper", return_value=w):
            result = runner.invoke(app, ["login", "--api-key", "k-abcdef1234567890", "--database", "canon-db"])
        assert result.exit_code == 0
        assert "deprecated" not in result.stderr
        # Saved under the canonical config key and readable back.
        assert "canon-db" in runner.invoke(app, ["doctor"]).output


class TestOutputFormat:
    def test_invalid_output_format(self):
        result = runner.invoke(app, ["--output", "xml", "doctor"])
        assert result.exit_code != 0

    def test_whoami_json_is_valid(self):
        result = runner.invoke(app, ["--output", "json", "whoami"])
        assert result.exit_code == 0
        # `whoami` itself is deprecated and warns on stderr; stdout must stay pure JSON.
        assert isinstance(json.loads(result.stdout), dict)
        assert "deprecated" in result.stderr
