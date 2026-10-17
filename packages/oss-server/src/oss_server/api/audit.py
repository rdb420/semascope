"""The audit log for administrators: `GET /api/audit`, newest first (staff only)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from oss_server.audit.models import AuditEntry

router = Router(tags=["admin"])

MAX_LIMIT = 500


class AuditOut(Schema):
    id: int
    at: datetime
    action: str
    outcome: str
    actor: str
    via: str
    ip: str | None
    user_agent: str
    target_type: str
    target_id: str
    target_name: str
    collection: str
    detail: dict[str, Any]


@router.get("", response=list[AuditOut])
def audit_log(
    request: HttpRequest,
    action: str | None = None,
    actor: str | None = None,
    outcome: str | None = None,
    collection: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before: int | None = None,
    limit: int = 100,
) -> list[AuditOut]:
    """Entries filtered by action (a prefix: `auth.` for sign-ins and sign-outs), actor,
    outcome, collection and time; `before=<id>` pages back from an entry."""

    if not (getattr(request.user, "is_staff", False) and request.user.is_active):
        raise HttpError(403, "staff only")
    if not 1 <= limit <= MAX_LIMIT:
        raise HttpError(422, f"limit must be between 1 and {MAX_LIMIT}")
    qs = AuditEntry.objects.all()
    if action:
        qs = qs.filter(action__startswith=action)
    if actor:
        qs = qs.filter(actor_name=actor)
    if outcome:
        qs = qs.filter(outcome=outcome)
    if collection:
        qs = qs.filter(collection=collection)
    if since:
        qs = qs.filter(at__gte=since)
    if until:
        qs = qs.filter(at__lt=until)
    if before:
        qs = qs.filter(id__lt=before)
    return [
        AuditOut(
            id=e.id,
            at=e.at,
            action=e.action,
            outcome=e.outcome,
            actor=e.actor_name,
            via=e.via,
            ip=e.ip,
            user_agent=e.user_agent,
            target_type=e.target_type,
            target_id=e.target_id,
            target_name=e.target_name,
            collection=e.collection,
            detail=e.detail,
        )
        for e in qs.order_by("-id")[:limit]
    ]
