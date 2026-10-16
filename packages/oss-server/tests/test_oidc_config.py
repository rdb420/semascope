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
