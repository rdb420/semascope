"""Credential masking, page security headers, error answers, request caps and the token
write (SEMA-008, SEMA-038, SEMA-047, SEMA-051, SEMA-069, SEMA-075)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from oss_server.api.auth import issue_token
from oss_server.catalogue.models import ApiToken, Collection, CollectionRole, Source
from oss_server.curation.models import Webhook
from oss_server.redact import MASK, mask_url, redact, restore_masked

SECRET = "s3cr3t-probe-value"  # noqa: S105 - a probe value, not a credential


@pytest.fixture
def staff(db: None) -> tuple[User, dict[str, str]]:
    user = User.objects.create_user("boss", is_staff=True, is_superuser=True)
    return user, {"Authorization": f"Bearer {issue_token(user, 't')}"}


# --- credential masking (SEMA-008) -------------------------------------------------------------


def test_redact_masks_credentials_wherever_they_sit() -> None:
    settings = {
        "urls": [f"https://crawler:{SECRET}@intranet.example/start"],
        "headers": {"Authorization": f"Bearer {SECRET}", "Accept": "text/html"},
        "endpoint": f"https://u:{SECRET}@s3.example",
        "client_secret": SECRET,
        "access_key": "AKIA",
        "max_tokens": 512,
        "nested": [{"api_key": SECRET}],
        "bucket": "docs",
    }
    out = redact(settings)
    text = repr(out)
    assert SECRET not in text
    assert out["headers"]["Accept"] == "text/html" and out["bucket"] == "docs"
    assert out["access_key"] == MASK and out["max_tokens"] == 512
    assert redact(settings, identifiers=True)["access_key"] == "AKIA"
    assert redact({"region_code": SECRET}, credentials=("region_code",)) == {"region_code": MASK}
    assert mask_url(f"https://h.example/hook?token={SECRET}&x=1") == (
        f"https://h.example/hook?token={MASK}&x=1"
    )
    # the admin form sends masks back: the stored values stay
    assert restore_masked(redact(settings, identifiers=True), settings) == settings


@pytest.mark.django_db
def test_sources_collections_and_webhooks_never_echo_a_credential(
    client: Client, staff: tuple[User, dict[str, str]]
) -> None:
    _, auth = staff
    c = Collection.objects.create(slug="c", name="c", settings={"note": f"https://a:{SECRET}@x"})
    Source.objects.create(
        collection=c,
        name="intranet",
        connector="web",
        settings={
            "urls": [f"https://crawler:{SECRET}@intranet.example/"],
            "headers": {"Cookie": f"session={SECRET}"},
        },
        last_error=f"fetch https://crawler:{SECRET}@intranet.example/ failed",
    )
    Webhook.objects.create(url=f"https://u:{SECRET}@hooks.example/x?token={SECRET}", secret=SECRET)
    for path in ("/api/collections/c/sources", "/api/collections", "/api/curation/webhooks"):
        body = client.get(path, headers=auth).content.decode()
        assert SECRET not in body, path
        assert MASK in body, path
    curator = User.objects.create_user("cur")
    CollectionRole.objects.create(user=curator, collection=c, role="curator")
    seen = client.get(
        "/api/collections/c/sources",
        headers={"Authorization": f"Bearer {issue_token(curator, 'x')}"},
    ).json()
    assert seen[0]["settings"] == {}  # below editor: no settings at all


@pytest.mark.django_db
def test_the_admin_shows_masks_and_keeps_secrets_on_save(
    staff: tuple[User, dict[str, str]],
) -> None:
    boss, _ = staff
    browser = Client()
    browser.force_login(boss)
    c = Collection.objects.create(slug="c", name="c")
    source = Source.objects.create(
        collection=c,
        name="bucket",
        connector="s3",
        settings={"bucket": "docs", "access_key": "AKIA", "secret_key": SECRET},
        last_error=f"https://u:{SECRET}@s3.example refused",
    )
    page = browser.get(f"/admin/catalogue/source/{source.pk}/change/").content.decode()
    assert SECRET not in page and MASK in page
    form = {
        "collection": c.pk,
        "name": "bucket",
        "connector": "s3",
        "settings": '{"bucket": "docs2", "access_key": "AKIA", "secret_key": "' + MASK + '"}',
        "enabled": "on",
        "schedule": "",
        "status": source.status,
    }
    resp = browser.post(f"/admin/catalogue/source/{source.pk}/change/", form)
    assert resp.status_code == 302, resp.content.decode()[:2000]
    source.refresh_from_db()
    assert source.settings == {"bucket": "docs2", "access_key": "AKIA", "secret_key": SECRET}
    hook = Webhook.objects.create(url="https://hooks.example/x", secret=SECRET)
    page = browser.get(f"/admin/curation/webhook/{hook.pk}/change/").content.decode()
    assert SECRET not in page
    browser.post(
        f"/admin/curation/webhook/{hook.pk}/change/",
        {"url": "https://hooks.example/y", "kinds": "[]", "secret": "", "enabled": "on"},
    )
    hook.refresh_from_db()
    assert hook.url.endswith("/y") and hook.secret == SECRET


def test_access_log_lines_carry_no_query_string() -> None:
    from oss_server import entry

    assert "%(U)s" in entry.ACCESS_LOG_FORMAT  # the path alone
    assert "%(r)s" not in entry.ACCESS_LOG_FORMAT and "%(q)s" not in entry.ACCESS_LOG_FORMAT
    assert "%(f)s" not in entry.ACCESS_LOG_FORMAT  # nor the referrer


@pytest.mark.django_db
def test_a_generated_cli_token_goes_to_a_private_file_not_the_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import django.core.management

    from oss_server import entry, services
    from oss_server.knowledge import synonyms

    monkeypatch.setattr(django.core.management, "call_command", lambda *a, **kw: None)
    monkeypatch.setattr(services, "bootstrap_solr", lambda: [])
    monkeypatch.setattr(services, "import_bundled_packs", lambda: [])
    monkeypatch.setattr(synonyms, "sync_synonyms", lambda: None)
    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEMASCOPE_ADMIN_PASSWORD", "a-long-admin-password")
    monkeypatch.delenv("SEMASCOPE_API_TOKEN", raising=False)
    with caplog.at_level("INFO"):
        entry.prepare()
    file = tmp_path / "cli-token"
    token = file.read_text().strip()
    assert token.startswith("oss_") and oct(file.stat().st_mode & 0o777) == "0o600"
    assert ApiToken.objects.get(name="cli").key_hash == ApiToken.hash_key(token)
    assert token not in caplog.text


# --- headers (SEMA-069) ------------------------------------------------------------------------


@pytest.mark.django_db
def test_pages_get_a_content_security_policy(
    client: Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from oss_server import urls

    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>x</title>")
    monkeypatch.setattr(urls, "WEB_DIR", web)
    monkeypatch.setenv("SEMASCOPE_MAP_TILE_URL", "https://tiles.example:8443/{z}/{x}/{y}.png")
    monkeypatch.setenv("SEMASCOPE_OIDC_SERVER_URL", "https://login.example/realms/x")
    page = Client().get("/search")
    policy = page["Content-Security-Policy"]
    assert "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert "'unsafe-eval'" not in policy and "script-src 'self' 'unsafe-inline'" not in policy
    assert "connect-src 'self' https://tiles.example:8443" in policy
    assert "form-action 'self' https://login.example" in policy
    assert "Content-Security-Policy" in Client().get("/admin/login/")
    api = Client().get("/api/auth/providers")
    assert "Content-Security-Policy" not in api  # JSON: nosniff and its media type
    assert page["X-Content-Type-Options"] == "nosniff"
    assert page["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_the_caddyfile_sets_each_header_once_and_hides_the_server() -> None:
    caddyfile = (Path(__file__).resolve().parents[3] / "deploy/caddy/Caddyfile").read_text()
    header = caddyfile[caddyfile.index("\theader {") :]
    assert "defer" in header and "-Server" in header  # replaces Django's, drops the banner
    for name in ("Strict-Transport-Security", "Permissions-Policy", "X-Content-Type-Options"):
        assert header.count(name) == 1, name
    assert "http_redirect" in caddyfile and "max_size" in caddyfile
    assert "{$SEMASCOPE_TLS:internal}" in caddyfile and "{$SEMASCOPE_ADMIN_ALLOW" in caddyfile


# --- errors (SEMA-051) -------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_unexpected_error_answers_with_a_reference_only(
    client: Client, staff: tuple[User, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import search as search_mod

    def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError("solr at http://solr:8983 said: /var/solr/data is full")

    monkeypatch.setattr(search_mod, "search", broken)
    _, auth = staff
    resp = client.post(
        "/api/search", data={"q": "x"}, content_type="application/json", headers=auth
    )
    assert resp.status_code == 500
    assert "reference" in resp.json()["detail"] and b"solr" not in resp.content


@pytest.mark.django_db
def test_console_errors_name_the_kind_not_the_message(
    client: Client, staff: tuple[User, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import graph

    def failing(*a: Any, **k: Any) -> Any:
        raise ValueError("Invalid input 'x': expected ... bolt://neo4j:7687 user neo4j")

    monkeypatch.setattr(graph, "_console_rows", failing)
    _, auth = staff
    resp = client.post(
        "/api/graph/cypher",
        data={"query": "MATCH x"},
        content_type="application/json",
        headers=auth,
    )
    assert resp.status_code == 422 and "ValueError" in resp.json()["detail"]
    assert b"neo4j:7687" not in resp.content


# --- caps (SEMA-038) ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_uploads_above_the_cap_are_refused_before_anything_is_stored(
    client: Client,
    staff: tuple[User, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SEMASCOPE_UPLOAD_MAX_MB", "1")
    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(tmp_path))
    Collection.objects.create(slug="c", name="c")
    _, auth = staff
    big = SimpleUploadedFile("big.bin", b"x" * (1024 * 1024 + 1))
    resp = client.post("/api/collections/c/upload", data={"files": [big]}, headers=auth)
    assert resp.status_code == 413 and "big.bin" in resp.json()["detail"]
    assert not list(tmp_path.rglob("big.bin"))


@pytest.mark.django_db
def test_request_bodies_above_the_cap_answer_413(
    client: Client, staff: tuple[User, dict[str, str]], settings: Any
) -> None:
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 1000
    _, auth = staff
    resp = client.post(
        "/api/search", data={"q": "x" * 2000}, content_type="application/json", headers=auth
    )
    assert resp.status_code == 413


@pytest.mark.django_db
def test_reconciliation_input_is_bounded(
    client: Client, staff: tuple[User, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import reconcile

    seen: list[int] = []

    def lookup(q: str, t: Any = None, limit: int = 5) -> list[Any]:
        seen.append(limit)
        return []

    monkeypatch.setattr(reconcile, "reconcile_one", lookup)
    _, auth = staff

    def post(body: Any, raw: bool = False) -> Any:
        return client.post(
            "/api/reconcile",
            data=body if raw else {"queries": body},
            content_type="application/json",
            headers=auth,
        )

    assert post("{not json", raw=True).status_code == 400
    assert post({str(i): "x" for i in range(reconcile.MAX_QUERIES + 1)}).status_code == 400
    assert post({"a": {"query": "x", "limit": "many"}}).status_code == 400
    assert post({"a": {"query": "x" * 501}}).status_code == 400
    assert post({"a": {"query": "x", "limit": 10_000}}).status_code == 200
    assert seen == [reconcile.MAX_LIMIT]


@pytest.mark.django_db
def test_tag_query_looks_up_a_bounded_number_of_phrases(
    client: Client, staff: tuple[User, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import reconcile

    calls: list[str] = []

    def lookup(q: str, *a: Any, **k: Any) -> list[Any]:
        calls.append(q)
        return []

    monkeypatch.setattr(reconcile, "reconcile_one", lookup)
    _, auth = staff
    words = " ".join(f"w{i}" for i in range(150))
    resp = client.post(
        "/api/search/tag-query", data={"q": words}, content_type="application/json", headers=auth
    )
    assert resp.status_code == 200
    assert len(calls) == 3 * 12 - 3  # the first 12 words only


@pytest.mark.django_db
def test_a_token_writes_its_last_use_once_per_interval(client: Client) -> None:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    user = User.objects.create_user("script")
    auth = {"Authorization": f"Bearer {issue_token(user, 's')}"}
    client.get("/api/auth/me", headers=auth)
    first = ApiToken.objects.get(name="s").last_used_at
    assert first is not None
    with CaptureQueriesContext(connection) as queries:
        for _ in range(5):
            assert client.get("/api/auth/me", headers=auth).status_code == 200
    assert not [q for q in queries if q["sql"].startswith("UPDATE")]
    assert ApiToken.objects.get(name="s").last_used_at == first


def test_importing_the_settings_creates_no_folder(tmp_path: Path) -> None:
    env = {**os.environ, "SEMASCOPE_DATA_DIR": "./data", "DATABASE_URL": "sqlite://"}
    env |= {"SEMASCOPE_DEBUG": "true", "DJANGO_SETTINGS_MODULE": "oss_server.settings"}
    subprocess.run(  # noqa: S603 - this interpreter, a fixed statement
        [sys.executable, "-c", "import oss_server.settings"],
        cwd=tmp_path,
        env=env,
        check=True,
    )
    assert list(tmp_path.iterdir()) == []
