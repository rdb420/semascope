"""Source schedules: a cron expression or `every <n>m|h|d` per source, checked once a minute."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from croniter import croniter

_EVERY = re.compile(r"^every\s+(\d+)\s*(m|min|minutes?|h|hours?|d|days?)$", re.I)


def next_run(schedule: str, after: datetime) -> datetime | None:
    """When a schedule next fires after `after`; None when the schedule is empty or invalid."""

    spec = schedule.strip()
    if not spec:
        return None
    m = _EVERY.match(spec)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()[0]
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
        return after + delta
    if croniter.is_valid(spec):
        fired: datetime = croniter(spec, after).get_next(datetime)
        return fired if fired.tzinfo else fired.replace(tzinfo=UTC)
    return None


def is_due(schedule: str, last_run_at: datetime | None, now: datetime) -> bool:
    if not schedule.strip():
        return False
    if last_run_at is None:
        return True
    due = next_run(schedule, last_run_at)
    return due is not None and due <= now
