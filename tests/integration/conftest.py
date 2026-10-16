"""Fixtures for the integration tests; see _stack.py for the configuration."""

from __future__ import annotations

import os
import warnings
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from _stack import base_url, collection, demo_texts, indexing_done, make_client, wait_for


@pytest.fixture(scope="session")
def api() -> Iterator[httpx.Client]:
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")
    with make_client() as client:
        try:
            client.get("/stats").raise_for_status()
        except httpx.HTTPError as exc:
            pytest.exit(f"the stack at {base_url()} does not answer: {exc}", returncode=2)
        yield client


@pytest.fixture(scope="session")
def indexed(api: httpx.Client) -> dict[str, Any]:
    """Wait until the demo subset has been processed; returns /api/stats."""

    timeout = float(os.environ.get("SEMASCOPE_IT_TIMEOUT", "900"))
    stats: dict[str, Any] = wait_for(
        lambda: indexing_done(api, len(demo_texts())),
        timeout=timeout,
        every=5.0,
        what="the demo subset to be indexed",
    )
    return stats


@pytest.fixture(scope="session")
def docs_by_slug(api: httpx.Client, indexed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Search hits of the demo collection keyed by corpus slug (file name without .txt)."""

    out = api.post(
        "/search", json={"q": "*:*", "collection": collection(), "page_size": 100, "facets": []}
    ).raise_for_status()
    return {h["uri"].rsplit("/", 1)[-1].removesuffix(".txt"): h for h in out.json()["hits"]}
