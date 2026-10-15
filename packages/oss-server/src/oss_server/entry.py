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


def _telemetry() -> None:
    """Export traces and metrics over OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set."""

    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        from opentelemetry.instrumentation.django import DjangoInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("OTEL endpoint set but the opentelemetry packages are not installed")
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "semascope")})
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    DjangoInstrumentor().instrument()
    CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]
    HTTPXClientInstrumentor().instrument()
    log.info("telemetry: exporting to %s", os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"])


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
    from oss_server.celery import app

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
