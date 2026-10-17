"""What `oss doctor` checks: every service and store, the Solr collections, versions, packs.

Each check returns a `Row`; a row that is not `ok` makes `doctor` exit 1. The HTTP
checks take an httpx client and the store checks a connect function, so tests can
replace both.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from oss_core import __version__

SOLR_COLLECTIONS = ("documents", "entities", "chunks")
BASE_PACK = "semascope-base"
# the Django apps whose latest applied migration `doctor` reports (the schema versions)
MIGRATION_APPS = ("catalogue", "knowledge", "curation")


@dataclass(frozen=True)
class Row:
    name: str
    endpoint: str
    status: str
    ok: bool


def _failure(exc: Exception) -> str:
    detail = str(exc).strip().splitlines()[0][:120] if str(exc).strip() else ""
    return f"unreachable ({type(exc).__name__}{': ' + detail if detail else ''})"


def solr_auth(settings: Any) -> dict[str, str]:
    """The basic-auth header the stack's Solr expects (see deploy/solr/init-security.sh)."""

    password = settings.solr_password.get_secret_value() if settings.solr_password else ""
    token = base64.b64encode(f"{settings.solr_user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _get(
    client: httpx.Client, name: str, url: str, headers: dict[str, str] | None = None
) -> tuple[httpx.Response | None, Row | None]:
    """The response, or the failed row when the service does not answer with a success."""

    try:
        resp = client.get(url, headers=headers or {})
    except httpx.HTTPError as exc:
        return None, Row(name, url, _failure(exc), False)
    if resp.status_code >= 400:
        return None, Row(name, url, f"{resp.status_code}", False)
    return resp, None


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def server_row(settings: Any, client: httpx.Client) -> tuple[Row, list[str]]:
    url = f"{settings.semascope_api_url.rstrip('/')}/healthz"
    resp, failed = _get(client, "server", url)
    if failed:
        return failed, []
    version = str(_json(resp).get("version") or "?") if resp is not None else "?"
    notes = []
    if version not in ("?", __version__):
        notes.append(f"the server runs {version} and this CLI is {__version__}")
    return Row("server", url, f"ok {version}", True), notes


def solr_rows(settings: Any, client: httpx.Client) -> list[Row]:
    """Solr itself (with its version) and the three collections the product needs."""

    base = settings.solr_url.rstrip("/")
    auth = solr_auth(settings)
    url = f"{base}/admin/info/system?wt=json"
    resp, failed = _get(client, "solr", url, auth)
    if failed:
        return [failed]
    info = _json(resp) if resp is not None else {}
    lucene = info.get("lucene") or {}
    version = lucene.get("solr-spec-version", "?") if isinstance(lucene, dict) else "?"
    # the Collections API, aliases and the uploaded configsets need SolrCloud (embedded ZooKeeper)
    mode = str(info.get("mode") or "solrcloud")
    if mode != "solrcloud":
        return [Row("solr", url, f"{version} in {mode} mode; Semascope needs solrcloud", False)]
    rows = [Row("solr", url, f"ok {version} solrcloud", True)]
    url = f"{base}/admin/collections?action=LIST&wt=json"
    resp, failed = _get(client, "solr collections", url, auth)
    if failed:
        return [*rows, failed]
    have = set(_json(resp).get("collections") or []) if resp is not None else set()
    missing = [c for c in SOLR_COLLECTIONS if c not in have]
    status = f"missing {', '.join(missing)}" if missing else "ok " + ", ".join(SOLR_COLLECTIONS)
    return [*rows, Row("solr collections", url, status, not missing)]


def tika_row(settings: Any, client: httpx.Client) -> Row:
    url = f"{settings.tika_url.rstrip('/')}/version"
    resp, failed = _get(client, "tika", url)
    if failed:
        return failed
    text = resp.text.strip() if resp is not None else ""
    return Row("tika", url, f"ok {text.removeprefix('Apache Tika ')}".strip(), True)


