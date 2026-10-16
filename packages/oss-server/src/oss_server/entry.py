"""Console scripts: oss-server, oss-worker, oss-beat, oss-manage."""

from __future__ import annotations

import argparse
import logging
import os
import sys

log = logging.getLogger("semascope")


def _django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oss_server.settings")
    import django

    django.setup()
    _telemetry()


_TELEMETRY_STARTED = False


def _telemetry(*, django: bool = True) -> bool:
    """Export traces and metrics over OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set.

    Sets a tracer provider (spans in batches) and a meter provider (metrics every
    `OTEL_METRIC_EXPORT_INTERVAL` ms, 60 s by default), both over OTLP/HTTP, then
    instruments Celery and httpx, and Django in the web process. Runs once per
    process; a Celery worker calls it in every pool process after the fork
    (`worker_process_init`), because exporter threads do not survive a fork.
    Returns whether telemetry was started.
    """

    global _TELEMETRY_STARTED
    if _TELEMETRY_STARTED or not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTEL endpoint set but the opentelemetry packages are not installed")
        return False
    resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "semascope")})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
        )
    )
    if django:
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        DjangoInstrumentor().instrument()
    CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]
    HTTPXClientInstrumentor().instrument()
    _TELEMETRY_STARTED = True
    log.info("telemetry: traces and metrics to %s", os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"])
    return True


def _worker_process_telemetry(**_: object) -> None:
    """Celery `worker_process_init` receiver: start telemetry in a pool process."""

    _telemetry(django=False)


def prepare() -> None:
    """Migrate, collect static files, create the first admin, bootstrap Solr."""

    from django.contrib.auth.models import User
    from django.core.management import call_command

    from oss_core.config import get_settings
    from oss_server.api.auth import issue_token
    from oss_server.services import bootstrap_solr

    call_command("migrate", interactive=False, verbosity=0)
    call_command("collectstatic", interactive=False, verbosity=0)
    s = get_settings()
    if not User.objects.exists() and s.semascope_admin_password:
        admin = User.objects.create_superuser(
            "admin", password=s.semascope_admin_password.get_secret_value()
        )
        log.info("created admin user")
        if s.semascope_api_token:
            from oss_server.catalogue.models import ApiToken

            key = s.semascope_api_token.get_secret_value()
            ApiToken.objects.get_or_create(
                key_hash=ApiToken.hash_key(key),
                defaults={"name": "cli", "prefix": key[:8], "user": admin},
            )
        else:
            log.info("api token for the cli: %s", issue_token(admin, "cli"))
    try:
        created = bootstrap_solr()
        if created:
            log.info("created Solr collections: %s", created)
    except Exception as exc:  # noqa: BLE001
        log.error("Solr bootstrap failed: %s", exc)
        return
    try:
        from oss_server.services import import_bundled_packs

        log.info("packs: %s", import_bundled_packs())
    except Exception:  # noqa: BLE001
        log.exception("bundled pack import failed")


def server() -> None:
    parser = argparse.ArgumentParser(prog="oss-server")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container-internal
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    _django()
    prepare()
    if args.reload:
        from django.core.management import call_command

        call_command("runserver", f"{args.host}:{args.port}")
        return
    import gunicorn.app.wsgiapp  # noqa: F401 - ensure installed

    sys.argv = [
        "gunicorn",
        "oss_server.wsgi:application",
        "--bind",
        f"{args.host}:{args.port}",
        "--workers",
        str(args.workers),
        "--access-logfile",
        "-",
    ]
    from gunicorn.app.wsgiapp import run

    run()


def worker() -> None:
    parser = argparse.ArgumentParser(prog="oss-worker")
    parser.add_argument("--queues", default="io,cpu,ocr,nlp,graph")
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    from celery.signals import worker_process_init

    from oss_server.celery import app

    worker_process_init.connect(_worker_process_telemetry, weak=False)
    sys.exit(
        app.worker_main(
            ["worker", "-Q", args.queues, "-c", str(args.concurrency), "--loglevel=INFO"]
        )
    )


def beat() -> None:
    _django()
    from oss_core.config import get_settings
    from oss_server.celery import app

    schedule = get_settings().semascope_data_dir / "celerybeat-schedule"
    schedule.parent.mkdir(parents=True, exist_ok=True)
    sys.exit(app.start(["beat", "--loglevel=INFO", "--schedule", str(schedule)]))


def manage() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oss_server.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
