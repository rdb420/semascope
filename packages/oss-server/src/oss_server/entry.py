"""Console scripts: oss-server, oss-worker, oss-beat, oss-manage; and the containers'
health checks (`python -m oss_server.entry health worker|beat`)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

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
    # Without its collections the server cannot answer a search, so it does not start: the
    # container exits, compose restarts it, and its health check fails until Solr is ready.
    try:
        created = bootstrap_solr()
    except Exception as exc:
        log.error("Solr bootstrap failed: %s", exc)
        raise SystemExit(f"Solr bootstrap failed: {exc}") from exc
    if created:
        log.info("created Solr collections: %s", created)
    try:
        from oss_server.services import import_bundled_packs

        log.info("packs: %s", import_bundled_packs())
    except Exception:  # noqa: BLE001 - search works without it; `oss doctor` reports it
        log.exception("bundled pack import failed; `oss doctor` shows the base pack missing")
    from oss_server.knowledge.synonyms import sync_synonyms

    sync_synonyms()  # the upload above reset synonyms_packs.txt; never raises (WS-4)


def prepare_in_child() -> None:
    """Run `prepare` in a short-lived process, so the server's master never opens the stores.

    The bundled pack import opens the embedded pack store (RocksDB), whose threads do not
    survive gunicorn's fork; a master that held it could leave its workers hanging.
    """

    import subprocess

    code = (
        "import logging; from oss_server import entry; "
        "logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s'); "
        "entry._django(); entry.prepare()"
    )
    subprocess.run([sys.executable, "-c", code], check=True)  # noqa: S603 - fixed argv


def server() -> None:
    parser = argparse.ArgumentParser(prog="oss-server")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container-internal
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    prepare_in_child()
    _django()
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


MIB = 1024 * 1024
MAIN_PROCESS_MIB = 512  # the worker's main process: no models, the broker connection
TASK_GROWTH_MIB = 768  # what one large document adds while a pool process works on it
MIN_CHILD_MIB = 1536  # a fresh pool process holds about 1 GB of models before its first task


def memory_limit_bytes() -> int | None:
    """The container's memory cap: the cgroup limit, else SEMASCOPE_WORKER_MEMORY (e.g. `6g`)."""

    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text(encoding="ascii").strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < 1 << 60:  # cgroup v1 reports "no limit" as a huge number
            return int(raw)
    value = os.environ.get("SEMASCOPE_WORKER_MEMORY", "").strip().lower()
    units = {"k": 1024, "m": MIB, "g": 1024 * MIB}
    if value[:-1].isdigit() and value[-1:] in units:
        return int(value[:-1]) * units[value[-1]]
    return int(value) if value.isdigit() else None


def max_memory_per_child_mib(limit_bytes: int | None, concurrency: int) -> int | None:
    """Resident size above which a pool process is replaced after its current task.

    Celery checks after each task, so a process may still grow by one document's worth
    (`TASK_GROWTH_MIB`) before it is replaced: the share of the cap left after the main
    process, less that growth. With `oss init --size`: small (1 process, 4 GB) 2,816 MiB,
    medium (2, 6 GB) 2,048 MiB, large (4, 12 GB) 2,176 MiB. Pool processes grew by about
    1.2 GB over 1,000 documents on rc.2 (docs/quality.md), so each is replaced after some
    hundreds of documents, well before the cap.
    """

    if not limit_bytes:
        return None
    share = (limit_bytes // MIB - MAIN_PROCESS_MIB) // max(1, concurrency) - TASK_GROWTH_MIB
    if share < MIN_CHILD_MIB:
        log.warning(
            "worker memory cap %d MiB is small for %d processes; replacing them at %d MiB",
            limit_bytes // MIB,
            concurrency,
            MIN_CHILD_MIB,
        )
    return max(share, MIN_CHILD_MIB)


def worker() -> None:
    parser = argparse.ArgumentParser(prog="oss-worker")
    parser.add_argument("--queues", default="io,cpu")
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    from celery.signals import worker_process_init

    from oss_core.config import get_settings
    from oss_server.celery import app

    worker_process_init.connect(_worker_process_telemetry, weak=False)
    argv = ["worker", "-Q", args.queues, "-c", str(args.concurrency), "--loglevel=INFO"]
    cap = get_settings().semascope_worker_max_memory_mb or max_memory_per_child_mib(
        memory_limit_bytes(), args.concurrency
    )
    if cap:
        argv.append(f"--max-memory-per-child={cap * 1024}")  # Celery counts KiB
        log.info("pool processes are replaced above %d MiB resident", cap)
    sys.exit(app.worker_main(argv))


def beat_schedule_path() -> Path:
    from oss_core.config import get_settings

    return get_settings().semascope_data_dir / "celerybeat-schedule"


def beat() -> None:
    _django()
    from oss_server.celery import app

    schedule = beat_schedule_path()
    schedule.parent.mkdir(parents=True, exist_ok=True)
    sys.exit(app.start(["beat", "--loglevel=INFO", "--schedule", str(schedule)]))


def manage() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oss_server.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


# Beat writes its schedule file after every task it sends: `tick` every 60 s, `recover` every
# 120 s (oss_server.celery). Five minutes without a write means it is stuck.
BEAT_STALE_S = 300


def health(role: str) -> int:
    """Health check of a worker or beat container; 0 when healthy (Docker's convention).

    worker: this container's Celery node answers a ping over the broker within 10 s, which
    needs the main process alive and connected to Valkey. beat: the schedule file was
    written in the last five minutes.
    """

    import socket
    import time

    if role == "beat":
        schedule = beat_schedule_path()
        # the dbm module may add a suffix (.db, .dat) to the shelve's file name
        written = [f.stat().st_mtime for f in schedule.parent.glob(f"{schedule.name}*")]
        if not written:
            print(f"no beat schedule at {schedule} yet")
            return 1
        age = time.time() - max(written)
        print(f"schedule written {age:.0f} s ago")
        return 0 if age < BEAT_STALE_S else 1
    if role == "worker":
        from oss_server.celery import app

        node = f"celery@{socket.gethostname()}"
        replies = app.control.ping(destination=[node], timeout=10.0)
        ok = any(node in reply for reply in replies or [])
        print(f"{node}: {'pong' if ok else 'no answer'}")
        return 0 if ok else 1
    print(f"unknown role {role!r} (worker or beat)")
    return 2


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "health":
        sys.exit(health(sys.argv[2]))
    sys.exit("usage: python -m oss_server.entry health worker|beat")