def neo4j_row(settings: Any, client: httpx.Client) -> Row:
    url = settings.neo4j_uri.replace("bolt://", "http://").replace(":7687", ":7474")
    resp, failed = _get(client, "neo4j", url)
    if failed:
        return failed
    version = _json(resp).get("neo4j_version", "") if resp is not None else ""
    return Row("neo4j", url, f"ok {version}".strip(), True)


def postgres_row(settings: Any, connect: Callable[[str], Any] | None = None) -> Row:
    """Connects with the application's credentials; reports the server and schema versions."""

    url = settings.database_url
    endpoint = url.rpartition("@")[2] if "@" in url else url
    if url.startswith("sqlite"):
        return Row("postgres", endpoint, "not used (sqlite)", True)
    if connect is None:
        try:
            import psycopg  # noqa: PLC0415 - only the image has it; doctor runs there
        except ImportError:
            return Row("postgres", endpoint, "not checked (psycopg missing)", True)
        connect = lambda u: psycopg.connect(u, connect_timeout=5)  # noqa: E731
    try:
        with connect(url) as conn:
            version = (conn.execute("SHOW server_version").fetchone() or ["?"])[0]
            rows = conn.execute(
                "SELECT app, max(name) FROM django_migrations WHERE app = ANY(%s) GROUP BY app",
                (list(MIGRATION_APPS),),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 - any driver error is a failed check
        return Row("postgres", endpoint, _failure(exc), False)
    latest = dict(rows)
    missing = [app for app in MIGRATION_APPS if app not in latest]
    if missing:
        return Row("postgres", endpoint, f"{version}; not migrated: {', '.join(missing)}", False)
    schema = ", ".join(f"{app} {latest[app].split('_', 1)[0]}" for app in MIGRATION_APPS)
    return Row("postgres", endpoint, f"ok {version}; schema {schema}", True)


def valkey_row(settings: Any, connect: Callable[[str], Any] | None = None) -> Row:
    """Connects with the password; reports the version and the queue lengths."""

    url = settings.valkey_url
    endpoint = url.rpartition("@")[2] if "@" in url else url.partition("//")[2]
    if connect is None:
        try:
            import redis  # noqa: PLC0415 - Celery's broker client, in the image
        except ImportError:
            return Row("valkey", endpoint, "not checked (redis client missing)", True)
        connect = lambda u: redis.Redis.from_url(u, socket_timeout=5)  # noqa: E731
    try:
        client: Any = connect(url)
        info: dict[str, Any] = client.info("server")
        queued = {q: int(client.llen(q)) for q in ("cpu", "io")}
    except Exception as exc:  # noqa: BLE001 - any client error is a failed check
        return Row("valkey", endpoint, _failure(exc), False)
    version = info.get("valkey_version") or info.get("redis_version") or "?"
    queues = ", ".join(f"{q} {n}" for q, n in queued.items())
    return Row("valkey", endpoint, f"ok {version}; queued {queues}", True)


def packs_row(settings: Any, client: httpx.Client) -> Row | None:
    """The base pack is active (the server imports it at start); needs the API token."""

    token = settings.semascope_api_token.get_secret_value() if settings.semascope_api_token else ""
    if not token:
        return None
    url = f"{settings.semascope_api_url.rstrip('/')}/api/knowledge/packs?status=active"
    try:
        resp = client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        return Row("packs", "/api/knowledge/packs", _failure(exc), False)
    if resp.status_code >= 400:
        return Row("packs", "/api/knowledge/packs", f"{resp.status_code}", False)
    try:
        packs = resp.json()
    except ValueError:
        packs = []
    active = sorted(
        f"{p['pack_id']}@{p['version']}" for p in packs if isinstance(p, dict) and "pack_id" in p
    )
    has_base = any(a.startswith(f"{BASE_PACK}@") for a in active)
    status = ("ok " if has_base else f"{BASE_PACK} not active; active: ") + (
        ", ".join(active) or "none"
    )
    return Row("packs", "/api/knowledge/packs", status, has_base)
