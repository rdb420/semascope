"""Server hardening on the running stack, through Caddy: security headers, request caps,
the sign-in lockout (counted in Valkey across the server's processes), per-token rate
limits and the audit log."""

from __future__ import annotations

import json
import secrets
import warnings
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from _stack import base_url, compose

PASSWORD = "Hx7#" + secrets.token_urlsafe(12)


def shell(code: str) -> str:
    """Run Python in the server container (Django set up) and return what it printed."""

    proc = compose("exec", "-T", "server", "oss-manage", "shell", "-c", code, timeout=120)
    if proc.returncode != 0:
        pytest.fail(f"server shell failed: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


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


def test_pages_carry_the_security_headers_once(browser: httpx.Client) -> None:
    page = browser.get("/")
    assert page.status_code == 200, page.status_code
    policy = page.headers.get_list("content-security-policy")
    assert len(policy) == 1 and "script-src 'self'" in policy[0]
    assert "frame-ancestors 'none'" in policy[0]
    for name in (
        "x-content-type-options",
        "referrer-policy",
        "strict-transport-security",
        "permissions-policy",
        "cross-origin-opener-policy",
        "x-frame-options",
    ):
        assert len(page.headers.get_list(name)) == 1, (name, page.headers.get_list(name))
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "server" not in page.headers  # neither Caddy nor gunicorn names itself
    health = browser.get("/healthz")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert "server" not in health.headers
    admin = browser.get("/admin/login/")
    assert "script-src 'self'" in admin.headers["content-security-policy"]


def test_plain_http_on_the_https_port_is_redirected(browser: httpx.Client) -> None:
    parts = urlsplit(base_url())
    plain = httpx.get(f"http://{parts.netloc}/search", follow_redirects=False, timeout=30)
    assert plain.status_code in (301, 302, 307, 308), plain.status_code
    assert plain.headers["location"].startswith("https://")


def test_oversized_request_bodies_are_refused_at_the_proxy(api: httpx.Client) -> None:
    body = json.dumps({"q": "x" * (40 * 1024 * 1024)})
    resp = api.post("/search", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413, resp.status_code


def test_reconciliation_needs_a_signed_in_member(browser: httpx.Client, api: httpx.Client) -> None:
    assert browser.get("/api/reconcile").status_code == 401
    assert api.get("/reconcile").status_code == 200


def _login(browser: httpx.Client, username: str, password: str) -> httpx.Response:
    token = browser.get("/api/auth/csrf").json()["csrftoken"]
    return browser.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRFToken": token, "Referer": base_url() + "/"},
    )


def test_sign_in_needs_csrf_and_locks_out_after_repeated_failures(
    browser: httpx.Client, api: httpx.Client
) -> None:
    name = "it-lockout-" + secrets.token_hex(4)
    shell(
        "from django.contrib.auth.models import User; "
        f"User.objects.create_user({name!r}, password={PASSWORD!r}); print('ok')"
    )
    bare = browser.post("/api/auth/login", json={"username": name, "password": PASSWORD})
    assert bare.status_code == 403, bare.text  # no CSRF token: refused (SEMA-050)
    for _ in range(5):  # SEMASCOPE_LOGIN_MAX_FAILURES, the default
        assert _login(browser, name, "wrong").status_code == 401
    locked = _login(browser, name, PASSWORD)
    assert locked.status_code == 429, locked.text
    assert int(locked.headers["retry-after"]) > 0
    # the audit log has the failures, the refusal and where they came from
    rows = api.get("/audit", params={"action": "auth.login", "limit": 50}).json()
    mine = [r for r in rows if r["target_name"] == name]
    outcomes = [r["outcome"] for r in mine]
    assert outcomes.count("failed") >= 5 and "refused" in outcomes, outcomes
    assert all(r["ip"] for r in mine), mine


def test_a_token_is_rate_limited_on_the_consoles(browser: httpx.Client) -> None:
    key = shell(
        "from django.contrib.auth.models import User; "
        "from oss_server.api.auth import issue_token; "
        "print(issue_token(User.objects.get(username='admin'), 'it-rate-limit'))"
    )
    assert key.startswith("oss_")
    headers = {"Authorization": f"Bearer {key}"}
    query = {"query": "MATCH (n) RETURN count(n) AS n", "limit": 1}
    codes = [
        browser.post("/api/graph/cypher", json=query, headers=headers).status_code
        for _ in range(61)  # console: 60 a minute by default
    ]
    assert codes[:60] == [200] * 60, codes
    assert codes[60] == 429
    refused = browser.post("/api/graph/cypher", json=query, headers=headers)
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) >= 1


def test_changes_through_the_api_are_in_the_audit_log(api: httpx.Client) -> None:
    slug = "it-audit-" + secrets.token_hex(3)
    assert api.post("/collections", json={"slug": slug}).status_code == 201
    assert api.delete(f"/collections/{slug}").status_code == 204
    rows: list[dict[str, Any]] = api.get("/audit", params={"collection": slug}).json()
    assert [r["action"] for r in rows] == ["collection.delete", "collection.create"], rows
    assert all(r["actor"] == "admin" and r["via"] == "token" and r["ip"] for r in rows), rows
