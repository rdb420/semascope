"""Helpers shared by stages: timing and stage-run records."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from oss_core.contracts import Stage, StageContext, StageRun, WorkingDocument


def run_stage(stage: Stage, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
    """Run one stage and append its StageRun, converting exceptions into a failed run."""

    started = datetime.now(UTC)
    t0 = time.perf_counter()
    outcome: Literal["ok", "skipped", "failed"]
    message: str | None
    try:
        doc = stage.run(doc, ctx)
        outcome, message = "ok", None
    except SkipStageError as skip:
        outcome, message = "skipped", str(skip)
    except Exception as exc:  # noqa: BLE001 - a stage failure is data, not a crash
        outcome, message = "failed", f"{type(exc).__name__}: {exc}"[:500]
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
