"""A small Solr 10 client on httpx: configsets, collections, updates, queries.

Only what Semascope needs. Every call has a timeout, and errors carry Solr's
own message so a failing schema or query is readable in the logs.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx


class SolrError(RuntimeError):
    pass


class SolrClient:
    def __init__(
        self,
        base_url: str,
        *,
        user: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")  # e.g. http://solr:8983/solr
        root = self.base_url.rsplit("/solr", 1)[0]
        self.api_url = f"{root}/api"
        auth = (user, password) if user and password else None
        self._http = httpx.Client(timeout=timeout, auth=auth)

    # --- plumbing -----------------------------------------------------------------------------

    def _raise(self, resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:500]}
        if resp.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
            err = body.get("error", {}) if isinstance(body, dict) else {}
            msg = err.get("msg") if isinstance(err, dict) else None
            raise SolrError(f"{resp.request.method} {resp.request.url}: {msg or body}")
        return body if isinstance(body, dict) else {"raw": body}

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._raise(
            self._http.get(f"{self.base_url}{path}", params={"wt": "json", **params})
        )

    def _post_json(self, path: str, payload: Any, **params: Any) -> dict[str, Any]:
        return self._raise(
            self._http.post(f"{self.base_url}{path}", json=payload, params={"wt": "json", **params})
        )

    # --- admin ---------------------------------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        return self._get("/admin/info/system")

    def list_configsets(self) -> list[str]:
        body = self._get("/admin/configs", action="LIST")
        return list(body.get("configSets", []))

    def upload_configset(self, name: str, conf_dir: Path) -> None:
        """Zip a conf directory and upload it, replacing any previous version."""

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(p for p in conf_dir.rglob("*") if p.is_file()):
                zf.write(file, file.relative_to(conf_dir).as_posix())
        resp = self._http.put(
            f"{self.api_url}/configsets/{name}",
            params={"overwrite": "true", "cleanup": "true"},
            content=buf.getvalue(),
            headers={"Content-Type": "application/octet-stream"},
        )
        self._raise(resp)

    def list_collections(self) -> list[str]:
        body = self._get("/admin/collections", action="LIST")
        return list(body.get("collections", []))

    def create_collection(self, name: str, configset: str, *, shards: int = 1) -> None:
        self._get(
            "/admin/collections",
            action="CREATE",
            name=name,
            numShards=shards,
            replicationFactor=1,
            **{"collection.configName": configset},
        )

    def reload_collection(self, name: str) -> None:
        self._get("/admin/collections", action="RELOAD", name=name)

    def ensure_collection(self, name: str, configset: str, conf_dir: Path) -> bool:
        """Upload the configset and create the collection if missing. Returns True if created."""

        self.upload_configset(configset, conf_dir)
        if name in self.list_collections():
            self.reload_collection(name)
            return False
        self.create_collection(name, configset)
        return True

    # --- data ----------------------------------------------------------------------------------

    def add(
        self, collection: str, docs: list[dict[str, Any]], *, commit_within_ms: int = 2000
    ) -> None:
        self._post_json(f"/{collection}/update", docs, commitWithin=commit_within_ms)

    def delete_by_id(self, collection: str, ids: list[str]) -> None:
        self._post_json(f"/{collection}/update", {"delete": ids}, commitWithin=1000)

    def delete_by_query(self, collection: str, query: str) -> None:
        self._post_json(f"/{collection}/update", {"delete": {"query": query}}, commitWithin=1000)

    def commit(self, collection: str) -> None:
        self._get(f"/{collection}/update", commit="true")

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        """Run /select with JSON-friendly params; multi-valued params are passed as lists."""

        resp = self._http.post(
            f"{self.base_url}/{collection}/select",
            content=urlencode(_flatten(params)),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return self._raise(resp)

    def count(self, collection: str, query: str = "*:*") -> int:
        body = self.select(collection, q=query, rows=0)
        return int(body["response"]["numFound"])

    def close(self) -> None:
        self._http.close()


def _flatten(params: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = [("wt", "json")]
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, list | tuple):
            out.extend((key, str(v)) for v in value)
        elif isinstance(value, bool):
            out.append((key, "true" if value else "false"))
        else:
            out.append((key, str(value)))
    return out
