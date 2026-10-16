"""Helpers for the integration tests, which run against a live stack with the demo subset.

They talk to the HTTP API through Caddy, the way a client does, and shell out
to `./oss` where the capability is a command (reindex, backup, restore).
`scripts/integration.sh` brings a throwaway stack up and runs them; see
docs/operations.md#integration-tests. Configuration, all optional:

    SEMASCOPE_IT_URL         https://localhost:${SEMASCOPE_HTTPS_PORT:-8473}
    SEMASCOPE_ENV_FILE       env file written by `oss init` (default .env); the
                             API token is read from it unless SEMASCOPE_API_TOKEN is set
    SEMASCOPE_DEMO_ONLY      the demo slugs that were loaded (space-separated)
    SEMASCOPE_IT_COLLECTION  demo
    SEMASCOPE_IT_OSS         the oss entry point (default ./oss)
    SEMASCOPE_IT_TIMEOUT     seconds to wait for indexing (default 900)
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEXTS = "odyssey iliad dhammapada song-celestial"
DEMO = "https://semascope.dev/ontology/demo#"


def env_value(name: str) -> str:
    """A variable from the environment, else from the stack's env file."""

    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / os.environ.get("SEMASCOPE_ENV_FILE", ".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip()
    return ""


def base_url() -> str:
    port = env_value("SEMASCOPE_HTTPS_PORT") or "8473"
    return os.environ.get("SEMASCOPE_IT_URL", f"https://localhost:{port}").rstrip("/")


def demo_texts() -> list[str]:
    return os.environ.get("SEMASCOPE_DEMO_ONLY", DEFAULT_TEXTS).split()


def collection() -> str:
    return os.environ.get("SEMASCOPE_IT_COLLECTION", "demo")


def wait_for(
    probe: Callable[[], Any], *, timeout: float, every: float = 3.0, what: str = "condition"
) -> Any:
    """Call `probe` until it returns something truthy; fail with its last value on timeout."""

    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(every)
    pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}; last value: {last!r}")


def oss(*args: str, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    """Run the oss entry point from the repository root and fail the test on a non-zero exit."""

    cmd = [*shlex.split(os.environ.get("SEMASCOPE_IT_OSS", "./oss")), *args]
    proc = subprocess.run(  # noqa: S603 - the command is the project's own entry point
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False
    )
    print(f"$ {shlex.join(cmd)}\n{proc.stdout}{proc.stderr}")
    if proc.returncode != 0:
        pytest.fail(f"{shlex.join(cmd)} exited {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")
    return proc


def make_client() -> httpx.Client:
    token = env_value("SEMASCOPE_API_TOKEN")
    if not token:
        pytest.exit("no SEMASCOPE_API_TOKEN in the environment or the env file", returncode=2)
    return httpx.Client(
        base_url=base_url() + "/api",
        headers={"Authorization": f"Bearer {token}"},
        verify=False,  # noqa: S501 - Caddy's internal CA on a throwaway stack
        timeout=120.0,
    )


def compose(*args: str, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    """`docker compose` for this stack (COMPOSE_PROJECT_NAME and COMPOSE_FILE from the env)."""

    cmd = ["docker", "compose", *args]
    return subprocess.run(  # noqa: S603 - fixed program, arguments from the tests
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False
    )


def service_url(service: str, port: int) -> str:
    """http://127.0.0.1:<published port> of a test service in tests/integration/stack/compose.yaml.

    Skips when the stack runs without that file, unless SEMASCOPE_IT_REQUIRE_FIXTURES=1
    (scripts/integration.sh sets it), where a missing service fails the test.
    """

    proc = compose("port", service, str(port), timeout=60)
    published = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if proc.returncode != 0 or not published or published.endswith(":0"):
        message = f"no {service} service on this stack ({proc.stderr.strip()[:200]})"
        if os.environ.get("SEMASCOPE_IT_REQUIRE_FIXTURES") == "1":
            pytest.fail(message)
        pytest.skip(message + "; layer tests/integration/stack/compose.yaml")
    host, _, mapped = published.rpartition(":")
    return f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{mapped}"  # noqa: S104


def run_task(name: str) -> None:
    """Queue a Celery task by name on the stack's worker (instead of waiting for the beat)."""

    code = (
        "from oss_server.celery import app; "
        f"app.send_task({name!r}, queue='io'); print('queued {name}')"
    )
    proc = compose("exec", "-T", "server", "oss-manage", "shell", "-c", code, timeout=120)
    if proc.returncode != 0:
        pytest.fail(f"could not queue {name}: {proc.stdout}{proc.stderr}")


def doc_id(collection_slug: str, uri: str) -> str:
    """A document's id, as oss_server.catalogue.models.document_id computes it."""

    import hashlib

    return hashlib.sha256(f"{collection_slug}\n{uri}".encode()).hexdigest()[:32]


def ensure_collection(api: httpx.Client, slug: str, profile: str = "fast") -> None:
    if api.get(f"/collections/{slug}").status_code == 404:
        api.post("/collections", json={"slug": slug, "profile": profile}).raise_for_status()


def add_source(
    api: httpx.Client,
    slug: str,
    name: str,
    connector: str,
    settings: dict[str, Any],
    *,
    schedule: str = "",
    run: bool = True,
) -> dict[str, Any]:
    """Create (or find) a source and, unless run=False, start a run of it; returns the source."""

    resp = api.post(
        f"/collections/{slug}/sources",
        json={"name": name, "connector": connector, "settings": settings, "schedule": schedule},
    )
    if resp.status_code == 409:
        source: dict[str, Any] = next(
            s for s in api.get(f"/collections/{slug}/sources").json() if s["name"] == name
        )
    else:
        assert resp.status_code == 201, resp.text
        source = resp.json()
    if run:
        api.post(f"/sources/{source['id']}/run").raise_for_status()
    return source


def source_settled(api: httpx.Client, slug: str, source_id: int) -> dict[str, Any] | None:
    """The source once its run has finished (idle after a run, or failed)."""

    for s in api.get(f"/collections/{slug}/sources").raise_for_status().json():
        if s["id"] == source_id and (
            s["status"] == "failed" or (s["status"] == "idle" and s["last_run_at"])
        ):
            return dict(s)
    return None


def documents_settled(
    api: httpx.Client, slug: str, uris: list[str]
) -> dict[str, dict[str, Any]] | None:
    """The documents for these URIs once none is queued or processing (None until then)."""

    out: dict[str, dict[str, Any]] = {}
    for uri in uris:
        resp = api.get(f"/documents/{doc_id(slug, uri)}")
        if resp.status_code == 404:
            return None
        doc = resp.raise_for_status().json()
        if doc["status"] in ("queued", "processing"):
            return None
        out[uri] = doc
    return out


def search_uris(api: httpx.Client, slug: str, q: str = "*:*") -> set[str]:
    body = {"q": q, "collection": slug, "page_size": 100, "facets": []}
    hits = api.post("/search", json=body).raise_for_status().json()["hits"]
    return {h["uri"] for h in hits}


def indexing_done(api: httpx.Client, expected: int) -> dict[str, Any] | None:
    stats: dict[str, Any] = api.get("/stats").raise_for_status().json()
    docs = stats["documents"]
    busy = docs.get("queued", 0) + docs.get("processing", 0)
    settled = docs.get("indexed", 0) + docs.get("failed", 0)
    # Postgres marks a document indexed before Solr's soft commit makes it visible
    visible = (stats.get("solr_documents") or 0) >= docs.get("indexed", 0)
    return stats if not busy and settled >= expected and visible else None
