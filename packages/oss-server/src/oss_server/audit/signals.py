"""What the audit log records by itself, through Django's signals.

Sign-ins (success and failure, from the API, the admin and single sign-on) and
sign-outs; and creating, changing and deleting the models that decide who may do
what or where data comes from, whether the change came through the API, the Django
admin or a shell: users (staff and superuser flags, active, password, e-mail,
permissions), collection roles, API tokens, collections, sources, webhooks, pack
versions, and annotation deletions.

Saves that change none of the watched fields (a sign-in updating `last_login`, a
pack job touching its timestamps) write nothing. Values are recorded for plain
fields (a role, a flag, a schedule); for settings, passwords, secrets and URLs only
the fact that they changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models import Model
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save

from oss_server.audit.log import record


@dataclass(frozen=True)
class Watch:
    """A model whose changes are audited: `fields` are compared, `values` also shown."""

    prefix: str
    fields: tuple[str, ...]
    values: tuple[str, ...] = ()
    collection: str = ""  # attribute path to the collection slug, if any
    create_detail: tuple[str, ...] = field(default_factory=tuple)
    on_create: bool = True  # False: deletions only (annotations are curation, not security)


def _watches() -> dict[type[Model], Watch]:
    from oss_server.catalogue.models import ApiToken, Collection, CollectionRole, Source
    from oss_server.curation.models import Annotation, Webhook
    from oss_server.knowledge.models import Pack

    user_model = get_user_model()
    return {
        user_model: Watch(
            "user",
            ("username", "email", "is_active", "is_staff", "is_superuser", "password"),
            values=("username", "is_active", "is_staff", "is_superuser"),
            create_detail=("is_active", "is_staff", "is_superuser"),
        ),
        CollectionRole: Watch(
            "role",
            ("user_id", "collection_id", "role"),
            values=("role",),
            collection="collection.slug",
            create_detail=("role",),
        ),
        ApiToken: Watch(
            "token", ("name", "user_id"), values=("name",), create_detail=("name", "prefix")
        ),
        Collection: Watch(
            "collection",
            ("slug", "name", "description", "profile", "settings"),
            values=("name", "profile"),
            collection="slug",
            create_detail=("name", "profile"),
        ),
        Source: Watch(
            "source",
            ("name", "connector", "settings", "enabled", "schedule", "collection_id"),
            values=("name", "connector", "enabled", "schedule"),
            collection="collection.slug",
            create_detail=("name", "connector", "enabled", "schedule"),
        ),
        Webhook: Watch(
            "webhook", ("url", "kinds", "enabled", "secret"), values=("kinds", "enabled")
        ),
        Pack: Watch(
            "pack",
            ("status",),
            values=("status",),
            create_detail=("pack_id", "version", "status"),
        ),
        Annotation: Watch("annotation", (), collection="document.collection.slug", on_create=False),
    }


WATCHES: dict[type[Model], Watch] = {}


def _attr(obj: Any, path: str) -> str:
    for part in path.split("."):
        try:
            obj = getattr(obj, part, None)
        except Exception:  # noqa: BLE001 - a deleted relation
            return ""
        if obj is None:
            return ""
    return str(obj)


def _detail_of_user(instance: Model) -> dict[str, Any]:
    """Who a role or token belongs to: the user's name, not their object."""

    user = getattr(instance, "user", None)
    return {"user": user.get_username()} if user is not None else {}


def _name(instance: Model) -> str:
    """How an entry names its target; a webhook by its host only, since webhook URLs
    often carry their secret in the path."""

    url = getattr(instance, "url", None)
    if isinstance(url, str) and url:
        from urllib.parse import urlsplit

        try:
            return urlsplit(url).hostname or ""
        except ValueError:
            return ""
    return str(instance)


def _before_save(sender: type[Model], instance: Model, update_fields: Any = None, **_: Any) -> None:
    watch = WATCHES.get(sender)
    if watch is None or not watch.fields or instance.pk is None or instance._state.adding:
        return
    if update_fields is not None and not set(update_fields) & set(watch.fields):
        return
    old = sender._default_manager.filter(pk=instance.pk).values(*watch.fields).first()
    instance._audit_before = old  # type: ignore[attr-defined]


