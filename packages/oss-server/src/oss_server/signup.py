"""Who may create an account: nobody through a local sign-up form, single sign-on by setting.

Imported by django-allauth only, when OpenID Connect is configured
(`ACCOUNT_ADAPTER` and `SOCIALACCOUNT_ADAPTER` in `oss_server.settings`). Local
accounts are made by an administrator (the Django admin or `oss-manage`), never
by the visitor. A first sign-in through the provider creates the account when
`SEMASCOPE_OIDC_AUTO_SIGNUP` is true (the default); the new user then gets
`SEMASCOPE_OIDC_DEFAULT_ROLE` as a global role (`api.access.grant_sign_on_role`).
With it false, only users an administrator created (with the same e-mail
address) can sign in that way.
"""

from __future__ import annotations

from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.http import HttpRequest

from oss_core.config import get_settings


class ClosedSignupAccountAdapter(DefaultAccountAdapter):  # type: ignore[misc]
    """Local sign-up is closed: `/accounts/signup/` shows the sign-up-closed page."""

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return False


class SignOnAccountAdapter(DefaultSocialAccountAdapter):  # type: ignore[misc]
    """Accounts created by single sign-on: allowed by setting, with the sign-on role."""

    def is_open_for_signup(self, request: HttpRequest, sociallogin: Any) -> bool:
        return bool(get_settings().semascope_oidc_auto_signup)

    def save_user(self, request: HttpRequest, sociallogin: Any, form: Any = None) -> Any:
        from oss_server.api.access import grant_sign_on_role

        user = super().save_user(request, sociallogin, form)
        grant_sign_on_role(user)
        return user
