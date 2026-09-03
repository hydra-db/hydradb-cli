"""Thin, hand-owned wrapper over ``hydradb-sdk`` (import package ``hydra_db``).

This is the firewall described in ``CONTRACT.md``: the SDK's method names are
generated from OpenAPI summary text and can be renamed by a patch release, so
every client wraps the SDK behind a hand-owned layer that exposes the *canonical*
vocabulary (§1) and pins the SDK **exactly** (never a range).

Responsibilities (CONTRACT §2):
  * own the ``hydradb-sdk`` dependency and construct ``HydraDB(token, base_url)``
  * unwrap ``HandlerEnvelope{data, success, meta}`` to plain dicts — but by
    *checking* for the envelope shape, never assuming it
  * translate SDK exceptions back into :class:`HydraDBClientError`
  * apply default database/collection scope from config
  * send ``API-Version: 2`` (the SDK does this by default)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
from hydra_db import HydraDB as _SdkHydraDB
from hydra_db.core.api_error import ApiError
from hydra_db.core.file import File
from hydra_db.core.parse_error import ParsingError

from hydradb_cli.hydra.errors import HydraDBClientError, _stringify_body, translate_sdk_error

# Restated rather than imported from ``config`` to keep this module free of a
# dependency on CLI configuration — the wrapper is meant to be portable.
DEFAULT_BASE_URL = "https://api.hydradb.com"
DEFAULT_GRAPH_COLLECTION = "default"


def _is_envelope(obj: Any) -> bool:
    """Whether ``obj`` looks like a ``HandlerEnvelope{data, success, meta}``.

    We check for the shape rather than assuming it: some SDK methods return
    bare payloads (e.g. ``databases.update_metadata_schema``) that must not be
    unwrapped.
    """
    return hasattr(obj, "data") and hasattr(obj, "success") and hasattr(obj, "meta")


def _dump(obj: Any) -> Any:
    """Turn a pydantic model into a plain JSON-able dict; pass through the rest."""
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return obj


def _unwrap(obj: Any) -> Any:
    """Return the plain-dict payload for an SDK response.

    Enveloped responses collapse to their ``.data`` (an empty dict when data is
    null); non-enveloped responses are dumped as-is.
    """
    if _is_envelope(obj):
        data = _dump(obj.data)
        return data if data is not None else {}
    dumped = _dump(obj)
    return dumped if dumped is not None else {}


def _unwrap_payload(body: Any) -> Any:
    """Unwrap a raw JSON ``HandlerEnvelope`` returned by the non-SDK path.

    The SDK hands back pydantic models, so :func:`_unwrap` inspects attributes.
    The BYOG endpoints are called directly and return plain parsed JSON, so the
    same envelope has to be recognised as a dict instead.

    Unwrapping is by SHAPE, never by assumption (CONTRACT §2 rule 2): a value is
    an envelope only when it carries ``data`` alongside one of its siblings.
    ``data`` may legitimately be a list (every ``/byog/query`` result is one),
    so an empty result must not be confused with a missing payload.
    """
    if isinstance(body, dict) and "data" in body and ({"success", "meta", "error"} & body.keys()):
        data = body["data"]
        return data if data is not None else {}
    return body if body is not None else {}


def _bool_str(value: bool | None) -> str | None:
    """The SDK's multipart ``upsert`` field is a string; map bools to it."""
    if value is None:
        return None
    return "true" if value else "false"


