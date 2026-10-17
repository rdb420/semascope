"""Sign-in lockout, login CSRF and per-user rate limits on the expensive routes (SEMA-038,
SEMA-050)."""

from __future__ import annotations

import time
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server import ratelimit
from oss_server.api.auth import issue_token
from oss_server.api.root import api
from oss_server.catalogue.models import Collection, CollectionRole

PASSWORD = "p" * 16


@pytest.fixture(autouse=True)
def _fast_passwords(settings: Any) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def _login(client: Client, username: str, password: str, ip: str = "203.0.113.5") -> Any:
    return client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
        content_type="application/json",
        headers={"X-Forwarded-For": ip},
    )


@pytest.mark.django_db
def test_an_account_is_locked_after_repeated_failures(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_LOGIN_MAX_FAILURES", "3")
    User.objects.create_user("alice", password=PASSWORD)
    for _ in range(3):
        assert _login(client, "alice", "wrong").status_code == 401
    locked = _login(client, "alice", PASSWORD)  # the right password, too late
    assert locked.status_code == 429, locked.content
    assert int(locked["Retry-After"]) > 0
    assert client.get("/api/auth/me").status_code == 401  # not signed in
    # the same holds from another address, and for the Django admin's form
    assert _login(client, "alice", PASSWORD, ip="198.51.100.7").status_code == 429
    admin_client = Client()
    User.objects.filter(username="alice").update(is_staff=True)
    admin_client.post("/admin/login/", {"username": "alice", "password": PASSWORD})
    assert admin_client.get("/admin/").status_code == 302  # still sent to the login page


@pytest.mark.django_db
def test_the_lockout_ends_after_its_window_and_success_clears_failures(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_LOGIN_MAX_FAILURES", "2")
    monkeypatch.setenv("SEMASCOPE_LOGIN_LOCKOUT_S", "60")
    User.objects.create_user("bob", password=PASSWORD)
    assert _login(client, "bob", "wrong").status_code == 401
    assert _login(client, "bob", PASSWORD).status_code == 200  # clears the count
    assert _login(Client(), "bob", "wrong").status_code == 401
    assert _login(Client(), "bob", PASSWORD).status_code == 200
    for _ in range(2):
        _login(Client(), "bob", "wrong")
    assert _login(Client(), "bob", PASSWORD).status_code == 429
    later = time.time() + 61
    monkeypatch.setattr(time, "time", lambda: later)
    assert _login(Client(), "bob", PASSWORD).status_code == 200


@pytest.mark.django_db
def test_an_address_is_locked_across_accounts(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_LOGIN_MAX_FAILURES_PER_IP", "4")
    User.objects.create_user("carol", password=PASSWORD)
    for i in range(4):
        assert _login(client, f"guess{i}", "wrong", ip="192.0.2.9").status_code == 401
    assert _login(client, "carol", PASSWORD, ip="192.0.2.9").status_code == 429
    assert _login(client, "carol", PASSWORD, ip="192.0.2.10").status_code == 200


def test_the_client_address_is_the_one_the_proxy_saw(monkeypatch: pytest.MonkeyPatch) -> None:
    from django.test import RequestFactory

    req = RequestFactory().get(
        "/", REMOTE_ADDR="172.18.0.3", HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.5"
    )
    assert ratelimit.client_ip(req) == "203.0.113.5"  # a forged first entry is ignored
    monkeypatch.setenv("SEMASCOPE_TRUSTED_PROXIES", "0")
    assert ratelimit.client_ip(req) == "172.18.0.3"


@pytest.mark.django_db
def test_login_needs_the_csrf_token() -> None:
    User.objects.create_user("dave", password=PASSWORD)
    browser = Client(enforce_csrf_checks=True)
    body = {"username": "dave", "password": PASSWORD}
    refused = browser.post("/api/auth/login", data=body, content_type="application/json")
    assert refused.status_code == 403
    token = browser.get("/api/auth/csrf").json()["csrftoken"]
    ok = browser.post(
        "/api/auth/login",
        data=body,
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )
    assert ok.status_code == 200 and ok.json()["username"] == "dave"


@pytest.mark.django_db
def test_a_locked_cache_fails_open(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    User.objects.create_user("erin", password=PASSWORD)

    def down(*a: Any, **k: Any) -> Any:
        raise ConnectionError("valkey unreachable")

    from django.core.cache import cache

    monkeypatch.setattr(cache, "get", down)
    monkeypatch.setattr(cache, "set", down)
    assert _login(client, "erin", PASSWORD).status_code == 200


# --- API rate limits ---------------------------------------------------------------------------


def _served_throttles() -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {}
    for bound in api._get_bound_routers():  # noqa: SLF001
        for path, view in bound.path_operations.items():
            for op in view.operations:
                for method in op.methods:
                    scopes = [
                        str(t.scope)
                        for t in op.throttle_objects
                        if isinstance(t, ratelimit.ScopedThrottle)
                    ]
                    out[(method, "/api" + bound.prefix + path)] = scopes
    return out


def test_every_expensive_route_is_throttled_in_its_scope() -> None:
    served = _served_throttles()
    for route, scope in ratelimit.RATE_LIMITED_ROUTES.items():
        assert served.get(route) == [scope], route
    assert served[("GET", "/api/collections")] == []


def test_rates_come_from_the_settings() -> None:
    rates = ratelimit.configured_rates("export=2/m, console=off, nonsense=1/s, list=x/y")
    assert rates["export"] == "2/m" and rates["console"] is None
    assert rates["list"] == ratelimit.DEFAULT_RATES["list"]  # malformed: the default stays
    assert set(ratelimit.configured_rates("off").values()) == {None}


@pytest.fixture
def reader(db: None, monkeypatch: pytest.MonkeyPatch) -> User:
    from oss_server.api import searchplus

    class EmptySolr:
        def select(self, collection: str, **params: Any) -> dict[str, Any]:
            return {"response": {"numFound": 0, "docs": []}, "facet_counts": {}}

    monkeypatch.setattr(searchplus, "solr_client", lambda: EmptySolr())
    a = Collection.objects.create(slug="a", name="a")
    user = User.objects.create_user("reader")
    CollectionRole.objects.create(user=user, collection=a, role="reader")
    return user


def test_an_expensive_route_answers_429_past_its_rate(
    client: Client, reader: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_RATE_LIMITS", "export=2/m")
    first = {"Authorization": f"Bearer {issue_token(reader, 'one')}"}
    second = {"Authorization": f"Bearer {issue_token(reader, 'two')}"}
    body = {"q": "x", "collection": "a"}

    def export(headers: dict[str, str]) -> Any:
        return client.post(
            "/api/search/export.csv", data=body, content_type="application/json", headers=headers
        )

    assert [export(first).status_code for _ in range(2)] == [200, 200]
    refused = export(first)
    assert refused.status_code == 429 and int(refused["Retry-After"]) >= 1
    assert export(second).status_code == 200  # each token counts on its own
    # another scope is not touched by this one's count
    listed = client.post(
        "/api/search/list",
        data={"names": ["x"], "collection": "a"},
        content_type="application/json",
        headers=first,
    )
    assert listed.status_code == 200
    monkeypatch.setenv("SEMASCOPE_RATE_LIMITS", "off")
    assert export(first).status_code == 200


def test_an_unreachable_cache_does_not_block_the_api(
    client: Client, reader: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_RATE_LIMITS", "export=1/m")

    def down(*a: Any, **k: Any) -> Any:
        raise ConnectionError("valkey unreachable")

    monkeypatch.setattr(ratelimit.ScopedThrottle, "cache", type("C", (), {"get": down})())
    headers = {"Authorization": f"Bearer {issue_token(reader, 'one')}"}
    for _ in range(3):
        resp = client.post(
            "/api/search/export.csv",
            data={"q": "x", "collection": "a"},
            content_type="application/json",
            headers=headers,
        )
        assert resp.status_code == 200


@pytest.mark.django_db
def test_public_reconciliation_counts_per_client_address(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_RECONCILE_PUBLIC", "true")
    monkeypatch.setenv("SEMASCOPE_RATE_LIMITS", "reconcile=3/m")
    codes = [
        client.get("/api/reconcile", headers={"X-Forwarded-For": "192.0.2.1"}).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]
    assert client.get("/api/reconcile", headers={"X-Forwarded-For": "192.0.2.2"}).status_code == 200
