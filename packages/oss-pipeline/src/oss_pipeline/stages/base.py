"""Helpers shared by stages: timing, stage-run records and retries of transient failures."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from oss_core.contracts import Stage, StageContext, StageRun, WorkingDocument
from oss_pipeline.retry import backoff, is_transient

STAGE_RETRIES = 2  # extra attempts of a stage after a transient failure (settings: stage_retries)
STAGE_BACKOFF_S = 2.0  # first wait; doubles each time (settings: stage_retry_backoff_s)
TRANSIENT_FAILURES = "transient_failures"  # doc.fields: stages that failed transiently


def run_stage(stage: Stage, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
    """Run one stage and append its StageRun, converting exceptions into a failed run.

    A transient failure (`oss_pipeline.retry.is_transient`: a service down, slow or
    overloaded) is retried in place, `stage_retries` times (default 2) after 2 s and
    4 s, each attempt on a fresh copy of the document. A stage that still fails
    transiently is listed in `doc.fields["transient_failures"]`, which the task uses
    to retry the whole document later. Permanent failures are not retried.
    """

    retries = max(0, int(ctx.settings.get("stage_retries", STAGE_RETRIES)))
    base = float(ctx.settings.get("stage_retry_backoff_s", STAGE_BACKOFF_S))
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    outcome: Literal["ok", "skipped", "failed"]
    message: str | None
    attempt = 0
    while True:
        attempt += 1
        try:
            doc = stage.run(doc.model_copy(deep=True) if retries else doc, ctx)
            outcome, message = "ok", None
        except SkipStageError as skip:
            outcome, message = "skipped", str(skip)
        except Exception as exc:  # noqa: BLE001 - a stage failure is data, not a crash
            outcome, message = "failed", f"{type(exc).__name__}: {exc}"[:500]
            if is_transient(exc):
                if attempt <= retries:
                    time.sleep(backoff(attempt, base))
                    continue
                transient = list(doc.fields.get(TRANSIENT_FAILURES) or [])
                doc.fields[TRANSIENT_FAILURES] = [*transient, stage.name]
                if attempt > 1:
                    message = f"{message} (after {attempt} attempts)"[:500]
        break
    doc.stage_runs.append(
        StageRun(
            stage=stage.name,
            version=stage.version,
            started_at=started,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            outcome=outcome,
            message=message,
        )
    )
    return doc


class SkipStageError(Exception):
    """Raise inside a stage to record it as skipped with a reason."""


def run_profile(
    stages: list[Stage],
    doc: WorkingDocument,
    ctx: StageContext,
    *,
    stop_on_failure: tuple[str, ...] = ("fetch", "extract"),
    on_stage: Callable[[str, WorkingDocument], None] | None = None,
) -> WorkingDocument:
    for stage in stages:
        doc = run_stage(stage, doc, ctx)
        if on_stage:
            on_stage(stage.name, doc)
        last = doc.stage_runs[-1]
        if last.outcome == "failed" and stage.name in stop_on_failure:
            break
    return doc


def solr_key(ctx: StageContext) -> tuple[str, ...]:
    """What a stage's Solr client depends on: a stage instance lives as long as its worker
    process, so it builds a new client only when these settings change."""

    s = ctx.settings
    return tuple(str(s.get(k) or "") for k in ("solr_url", "solr_user", "solr_password"))
