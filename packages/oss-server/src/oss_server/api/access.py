"""Roles per collection.

Four roles, each including the ones before it: reader (search and read),
curator (decide reviews, tag, annotate), editor (packs, deltas, sources),
admin (everything, equals Django staff). Superusers and staff are admins
everywhere. A `CollectionRole` row without a collection is a global role.

Reading a collection needs at least `reader` on it. A signed-in user with no
row for a collection (and no global row) gets the deployment default,
`SEMASCOPE_DEFAULT_ROLE`: `reader` (the default) keeps reading open to every
signed-in user; `none` makes every collection private to the users given a
role on it. An unknown value counts as `none`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest
from ninja.errors import HttpError

from oss_core.config import get_settings

log = logging.getLogger(__name__)

ROLES = ("reader", "curator", "editor", "admin")
NO_ROLE = ("", "none")


def default_role() -> str:
    """The role of a signed-in user on a collection they have no row for ('' for none)."""

    value = (get_settings().semascope_default_role or "").strip().lower()
    if value in ROLES:
        return value
    if value not in NO_ROLE:
        log.warning("SEMASCOPE_DEFAULT_ROLE=%r is not a role; treating it as none", value)
    return ""


def _authenticated(user: Any) -> bool:
    return user is not None and bool(getattr(user, "is_authenticated", False))


def _is_admin(user: Any) -> bool:
    return bool(getattr(user, "is_superuser", False) or getattr(user, "is_staff", False))


def role_for(user: Any, collection_slug: str | None) -> str:
    """The user's effective role on a collection (or globally when no collection is given).

    '' means no role: the user may not even read.
    """

    if not _authenticated(user) or not getattr(user, "is_active", True):
        return ""
    if _is_admin(user):
        return "admin"
    from oss_server.catalogue.models import CollectionRole

    rows = CollectionRole.objects.filter(user=user, collection__isnull=True)
    if collection_slug:
        rows = rows | CollectionRole.objects.filter(user=user, collection__slug=collection_slug)
    best = ""
    for row in rows:
        if row.role in ROLES and ROLES.index(row.role) > (ROLES.index(best) if best else -1):
            best = row.role
    return best or default_role()


def readable_collections(user: Any) -> set[str] | None:
    """The collection slugs the user may read, or None when they may read every collection."""

    if not _authenticated(user) or not getattr(user, "is_active", True):
        return set()
    if _is_admin(user):
        return None
    from oss_server.catalogue.models import CollectionRole

    rows = list(
        CollectionRole.objects.filter(user=user, role__in=ROLES).values_list(
            "collection__slug", flat=True
        )
    )
    if any(slug is None for slug in rows) or default_role():
        return None
    return {slug for slug in rows if slug}


def can_read(user: Any, collection_slug: str) -> bool:
    return bool(role_for(user, collection_slug))


def read_scope(request: HttpRequest, collection: str | None = None) -> list[str] | None:
    """The collections a read may touch.

    With an explicit `collection` the caller needs reader on it (403 otherwise) and the
    result is None: the caller's own filter on that collection is the whole scope. Without
    one, the result is None when the caller may read everything, else the sorted list of
    readable slugs (possibly empty), which the caller must filter on.
    """

    if collection:
        check(request, "reader", collection)
        return None
    allowed = readable_collections(getattr(request, "user", None))
    return None if allowed is None else sorted(allowed)


def require(role: str, collection_arg: str | None = None) -> Callable[..., Any]:
    """Decorator for Ninja operations: the caller needs at least `role` on the collection."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
            slug = kwargs.get(collection_arg) if collection_arg else None
            check(request, role, slug)
            return fn(request, *args, **kwargs)

        return wrapper

    return deco


def check(request: HttpRequest, role: str, collection_slug: str | None = None) -> None:
    have = role_for(getattr(request, "user", None), collection_slug)
    if not have or ROLES.index(have) < ROLES.index(role):
        raise HttpError(
            403, f"{role} role required" + (f" on {collection_slug}" if collection_slug else "")
        )
