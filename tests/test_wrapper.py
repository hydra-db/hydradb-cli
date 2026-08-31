"""Unit tests for the hand-owned SDK wrapper (``hydradb_cli.hydra``).

The wrapper is exercised against the real SDK wired to an ``httpx.MockTransport``
so genuine request-building and response-parsing run, plus a few direct unit
tests for envelope unwrapping and error translation.
"""

import json

import httpx
import pytest
from hydra_db import HydraDB as _SdkHydraDB
from hydra_db.core.api_error import ApiError
from hydra_db.errors.bad_request_error import BadRequestError
from hydra_db.errors.not_found_error import NotFoundError
from hydra_db.types.handler_error_response import HandlerErrorResponse

from hydradb_cli.hydra import HydraDB, HydraDBClientError
from hydradb_cli.hydra.client import _bool_str, _is_envelope, _unwrap


def _multipart_fields(request: httpx.Request) -> dict:
    """Parse a multipart request body into {field name: text value}."""
    content_type = request.headers.get("content-type", "")
    boundary = content_type.split("boundary=", 1)[1].encode()
    fields = {}
    for part in request.content.split(b"--" + boundary):
        if b'name="' not in part or b"\r\n\r\n" not in part:
            continue
        name = part.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
        value = part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
        fields[name] = value.decode(errors="replace")
    return fields


class _FakeEnvelope:
    def __init__(self, data):
        self.data = data
        self.success = True
        self.meta = None


