"""Integration tests for CLI commands using typer.testing.CliRunner."""

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from hydradb_cli.main import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_config(tmp_path, monkeypatch):
    """Use temp config for all tests."""
    config_dir = tmp_path / ".hydradb"
    config_file = config_dir / "config.json"
    monkeypatch.setattr("hydradb_cli.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("hydradb_cli.config.CONFIG_FILE", config_file)
    for var in ("HYDRA_DB_API_KEY", "HYDRA_DB_TENANT_ID", "HYDRA_DB_SUB_TENANT_ID", "HYDRA_DB_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def _login(api_key="k", tenant_id="t1"):
    """Login helper that skips real API validation."""
    with patch("hydradb_cli.commands.auth.HydraDBClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.monitor_tenant.return_value = {"status": "ok"}
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_cls.return_value = mock_client
        return runner.invoke(app, ["login", "--api-key", api_key, "--tenant-id", tenant_id])


class TestVersion:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "hydradb-cli" in result.output

    def test_short_version_flag(self):
        result = runner.invoke(app, ["-v"])
        assert result.exit_code == 0


class TestHelp:
    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "tenant" in result.output
        assert "memories" in result.output
        assert "recall" in result.output
        assert "knowledge" in result.output
        assert "fetch" in result.output

    def test_tenant_help(self):
        result = runner.invoke(app, ["tenant", "--help"])
        assert result.exit_code == 0
        assert "create" in result.output
        assert "monitor" in result.output
        assert "delete" in result.output

    def test_recall_help(self):
        result = runner.invoke(app, ["recall", "--help"])
        assert result.exit_code == 0
        assert "full" in result.output
        assert "preferences" in result.output
        assert "keyword" in result.output


class TestLogin:
    def test_login_saves_config(self, clean_config):
        result = _login(api_key="test-key-abcdef1234567890", tenant_id="t1")
        assert result.exit_code == 0
        assert "Logged in" in result.output

        result2 = runner.invoke(app, ["whoami"])
        assert "test-key" in result2.output
        assert "t1" in result2.output

    def test_login_json_output(self, clean_config):
        with patch("hydradb_cli.commands.auth.HydraDBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.monitor_tenant.return_value = {"status": "ok"}
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = mock_client
            result = runner.invoke(app, ["--output", "json", "login", "--api-key", "test-key-abc", "--tenant-id", "t1"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True

    def test_login_empty_key_fails(self, clean_config):
        result = runner.invoke(app, ["login", "--api-key", ""])
        assert result.exit_code != 0

    def test_login_invalid_key_warns(self, clean_config):
        with patch("hydradb_cli.commands.auth.HydraDBClient") as mock_cls:
            from hydradb_cli.client import HydraDBClientError

            mock_client = MagicMock()
            mock_client.monitor_tenant.side_effect = HydraDBClientError(403, "Forbidden")
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = mock_client
            result = runner.invoke(app, ["login", "--api-key", "bad-key", "--tenant-id", "t1"])
        assert result.exit_code == 0
        assert "rejected" in result.output


class TestLogout:
    def test_logout(self, clean_config):
        _login()
        result = runner.invoke(app, ["logout"])
        assert result.exit_code == 0
        assert "Logged out" in result.output

    def test_logout_json(self, clean_config):
        result = runner.invoke(app, ["--output", "json", "logout"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True


class TestWhoami:
    def test_whoami_no_config(self, clean_config):
        result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 0
        assert "Not configured" in result.output

    def test_whoami_with_config(self, clean_config):
        _login(api_key="mykey12345678", tenant_id="t1")
        result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 0
        assert "mykey123" in result.output
        assert "t1" in result.output

    def test_whoami_json(self, clean_config):
        result = runner.invoke(app, ["--output", "json", "whoami"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "api_key" in data
        assert "base_url" in data


class TestConfigCommands:
    def test_config_show(self, clean_config):
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "base_url" in result.output

    def test_config_set(self, clean_config):
        result = runner.invoke(app, ["config", "set", "tenant_id", "my-t"])
        assert result.exit_code == 0
        assert "Set" in result.output

        result2 = runner.invoke(app, ["config", "show"])
        assert "my-t" in result2.output

    def test_config_set_invalid_key(self, clean_config):
        result = runner.invoke(app, ["config", "set", "invalid_key", "val"])
        assert result.exit_code != 0

    def test_config_show_json(self, clean_config):
        result = runner.invoke(app, ["--output", "json", "config", "show"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "base_url" in data

    def test_config_set_empty_api_key_fails(self, clean_config):
        result = runner.invoke(app, ["config", "set", "api_key", ""])
        assert result.exit_code != 0

    def test_config_set_empty_base_url_fails(self, clean_config):
        result = runner.invoke(app, ["config", "set", "base_url", "   "])
        assert result.exit_code != 0


class TestTenantCommands:
    def _setup_auth(self, clean_config):
        _login()

    @patch("hydradb_cli.commands.tenant.get_client")
    def test_tenant_create(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.create_tenant.return_value = {"success": True, "tenant_id": "new-t"}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["tenant", "create", "new-t"])
        assert result.exit_code == 0
        mock_client.create_tenant.assert_called_once()

    @patch("hydradb_cli.commands.tenant.get_client")
    def test_tenant_monitor(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.monitor_tenant.return_value = {"status": "healthy", "sources": 10}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["tenant", "monitor"])
        assert result.exit_code == 0

    @patch("hydradb_cli.commands.tenant.get_client")
    def test_tenant_monitor_with_option(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.monitor_tenant.return_value = {"status": "healthy"}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["tenant", "monitor", "--tenant-id", "other-t"])
        assert result.exit_code == 0
        mock_client.monitor_tenant.assert_called_once_with("other-t")

    @patch("hydradb_cli.commands.tenant.get_client")
    def test_tenant_delete_requires_confirm(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["tenant", "delete", "t1"], input="n\n")
        assert result.exit_code != 0 or "Aborted" in result.output

    @patch("hydradb_cli.commands.tenant.get_client")
    def test_tenant_delete_with_yes(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.delete_tenant.return_value = {"deleted": True}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["tenant", "delete", "t1", "--yes"])
        assert result.exit_code == 0


class TestMemoriesCommands:
    def _setup_auth(self, clean_config):
        _login()

    @patch("hydradb_cli.commands.memories.get_client")
    def test_memories_add(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.add_memory.return_value = {
            "success": True,
            "success_count": 1,
            "failed_count": 0,
            "results": [{"source_id": "src_1", "status": "ok"}],
        }
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["memories", "add", "--text", "User prefers dark mode"])
        assert result.exit_code == 0
        assert "Memory added" in result.output

    @patch("hydradb_cli.commands.memories.get_client")
    def test_memories_add_json(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.add_memory.return_value = {"success_count": 1, "failed_count": 0}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["--output", "json", "memories", "add", "--text", "test"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success_count"] == 1

    @patch("hydradb_cli.commands.memories.get_client")
    def test_memories_add_empty_text_fails(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        result = runner.invoke(app, ["memories", "add", "--text", "   "])
        assert result.exit_code != 0
        assert "empty" in result.output.lower() or "whitespace" in result.output.lower()

    @patch("hydradb_cli.commands.memories.get_client")
    def test_memories_add_no_text_fails(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        result = runner.invoke(app, ["memories", "add"])
        assert result.exit_code != 0

    @patch("hydradb_cli.commands.memories.get_client")
    def test_memories_list(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.list_memories.return_value = {
            "user_memories": [
                {"memory_id": "m1", "memory_content": "Likes dark mode"},
                {"memory_id": "m2", "memory_content": "Prefers email"},
            ]
        }
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["memories", "list"])
        assert result.exit_code == 0
        assert "2 memories" in result.output
        assert "m1" in result.output

    @patch("hydradb_cli.commands.memories.get_client")
    def test_memories_delete(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.delete_memory.return_value = {"success": True, "user_memory_deleted": True}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["memories", "delete", "m1", "--yes"])
        assert result.exit_code == 0
        assert "deleted" in result.output


class TestRecallCommands:
    def _setup_auth(self, clean_config):
        _login()

    @patch("hydradb_cli.commands.recall.get_client")
    def test_recall_full(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.full_recall.return_value = {
            "chunks": [
                {
                    "chunk_content": "Pricing is $29/month for starter",
                    "relevancy_score": 0.92,
                    "source_title": "Pricing Doc",
                }
            ]
        }
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["recall", "full", "pricing info"])
        assert result.exit_code == 0
        assert "1 result" in result.output
        assert "Pricing" in result.output

    @patch("hydradb_cli.commands.recall.get_client")
    def test_recall_full_json(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.full_recall.return_value = {"chunks": []}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["--output", "json", "recall", "full", "test query"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "chunks" in data

    @patch("hydradb_cli.commands.recall.get_client")
    def test_recall_preferences(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.recall_preferences.return_value = {"chunks": []}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["recall", "preferences", "user prefs"])
        assert result.exit_code == 0

    @patch("hydradb_cli.commands.recall.get_client")
    def test_recall_keyword(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.boolean_recall.return_value = {"chunks": []}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["recall", "keyword", "John Smith", "--operator", "phrase"])
        assert result.exit_code == 0

    @patch("hydradb_cli.commands.recall.get_client")
    def test_recall_empty_query_fails(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        result = runner.invoke(app, ["recall", "full", ""])
        assert result.exit_code != 0
        assert "empty" in result.output.lower()

    @patch("hydradb_cli.commands.recall.get_client")
    def test_recall_invalid_alpha_fails(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        result = runner.invoke(app, ["recall", "full", "test", "--alpha", "1.5"])
        assert result.exit_code != 0

    @patch("hydradb_cli.commands.recall.get_client")
    def test_recall_invalid_mode_fails(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        result = runner.invoke(app, ["recall", "full", "test", "--mode", "invalid"])
        assert result.exit_code != 0

    @patch("hydradb_cli.commands.recall.get_client")
    def test_recall_invalid_max_results_fails(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        result = runner.invoke(app, ["recall", "full", "test", "--max-results", "0"])
        assert result.exit_code != 0


class TestKnowledgeCommands:
    def _setup_auth(self, clean_config):
        _login()

    @patch("hydradb_cli.commands.knowledge.get_client")
    def test_knowledge_upload_text(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.upload_text.return_value = {"results": [{"source_id": "src_1", "id": "src_1"}]}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["knowledge", "upload-text", "--text", "Meeting notes content"])
        assert result.exit_code == 0
        assert "uploaded" in result.output.lower()

    @patch("hydradb_cli.commands.knowledge.get_client")
    def test_knowledge_upload_text_sub_tenant_wiring(self, mock_get_client, clean_config):
        """--sub-tenant-id and --source-id flags are forwarded correctly to client.upload_text."""
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.upload_text.return_value = {"results": [{"source_id": "my-sid", "id": "my-sid"}]}
        mock_get_client.return_value = mock_client

        result = runner.invoke(
            app,
            [
                "knowledge",
                "upload-text",
                "--text",
                "Q4 pricing: Starter $29, Pro $79",
                "--sub-tenant-id",
                "sub-acme",
                "--source-id",
                "my-sid",
                "--title",
                "Pricing Notes",
                "--upsert",
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_client.upload_text.call_args[1]
        assert call_kwargs["sub_tenant_id"] == "sub-acme"
        assert call_kwargs["source_id"] == "my-sid"
        assert call_kwargs["title"] == "Pricing Notes"
        assert call_kwargs["text"] == "Q4 pricing: Starter $29, Pro $79"
        assert call_kwargs["upsert"] is True

    @patch("hydradb_cli.commands.knowledge.get_client")
    def test_knowledge_upload_text_empty_fails(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        result = runner.invoke(app, ["knowledge", "upload-text", "--text", ""])
        assert result.exit_code != 0

    @patch("hydradb_cli.commands.knowledge.get_client")
    def test_knowledge_upload_text_failed_response_exits_nonzero(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.upload_text.return_value = {
            "success": False,
            "success_count": 0,
            "failed_count": 1,
            "results": [
                {
                    "source_id": "stable-id",
                    "status": "failed",
                    "error": "Source already exists and upsert is false",
                }
            ],
        }
        mock_get_client.return_value = mock_client

        result = runner.invoke(
            app,
            ["--output", "json", "knowledge", "upload-text", "--text", "replacement", "--source-id", "stable-id"],
        )

        assert result.exit_code == 1
        assert json.loads(result.stdout)["failed_count"] == 1
        assert "Source already exists and upsert is false" in result.stderr

    @patch("hydradb_cli.commands.knowledge.get_client")
    def test_knowledge_file_upload_failed_item_exits_nonzero(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.upload_knowledge.return_value = {
            "success": True,
            "results": [{"source_id": "report-id", "status": "failed", "error": "Unsupported file format"}],
        }
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["--output", "json", "knowledge", "upload", "report.bin"])

        assert result.exit_code == 1
        assert json.loads(result.stdout)["results"][0]["status"] == "failed"
        assert "Unsupported file format" in result.stderr

    @patch("hydradb_cli.commands.knowledge.get_client")
    def test_knowledge_delete(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.delete_knowledge.return_value = {"success": True}
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["knowledge", "delete", "doc1", "doc2", "--yes"])
        assert result.exit_code == 0

    @patch("hydradb_cli.commands.knowledge.get_client")
    def test_knowledge_verify(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.verify_processing.return_value = {
            "statuses": [
                {"file_id": "f1", "indexing_status": "indexed"},
                {"file_id": "f2", "indexing_status": "errored", "error_code": "FILE_NOT_FOUND"},
            ]
        }
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["knowledge", "verify", "f1", "f2"])
        assert result.exit_code == 0
        assert "indexed" in result.output
        assert "not found" in result.output


class TestFetchCommands:
    def _setup_auth(self, clean_config):
        _login()

    @patch("hydradb_cli.commands.fetch.get_client")
    def test_fetch_sources(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.list_data.return_value = {
            "sources": [
                {"id": "s1", "title": "Report", "type": "pdf"},
                {"id": "s2", "title": "Notes", "type": "text"},
            ],
            "total": 2,
        }
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["fetch", "sources"])
        assert result.exit_code == 0
        assert "2 source" in result.output
        assert "Report" in result.output

    @patch("hydradb_cli.commands.fetch.get_client")
    def test_fetch_content(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        mock_client = MagicMock()
        mock_client.fetch_content.return_value = {
            "success": True,
            "content": "Full document text here",
            "content_type": "text/plain",
        }
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["fetch", "content", "src_1"])
        assert result.exit_code == 0
        assert "Full document text" in result.output

    @patch("hydradb_cli.commands.fetch.get_client")
    def test_fetch_content_not_found(self, mock_get_client, clean_config):
        self._setup_auth(clean_config)
        from hydradb_cli.client import HydraDBClientError

        mock_client = MagicMock()
        mock_client.fetch_content.side_effect = HydraDBClientError(404, "File not found")
        mock_get_client.return_value = mock_client

        result = runner.invoke(app, ["fetch", "content", "bad-id"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()
        assert "sub-tenant" in result.output.lower()

    def test_fetch_sources_invalid_kind_fails(self, clean_config):
        _login()
        result = runner.invoke(app, ["fetch", "sources", "--kind", "invalid"])
        assert result.exit_code != 0

    def test_fetch_sources_invalid_page_fails(self, clean_config):
        _login()
        result = runner.invoke(app, ["fetch", "sources", "--page", "0"])
        assert result.exit_code != 0

    def test_fetch_content_invalid_mode_fails(self, clean_config):
        _login()
        result = runner.invoke(app, ["fetch", "content", "src_1", "--mode", "invalid"])
        assert result.exit_code != 0

    def test_fetch_content_empty_id_fails(self, clean_config):
        _login()
        result = runner.invoke(app, ["fetch", "content", ""])
        assert result.exit_code != 0


class TestOutputFormat:
    def test_invalid_output_format(self):
        result = runner.invoke(app, ["--output", "xml", "whoami"])
        assert result.exit_code != 0

    def test_json_output_is_valid_json(self, clean_config):
        result = runner.invoke(app, ["--output", "json", "whoami"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)


class TestNoAuthErrors:
    def test_memories_add_no_auth(self, clean_config):
        result = runner.invoke(app, ["memories", "add", "--text", "test"])
        assert result.exit_code != 0
        assert "API key" in result.output or "api key" in result.output.lower() or "Error" in result.output

    def test_recall_no_auth(self, clean_config):
        result = runner.invoke(app, ["recall", "full", "test"])
        assert result.exit_code != 0
