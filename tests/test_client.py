"""Tests for hydradb_cli.client module."""

import json
import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest

from hydradb_cli.client import HydraDBClient, HydraDBClientError


@pytest.fixture
def client():
    """Create a client with test credentials."""
    return HydraDBClient(api_key="test-key", base_url="https://test.api.com")


class TestClientInit:
    def test_default_base_url(self):
        with (
            patch("hydradb_cli.client.get_api_key", return_value="k"),
            patch("hydradb_cli.client.get_base_url", return_value="https://api.hydradb.com"),
        ):
            c = HydraDBClient()
            assert c.base_url == "https://api.hydradb.com"

    def test_custom_base_url(self):
        c = HydraDBClient(api_key="k", base_url="https://custom.com/")
        assert c.base_url == "https://custom.com"

    def test_headers_include_auth(self, client):
        headers = client._headers()
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["Content-Type"] == "application/json"


class TestHandleResponse:
    def test_success_returns_json(self, client):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"success": True}
        assert client._handle_response(resp) == {"success": True}

    def test_error_raises_exception(self, client):
        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"detail": "Unauthorized"}
        with pytest.raises(HydraDBClientError) as exc_info:
            client._handle_response(resp)
        assert exc_info.value.status_code == 401

    def test_error_with_text_fallback(self, client):
        resp = MagicMock()
        resp.status_code = 500
        resp.json.side_effect = Exception("not json")
        resp.text = "Internal Server Error"
        with pytest.raises(HydraDBClientError) as exc_info:
            client._handle_response(resp)
        assert "Internal Server Error" in exc_info.value.detail


class TestTenantMethods:
    def test_create_tenant_body(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"success": True}
            mock_post.return_value = mock_resp

            client.create_tenant("my-tenant")

            call_args = mock_post.call_args
            assert call_args[0][0] == "https://test.api.com/tenants/create"
            body = call_args[1]["json"]
            assert body["tenant_id"] == "my-tenant"

    def test_monitor_tenant(self, client):
        with patch.object(client._http, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "healthy"}
            mock_get.return_value = mock_resp

            result = client.monitor_tenant("t1")
            assert result == {"status": "healthy"}
            assert mock_get.call_args[1]["params"]["tenant_id"] == "t1"

    def test_delete_tenant(self, client):
        with patch.object(client._http, "delete") as mock_del:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"deleted": True}
            mock_del.return_value = mock_resp

            result = client.delete_tenant("t1")
            assert result["deleted"] is True


class TestMemoryMethods:
    def test_add_memory_body(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"success": True, "success_count": 1}
            mock_post.return_value = mock_resp

            client.add_memory(
                tenant_id="t1",
                text="User likes dark mode",
                infer=True,
                title="Preference",
            )

            body = mock_post.call_args[1]["json"]
            assert body["tenant_id"] == "t1"
            assert body["memories"][0]["text"] == "User likes dark mode"
            assert body["memories"][0]["infer"] is True
            assert body["memories"][0]["title"] == "Preference"

    def test_list_memories(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"user_memories": []}
            mock_post.return_value = mock_resp

            client.list_memories("t1")
            body = mock_post.call_args[1]["json"]
            assert body["kind"] == "memories"

    def test_delete_memory(self, client):
        with patch.object(client._http, "delete") as mock_del:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"user_memory_deleted": True}
            mock_del.return_value = mock_resp

            client.delete_memory("t1", "mem_123")
            params = mock_del.call_args[1]["params"]
            assert params["memory_id"] == "mem_123"


class TestRecallMethods:
    def test_full_recall_body(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"chunks": []}
            mock_post.return_value = mock_resp

            client.full_recall(
                tenant_id="t1",
                query="pricing info",
                max_results=5,
                mode="thinking",
                graph_context=True,
            )

            url = mock_post.call_args[0][0]
            assert "/recall/full_recall" in url
            body = mock_post.call_args[1]["json"]
            assert body["query"] == "pricing info"
            assert body["max_results"] == 5
            assert body["mode"] == "thinking"
            assert body["graph_context"] is True

    def test_recall_preferences_endpoint(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"chunks": []}
            mock_post.return_value = mock_resp

            client.recall_preferences(tenant_id="t1", query="user prefs")
            url = mock_post.call_args[0][0]
            assert "/recall/recall_preferences" in url

    def test_boolean_recall_body(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"chunks": []}
            mock_post.return_value = mock_resp

            client.boolean_recall(
                tenant_id="t1",
                query="John AND Smith",
                operator="and",
                search_mode="memories",
            )

            body = mock_post.call_args[1]["json"]
            assert body["operator"] == "and"
            assert body["search_mode"] == "memories"