def _wrapper_with_response(response_json, status=200, captured=None):
    """Build a wrapper whose SDK returns ``response_json`` for any request."""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["request"] = request
        return httpx.Response(status, json=response_json)

    w = HydraDB(token="x", base_url="http://test.local", database="db_test", collection="col_test")
    w._sdk = _SdkHydraDB(
        token="x", base_url="http://test.local", httpx_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    return w


class TestUnwrap:
    def test_is_envelope_true(self):
        assert _is_envelope(_FakeEnvelope({"a": 1}))

    def test_is_envelope_false_for_plain_dict_model(self):
        class Bare:
            chunks = []

        assert not _is_envelope(Bare())

    def test_unwrap_returns_data(self):
        assert _unwrap(_FakeEnvelope({"chunks": [1]})) == {"chunks": [1]}

    def test_unwrap_null_data_is_empty_dict(self):
        assert _unwrap(_FakeEnvelope(None)) == {}

    def test_bool_str(self):
        assert _bool_str(True) == "true"
        assert _bool_str(False) == "false"
        assert _bool_str(None) is None


class TestQuery:
    def test_query_unwraps_and_scopes(self):
        captured = {}
        env = {"success": True, "data": {"chunks": [{"chunk_content": "hi", "relevancy_score": 0.9}]}, "meta": {}}
        w = _wrapper_with_response(env, captured=captured)
        result = w.context.query(query="q", kind="memory")
        assert result["chunks"][0]["chunk_content"] == "hi"
        body = json.loads(captured["request"].content)
        assert body["type"] == "memory"
        assert body["database"] == "db_test"
        assert body["collection"] == "col_test"


class TestIngest:
    def test_ingest_memory_encodes_memories(self):
        captured = {}
        w = _wrapper_with_response({"success": True, "data": {"success_count": 1}, "meta": {}}, captured=captured)
        w.context.ingest(kind="memory", text="dark mode", title="pref")
        request = captured["request"]
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b'name="memories"' in request.content
        assert b"dark mode" in request.content
        assert b'name="app_knowledge"' not in request.content

    def test_ingest_knowledge_text_uses_app_knowledge(self):
        captured = {}
        w = _wrapper_with_response({"success": True, "data": {}, "meta": {}}, captured=captured)
        w.context.ingest(kind="knowledge", text="report", title="Q3")
        request = captured["request"]
        assert request.headers["content-type"].startswith("multipart/form-data")
        # Text knowledge goes through app_knowledge (preserves a client id),
        # never the v1 app_sources field or a raw documents upload.
        assert b'name="app_knowledge"' in request.content
        assert b'name="type"' in request.content
        assert b"knowledge" in request.content
        assert b'name="app_sources"' not in request.content
        assert b'name="documents"' not in request.content

    def test_ingest_knowledge_text_preserves_source_id_for_delete(self):
        """`--source-id foo` must survive ingest (app_knowledge `id`) so a later
        delete-by-id addresses the same source."""
        cap_ingest = {}
        w = _wrapper_with_response({"success": True, "data": {}, "meta": {}}, captured=cap_ingest)
        w.context.ingest(kind="knowledge", text="report", source_id="foo")
        fields = _multipart_fields(cap_ingest["request"])
        item = json.loads(fields["app_knowledge"])[0]
        assert item["id"] == "foo"

        cap_delete = {}
        w2 = _wrapper_with_response({"success": True, "data": {"deleted_count": 1}, "meta": {}}, captured=cap_delete)
        w2.context.delete(ids=["foo"], kind="knowledge")
        assert json.loads(cap_delete["request"].content)["ids"] == ["foo"]

    def test_ingest_many_merges_results(self):
        # Each file gets one call; results and counts are merged.
        w = _wrapper_with_response(
            {"success": True, "data": {"success_count": 1, "failed_count": 0, "results": [{"id": "x"}]}, "meta": {}}
        )
        docs = [("a.txt", b"aaa", None), ("b.txt", b"bbb", None)]
        result = w.context.ingest_many(kind="knowledge", documents=docs)
        assert result["success_count"] == 2
        assert len(result["results"]) == 2


class TestListFlatten:
    def test_list_flattens_inner(self):
        env = {"success": True, "data": {"inner": {"sources": [{"id": "s1"}], "total": 1}}, "meta": {}}
        w = _wrapper_with_response(env)
        result = w.context.list(kind="knowledge")
        assert result["sources"][0]["id"] == "s1"
        assert result["total"] == 1
        assert "inner" not in result


class TestScope:
    def test_missing_database_raises(self):
        w = HydraDB(token="x", base_url="http://test.local")  # no default database
        with pytest.raises(HydraDBClientError) as exc:
            w.context.query(query="q")
        assert exc.value.status_code == 0
        assert "database" in exc.value.detail.lower()


class TestErrorTranslation:
    def test_bad_request_becomes_client_error(self):
        body = {"success": False, "error": {"code": "BAD", "message": "bad input"}}
        w = _wrapper_with_response(body, status=400)
        with pytest.raises(HydraDBClientError) as exc:
            w.databases.create(database="x")
        assert exc.value.status_code == 400
        assert "bad input" in exc.value.detail

    def test_not_found_becomes_client_error(self):
        w = _wrapper_with_response({"detail": "nope"}, status=404)
        with pytest.raises(HydraDBClientError) as exc:
            w.context.inspect(id="missing")
        assert exc.value.status_code == 404

    def test_network_error_is_status_zero(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        w = HydraDB(token="x", base_url="http://test.local", database="db_test")
        w._sdk = _SdkHydraDB(
            token="x", base_url="http://test.local", httpx_client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        with pytest.raises(HydraDBClientError) as exc:
            w.databases.list()
        assert exc.value.status_code == 0

    def test_translate_from_sdk_exception_types(self):
        from hydradb_cli.hydra import translate_sdk_error

        err = BadRequestError(body=HandlerErrorResponse(success=False))
        assert isinstance(translate_sdk_error(err), HydraDBClientError)
        assert translate_sdk_error(err).status_code == 400
        assert translate_sdk_error(NotFoundError(body={"x": 1})).status_code == 404
        assert translate_sdk_error(ApiError(status_code=503, body="down")).status_code == 503


class TestConnectors:
    """The connector surface, including the two things that make it different.

    Connector responses are mostly NOT enveloped, and the SDK's method names on
    this resource are the least stable in the whole API.
    """

    def test_list_handles_a_non_enveloped_response(self):
        """`GET /connectors` returns a bare {"connectors": [...]}.

        ``_unwrap`` checks for the envelope shape rather than assuming it
        (CONTRACT S2 rule 2). This test fails if that ever becomes an
        assumption, because unwrapping a non-envelope would silently return {}
        and the CLI would report "no connectors" for a populated account.
        """
        w = _wrapper_with_response({"connectors": [{"connector_id": "c1", "provider": "slack"}]})
        assert w.connectors.list() == [{"connector_id": "c1", "provider": "slack"}]

    def test_list_also_handles_an_enveloped_response(self):
        """If the endpoint ever starts enveloping, the wrapper must still cope."""
        w = _wrapper_with_response({"success": True, "meta": {}, "data": {"connectors": [{"connector_id": "c2"}]}})
        assert w.connectors.list() == [{"connector_id": "c2"}]

    def test_list_of_an_unexpected_shape_is_an_empty_list_not_a_crash(self):
        w = _wrapper_with_response({"unexpected": True})
        assert w.connectors.list() == []

    def test_get_passes_the_id_positionally(self):
        """Connector methods take path params positionally, unlike the rest.

        ``_invoke`` used to accept keyword arguments only, which made every one
        of these raise TypeError before a request was ever built.
        """
        captured = {}
        w = _wrapper_with_response({"connector_id": "c1"}, captured=captured)
        assert w.connectors.get("c1") == {"connector_id": "c1"}
        assert captured["request"].url.path.endswith("/connectors/c1")

    def test_delete_resource_passes_both_path_params(self):
        captured = {}
        w = _wrapper_with_response({}, captured=captured)
        w.connectors.remove_resource("c1", "r1")
        assert captured["request"].url.path.endswith("/connectors/c1/resources/r1")

    def test_rotate_credentials_hides_the_generated_sdk_name(self):
        """The SDK spells this `rotate_a_connectors_stored_o_auth_refresh_token`.

        That name is generated from OpenAPI summary text and is exactly the
        churn CONTRACT S2 exists to absorb, so it must appear nowhere outside
        the wrapper.
        """
        captured = {}
        w = _wrapper_with_response({}, captured=captured)
        w.connectors.rotate_credentials("c1", credentials={"access_token": "x"})
        assert captured["request"].url.path.endswith("/connectors/c1/credentials")

    def test_providers_reads_the_catalogue_over_raw_http(self):
        """The SDK has no method for /connectors/providers."""
        captured = {}
        w = _wrapper_with_response({"providers": [{"provider": "slack"}]}, captured=captured)
        # The raw path uses httpx directly, so point it at the same mock.
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"providers": [{"provider": "slack"}]})
        )
        with httpx.Client(transport=transport) as client:
            original = httpx.get

            def fake_get(url, **kwargs):
                kwargs.pop("timeout", None)
                return client.get(url, **kwargs)

            httpx.get = fake_get
            try:
                assert w.connectors.providers() == [{"provider": "slack"}]
            finally:
                httpx.get = original

    def test_provider_catalogue_error_becomes_a_client_error(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"message": "nope"}))
        w = HydraDB(token="x", base_url="http://test.local", database="db_test")
        with httpx.Client(transport=transport) as client:
            original = httpx.get

            def fake_get(url, **kwargs):
                kwargs.pop("timeout", None)
                return client.get(url, **kwargs)

            httpx.get = fake_get
            try:
                with pytest.raises(HydraDBClientError) as exc:
                    w.connectors.providers()
            finally:
                httpx.get = original
        assert exc.value.status_code == 404


