"""Single sign-on configuration: settings from the environment, and what allauth needs at run time.

The settings module reads the environment once at import, so each case runs in
a fresh interpreter. The full sign-in flow runs against a mock IdP in the
integration stack (tests/integration/test_oidc.py).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from typing import Any

import pytest

PROBE = """
import json
import oss_server.settings as s
print(json.dumps({
    "enabled": s.OIDC_ENABLED,
    "apps": s.INSTALLED_APPS,
    "middleware": s.MIDDLEWARE,
    "providers": getattr(s, "SOCIALACCOUNT_PROVIDERS", None),
    "backends": getattr(s, "AUTHENTICATION_BACKENDS", None),
}))
"""


def settings_with(tmp_path: Any, **env: str) -> dict[str, Any]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SEMASCOPE_OIDC_")}
    clean.update(
        DATABASE_URL="sqlite://",
        SEMASCOPE_DATA_DIR=str(tmp_path),
        DJANGO_SETTINGS_MODULE="oss_server.settings",
        **env,
    )
    proc = subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", PROBE],
        env=clean,
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    out: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return out


def test_sign_on_is_off_without_an_issuer(tmp_path: Any) -> None:
    out = settings_with(tmp_path, SEMASCOPE_OIDC_CLIENT_ID="only-a-client")
    assert out["enabled"] is False
    assert not [a for a in out["apps"] if a.startswith("allauth")]
    assert out["providers"] is None


def test_an_issuer_and_client_turn_on_the_openid_connect_provider(tmp_path: Any) -> None:
    out = settings_with(
        tmp_path,
        SEMASCOPE_OIDC_NAME="Company SSO",
        SEMASCOPE_OIDC_SERVER_URL="https://login.example.test/realms/x",
        SEMASCOPE_OIDC_CLIENT_ID="semascope",
        SEMASCOPE_OIDC_CLIENT_SECRET="client-secret",  # noqa: S106 - a test value
    )
    assert out["enabled"] is True
    assert "allauth.socialaccount.providers.openid_connect" in out["apps"]
    assert "allauth.account.middleware.AccountMiddleware" in out["middleware"]
    assert "allauth.account.auth_backends.AuthenticationBackend" in out["backends"]
    (app,) = out["providers"]["openid_connect"]["APPS"]
    assert app == {
        "provider_id": "oidc",
        "name": "Company SSO",
        "client_id": "semascope",
        "secret": "client-secret",
        "settings": {"server_url": "https://login.example.test/realms/x"},
    }


@pytest.mark.parametrize("module", ["jwt", "cryptography"])
def test_the_libraries_the_openid_connect_callback_needs_are_installed(module: str) -> None:
    assert importlib.util.find_spec(module) is not None, f"{module} is not installed"


SIGNUP_PROBE = """
import json
import django
django.setup()
from django.contrib.auth.models import User
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.management import call_command
from django.test import Client, RequestFactory
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialAccount, SocialLogin
from oss_server.api.access import role_for
from oss_server.catalogue.models import CollectionRole

call_command("migrate", interactive=False, verbosity=0)
password = "Tq7#local-signup-probe-2026"
page = Client(HTTP_HOST="localhost").post(
    "/accounts/signup/",
    {"username": "visitor", "email": "visitor@example.test",
     "password1": password, "password2": password},
    secure=True,
)
local = {"status": page.status_code, "users": User.objects.count(),
         "closed": "closed" in page.content.decode().lower()}

adapter = get_adapter()
user = User(username="sso-user", email="sso@example.test")
login = SocialLogin(user=user, account=SocialAccount(provider="oidc", uid="sso-1"))
request = RequestFactory().get("/accounts/oidc/oidc/login/callback/")
SessionMiddleware(lambda r: None).process_request(request)
opened = adapter.is_open_for_signup(request, login)
saved = adapter.save_user(request, login)
rows = list(CollectionRole.objects.filter(user=saved).values_list("collection", "role"))
print(json.dumps({"local": local, "sso_open": opened, "sso_rows": rows,
                  "sso_role": role_for(saved, None)}))
"""


def signup_with(tmp_path: Any, **env: str) -> dict[str, Any]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SEMASCOPE_OIDC_")}
    clean.update(
        DATABASE_URL=f"sqlite:///{tmp_path / 'db.sqlite3'}",
        SEMASCOPE_DATA_DIR=str(tmp_path),
        DJANGO_SETTINGS_MODULE="oss_server.settings",
        SEMASCOPE_OIDC_SERVER_URL="https://login.example.test/realms/x",
        SEMASCOPE_OIDC_CLIENT_ID="semascope",
        **{"SEMASCOPE_DEFAULT_ROLE": "reader", **env},
    )
    proc = subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", SIGNUP_PROBE],
        env=clean,
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    out: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return out


def test_local_sign_up_is_closed_and_sign_on_users_get_no_role_by_default(tmp_path: Any) -> None:
    out = signup_with(tmp_path)
    assert out["local"] == {"status": 200, "users": 0, "closed": True}
    assert out["sso_open"] is True
    assert out["sso_rows"] == []  # SEMASCOPE_OIDC_DEFAULT_ROLE=none: no role row written
    assert out["sso_role"] == "reader"  # what SEMASCOPE_DEFAULT_ROLE gives everyone


def test_sign_on_users_get_the_configured_role(tmp_path: Any) -> None:
    out = signup_with(
        tmp_path, SEMASCOPE_OIDC_DEFAULT_ROLE="curator", SEMASCOPE_DEFAULT_ROLE="none"
    )
    assert out["sso_rows"] == [[None, "curator"]]
    assert out["sso_role"] == "curator"


def test_sign_on_can_be_limited_to_existing_accounts(tmp_path: Any) -> None:
    out = signup_with(tmp_path, SEMASCOPE_OIDC_AUTO_SIGNUP="false")
    assert out["sso_open"] is False
    assert out["local"]["users"] == 0