def _after_save(sender: type[Model], instance: Model, created: bool, **_: Any) -> None:
    watch = WATCHES.get(sender)
    if watch is None:
        return
    collection = _attr(instance, watch.collection) if watch.collection else ""
    if created and not watch.on_create:
        return
    if created:
        created_with = {name: getattr(instance, name, None) for name in watch.create_detail}
        record(
            f"{watch.prefix}.create",
            target=instance,
            target_name=_name(instance),
            collection=collection,
            detail={**_detail_of_user(instance), **created_with},
        )
        return
    old = getattr(instance, "_audit_before", None)
    if not old:
        return
    instance._audit_before = None  # type: ignore[attr-defined]
    changed = [name for name in watch.fields if old.get(name) != getattr(instance, name, None)]
    if not changed:
        return
    detail: dict[str, Any] = {"changed": changed}
    for name in changed:
        if name in watch.values:
            detail[name] = {"from": old.get(name), "to": getattr(instance, name, None)}
    record(
        f"{watch.prefix}.update",
        target=instance,
        target_name=_name(instance),
        collection=collection,
        detail={**_detail_of_user(instance), **detail},
    )


def _after_delete(sender: type[Model], instance: Model, **_: Any) -> None:
    watch = WATCHES.get(sender)
    if watch is None:
        return
    collection = _attr(instance, watch.collection) if watch.collection else ""
    detail = {name: getattr(instance, name, None) for name in watch.values}
    record(
        f"{watch.prefix}.delete",
        target=instance,
        target_name=_name(instance),
        collection=collection,
        detail={**_detail_of_user(instance), **detail},
    )


def _permissions_changed(
    sender: Any, instance: Model, action: str, model: Any, pk_set: Any, **_: Any
) -> None:
    """Groups and permissions given to or taken from a user (staff can use the admin)."""

    if action not in ("post_add", "post_remove", "post_clear"):
        return
    user_model = get_user_model()
    if not isinstance(instance, user_model):
        return
    names = sorted(str(o) for o in model._default_manager.filter(pk__in=pk_set)) if pk_set else []
    record(
        "user.permissions",
        target=instance,
        detail={
            "change": action.removeprefix("post_"),
            "of": model._meta.model_name,
            "items": names,
        },
    )


def _logged_in(sender: Any, request: Any, user: Any, **_: Any) -> None:
    record("auth.login", request=request, actor=user, target=user)


def _logged_out(sender: Any, request: Any, user: Any, **_: Any) -> None:
    if user is not None:
        record("auth.logout", request=request, actor=user, target=user)


def _login_failed(sender: Any, credentials: dict[str, Any], request: Any = None, **_: Any) -> None:
    name = credentials.get("username") or credentials.get("email") or credentials.get("login")
    locked = bool(getattr(request, "_semascope_login_locked", False))
    record(
        "auth.login",
        outcome="refused" if locked else "failed",
        request=request,
        target_type="user",
        target_name=str(name or "")[:150],
        detail={"reason": "locked out after repeated failures" if locked else "bad credentials"},
    )


def connect() -> None:
    from oss_server import ratelimit

    WATCHES.update(_watches())
    for model in WATCHES:
        uid = f"semascope-audit-{model._meta.label_lower}"
        pre_save.connect(_before_save, sender=model, dispatch_uid=uid + "-pre")
        post_save.connect(_after_save, sender=model, dispatch_uid=uid + "-post")
        post_delete.connect(_after_delete, sender=model, dispatch_uid=uid + "-delete")
    user_model = get_user_model()
    for through in (user_model.groups.through, user_model.user_permissions.through):
        m2m_changed.connect(_permissions_changed, sender=through, dispatch_uid=f"audit-{through}")
    user_logged_in.connect(_logged_in, dispatch_uid="semascope-audit-login")
    user_logged_out.connect(_logged_out, dispatch_uid="semascope-audit-logout")
    user_login_failed.connect(_login_failed, dispatch_uid="semascope-audit-login-failed")
    # the sign-in lockout counts failures and clears them on success (oss_server.ratelimit)
    user_login_failed.connect(ratelimit.login_failed, dispatch_uid="semascope-login-failures")
    user_logged_in.connect(ratelimit.logged_in, dispatch_uid="semascope-login-clear")