class _Resource:
    """Base for the ``databases``/``context`` sub-resources."""

    def __init__(self, wrapper: HydraDB):
        self._w = wrapper

    def _invoke(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Call an SDK method, dropping ``None`` kwargs so the SDK's own OMIT
        defaults apply, and translating any SDK/transport error.

        Positional arguments pass through untouched. The connector methods take
        their path parameters positionally (``connectors.get(id)``,
        ``connectors.delete_resource(id, resource_id)``), unlike the
        database/context methods which are keyword-only — and unlike kwargs,
        a positional is never dropped, which is right: a path parameter is
        never optional.
        """
        clean = {k: v for k, v in kwargs.items() if v is not None}
        try:
            return fn(*args, **clean)
        except (ApiError, ParsingError, httpx.HTTPError) as exc:
            raise translate_sdk_error(exc) from exc


class _Databases(_Resource):
    """Database-scoped operations (was the ``tenant`` group)."""

    def create(
        self,
        *,
        database: str,
        embeddings_dimension: int | None = None,
        is_embeddings_tenant: bool | None = None,
        database_metadata_schema: Any | None = None,
    ) -> dict:
        resp = self._invoke(
            self._w._sdk.databases.create,
            database=database,
            embeddings_dimension=embeddings_dimension,
            is_embeddings_tenant=is_embeddings_tenant,
            database_metadata_schema=database_metadata_schema,
        )
        return _unwrap(resp)

    def delete(self, *, database: str) -> dict:
        resp = self._invoke(self._w._sdk.databases.delete, database=database)
        return _unwrap(resp)

    def list(self) -> dict:
        resp = self._invoke(self._w._sdk.databases.list)
        return _unwrap(resp)

    def collections(self, *, database: str | None = None) -> dict:
        resp = self._invoke(self._w._sdk.databases.collections, database=self._w._require_database(database))
        return _unwrap(resp)

    def stats(self, *, database: str | None = None) -> dict:
        resp = self._invoke(self._w._sdk.databases.stats, database=self._w._require_database(database))
        return _unwrap(resp)

    def readiness(self, *, database: str | None = None) -> dict:
        """Infrastructure provisioning status (CONTRACT: renamed away from ``status``)."""
        resp = self._invoke(self._w._sdk.databases.status, database=self._w._require_database(database))
        return _unwrap(resp)


class _Context(_Resource):
    """Context-family operations: query, ingest, list, inspect, delete, relations."""

    def query(
        self,
        *,
        query: str,
        kind: str | None = None,
        operator: str | None = None,
        max_results: int | None = None,
        mode: str | None = None,
        alpha: float | None = None,
        recency_bias: float | None = None,
        graph_context: bool | None = None,
        additional_context: str | None = None,
        query_by: str | None = None,
        acl: list[str] | None = None,
        database: str | None = None,
        collection: str | None = None,
    ) -> dict:
        """The single retrieval entry point (absorbs recall full/preferences/keyword).

        Maps to the SDK's top-level ``client.query``; ``kind`` becomes ``type``.
        """
        resp = self._invoke(
            self._w._sdk.query,
            type=kind,
            query=query,
            operator=operator,
            max_results=max_results,
            mode=mode,
            alpha=alpha,
            recency_bias=recency_bias,
            graph_context=graph_context,
            additional_context=additional_context,
            query_by=query_by,
            acl=acl,
            database=self._w._require_database(database),
            collection=self._w._resolve_collection(collection),
        )
        return _unwrap(resp)

    def ingest(
        self,
        *,
        kind: str,
        text: str | None = None,
        documents: File | None = None,
        title: str | None = None,
        source_id: str | None = None,
        user_name: str | None = None,
        infer: bool | None = None,
        is_markdown: bool | None = None,
        upsert: bool | None = None,
        document_metadata: str | None = None,
        database: str | None = None,
        collection: str | None = None,
    ) -> dict:
        """Ingest one memory (``memories``), one text/structured knowledge item
        (``app_knowledge``), or one knowledge file (``documents``) as a single
        multipart ``context/ingest`` call.

        Field choice matters (v2 handler, per the PRO-1298 ruling): only
        ``app_knowledge`` preserves a client-assigned ``id`` verbatim as the
        source_id — a ``documents`` upload always gets a server-minted uuid. So
        text/structured knowledge goes through ``app_knowledge`` (keeping
        ``--source-id`` addressable for later delete/verify), and ``documents``
        is reserved for actual FILE uploads where no client id is supplied.
        There is no ``app_sources`` on the SDK path (that was the v1 field).

        Multi-file ingest is a caller-side loop over this method (see
        ``ingest_many``); the SDK's ``documents`` takes exactly one file.
        """
        memories: str | None = None
        app_knowledge: str | None = None
        if kind == "memory":
            memory: dict[str, Any] = {
                "text": text,
                "infer": True if infer is None else bool(infer),
                "is_markdown": bool(is_markdown),
            }
            if title:
                memory["title"] = title
            if source_id:
                memory["source_id"] = source_id
            if user_name:
                memory["user_name"] = user_name
            memories = json.dumps([memory])
        elif kind == "knowledge" and documents is None and text is not None:
            # Text/structured knowledge -> app_knowledge so a client-supplied id
            # survives verbatim as the source_id (empty id -> server mints one).
            item: dict[str, Any] = {"id": source_id or "", "content": {"text": text}}
            if title:
                item["title"] = title
            app_knowledge = json.dumps([item])

        resp = self._invoke(
            self._w._sdk.context.ingest,
            database=self._w._require_database(database),
            collection=self._w._resolve_collection(collection),
            type=kind,
            memories=memories,
            app_knowledge=app_knowledge,
            documents=documents,
            document_metadata=document_metadata,
            upsert=_bool_str(upsert),
        )
        return _unwrap(resp)

    def ingest_many(
        self,
        *,
        kind: str,
        documents: list[File],
        upsert: bool | None = None,
        document_metadata: str | None = None,
        database: str | None = None,
        collection: str | None = None,
    ) -> dict:
        """Ingest N files by looping :meth:`ingest` once per file and merging the
        per-file results into one response — the SDK accepts a single file per
        call, so files must never be silently dropped."""
        merged: dict[str, Any] = {"success_count": 0, "failed_count": 0, "results": []}
        for document in documents:
            result = self.ingest(
                kind=kind,
                documents=document,
                upsert=upsert,
                document_metadata=document_metadata,
                database=database,
                collection=collection,
            )
            merged["success_count"] += result.get("success_count") or 0
            merged["failed_count"] += result.get("failed_count") or 0
            merged["results"].extend(result.get("results") or [])
        return merged

    def list(
        self,
        *,
        kind: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        ids: list[str] | None = None,
        acl: list[str] | None = None,
        database: str | None = None,
        collection: str | None = None,
    ) -> dict:
        resp = self._invoke(
            self._w._sdk.context.list,
            database=self._w._require_database(database),
            collection=self._w._resolve_collection(collection),
            type=kind,
            page=page,
            page_size=page_size,
            ids=ids,
            acl=acl,
        )
        data = _unwrap(resp)
        # The v2 list payload nests the real fields under `inner`; flatten it so
        # callers see the familiar {sources, total, pagination, ...} shape.
        if isinstance(data, dict):
            inner = data.get("inner")
            if isinstance(inner, dict):
                return inner
            if "inner" in data:
                return {k: v for k, v in data.items() if k != "inner"}
        return data

    def inspect(
        self,
        *,
        id: str,
        mode: str | None = None,
        expiry_seconds: int | None = None,
        acl: list[str] | None = None,
        database: str | None = None,
        collection: str | None = None,
    ) -> dict:
        """Fetch a source's content, inferred content, and a download URL (was
        "fetch content")."""
        resp = self._invoke(
            self._w._sdk.context.inspect,
            id=id,
            database=self._w._require_database(database),
            collection=self._w._resolve_collection(collection),
            mode=mode,
            expiry_seconds=expiry_seconds,
            acl=acl,
        )
        return _unwrap(resp)

    def delete(
        self,
        *,
        ids: list[str],
        kind: str | None = None,
        database: str | None = None,
        collection: str | None = None,
    ) -> dict:
        resp = self._invoke(
            self._w._sdk.context.delete,
            database=self._w._require_database(database),
            collection=self._w._resolve_collection(collection),
            ids=ids,
            type=kind,
        )
        return _unwrap(resp)

    def relations(
        self,
        *,
        id: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
        cursor: float | None = None,
        acl: list[str] | None = None,
        database: str | None = None,
        collection: str | None = None,
    ) -> dict:
        resp = self._invoke(
            self._w._sdk.context.relations,
            database=self._w._require_database(database),
            collection=self._w._resolve_collection(collection),
            id=id,
            type=kind,
            limit=limit,
            cursor=cursor,
            acl=acl,
        )
        return _unwrap(resp)

    def subgraph(
        self,
        *,
        id: str,
        kind: str | None = None,
        depth: int | None = None,
        max_sources: int | None = None,
        acl: list[str] | None = None,
        database: str | None = None,
        collection: str | None = None,
    ) -> dict:
        """The connected subgraph of one item (``GET /context/{id}/subgraph``).

        Every item reachable from ``id`` through item-level relations — explicit
        links declared at ingest, a shared thread, parent/child hierarchy — plus
        the relations among the members and the structural graph around them.

        No SDK resource for this yet (CONTRACT §2 rule 7), so it takes the raw
        path behind the same surface: same headers, same envelope unwrap, same
        :class:`HydraDBClientError`. When the SDK grows ``context.subgraph``,
        only this method changes.
        """
        params: dict[str, Any] = {
            "database": self._w._require_database(database),
        }
        col = self._w._resolve_collection(collection)
        if col:
            params["collection"] = col
        if kind:
            params["type"] = kind
        if depth is not None:
            params["depth"] = depth
        if max_sources is not None:
            params["max_sources"] = max_sources
        # Repeated params (httpx encodes a list value as ?acl=a&acl=b), which
        # the API reads alongside the comma-separated form. Omit when unset:
        # the API treats [] the same as absent, so sending nothing keeps the
        # request faithful to what the caller said.
        if acl:
            params["acl"] = list(acl)
        # The id is a path segment: escape it, or an id with a slash walks the
        # request to a different route.
        return self._w._raw_get(f"/context/{quote(id, safe='')}/subgraph", params=params)

    def ingestion_status(
        self,
        *,
        ids: list[str],
        database: str | None = None,
        collection: str | None = None,
    ) -> dict:
        """Per-source indexing progress (CONTRACT: renamed away from ``status``)."""
        resp = self._invoke(
            self._w._sdk.context.status,
            database=self._w._require_database(database),
            collection=self._w._resolve_collection(collection),
            ids=ids,
        )
        return _unwrap(resp)


class _Graph(_Resource):
    """BYOG (Bring Your Own Graph) operations — full Cypher over graph collections.

    Unlike every other resource here, this one does NOT go through the SDK: the
    pinned ``hydradb-sdk==2.1.2`` exposes ``context``, ``databases``,
    ``connectors`` and ``webhooks`` and has no ``byog`` resource at all, so the
    ``/byog/*`` endpoints are unreachable through it.

    It is still not a second client. It reuses the wrapper's own ``httpx``
    dependency, unwraps the same ``HandlerEnvelope`` by shape, and raises the
    same :class:`HydraDBClientError` with the same status codes, so
    ``handle_api_error`` treats a BYOG failure exactly like an SDK one. When the
    SDK grows a ``byog`` resource, this class is reimplemented over it and no
    caller changes.
    """

    def _request(self, method: str, path: str, *, json_body: Any = None, params: dict | None = None) -> Any:
        url = f"{self._w._base_url.rstrip('/')}{path}"
        headers = {
            "Authorization": f"Bearer {self._w._token}",
            "Content-Type": "application/json",
            # CONTRACT §2 rule 6. The SDK sends this on every call; a hand-rolled
            # path that omitted it would silently get v1 behaviour from the same
            # endpoints.
            "API-Version": "2",
        }
        try:
            response = httpx.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=self._w._timeout,
            )
        except httpx.HTTPError as exc:
            raise translate_sdk_error(exc) from exc

        try:
            body = response.json() if response.content else None
        except ValueError:
            body = response.text or None

        if response.is_error:
            # Route through the same message extraction the SDK path uses, so a
            # rejected Cypher query surfaces the compiler's own feedback rather
            # than a bare status code.
            raise HydraDBClientError(response.status_code, _stringify_body(body))

        return _unwrap_payload(body)

    def query(
        self,
        *,
        query: str,
        params: dict | None = None,
        database: str | None = None,
        collection: str | None = None,
    ) -> list[dict]:
        """Run Cypher against one collection (``POST /byog/query``).

        Returns rows verbatim: a list of row dicts keyed by the query's RETURN
        column names. A write with no RETURN yields ``[]``, which is a success
        and must not be read as a failure.
        """
        body = {
            "database": self._w._require_database(database),
            "collection": collection or self._w.default_graph_collection,
            "query": query,
        }
        if params:
            body["params"] = params
        rows = self._request("POST", "/byog/query", json_body=body)
        return rows if isinstance(rows, list) else []

    def create_database(self, *, database: str) -> dict:
        """Create a graph database (``POST /byog/databases``). Ready immediately."""
        result = self._request("POST", "/byog/databases", json_body={"database": database})
        return result if isinstance(result, dict) else {}

    def collections(self, *, database: str | None = None) -> list[str]:
        """List the collections in a graph database (``GET /byog/collections``)."""
        result = self._request(
            "GET",
            "/byog/collections",
            params={"database": self._w._require_database(database)},
        )
        if isinstance(result, dict) and isinstance(result.get("collections"), list):
            return result["collections"]
        return []

    def drop_collection(self, *, collection: str, database: str | None = None) -> dict:
        """Drop one collection and all its data. Idempotent."""
        result = self._request(
            "DELETE",
            "/byog/collections",
            json_body={"database": self._w._require_database(database), "collection": collection},
        )
        return result if isinstance(result, dict) else {}

    def drop_database(self, *, database: str | None = None) -> dict:
        """Drop a graph database and every collection in it.

        ``deleted`` is ``False`` when the database was created through the
        standard database API and merely holds graph collections — in that case
        only the collections go. Callers must report that distinction rather
        than smoothing it over.
        """
        result = self._request(
            "DELETE",
            "/byog/databases",
            json_body={"database": self._w._require_database(database)},
        )
        return result if isinstance(result, dict) else {}


class _Connectors(_Resource):
    """Managed integrations that sync external sources into a database.

    This resource is where CONTRACT §2's firewall earns its keep most visibly:
    the SDK's method for rotating a stored credential is generated from OpenAPI
    summary text and is called
    ``rotate_a_connectors_stored_o_auth_refresh_token``. That name is one
    regeneration away from changing, and no CLI command should ever reference
    it. Every method here exposes the canonical verb and maps to whatever the
    SDK currently calls it.

    The documented lifecycle is create → discover → configure → sync → poll →
    query → delete.
    """

    def providers(self) -> list[dict]:
        """The provider catalogue (``GET /connectors/providers``).

        Read from the API rather than hardcoded: the catalogue gains providers
        without a CLI release, and a baked-in list would quietly become a lie
        that tells users a supported provider does not exist.

        The SDK has no method for this endpoint — its ``connectors.list``
        ``provider`` argument filters *connectors*, not the catalogue — so this
        one call goes over raw HTTP. It still returns through the same error
        translation as every other method here.
        """
        result = self._w._raw_get("/connectors/providers")
        if isinstance(result, dict) and isinstance(result.get("providers"), list):
            return result["providers"]
        return []

    def provider(self, provider_id: str) -> dict:
        """One provider's credential schema and searchable/filterable fields."""
        result = self._w._raw_get("/connectors/providers", params={"id": provider_id})
        return result if isinstance(result, dict) else {}

    def list(self, *, provider: str | None = None) -> list[dict]:
        """List connectors for the organisation.

        Note the response is NOT enveloped — it is a bare ``{"connectors": [...]}``.
        ``_unwrap`` checks for the envelope shape rather than assuming it
        (CONTRACT §2 rule 2), which is exactly why that check exists: assuming
        the envelope here would silently return ``{}``.
        """
        resp = self._invoke(self._w._sdk.connectors.list, provider=provider)
        data = _unwrap_payload(_unwrap(resp))
        if isinstance(data, dict) and isinstance(data.get("connectors"), list):
            return data["connectors"]
        return []

    def get(self, connector_id: str) -> dict:
        resp = self._invoke(self._w._sdk.connectors.get, connector_id)
        return _unwrap(resp)

    def create(
        self,
        *,
        provider: str,
        name: str | None = None,
        database: str | None = None,
        collection: str | None = None,
        provider_account_scope: str | None = None,
        credentials: dict | None = None,
        auth_type: str | None = None,
        sync_interval_seconds: int | None = None,
    ) -> dict:
        resp = self._invoke(
            self._w._sdk.connectors.create,
            provider=provider,
            name=name,
            database=self._w._require_database(database),
            collection=self._w._resolve_collection(collection),
            provider_account_scope=provider_account_scope,
            credentials=credentials,
            auth_type=auth_type,
            sync_interval_seconds=sync_interval_seconds,
        )
        return _unwrap(resp)

    def delete(self, connector_id: str) -> dict:
        resp = self._invoke(self._w._sdk.connectors.delete, connector_id)
        return _unwrap(resp)

    def discover(self, connector_id: str, *, cursor: str | None = None, limit: int | None = None) -> dict:
        """List resources available from the provider, before configuring any."""
        resp = self._invoke(self._w._sdk.connectors.discover, connector_id, cursor=cursor, limit=limit)
        return _unwrap(resp)

    def configure(
        self,
        connector_id: str,
        *,
        resources: list[dict],
        lookback_days: int | None = None,
    ) -> dict:
        """Activate resources and set sync options."""
        resp = self._invoke(
            self._w._sdk.connectors.configure,
            connector_id,
            resources=resources,
            lookback_days=lookback_days,
        )
        return _unwrap(resp)

    def resources(self, connector_id: str) -> list[dict]:
        """Configured resources and their per-resource sync state."""
        resp = self._invoke(self._w._sdk.connectors.list_resources, connector_id)
        data = _unwrap_payload(_unwrap(resp))
        if isinstance(data, dict) and isinstance(data.get("resources"), list):
            return data["resources"]
        return data if isinstance(data, list) else []

    def add_resource(
        self,
        connector_id: str,
        *,
        resource_id: str,
        resource_type: str | None = None,
        display_name: str | None = None,
        collection_override: str | None = None,
    ) -> dict:
        resp = self._invoke(
            self._w._sdk.connectors.create_resource,
            connector_id,
            resource_id=resource_id,
            resource_type=resource_type,
            display_name=display_name,
            collection_override=collection_override,
        )
        return _unwrap(resp)

    def remove_resource(self, connector_id: str, resource_id: str) -> dict:
        resp = self._invoke(self._w._sdk.connectors.delete_resource, connector_id, resource_id)
        return _unwrap(resp)

    def sync(self, connector_id: str) -> dict:
        """Trigger an on-demand sync cycle."""
        resp = self._invoke(self._w._sdk.connectors.sync, connector_id)
        return _unwrap(resp)

    # The SDK generates this method's name from the endpoint's summary text, so
    # it moves whenever the summary is reworded: 2.1.2 called it
    # ``rotate_a_connectors_stored_o_auth_refresh_token`` and 2.1.4 appends
    # ``_internal_use_only``. Absorbing that churn is what this wrapper is for
    # (CONTRACT S2), so the candidates live here, newest spelling first, rather
    # than pinning the CLI to one SDK release.
    _ROTATE_SDK_NAMES = (
        "rotate_a_connectors_stored_o_auth_refresh_token_internal_use_only",
        "rotate_a_connectors_stored_o_auth_refresh_token",
    )

    def rotate_credentials(self, connector_id: str, *, credentials: dict) -> dict:
        """Replace a connector's stored credentials."""
        for name in self._ROTATE_SDK_NAMES:
            fn = getattr(self._w._sdk.connectors, name, None)
            if fn is not None:
                resp = self._invoke(fn, connector_id, request=credentials)
                return _unwrap(resp)
        # A rename we have not seen. Say so plainly instead of failing with a
        # bare AttributeError from somewhere inside the generated client.
        raise AttributeError(
            "hydradb-sdk exposes no known credential-rotation method; tried: " + ", ".join(self._ROTATE_SDK_NAMES)
        )


class HydraDB:
    """Canonical, hand-owned wrapper around the generated ``hydra_db`` SDK.

    Exposes :attr:`databases`, :attr:`context`, :attr:`graph` and
    :attr:`connectors` sub-resources whose method names follow the shared
    contract's canonical vocabulary.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str | None = None,
        database: str | None = None,
        collection: str | None = None,
        graph_collection: str | None = None,
        timeout: float = 60.0,
    ):
        self._sdk = _SdkHydraDB(token=token, base_url=base_url, timeout=timeout)
        self.default_database = database
        self.default_collection = collection
        # A graph collection is a different namespace from a context collection:
        # the same database can hold both, and Cypher aimed at the wrong one
        # reads an empty graph rather than failing. So it is scoped separately
        # and never falls back to `default_collection`.
        self.default_graph_collection = graph_collection or DEFAULT_GRAPH_COLLECTION
        # The BYOG path is hand-rolled rather than an SDK call, so it needs the
        # raw credentials and base URL the SDK client keeps to itself.
        self._token = token
        self._base_url = base_url or DEFAULT_BASE_URL
        self._timeout = timeout
        self.databases = _Databases(self)
        self.context = _Context(self)
        self.graph = _Graph(self)
        self.connectors = _Connectors(self)

    def _raw_get(self, path: str, *, params: dict | None = None) -> Any:
        """GET an endpoint the SDK does not expose, with the same error contract.

        Used for ``/connectors/providers`` and ``/context/{id}/subgraph``. It
        sends the same auth and
        ``API-Version: 2`` headers the SDK does, unwraps the envelope by shape,
        and raises the same :class:`HydraDBClientError`, so a caller cannot tell
        it apart from an SDK call and ``handle_api_error`` works unchanged.
        """
        headers = {
            "Authorization": f"Bearer {self._token}",
            # CONTRACT §2 rule 6 — omitting this would silently get v1 behaviour.
            "API-Version": "2",
        }
        try:
            response = httpx.get(
                f"{self._base_url.rstrip('/')}{path}",
                headers=headers,
                params=params,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise translate_sdk_error(exc) from exc

        try:
            body = response.json() if response.content else None
        except ValueError:
            body = response.text or None

        if response.is_error:
            raise HydraDBClientError(response.status_code, _stringify_body(body))

        # Unwrap by SHAPE, never by assumption: the connector endpoints mostly
        # return bare objects rather than a HandlerEnvelope.
        if isinstance(body, dict) and "data" in body and ({"success", "meta", "error"} & body.keys()):
            data = body["data"]
            return data if data is not None else {}
        return body if body is not None else {}

    def _require_database(self, database: str | None) -> str:
        db = database or self.default_database
        if not db or not str(db).strip():
            raise HydraDBClientError(
                0,
                "No database specified. Use --database or run 'hydradb config set tenant_id <id>'.",
            )
        return db

    def _resolve_collection(self, collection: str | None) -> str | None:
        return collection or self.default_collection
