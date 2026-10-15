"""Session login for the browser UI. Tokens remain for scripts and the CLI."""

from __future__ import annotations

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