class TestKnowledgeMethods:
    def test_upload_text(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"results": []}
            mock_post.return_value = mock_resp

            client.upload_text(
                tenant_id="t1",
                text="Meeting notes content",
                title="Meeting Notes",
                source_id="custom-id",
            )

            data = mock_post.call_args[1]["data"]
            assert data["tenant_id"] == "t1"
            assert data["upsert"] == "false"
            app_sources = json.loads(data["app_sources"])
            assert app_sources["id"] == "custom-id"
            assert app_sources["tenant_id"] == "t1"
            assert app_sources["sub_tenant_id"] == "t1"
            assert app_sources["content"] == {"text": "Meeting notes content"}
            assert app_sources["title"] == "Meeting Notes"

    def test_upload_text_explicit_upsert(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"results": []}
            mock_post.return_value = mock_resp

            client.upload_text(tenant_id="t1", text="updated", source_id="stable-id", upsert=True)

            assert mock_post.call_args[1]["data"]["upsert"] == "true"

    def test_upload_text_auto_generates_id(self, client):
        """When no source_id is given, a UUID is auto-generated."""
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"results": []}
            mock_post.return_value = mock_resp

            client.upload_text(tenant_id="t1", text="hello")

            data = mock_post.call_args[1]["data"]
            app_sources = json.loads(data["app_sources"])
            # id should be a valid UUID string
            uuid.UUID(app_sources["id"])  # raises ValueError if invalid
            assert app_sources["tenant_id"] == "t1"
            assert app_sources["sub_tenant_id"] == "t1"
            assert app_sources["content"] == {"text": "hello"}
            assert "title" not in app_sources

    def test_upload_text_with_sub_tenant(self, client):
        """sub_tenant_id is used when explicitly provided."""
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"results": []}
            mock_post.return_value = mock_resp

            client.upload_text(tenant_id="t1", text="hello", sub_tenant_id="sub1")

            data = mock_post.call_args[1]["data"]
            app_sources = json.loads(data["app_sources"])
            assert app_sources["sub_tenant_id"] == "sub1"
            assert data["sub_tenant_id"] == "sub1"

    def test_upload_text_empty_sub_tenant_consistent(self, client):
        """Empty-string sub_tenant_id: source model and form data agree (both use tenant_id fallback).

        Regression guard for the inconsistency where sub_tenant_id="" caused the source model
        to carry tenant_id (via `or` fallback) while form data carried "" (via `is not None`
        guard). After the fix both use the resolved stid value, so they are always in sync.
        """
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"results": []}
            mock_post.return_value = mock_resp

            client.upload_text(tenant_id="t1", text="hello", sub_tenant_id="")

            data = mock_post.call_args[1]["data"]
            app_sources = json.loads(data["app_sources"])
            # Source model resolves empty string to tenant_id via `or` fallback
            assert app_sources["sub_tenant_id"] == "t1"
            # Form data must NOT contain sub_tenant_id="" — that would disagree with the source model
            assert "sub_tenant_id" not in data

    def test_upload_knowledge_file_not_found(self, client):
        with pytest.raises(FileNotFoundError):
            client.upload_knowledge("t1", ["/nonexistent/file.pdf"])

    def test_upload_knowledge_explicitly_disables_upsert_by_default(self, client, tmp_path):
        source_file = tmp_path / "source.txt"
        source_file.write_text("original knowledge")

        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"results": []}
            mock_post.return_value = mock_resp

            client.upload_knowledge("t1", [str(source_file)])

            assert mock_post.call_args[1]["data"]["upsert"] == "false"

    def test_delete_knowledge(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"success": True}
            mock_post.return_value = mock_resp

            client.delete_knowledge("t1", ["doc1", "doc2"], sub_tenant_id="s1")
            body = mock_post.call_args[1]["json"]
            assert body["ids"] == ["doc1", "doc2"]
            assert body["sub_tenant_id"] == "s1"


class TestFetchMethods:
    def test_fetch_content(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"content": "hello"}
            mock_post.return_value = mock_resp

            client.fetch_content("t1", "src_1", mode="content")
            body = mock_post.call_args[1]["json"]
            assert body["source_id"] == "src_1"
            assert body["mode"] == "content"

    def test_list_data(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"sources": []}
            mock_post.return_value = mock_resp

            client.list_data("t1", kind="knowledge", page=2, page_size=10)
            body = mock_post.call_args[1]["json"]
            assert body["kind"] == "knowledge"
            assert body["page"] == 2
            assert body["page_size"] == 10

    def test_graph_relations(self, client):
        with patch.object(client._http, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"relations": []}
            mock_get.return_value = mock_resp

            client.graph_relations("t1", "src_1", is_memory=True, limit=5)
            params = mock_get.call_args[1]["params"]
            assert params["source_id"] == "src_1"
            assert params["is_memory"] == "true"
            assert params["limit"] == "5"


class TestNetworkErrors:
    """Test that network errors are converted to HydraDBClientError."""

    def test_connect_error_raises_client_error(self, client):
        with patch.object(client._http, "post", side_effect=httpx.ConnectError("Connection refused")):
            with pytest.raises(HydraDBClientError) as exc_info:
                client.create_tenant("t1")
            assert exc_info.value.status_code == 0
            assert "Could not connect" in exc_info.value.detail

    def test_timeout_error_raises_client_error(self, client):
        with patch.object(client._http, "get", side_effect=httpx.ReadTimeout("Read timed out")):
            with pytest.raises(HydraDBClientError) as exc_info:
                client.monitor_tenant("t1")
            assert exc_info.value.status_code == 0
            assert "timed out" in exc_info.value.detail

    def test_context_manager(self):
        with HydraDBClient(api_key="k", base_url="https://test.com") as client:
            assert client.api_key == "k"
        # After exiting, the http client should be closed
        assert client._http.is_closed


class TestOptionalParams:
    """Test that optional params are omitted when not provided."""

    def test_add_memory_minimal(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"success": True}
            mock_post.return_value = mock_resp

            client.add_memory(tenant_id="t1", text="hello")
            body = mock_post.call_args[1]["json"]
            assert "sub_tenant_id" not in body
            assert "title" not in body["memories"][0]
            assert "source_id" not in body["memories"][0]

    def test_full_recall_minimal(self, client):
        with patch.object(client._http, "post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"chunks": []}
            mock_post.return_value = mock_resp

            client.full_recall(tenant_id="t1", query="test")
            body = mock_post.call_args[1]["json"]
            assert "mode" not in body
            assert "alpha" not in body
            assert "graph_context" not in body
