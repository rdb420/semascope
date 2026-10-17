"""Bearer tokens for clients; the browser uses Django's session instead."""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpRequest
from django.utils import timezone
from ninja.security import HttpBearer, SessionAuth

from oss_server.catalogue.models import ApiToken

# `last_used_at` is written at most once in this interval per token, not on every request
LAST_USED_RESOLUTION = timedelta(minutes=5)


def touch_last_used(row: ApiToken) -> None:
    """Record that a token was used, unless that was already recorded in the last minutes.

    One conditional UPDATE, and none when the stored time is recent: a script calling
    the API in a loop no longer writes a row per request.
    """

    now = timezone.now()
    if row.last_used_at is not None and now - row.last_used_at < LAST_USED_RESOLUTION:
        return
    ApiToken.objects.filter(pk=row.pk).filter(
        Q(last_used_at__isnull=True) | Q(last_used_at__lt=now - LAST_USED_RESOLUTION)
    ).update(last_used_at=now)


class TokenAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> User | None:
        try:
            row = ApiToken.objects.select_related("user").get(key_hash=ApiToken.hash_key(token))
        except ApiToken.DoesNotExist:
            return None
        if not row.user.is_active:
            return None
        touch_last_used(row)
        request.user = row.user
        request.api_token_id = row.pk  # type: ignore[attr-defined]  # rate limits, audit
        return row.user


def issue_token(user: User, name: str) -> str:
    key = "oss_" + secrets.token_urlsafe(32)
    ApiToken.objects.create(name=name, key_hash=ApiToken.hash_key(key), prefix=key[:8], user=user)
    return key


AUTH = [TokenAuth(), SessionAuth()]
