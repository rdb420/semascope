"""One run per source at a time, and named leases for tasks that must not overlap.

A source run claims its source by moving it to `running` with a fresh run id, in one
conditional UPDATE: a second run of the same source (a click on "run" while the schedule's
run is crawling, two ticks) finds it `running` and stops without touching anything. A run
deletes documents only while it still holds the source, so a run that `recover` released
after its worker went quiet can never delete what a newer run has seen.

Leases serve tasks that the beat starts every minute and that may run longer than that
(webhook deliveries, the alert check): each takes a named lease with an expiry, and an
overlapping run of the same task skips what is leased.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from oss_server.catalogue.models import Lease, Source


def new_run_id() -> str:
    return secrets.token_hex(8)


def claim_run(source_id: int, now: datetime | None = None) -> str | None:
    """Mark the source `running` for a new run; its run id, or None while another run holds it."""

    run_id = new_run_id()
    claimed = (
        Source.objects.filter(pk=source_id)
        .exclude(status=Source.Status.RUNNING)
        .update(
            status=Source.Status.RUNNING,
            run_id=run_id,
            run_started_at=now or timezone.now(),
            last_error="",
        )
    )
    return run_id if claimed else None


def holds(source_id: int, run_id: str) -> bool:
    """Whether this run still holds the source (`recover` may have released it meanwhile)."""

    return Source.objects.filter(pk=source_id, status=Source.Status.RUNNING, run_id=run_id).exists()


def finish_run(source_id: int, run_id: str, **fields: object) -> bool:
    """Leave `running` with these fields, if this run still holds the source."""

    return bool(
        Source.objects.filter(pk=source_id, status=Source.Status.RUNNING, run_id=run_id).update(
            **fields
        )
    )


def is_running(source: Source) -> bool:
    """For the API: a run of this source is in progress (a new one would be refused)."""

    return Source.objects.filter(pk=source.pk, status=Source.Status.RUNNING).exists()


def take_lease(name: str, seconds: float, holder: str | None = None) -> str | None:
    """Take the named lease for `seconds`; the holder token, or None while someone holds it."""

    token = holder or secrets.token_hex(8)
    now = timezone.now()
    until = now + timedelta(seconds=seconds)
    # an expired lease is taken over in one conditional UPDATE
    if Lease.objects.filter(name=name, expires_at__lte=now).update(holder=token, expires_at=until):
        return token
    try:
        with transaction.atomic():
            Lease.objects.create(name=name, holder=token, expires_at=until)
    except IntegrityError:
        return None  # someone holds it
    return token


def release_lease(name: str, token: str) -> None:
    Lease.objects.filter(name=name, holder=token).delete()


def renew_lease(name: str, token: str, seconds: float) -> bool:
    """Extend a lease this holder still has; False when it expired and was taken over."""

    until = timezone.now() + timedelta(seconds=seconds)
    return bool(Lease.objects.filter(name=name, holder=token).update(expires_at=until))


@contextmanager
def lease(name: str, seconds: float) -> Iterator[str | None]:
    """`with lease("webhook:3", 600) as held:` — `held` is None when another task has it."""

    token = take_lease(name, seconds)
    try:
        yield token
    finally:
        if token is not None:
            release_lease(name, token)


def expired_leases() -> int:
    """Remove leases that expired more than a day ago (they are free anyway)."""

    cutoff = timezone.now() - timedelta(days=1)
    deleted, _ = Lease.objects.filter(Q(expires_at__lt=cutoff)).delete()
    return int(deleted)
