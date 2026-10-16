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


def indexing_done(api: httpx.Client, expected: int) -> dict[str, Any] | None:
    stats: dict[str, Any] = api.get("/stats").raise_for_status().json()
    docs = stats["documents"]
    busy = docs.get("queued", 0) + docs.get("processing", 0)
    settled = docs.get("indexed", 0) + docs.get("failed", 0)
    # Postgres marks a document indexed before Solr's soft commit makes it visible
    visible = (stats.get("solr_documents") or 0) >= docs.get("indexed", 0)
    return stats if not busy and settled >= expected and visible else None
