"""`oss doctor`: does the configured embedding model fit the Solr `chunks` schema?

The model's dimension comes from SEMASCOPE_EMBED_DIMENSIONS, else from the
remote service itself (one short embedding), else from a table of known
models. The schema's vector fields come from Solr's Schema API, and the
chunk count per model shows whether a re-embed is still due.
"""

from __future__ import annotations

from typing import Any

import httpx

from oss_core.vectors import check_vectors, known_dimensions, schema_vector_fields

PROBE = "dimension probe"


def _auth(settings: Any) -> tuple[str, str] | None:
    password = settings.solr_password.get_secret_value() if settings.solr_password else ""
    return (settings.solr_user, password) if settings.solr_user and password else None


def model_dimensions(settings: Any, client: httpx.Client) -> tuple[int | None, str]:
    """The configured model's vector length and where it came from."""

    if settings.semascope_embed_dimensions:
        return int(settings.semascope_embed_dimensions), "SEMASCOPE_EMBED_DIMENSIONS"
    base = (settings.semascope_embed_base_url or "").rstrip("/")
    key = settings.remote_key("embed")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        if settings.semascope_embedder == "sidecar" and base:
            resp = client.post(f"{base}/embed/dense", json={"texts": [PROBE]}, headers=headers)
            resp.raise_for_status()
            return len(resp.json()["vectors"][0]), "asked the sidecar"
        if settings.semascope_embedder == "openai" and base:
            resp = client.post(
                f"{base}/embeddings",
                json={"model": settings.semascope_embed_model, "input": [PROBE]},
                headers=headers,
            )
            resp.raise_for_status()
            return len(resp.json()["data"][0]["embedding"]), "asked the endpoint"
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        pass
    known = known_dimensions(settings.semascope_embed_model)
    return (known, "known model") if known else (None, "unknown model")


def vector_report(settings: Any, client: httpx.Client) -> tuple[str, bool, list[str]]:
    """(status for the doctor table, whether it is fine, notes to print under it)."""

    solr = settings.solr_url.rstrip("/")
    try:
        resp = client.get(f"{solr}/chunks/schema", params={"wt": "json"}, auth=_auth(settings))
        resp.raise_for_status()
        fields = schema_vector_fields(resp.json())
    except (httpx.HTTPError, ValueError):
        return "not checked (chunks schema unreadable)", True, []
    dims, source = model_dimensions(settings, client)
    ok, message = check_vectors(dims, fields)
    notes: list[str] = []
    try:
        resp = client.get(
            f"{solr}/chunks/select",
            params={
                "q": "*:*",
                "rows": 0,
                "wt": "json",
                "facet": "true",
                "facet.field": "model_s",
                "facet.mincount": 1,
            },
            auth=_auth(settings),
        )
        resp.raise_for_status()
        flat = resp.json()["facet_counts"]["facet_fields"]["model_s"]
        others = {
            str(flat[i]): int(flat[i + 1])
            for i in range(0, len(flat), 2)
            if str(flat[i]) != settings.semascope_embed_model
        }
        if others:
            listed = ", ".join(f"{m} ({n})" for m, n in sorted(others.items()))
            notes.append(
                f"chunks from other models: {listed}; semantic search ignores them until "
                "`./oss reindex --collection <slug> --stage embed` re-embeds each collection"
            )
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        pass
    return f"{message} ({source})" if dims else message, ok, notes
