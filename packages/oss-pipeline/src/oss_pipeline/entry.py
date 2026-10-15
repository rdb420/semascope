"""Console scripts for the worker and the scheduler."""

from __future__ import annotations

import argparse
import sys


def worker() -> None:
    parser = argparse.ArgumentParser(prog="oss-worker")
    parser.add_argument("--queues", default="io,cpu,ocr,nlp,graph")
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    from oss_pipeline.celery_app import app

    sys.exit(
        app.worker_main(
            ["worker", "-Q", args.queues, "-c", str(args.concurrency), "--loglevel=INFO"]
        )
    )


def beat() -> None:
    from oss_core.config import get_settings
    from oss_pipeline.celery_app import app

    schedule = get_settings().semascope_data_dir / "celerybeat-schedule"
    schedule.parent.mkdir(parents=True, exist_ok=True)
    sys.exit(app.start(["beat", "--loglevel=INFO", "--schedule", str(schedule)]))
