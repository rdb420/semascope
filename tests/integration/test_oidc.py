"""Single sign-on: the authorization-code flow against the mock IdP in the fixtures service.

The integration stack configures OpenID Connect (tests/integration/stack/compose.yaml)
with the provider at http://fixtures:8000/oidc. The server reaches it by name; the
browser steps (the redirect to the provider and back) are made here, with the
provider's host rewritten to its published port.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator
from urllib.parse import urljoin

import httpx
import pytest
from _stack import base_url, service_url

PROVIDER = "http://fixtures:8000"


@pytest.fixture
def browser() -> Iterator[httpx.Client]:
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")
    with httpx.Client(
        base_url=base_url(),
        verify=False,  # noqa: S501 - Caddy's internal CA on a throwaway stack
        follow_redirects=False,
        timeout=60.0,
    ) as client:
        yield client


def test_the_sign_in_page_offers_the_provider(browser: httpx.Client) -> None:
    out = browser.get("/api/auth/providers").raise_for_status().json()
    if not out["oidc"]:
        pytest.skip("this stack has no OpenID Connect provider configured")
    assert out == {"local": True, "oidc": {"name": "Mock IdP", "url": "/accounts/oidc/oidc/login/"}}


def test_sign_in_through_the_provider(browser: httpx.Client) -> None:
    idp = service_url("fixtures", 8000)
    start = browser.get("/accounts/oidc/oidc/login/")
    if start.status_code == 200:  # allauth asks before leaving the site: confirm with a POST
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', start.text)
        assert token, start.text[:500]
        start = browser.post(
            "/accounts/oidc/oidc/login/",
            data={"csrfmiddlewaretoken": token.group(1)},
            headers={"Referer": base_url() + "/accounts/oidc/oidc/login/"},
        )
    assert start.status_code == 302, (start.status_code, start.text[:500])
    authorize = start.headers["location"]
    assert authorize.startswith(PROVIDER + "/oidc/authorize?"), authorize

    # the browser at the provider, which signs the fixed user in at once
    back = httpx.get(authorize.replace(PROVIDER, idp), follow_redirects=False)
    assert back.status_code == 302, back.text
    callback = back.headers["location"]
    assert callback.startswith(base_url() + "/accounts/oidc/oidc/login/callback/"), callback

    done = browser.get(callback.removeprefix(base_url()))
    assert done.status_code == 302, (done.status_code, done.text[:800])
    assert urljoin(base_url() + "/", done.headers["location"]) == base_url() + "/"

    me = browser.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()
    assert user["username"] and not user["is_staff"] and not user["is_superuser"], user
    assert user["role"] == "reader", user
