"""Roles per collection.

Four roles, each including the ones before it: reader (search and read),
curator (decide reviews, tag, annotate), editor (packs, deltas, sources),
admin (everything, equals Django staff). A user with no CollectionRole rows
gets the deployment default (`SEMASCOPE_DEFAULT_ROLE`, reader), so a fresh
install works without configuring anyone; superusers and staff are admins
everywhere.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest
from ninja.errors import HttpError

from oss_core.config import get_settings

ROLES = ("reader", "curator", "editor", "admin")


def role_for(user: Any, collection_slug: str | None) -> str:
    """The user's effective role on a collection (or globally when no collection is given)."""

    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return "admin"
    from oss_server.catalogue.models import CollectionRole

    rows = CollectionRole.objects.filter(user=user, collection__isnull=True)
    if collection_slug:
        rows = rows | CollectionRole.objects.filter(user=user, collection__slug=collection_slug)
    best = ""
    for row in rows:
        if ROLES.index(row.role) > (ROLES.index(best) if best else -1):
            best = row.role
    if best:
        return best
    default = get_settings().semascope_default_role
    return default if default in ROLES else "reader"


def require(role: str, collection_arg: str | None = None) -> Callable[..., Any]:
    """Decorator for Ninja operations: the caller needs at least `role` on the collection."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
            slug = kwargs.get(collection_arg) if collection_arg else None
            if slug is None and collection_arg and args:
                slug = None
            have = role_for(getattr(request, "user", None), slug)
            if not have or ROLES.index(have) < ROLES.index(role):
                raise HttpError(403, f"{role} role required" + (f" on {slug}" if slug else ""))
            return fn(request, *args, **kwargs)

        return wrapper

    return deco


def check(request: HttpRequest, role: str, collection_slug: str | None = None) -> None:
    have = role_for(getattr(request, "user", None), collection_slug)
    if not have or ROLES.index(have) < ROLES.index(role):
        raise HttpError(
            403, f"{role} role required" + (f" on {collection_slug}" if collection_slug else "")
        )
