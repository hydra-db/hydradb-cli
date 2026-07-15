"""HTTP client for HydraDB API.

Thin wrapper around httpx that handles auth headers and error formatting.
All methods return raw dict responses — the CLI commands handle formatting.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import httpx

from hydradb_cli.config import get_api_key, get_base_url


class HydraDBClientError(Exception):
    """Raised when the HydraDB API returns an error."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class HydraDBClient:
    """Synchronous HTTP client for HydraDB API."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ):
        self.api_key = api_key or get_api_key()
        self.base_url = (base_url or get_base_url()).rstrip("/")
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> "HydraDBClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _auth_headers(self) -> dict[str, str]:
        """Return headers with only the Authorization token (for multipart requests)."""
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _headers(self) -> dict[str, str]:
        """Return headers with Authorization and JSON content type."""
        headers = {"Content-Type": "application/json"}
        headers.update(self._auth_headers())
        return headers

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Execute an HTTP request, converting network errors to HydraDBClientError."""
        try:
            return getattr(self._http, method)(url, **kwargs)
        except httpx.ConnectError as e:
            raise HydraDBClientError(
                0, f"Could not connect to {self.base_url}. Check your network and base URL."
            ) from e
        except httpx.ConnectTimeout as e:
            raise HydraDBClientError(0, f"Connection timed out reaching {self.base_url}.") from e
        except httpx.ReadTimeout as e:
            raise HydraDBClientError(
                0, "Request timed out waiting for a response. The server may be under heavy load."
            ) from e
        except httpx.TimeoutException as e:
            raise HydraDBClientError(0, f"Request timed out: {e}") from e
        except httpx.HTTPError as e:
            raise HydraDBClientError(0, f"Network error: {e}") from e

    def _handle_response(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise HydraDBClientError(response.status_code, str(detail))
        try:
            return response.json()
        except Exception:
            return {"raw": response.text}

    # --- Tenant ---

    def create_tenant(
        self,
        tenant_id: str,
        is_embeddings_tenant: bool | None = None,
        embeddings_dimension: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {"tenant_id": tenant_id}
        if is_embeddings_tenant is not None:
            body["is_embeddings_tenant"] = is_embeddings_tenant
        if embeddings_dimension is not None:
            body["embeddings_dimension"] = embeddings_dimension
        resp = self._request(
            "post",
            f"{self.base_url}/tenants/create",
            headers=self._headers(),
            json=body,
        )
        return self._handle_response(resp)

    def monitor_tenant(self, tenant_id: str) -> dict:
        resp = self._request(
            "get",
            f"{self.base_url}/tenants/monitor",
            headers=self._headers(),
            params={"tenant_id": tenant_id},
        )
        return self._handle_response(resp)

    def list_sub_tenants(self, tenant_id: str) -> dict:
        resp = self._request(
            "get",
            f"{self.base_url}/tenants/sub_tenant_ids",
            headers=self._headers(),
            params={"tenant_id": tenant_id},
        )
        return self._handle_response(resp)

    def delete_tenant(self, tenant_id: str) -> dict:
        resp = self._request(
            "delete",
            f"{self.base_url}/tenants/delete",
            headers=self._headers(),
            params={"tenant_id": tenant_id},
        )
        return self._handle_response(resp)

    # --- Memories ---

    def add_memory(
        self,
        tenant_id: str,
        text: str,
        sub_tenant_id: str | None = None,
        infer: bool = True,
        is_markdown: bool = False,
        title: str | None = None,
        source_id: str | None = None,
        user_name: str | None = None,
        upsert: bool = True,
    ) -> dict:
        memory: dict[str, Any] = {
            "text": text,
            "infer": infer,
            "is_markdown": is_markdown,
        }
        if title:
            memory["title"] = title
        if source_id:
            memory["source_id"] = source_id
        if user_name:
            memory["user_name"] = user_name

        body: dict[str, Any] = {
            "memories": [memory],
            "tenant_id": tenant_id,
            "upsert": upsert,
        }
        if sub_tenant_id is not None:
            body["sub_tenant_id"] = sub_tenant_id

        resp = self._request(
            "post",
            f"{self.base_url}/memories/add_memory",
            headers=self._headers(),
            json=body,
        )
        return self._handle_response(resp)

    def list_memories(
        self,
        tenant_id: str,
        sub_tenant_id: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "tenant_id": tenant_id,
            "kind": "memories",
        }
        if sub_tenant_id is not None:
            body["sub_tenant_id"] = sub_tenant_id
        resp = self._request(
            "post",
            f"{self.base_url}/list/data",
            headers=self._headers(),
            json=body,
        )
        return self._handle_response(resp)

    def delete_memory(
        self,
        tenant_id: str,
        memory_id: str,
        sub_tenant_id: str | None = None,
    ) -> dict:
        params: dict[str, str] = {
            "tenant_id": tenant_id,
            "memory_id": memory_id,
        }
        if sub_tenant_id is not None:
            params["sub_tenant_id"] = sub_tenant_id
        resp = self._request(
            "delete",
            f"{self.base_url}/memories/delete_memory",
            headers=self._headers(),
            params=params,
        )
        return self._handle_response(resp)

    # --- Knowledge (Ingestion) ---

    def upload_knowledge(
        self,
        tenant_id: str,
        file_paths: list[str],
        sub_tenant_id: str | None = None,
        upsert: bool = False,
        file_metadata: list[dict] | None = None,
    ) -> dict:
        paths = []
        for fp in file_paths:
            p = Path(fp)
            if not p.exists():
                raise FileNotFoundError(f"File not found: {fp}")
            if not p.is_file():
                raise FileNotFoundError(f"Not a file: {fp}")
            if p.stat().st_size == 0:
                raise FileNotFoundError(f"File is empty: {fp}")
            paths.append(p)

        opened = []
        files = []
        try:
            for p in paths:
                f = p.open("rb")
                opened.append(f)
                files.append(("files", (p.name, f)))

            data: dict[str, Any] = {
                "tenant_id": tenant_id,
                "upsert": str(upsert).lower(),
            }
            if sub_tenant_id is not None:
                data["sub_tenant_id"] = sub_tenant_id
            if file_metadata:
                data["file_metadata"] = json.dumps(file_metadata)

            resp = self._request(
                "post",
                f"{self.base_url}/ingestion/upload_knowledge",
                headers=self._auth_headers(),
                data=data,
                files=files,
            )
            return self._handle_response(resp)
        finally:
            for f in opened:
                f.close()

    def upload_text(
        self,
        tenant_id: str,
        text: str,
        sub_tenant_id: str | None = None,
        title: str | None = None,
        source_id: str | None = None,
        upsert: bool = False,
    ) -> dict:
        """Upload text content as a knowledge source (via app_sources).

        The app_sources field expects a SourceModel with required fields:
        id, tenant_id, sub_tenant_id, and content as a ContentModel dict.
        """
        sid = source_id or str(uuid.uuid4())
        stid = sub_tenant_id if sub_tenant_id else tenant_id

        source: dict[str, Any] = {
            "id": sid,
            "tenant_id": tenant_id,
            "sub_tenant_id": stid,
            "content": {"text": text},
        }
        if title:
            source["title"] = title

        data: dict[str, Any] = {
            "tenant_id": tenant_id,
            "upsert": str(upsert).lower(),
        }
        if sub_tenant_id:
            data["sub_tenant_id"] = sub_tenant_id
        data["app_sources"] = json.dumps(source)

        resp = self._request(
            "post",
            f"{self.base_url}/ingestion/upload_knowledge",
            headers=self._auth_headers(),
            data=data,
        )
        return self._handle_response(resp)

    def verify_processing(
        self,
        tenant_id: str,
        file_ids: list[str],
        sub_tenant_id: str | None = None,
    ) -> dict:
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "file_ids": ",".join(file_ids),
        }
        if sub_tenant_id is not None:
            params["sub_tenant_id"] = sub_tenant_id
        resp = self._request(
            "post",
            f"{self.base_url}/ingestion/verify_processing",
            headers=self._headers(),
            params=params,
        )
        return self._handle_response(resp)

    def delete_knowledge(
        self,
        tenant_id: str,
        ids: list[str],
        sub_tenant_id: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "tenant_id": tenant_id,
            "ids": ids,
        }
        if sub_tenant_id is not None:
            body["sub_tenant_id"] = sub_tenant_id
        resp = self._request(
            "post",
            f"{self.base_url}/knowledge/delete_knowledge",
            headers=self._headers(),
            json=body,
        )
        return self._handle_response(resp)

    # --- Recall ---

    def _recall(
        self,
        endpoint: str,
        tenant_id: str,
        query: str,
        sub_tenant_id: str | None = None,
        max_results: int = 10,
        mode: str | None = None,
        alpha: float | None = None,
        recency_bias: float | None = None,
        graph_context: bool | None = None,
        additional_context: str | None = None,
    ) -> dict:
        """Shared recall logic for full_recall and recall_preferences."""
        body: dict[str, Any] = {
            "tenant_id": tenant_id,
            "query": query,
            "max_results": max_results,
        }
        if sub_tenant_id is not None:
            body["sub_tenant_id"] = sub_tenant_id
        if mode:
            body["mode"] = mode
        if alpha is not None:
            body["alpha"] = alpha
        if recency_bias is not None:
            body["recency_bias"] = recency_bias
        if graph_context is not None:
            body["graph_context"] = graph_context
        if additional_context:
            body["additional_context"] = additional_context
        resp = self._request(
            "post",
            f"{self.base_url}/recall/{endpoint}",
            headers=self._headers(),
            json=body,
        )
        return self._handle_response(resp)

    def full_recall(self, tenant_id: str, query: str, **kwargs: Any) -> dict:
        return self._recall("full_recall", tenant_id, query, **kwargs)

    def recall_preferences(self, tenant_id: str, query: str, **kwargs: Any) -> dict:
        return self._recall("recall_preferences", tenant_id, query, **kwargs)

    def boolean_recall(
        self,
        tenant_id: str,
        query: str,
        sub_tenant_id: str | None = None,
        operator: str | None = None,
        max_results: int = 10,
        search_mode: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "tenant_id": tenant_id,
            "query": query,
            "max_results": max_results,
        }
        if sub_tenant_id is not None:
            body["sub_tenant_id"] = sub_tenant_id
        if operator:
            body["operator"] = operator
        if search_mode:
            body["search_mode"] = search_mode
        resp = self._request(
            "post",
            f"{self.base_url}/recall/boolean_recall",
            headers=self._headers(),
            json=body,
        )
        return self._handle_response(resp)

    # --- Fetch ---

    def list_data(
        self,
        tenant_id: str,
        sub_tenant_id: str | None = None,
        kind: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {"tenant_id": tenant_id}
        if sub_tenant_id is not None:
            body["sub_tenant_id"] = sub_tenant_id
        if kind:
            body["kind"] = kind
        if page is not None:
            body["page"] = page
        if page_size is not None:
            body["page_size"] = page_size
        resp = self._request(
            "post",
            f"{self.base_url}/list/data",
            headers=self._headers(),
            json=body,
        )
        return self._handle_response(resp)

    def fetch_content(
        self,
        tenant_id: str,
        source_id: str,
        sub_tenant_id: str | None = None,
        mode: str = "content",
    ) -> dict:
        body: dict[str, Any] = {
            "tenant_id": tenant_id,
            "source_id": source_id,
            "mode": mode,
        }
        if sub_tenant_id is not None:
            body["sub_tenant_id"] = sub_tenant_id
        resp = self._request(
            "post",
            f"{self.base_url}/fetch/content",
            headers=self._headers(),
            json=body,
        )
        return self._handle_response(resp)

    def graph_relations(
        self,
        tenant_id: str,
        source_id: str,
        sub_tenant_id: str | None = None,
        is_memory: bool | None = None,
        limit: int | None = None,
    ) -> dict:
        params: dict[str, Any] = {
            "source_id": source_id,
            "tenant_id": tenant_id,
        }
        if sub_tenant_id is not None:
            params["sub_tenant_id"] = sub_tenant_id
        if is_memory is not None:
            params["is_memory"] = str(is_memory).lower()
        if limit is not None:
            params["limit"] = str(limit)
        resp = self._request(
            "get",
            f"{self.base_url}/list/graph_relations_by_id",
            headers=self._headers(),
            params=params,
        )
        return self._handle_response(resp)
