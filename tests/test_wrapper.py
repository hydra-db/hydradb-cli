"""Unit tests for the hand-owned SDK wrapper (``hydradb_cli.hydra``).

The wrapper is exercised against the real SDK wired to an ``httpx.MockTransport``
so genuine request-building and response-parsing run, plus a few direct unit
tests for envelope unwrapping and error translation.
"""

import json
import types

import httpx
import pytest
from hydra_db import HydraDB as _SdkHydraDB
from hydra_db.core.api_error import ApiError
from hydra_db.errors.bad_request_error import BadRequestError
from hydra_db.errors.not_found_error import NotFoundError
from hydra_db.types.handler_error_response import HandlerErrorResponse

from hydradb_cli.hydra import HydraDB, HydraDBClientError
from hydradb_cli.hydra.client import _bool_str, _Connectors, _is_envelope, _unwrap


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


class TestACL:
    """PRO-1684: the caller declares the principals to answer as, and every
    read that the API scopes by ACL must carry them to the wire.

    The API treats an EMPTY acl exactly like an absent one (verified against
    staging: `acl: []` and no acl both returned 134 sources in a database where
    an unknown principal returned 130). [] is therefore not a way to ask for
    "nobody" — the design doc's rule is that absent and [] alike mean
    unrestricted, with __private__ as the marker that admits nobody.

    The wrapper still sends no key at all when the caller passed no --acl, so
    the request says what the caller said rather than leaning on that
    equivalence holding forever.
    """

    def test_query_sends_acl_principals(self):
        captured = {}
        w = _wrapper_with_response({"chunks": []}, captured=captured)
        w.context.query(query="q", acl=["alice@corp.com", "group:google:eng@corp.com"])
        body = json.loads(captured["request"].content)
        assert body["acl"] == ["alice@corp.com", "group:google:eng@corp.com"]

    def test_query_without_acl_omits_the_field_entirely(self):
        captured = {}
        w = _wrapper_with_response({"chunks": []}, captured=captured)
        w.context.query(query="q")
        body = json.loads(captured["request"].content)
        assert "acl" not in body, "an omitted acl must be absent from the request, not []"

    def test_list_sends_acl_principals(self):
        captured = {}
        w = _wrapper_with_response({"sources": []}, captured=captured)
        w.context.list(acl=["bob@corp.com"])
        body = json.loads(captured["request"].content)
        assert body["acl"] == ["bob@corp.com"]

    def test_inspect_sends_acl_principals(self):
        captured = {}
        w = _wrapper_with_response({}, captured=captured)
        w.context.inspect(id="s1", acl=["carol@corp.com"])
        req = captured["request"]
        assert "carol%40corp.com" in str(req.url) or "carol@corp.com" in str(req.url)

    def test_relations_sends_acl_principals(self):
        captured = {}
        w = _wrapper_with_response({"relations": []}, captured=captured)
        w.context.relations(id="s1", acl=["dan@corp.com"])
        req = captured["request"]
        assert "dan%40corp.com" in str(req.url) or "dan@corp.com" in str(req.url)


class TestRotateCredentialsSdkRename:
    """The rotate method's SDK name is generated from the endpoint summary and
    changed between 2.1.2 (`..._refresh_token`) and 2.1.4 (`..._internal_use_only`).
    The wrapper resolves whichever spelling the installed SDK ships, so the CLI
    is not pinned to one release."""

    @staticmethod
    def _wrapper_with_sdk_connectors(obj):
        from unittest.mock import MagicMock

        w = MagicMock()
        w._sdk = types.SimpleNamespace(connectors=obj)
        return _Connectors(w)

    def test_uses_the_new_spelling_when_present(self):
        seen = {}

        class NewOnly:
            def rotate_a_connectors_stored_o_auth_refresh_token_internal_use_only(self, cid, request=None):
                seen["called"] = ("new", cid, request)
                return {}

        self._wrapper_with_sdk_connectors(NewOnly()).rotate_credentials("c1", credentials={"access_token": "x"})
        assert seen["called"] == ("new", "c1", {"access_token": "x"})

    def test_falls_back_to_the_older_spelling(self):
        seen = {}

        class OldOnly:
            def rotate_a_connectors_stored_o_auth_refresh_token(self, cid, request=None):
                seen["called"] = ("old", cid, request)
                return {}

        self._wrapper_with_sdk_connectors(OldOnly()).rotate_credentials("c1", credentials={"access_token": "x"})
        assert seen["called"] == ("old", "c1", {"access_token": "x"})

    def test_unknown_spelling_raises_a_named_error(self):
        class Empty:
            pass

        try:
            self._wrapper_with_sdk_connectors(Empty()).rotate_credentials("c1", credentials={})
        except AttributeError as e:
            assert "no known credential-rotation method" in str(e)
        else:
            raise AssertionError("expected AttributeError naming the tried spellings")
