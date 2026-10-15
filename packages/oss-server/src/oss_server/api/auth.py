"""Bearer tokens for clients; the browser uses Django's session instead."""

from __future__ import annotations

import secrets

from django.contrib.auth.models import User
from django.http import HttpRequest
from django.utils import timezone
from ninja.security import HttpBearer, SessionAuth

from oss_server.catalogue.models import ApiToken


class TokenAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> User | None:
        try:
            row = ApiToken.objects.select_related("user").get(key_hash=ApiToken.hash_key(token))
        except ApiToken.DoesNotExist:
            return None
        if not row.user.is_active:
            return None
        ApiToken.objects.filter(pk=row.pk).update(last_used_at=timezone.now())
        request.user = row.user
        return row.user


def issue_token(user: User, name: str) -> str:
    key = "oss_" + secrets.token_urlsafe(32)
    ApiToken.objects.create(name=name, key_hash=ApiToken.hash_key(key), prefix=key[:8], user=user)
    return key


AUTH = [TokenAuth(), SessionAuth()]
