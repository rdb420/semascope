"""Writing audit entries: `record()`, and the middleware that tells it who is asking.

`AuditContextMiddleware` keeps the current request in a context variable, so model
signals (a role changed in the Django admin, a source edited through the API) know
the actor, the client address and the user agent without the view passing them on.
Outside a request (a worker, `oss-manage`) the actor is `system`.

`record` never raises: an audit failure is logged and the action goes on. Each entry
is also written as one line to the `semascope.audit` logger, for log shipping.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from django.db import transaction
from django.http import HttpRequest, HttpResponse

from oss_server.redact import mask_text, redact

log = logging.getLogger("semascope.audit")

_REQUEST: ContextVar[HttpRequest | None] = ContextVar("semascope_audit_request", default=None)

MAX_DETAIL_CHARS = 4000


class AuditContextMiddleware:
    """Makes the request visible to `record` while it is handled."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        token = _REQUEST.set(request)
        try:
            return self.get_response(request)
        finally:
            _REQUEST.reset(token)


def current_request() -> HttpRequest | None:
    return _REQUEST.get()


def _actor(request: HttpRequest | None, actor: Any) -> tuple[int | None, str, str]:
    """(user id, user name, via) of whoever acts."""

    user = actor if actor is not None else getattr(request, "user", None)
    signed_in = user is not None and bool(getattr(user, "is_authenticated", False))
    if request is None:
        via = "system"
    elif getattr(request, "api_token_id", None) is not None:
        via = "token"
    elif request.path.startswith("/admin/"):
        via = "admin"
    elif signed_in:
        via = "session"
    else:
        via = "anonymous"
    if not signed_in or user is None:
        return None, "", via
    return int(user.pk), str(user.get_username())[:150], via


def _clip(value: Any) -> Any:
    """Long strings shortened, so one entry stays small."""

    if isinstance(value, str):
        return value if len(value) <= 500 else value[:500] + "…"
    if isinstance(value, dict):
        return {str(k)[:100]: _clip(v) for k, v in list(value.items())[:50]}
    if isinstance(value, list | tuple):
        return [_clip(v) for v in list(value)[:50]]
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:500]


def record(
    action: str,
    *,
    outcome: str = "ok",
    request: HttpRequest | None = None,
    actor: Any = None,
    target: Any = None,
    target_type: str = "",
    target_id: Any = "",
    target_name: str = "",
    collection: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one audit entry. `target` may be a model instance (its type, pk and str)."""

    try:
        from oss_server.audit.models import AuditEntry
        from oss_server.ratelimit import client_ip

        request = request if request is not None else current_request()
        actor_id, actor_name, via = _actor(request, actor)
        if target is not None:
            target_type = target_type or target._meta.model_name
            target_id = target_id or target.pk
            target_name = target_name or str(target)
        clean = _clip(redact(detail or {}, identifiers=True))
        text = json.dumps(clean, default=str)
        if len(text) > MAX_DETAIL_CHARS:
            clean = {"truncated": text[:MAX_DETAIL_CHARS]}
        ip = client_ip(request) if request is not None else ""
        agent = str(request.META.get("HTTP_USER_AGENT") or "")[:200] if request else ""
        with transaction.atomic():  # a savepoint: a failure here leaves the caller's work intact
            entry = AuditEntry.objects.create(
                action=action[:64],
                outcome=outcome,
                actor_id=actor_id,
                actor_name=actor_name,
                via=via,
                ip=_valid_ip(ip),
                user_agent=agent,
                target_type=str(target_type)[:64],
                target_id=str(target_id)[:200],
                target_name=mask_text(str(target_name))[:200],
                collection=str(collection or "")[:100],
                detail=clean,
            )
        log.info(
            "audit %s %s actor=%s via=%s ip=%s target=%s:%s collection=%s id=%s",
            entry.action,
            entry.outcome,
            entry.actor_name or "-",
            entry.via,
            entry.ip or "-",
            entry.target_type or "-",
            entry.target_id or "-",
            entry.collection or "-",
            entry.pk,
        )
    except Exception:  # noqa: BLE001 - auditing must never break the action it records
        log.exception("could not write the audit entry for %s", action)


def _valid_ip(value: str) -> str | None:
    import ipaddress

    try:
        return str(ipaddress.ip_address(value.strip())) if value else None
    except ValueError:
        return None
