"""GET /suggest, /healthz, CSRF origins from the HTTPS port, and worker telemetry."""

from __future__ import annotations

import io
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_core import __version__
from oss_server import entry
from oss_server.api import root
from oss_server.api.auth import issue_token
from oss_server.settings import csrf_trusted_origins


class FakeSolr:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.params = params
        return {
            "response": {
                "docs": [
                    {"title_txt": "The Odyssey"},
                    {"title_txt": "The Odyssey"},
                    {"title_txt": "Odes"},
                    {},
                ]
            }
        }


def test_suggest_query_escapes_and_prefixes_the_last_word() -> None:
    assert root.suggest_query("odys") == "odys*"
    assert root.suggest_query("  the  odys ") == "the AND odys*"
    assert root.suggest_query('a:b "c') == 'a\\:b AND \\"c*'
    assert root.suggest_query("   ") == ""


@pytest.mark.django_db
def test_suggest_is_routed(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    solr = FakeSolr()
    monkeypatch.setattr(root, "solr_client", lambda: solr)
    from oss_server.catalogue.models import Collection, CollectionRole

    user = User.objects.create_user("sug", password="v" * 16)
    texts = Collection.objects.create(slug="texts", name="texts")
    CollectionRole.objects.create(user=user, collection=texts, role="reader")
    auth = {"Authorization": f"Bearer {issue_token(user, 't')}"}
    assert client.get("/api/suggest?q=odys").status_code == 401
    r = client.get("/api/suggest?q=odys&collection=texts", headers=auth)
    assert r.status_code == 200, r.content
    assert r.json() == {"suggestions": ["The Odyssey", "Odes"]}
    assert solr.params["q"] == "odys*" and solr.params["qf"] == "title_txt"
    assert solr.params["fq"] == ['collection_s:"texts"']
    assert client.get("/api/suggest?q=", headers=auth).json() == {"suggestions": []}


def test_healthz_reports_the_version(client: Client) -> None:
    body = client.get("/healthz").json()
    assert body == {"status": "ok", "version": __version__}


def test_csrf_origins_follow_the_https_port() -> None:
    assert csrf_trusted_origins("search.example", 8463) == [
        "https://search.example",
        "https://search.example:8463",
    ]
    assert csrf_trusted_origins("search.example", 443) == ["https://search.example"]


def test_worker_starts_telemetry_in_each_pool_process(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from celery.signals import worker_process_init

    from oss_server.celery import app

    started: list[bool] = []
    monkeypatch.setattr(entry, "_telemetry", lambda *, django=True: started.append(django))
    monkeypatch.setattr(app, "worker_main", lambda argv: 0)
    monkeypatch.setattr(sys, "argv", ["oss-worker"])
    with pytest.raises(SystemExit):
        entry.worker()
    try:
        worker_process_init.send(sender=None)
        assert started == [False], "the pool process instruments Celery and httpx, not Django"
    finally:
        worker_process_init.disconnect(entry._worker_process_telemetry)


def test_telemetry_sets_trace_and_metric_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http import metric_exporter, trace_exporter
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    providers: dict[str, Any] = {}
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4318")
    monkeypatch.setattr(entry, "_TELEMETRY_STARTED", False)
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", InMemorySpanExporter)
    monkeypatch.setattr(
        metric_exporter, "OTLPMetricExporter", lambda: ConsoleMetricExporter(out=io.StringIO())
    )
    monkeypatch.setattr(trace, "set_tracer_provider", lambda p: providers.update(trace=p))
    monkeypatch.setattr(metrics, "set_meter_provider", lambda p: providers.update(metrics=p))
    monkeypatch.setattr(CeleryInstrumentor, "instrument", lambda self, **kw: None)
    monkeypatch.setattr(HTTPXClientInstrumentor, "instrument", lambda self, **kw: None)
    assert entry._telemetry(django=False) is True
    assert isinstance(providers["trace"], TracerProvider)
    assert isinstance(providers["metrics"], MeterProvider)
    assert entry._telemetry(django=False) is False, "once per process"
    providers["metrics"].shutdown()
    providers["trace"].shutdown()
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    monkeypatch.setattr(entry, "_TELEMETRY_STARTED", False)
    assert entry._telemetry() is False, "no endpoint, no telemetry"


@pytest.mark.django_db
def test_stored_documents_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from oss_core.contracts import StageRun, WorkingDocument
    from oss_server.catalogue.models import Collection, Document, Source
    from oss_server.pipeline import tasks

    recorded: list[tuple[str, Any, dict[str, Any]]] = []

    class Instrument:
        def __init__(self, name: str) -> None:
            self.name = name

        def add(self, value: int, attrs: dict[str, Any]) -> None:
            recorded.append((self.name, value, attrs))

        record = add

    monkeypatch.setattr(tasks, "_instruments", lambda: (Instrument("docs"), Instrument("ms")))
    c = Collection.objects.create(slug="m", name="m")
    s = Source.objects.create(collection=c, name="s", connector="filesystem")
    row = Document.objects.create(id="m" * 32, collection=c, source=s, uri="u", content_hash="h")
    work = WorkingDocument(id=row.id, collection="m", source_id="1", uri="u", content_hash="h")
    work.stage_runs = [
        StageRun(
            stage="tag", version="1", started_at=datetime.now(UTC), duration_ms=7, outcome="ok"
        )
    ]
    tasks._store(row, work)
    assert ("docs", 1, {"collection": "m", "status": "failed"}) in recorded
    assert ("ms", 7, {"stage": "tag", "outcome": "ok"}) in recorded
