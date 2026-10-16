"""Atom and RSS feeds of searches, read with a feed token.

A feed reader cannot sign in or send a bearer header, so feeds take a
`?token=` that is good for nothing but feeds. The token is the user's id and
the id of a hidden `ApiToken` row named `feeds`, signed with the server's
secret key: it never reveals or doubles as an API key, it is revoked by
deleting that row (the "Revoke" button, `DELETE /api/feeds/token` or the
admin), and rotating it makes old feed URLs stop working. A feed is read as
its owner at request time, so it shows only what that user may read now
(`access.read_scope`); a saved search on a collection they lost access to
answers 403.

The routes live in `views.py`.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Any

from django.contrib.auth.models import User
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.utils import feedgenerator, timezone
from django.utils.dateparse import parse_datetime
from ninja.security import APIKeyQuery

from oss_server.api.schemas import SearchHit
from oss_server.catalogue.models import ApiToken

FEED_TOKEN_NAME = "feeds"  # noqa: S105 - the name of the anchor row, not a secret
_SALT = "semascope.feeds"
_TAGS = re.compile(r"<[^>]+>")


def feed_token_row(user: User) -> ApiToken | None:
    return ApiToken.objects.filter(user=user, name=FEED_TOKEN_NAME).order_by("-pk").first()


def sign(user: User, row: ApiToken) -> str:
    return signing.dumps({"u": user.pk, "k": row.pk}, salt=_SALT, compress=False)


def issue_feed_token(user: User) -> str:
    """A new feed token for the user; earlier ones stop working."""

    ApiToken.objects.filter(user=user, name=FEED_TOKEN_NAME).delete()
    hidden = "feed_" + secrets.token_urlsafe(32)  # never shown: the row is only an anchor
    row = ApiToken.objects.create(
        name=FEED_TOKEN_NAME, key_hash=ApiToken.hash_key(hidden), prefix="feeds", user=user
    )
    return sign(user, row)


def current_feed_token(user: User) -> str | None:
    row = feed_token_row(user)
    return sign(user, row) if row else None


def revoke_feed_token(user: User) -> int:
    deleted, _ = ApiToken.objects.filter(user=user, name=FEED_TOKEN_NAME).delete()
    return deleted


def feed_user(token: str) -> User | None:
    """The active user a feed token belongs to, or None when it is forged or revoked."""

    try:
        data = signing.loads(token, salt=_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    pk, uid = data.get("k"), data.get("u")
    if not isinstance(pk, int) or not isinstance(uid, int):
        return None
    row = (
        ApiToken.objects.select_related("user")
        .filter(pk=pk, user_id=uid, name=FEED_TOKEN_NAME)
        .first()
    )
    if row is None or not row.user.is_active:
        return None
    ApiToken.objects.filter(pk=row.pk).update(last_used_at=timezone.now())
    return row.user


class FeedTokenAuth(APIKeyQuery):
    """`?token=<feed token>`: authenticates the feed routes only."""

    param_name = "token"

    def authenticate(self, request: HttpRequest, key: str | None) -> User | None:
        if not key:
            return None
        user = feed_user(key)
        if user is not None:
            request.user = user
        return user


def _when(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value.replace("Z", "+00:00"))
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, UTC)
    return parsed


def plain(snippet: str) -> str:
    """A highlighted snippet as plain text (the feed generator escapes it)."""

    return " ".join(_TAGS.sub("", snippet).split())


def render(
    request: HttpRequest,
    *,
    title: str,
    link: str,
    description: str,
    entries: list[tuple[SearchHit, list[str]]],
    fmt: str = "atom",
) -> HttpResponse:
    """An Atom (default) or RSS 2.0 document; each entry links to the document page."""

    cls: Any = feedgenerator.Rss201rev2Feed if fmt == "rss" else feedgenerator.Atom1Feed
    feed = cls(
        title=title,
        link=request.build_absolute_uri(link),
        description=description,
        feed_url=request.build_absolute_uri(request.path),
        language="en",
    )
    for hit, categories in entries:
        url = request.build_absolute_uri(f"/documents/{hit.id}")
        summary = " … ".join(plain(s) for s in hit.snippets)
        summary = summary or (hit.passages[0] if hit.passages else "")
        feed.add_item(
            title=hit.title or hit.uri,
            link=url,
            description=summary or hit.uri,
            unique_id=f"urn:semascope:document:{hit.id}",
            unique_id_is_permalink=False,
            updateddate=_when(hit.indexed_at),
            pubdate=_when(hit.modified_at) or _when(hit.indexed_at),
            categories=[hit.collection, *categories],
        )
    response = HttpResponse(content_type=f"{feed.content_type.split(';')[0]}; charset=utf-8")
    feed.write(response, "utf-8")
    response["Cache-Control"] = "private, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    return response
