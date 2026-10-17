"""`oss doctor` checks PostgreSQL, Valkey, the Solr collections, versions and the base pack."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import oss_cli.main as cli
from oss_cli import health
from oss_core import __version__
from oss_core.config import Settings

runner = CliRunner()
RealClient = httpx.Client  # the doctor test replaces httpx.Client with a stack


def settings(**values: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://semascope:pw@postgres:5432/semascope",
        "valkey_url": "redis://:pw@valkey:6379/0",
        "solr_url": "http://solr:8983/solr",
        "tika_url": "http://tika:9998",
        "neo4j_uri": "bolt://neo4j:7687",
        "semascope_api_url": "http://server:8000",
    }
    return Settings.model_validate({**base, **values})


def stack(collections: list[str], packs: list[dict[str, str]] | None = None) -> httpx.Client:
    """A stack that answers like the real services do."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/healthz"):
            return httpx.Response(200, json={"status": "ok", "version": __version__})
        if "/admin/info/system" in url:
            assert request.headers["Authorization"].startswith("Basic ")
            body = {"mode": "solrcloud", "lucene": {"solr-spec-version": "10.0.0"}}
            return httpx.Response(200, json=body)
        if "/admin/collections" in url:
            return httpx.Response(200, json={"collections": collections})
        if url.endswith("/version"):
            return httpx.Response(200, text="Apache Tika 3.3.0")
        if url.startswith("http://neo4j:7474"):
            return httpx.Response(200, json={"neo4j_version": "5.26.0"})
        if "/api/knowledge/packs" in url:
            return httpx.Response(200, json=packs or [])
        return httpx.Response(404)

    return RealClient(transport=httpx.MockTransport(handler))


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchone(self) -> tuple[Any, ...]:
        return self.rows[0]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class FakePostgres:
    def __init__(self, migrations: list[tuple[str, str]]) -> None:
        self.migrations = migrations

    def __enter__(self) -> FakePostgres:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        if sql.startswith("SHOW"):
            return FakeCursor([("17.6",)])
        return FakeCursor(self.migrations)


MIGRATED = [
    ("catalogue", "0014_document_attempts"),
    ("knowledge", "0006_pack_source"),
    ("curation", "0005_x"),
]


def test_solr_collections_must_all_exist() -> None:
    rows = health.solr_rows(settings(), stack(["documents", "entities", "chunks"]))
    assert [r.ok for r in rows] == [True, True]
    assert rows[0].status == "ok 10.0.0 solrcloud"
    rows = health.solr_rows(settings(), stack(["documents"]))
    assert rows[1].status == "missing entities, chunks" and not rows[1].ok


def test_postgres_reports_server_and_schema_versions() -> None:
    row = health.postgres_row(settings(), lambda url: FakePostgres(MIGRATED))
    assert row.ok and row.status == "ok 17.6; schema catalogue 0014, knowledge 0006, curation 0005"
    assert "pw" not in row.endpoint
    row = health.postgres_row(settings(), lambda url: FakePostgres(MIGRATED[:1]))
    assert not row.ok and "not migrated: knowledge, curation" in row.status


def test_postgres_refusing_the_password_fails() -> None:
    def refuse(url: str) -> Any:
        raise RuntimeError('password authentication failed for user "semascope"')

    row = health.postgres_row(settings(), refuse)
    assert not row.ok and "password authentication failed" in row.status


class FakeValkey:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def info(self, section: str) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("NOAUTH Authentication required.")
        return {"valkey_version": "8.1.4", "redis_version": "7.2.4"}

    def llen(self, queue: str) -> int:
        return {"cpu": 3, "io": 0}[queue]


def test_valkey_reports_version_and_queues() -> None:
    row = health.valkey_row(settings(), lambda url: FakeValkey())
    assert row.ok and row.status == "ok 8.1.4; queued cpu 3, io 0"
    assert row.endpoint == "valkey:6379/0"
    row = health.valkey_row(settings(), lambda url: FakeValkey(fail=True))
    assert not row.ok and "NOAUTH" in row.status


def test_the_base_pack_must_be_active() -> None:
    token = settings(semascope_api_token=SecretStr("oss_t"))
    base = {"pack_id": "semascope-base", "version": "1.0.0"}
    assert health.packs_row(token, stack([], [base])) == health.Row(
        "packs", "/api/knowledge/packs", "ok semascope-base@1.0.0", True
    )
    row = health.packs_row(token, stack([], []))
    assert row is not None and not row.ok and row.status.startswith("semascope-base not active")
    assert health.packs_row(settings(), stack([])) is None  # no token: not checked


def test_server_version_mismatch_is_a_note() -> None:
    row, notes = health.server_row(settings(), stack([]))
    assert row.ok and row.status == f"ok {__version__}" and notes == []


def test_doctor_fails_on_a_missing_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings())
    monkeypatch.setattr(health, "postgres_row", lambda s: health.Row("postgres", "", "ok", True))
    monkeypatch.setattr(health, "valkey_row", lambda s: health.Row("valkey", "", "ok", True))
    monkeypatch.setattr(cli, "vector_report", lambda s, c: ("ok", True, []))
    monkeypatch.setattr(httpx, "Client", lambda **kw: stack(["documents", "chunks"]))
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "missing entities" in result.stdout and "failed: solr collections" in result.stdout
    monkeypatch.setattr(httpx, "Client", lambda **kw: stack(["documents", "entities", "chunks"]))
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.stdout
