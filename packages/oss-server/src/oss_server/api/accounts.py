"""Session login for the browser UI. Tokens remain for scripts and the CLI."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import authenticate, login, logout
from django.http import HttpRequest
from django.middleware.csrf import get_token
from ninja import Router, Schema
from ninja.errors import HttpError

router = Router(tags=["auth"])


class LoginIn(Schema):
    username: str
    password: str


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


@router.post("/login", auth=None, response=UserOut)
def login_view(request: HttpRequest, payload: LoginIn) -> UserOut:
    user = authenticate(request, username=payload.username, password=payload.password)
    if user is None:
        raise HttpError(401, "invalid credentials")
    login(request, user)
    return UserOut(
        username=user.get_username(), is_staff=user.is_staff, is_superuser=user.is_superuser
    )


@router.post("/logout", response={204: None})
def logout_view(request: HttpRequest) -> tuple[int, None]:
    logout(request)
    return 204, None


@router.get("/me", response=UserOut)
def me(request: HttpRequest) -> UserOut:
    from oss_server.api.access import role_for

    user = request.user
    return UserOut(
        username=user.get_username(),
        is_staff=user.is_staff,
        is_superuser=user.is_superuser,
        role=role_for(user, None),
    )