class TestDeleteCollection:
    """The hand-rolled DELETE path, exercised directly rather than mocked.

    ``databases.delete_collection`` bypasses the SDK (the pinned version has no
    such method), so the command-level tests that patch the wrapper never run
    this code. These do.
    """

    def _wrapper(self):
        return HydraDB(token="tok", base_url="http://test.local", database="db_test")

    def test_empty_collection_raises_the_wrapper_error_not_a_type_error(self):
        # The command layer already rejects an empty collection, so this guard
        # only fires for direct callers. It still has to raise the wrapper's
        # own error: a TypeError here would escape handle_api_error and
        # traceback instead of printing a message.
        with pytest.raises(HydraDBClientError) as exc:
            self._wrapper().databases.delete_collection(collection="   ")
        assert exc.value.status_code == 0
        assert "collection" in exc.value.detail.lower()

    def test_sends_a_versioned_delete_and_unwraps_the_envelope(self, monkeypatch):
        seen = {}

        def fake_request(method, url, **kwargs):
            seen["method"] = method
            seen["url"] = url
            seen["headers"] = kwargs.get("headers", {})
            seen["params"] = kwargs.get("params")
            return httpx.Response(
                200,
                json={"success": True, "data": {"status": "deletion_scheduled"}},
                request=httpx.Request(method, url),
            )

        monkeypatch.setattr(httpx, "request", fake_request)
        out = self._wrapper().databases.delete_collection(database="acme", collection="support")

        assert seen["method"] == "DELETE"
        assert seen["url"].endswith("/databases/collections")
        # The fence and its 404 wording live behind API version 2.
        assert seen["headers"]["API-Version"] == "2"
        assert seen["headers"]["Authorization"] == "Bearer tok"
        # Canonical names on the wire. The server's TenantAliases middleware
        # maps database/collection onto tenant_id/sub_tenant_id, so sending the
        # canonical pair is correct and keeps the CLI off the deprecated names.
        assert seen["params"] == {"database": "acme", "collection": "support"}
        assert out == {"status": "deletion_scheduled"}

    def test_a_fenced_collection_surfaces_the_api_wording(self, monkeypatch):
        # A collection already being torn down answers 404. The point of
        # raising HydraDBClientError here is that the API's own message
        # reaches the user unchanged.
        def fake_request(method, url, **kwargs):
            return httpx.Response(
                404,
                json={"success": False, "error": {"message": "collection is being deleted"}},
                request=httpx.Request(method, url),
            )

        monkeypatch.setattr(httpx, "request", fake_request)
        with pytest.raises(HydraDBClientError) as exc:
            self._wrapper().databases.delete_collection(collection="support")
        assert exc.value.status_code == 404
        assert "being deleted" in exc.value.detail

    def test_a_transport_failure_is_status_zero(self, monkeypatch):
        def fake_request(method, url, **kwargs):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(httpx, "request", fake_request)
        with pytest.raises(HydraDBClientError) as exc:
            self._wrapper().databases.delete_collection(collection="support")
        assert exc.value.status_code == 0
