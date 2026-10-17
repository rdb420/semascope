"""Session login for the browser UI. Tokens remain for scripts and the CLI."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.signals import user_login_failed
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import get_token
from ninja import Router, Schema, Status
from ninja.errors import HttpError
from ninja.utils import check_csrf

from oss_server.api.access import role_for
from oss_server.ratelimit import lockout_remaining

router = Router(tags=["auth"])


class LoginIn(Schema):
    username: str
    password: str


class Message(Schema):
    detail: str


class UserOut(Schema):
    username: str
    is_staff: bool
    is_superuser: bool
    role: str = "reader"


@router.get("/providers", auth=None)
def providers(request: HttpRequest) -> dict[str, Any]:
    """Which sign-in methods exist: local accounts always; OIDC when configured."""

    from django.conf import settings

    from oss_core.config import get_settings

    s = get_settings()
    out: dict[str, Any] = {"local": True, "oidc": None}
    if getattr(settings, "OIDC_ENABLED", False):
        out["oidc"] = {
            "name": s.semascope_oidc_name or "Single sign-on",
            "url": "/accounts/oidc/oidc/login/",
        }
    return out


@router.get("/csrf", auth=None)
def csrf(request: HttpRequest) -> dict[str, str]:
    """Set the CSRF cookie; the UI calls this once before any POST."""

    return {"csrftoken": get_token(request)}


@router.post("/login", auth=None, response={200: UserOut, 401: Message, 429: Message})
def login_view(request: HttpRequest, response: HttpResponse, payload: LoginIn) -> Any:
    """Sign in with a local account and start a session.

    Needs the CSRF token like every other form post (`GET /csrf` sets it), so another
    site cannot sign a browser into an account of its choosing. Repeated failures lock
    the account, and the client address, out for a while (`oss_server.ratelimit`): the
    answer is then 429 with Retry-After, whatever the password.
    """

    refused = check_csrf(request)
    if refused is not None:
        raise HttpError(403, "CSRF check failed: fetch /api/auth/csrf and send X-CSRFToken")
    wait = lockout_remaining(request, payload.username)
    if wait:
        # counted and audited like any failure; the account itself is not revealed
        request._semascope_login_locked = True  # type: ignore[attr-defined]
        user_login_failed.send(
            sender=__name__, credentials={"username": payload.username}, request=request
        )
        response["Retry-After"] = str(wait)
        return Status(429, Message(detail="too many failed sign-ins; try again later"))
    user = authenticate(request, username=payload.username, password=payload.password)
    if user is None:
        return Status(401, Message(detail="invalid credentials"))
    login(request, user)
    return Status(
        200,
        UserOut(
            username=user.get_username(),
            is_staff=user.is_staff,
            is_superuser=user.is_superuser,
            role=role_for(user, None),
        ),
    )


@router.post("/logout", response={204: None})
def logout_view(request: HttpRequest) -> Status[None]:
    logout(request)
    return Status(204, None)


@router.get("/me", response=UserOut)
def me(request: HttpRequest) -> UserOut:
    user = request.user
    return UserOut(
        username=user.get_username(),
        is_staff=user.is_staff,
        is_superuser=user.is_superuser,
        role=role_for(user, None),
    )
