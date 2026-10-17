"""The audit log: who did what, when and from where, append-only, with no secret in it."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth.models import Permission, User
from django.test import Client
from django.utils import timezone

from oss_server.api.auth import issue_token
from oss_server.audit.models import AppendOnlyError, AuditEntry
from oss_server.catalogue.models import ApiToken, Collection, CollectionRole, Source
from oss_server.curation.models import Webhook

PASSWORD = "p" * 16
SECRET = "s3cr3t-value-never-stored"  # noqa: S105 - a probe value, not a credential


@pytest.fixture(autouse=True)
def _fast_passwords(settings: Any) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def actions(prefix: str = "") -> list[str]:
    return list(
        AuditEntry.objects.filter(action__startswith=prefix)
        .order_by("id")
        .values_list("action", flat=True)
    )


def last(action: str) -> AuditEntry:
    entry = AuditEntry.objects.filter(action=action).order_by("-id").first()
    assert entry is not None, f"no {action} entry; have {actions()}"
    return entry


def no_secret_anywhere(*secrets: str) -> None:
    dump = json.dumps(list(AuditEntry.objects.values()), default=str)
    for secret in secrets:
        assert secret not in dump


@pytest.fixture
def staff(db: None) -> tuple[User, dict[str, str]]:
    user = User.objects.create_user("boss", password=PASSWORD, is_staff=True)
    return user, {"Authorization": f"Bearer {issue_token(user, 'audit')}"}


@pytest.mark.django_db
def test_sign_ins_and_sign_outs_are_recorded_with_where_from(client: Client) -> None:
    User.objects.create_user("alice", password=PASSWORD)
    headers = {"X-Forwarded-For": "203.0.113.9", "User-Agent": "probe/1.0"}
    body = {"username": "alice", "password": "wrong-" + SECRET}
    client.post("/api/auth/login", data=body, content_type="application/json", headers=headers)
    failed = last("auth.login")
    assert failed.outcome == "failed" and failed.target_name == "alice"
    assert failed.ip == "203.0.113.9" and failed.user_agent == "probe/1.0"
    assert failed.actor_name == "" and failed.via == "anonymous"
    body["password"] = PASSWORD
    client.post("/api/auth/login", data=body, content_type="application/json", headers=headers)
    ok = last("auth.login")
    assert ok.outcome == "ok" and ok.actor_name == "alice" and ok.ip == "203.0.113.9"
    client.post("/api/auth/logout", headers=headers)
    assert last("auth.logout").actor_name == "alice"
    no_secret_anywhere(SECRET, PASSWORD)


@pytest.mark.django_db
def test_a_locked_out_attempt_is_recorded_as_refused(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_LOGIN_MAX_FAILURES", "1")
    User.objects.create_user("bob", password=PASSWORD)
    body = {"username": "bob", "password": "wrong"}
    client.post("/api/auth/login", data=body, content_type="application/json")
    body["password"] = PASSWORD
    assert (
        client.post("/api/auth/login", data=body, content_type="application/json").status_code
        == 429
    )
    refused = last("auth.login")
    assert refused.outcome == "refused" and "locked" in refused.detail["reason"]


@pytest.mark.django_db
def test_tokens_roles_and_staff_flags_are_recorded(staff: tuple[User, dict[str, str]]) -> None:
    boss, _ = staff
    user = User.objects.create_user("carol")
    key = issue_token(user, "script")
    created = last("token.create")
    assert created.detail == {"user": "carol", "name": "script", "prefix": key[:8]}
    ApiToken.objects.filter(name="script").delete()  # a queryset delete: still one entry each
    assert last("token.delete").detail["user"] == "carol"
    c = Collection.objects.create(slug="c", name="c")
    role = CollectionRole.objects.create(user=user, collection=c, role="reader")
    granted = last("role.create")
    assert granted.collection == "c" and granted.detail == {"user": "carol", "role": "reader"}
    role.role = "editor"
    role.save()
    assert last("role.update").detail["role"] == {"from": "reader", "to": "editor"}
    role.delete()
    assert last("role.delete").collection == "c"
    user.is_staff = True
    user.save()
    assert last("user.update").detail["is_staff"] == {"from": False, "to": True}
    user.set_password(SECRET)
    user.save()
    assert last("user.update").detail == {"changed": ["password"]}
    user.user_permissions.add(Permission.objects.get(codename="change_source"))
    assert last("user.permissions").detail["items"]
    no_secret_anywhere(SECRET, key, user.password)


@pytest.mark.django_db
def test_a_sign_in_or_a_job_touching_other_fields_records_nothing(client: Client) -> None:
    user = User.objects.create_user("dave", password=PASSWORD)
    before = AuditEntry.objects.count()
    user.save(update_fields=["last_login"])
    Source.objects.filter(pk__in=[]).update(status="idle")
    assert AuditEntry.objects.count() == before


@pytest.mark.django_db
def test_admin_changes_name_the_admin_and_the_channel(staff: tuple[User, dict[str, str]]) -> None:
    boss, _ = staff
    User.objects.filter(pk=boss.pk).update(is_superuser=True)
    browser = Client()
    browser.force_login(boss)
    user = User.objects.create_user("erin")
    c = Collection.objects.create(slug="c", name="c")
    resp = browser.post(
        "/admin/catalogue/collectionrole/add/",
        {"user": user.pk, "collection": c.pk, "role": "curator"},
        headers={"X-Forwarded-For": "192.0.2.44"},
    )
    assert resp.status_code == 302, resp.content[:500]
    entry = last("role.create")
    assert (entry.actor_name, entry.via, entry.ip) == ("boss", "admin", "192.0.2.44")


@pytest.mark.django_db
def test_collections_sources_and_webhooks_through_the_api(
    client: Client,
    staff: tuple[User, dict[str, str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oss_server.pipeline import retention

    monkeypatch.setattr(retention, "purge_source", lambda *a, **k: None)  # no Solr here
    _, auth = staff
    monkeypatch.setenv("SEMASCOPE_SOURCE_ROOTS", str(tmp_path))
    resp = client.post(
        "/api/collections", data={"slug": "docs"}, content_type="application/json", headers=auth
    )
    assert resp.status_code == 201
    made = last("collection.create")
    assert (made.actor_name, made.via, made.collection) == ("boss", "token", "docs")
    source = client.post(
        "/api/collections/docs/sources",
        data={
            "name": "bucket",
            "connector": "filesystem",
            "settings": {"path": str(tmp_path), "password": SECRET},
        },
        content_type="application/json",
        headers=auth,
    ).json()
    assert last("source.create").detail["connector"] == "filesystem"
    row = Source.objects.get(pk=source["id"])
    row.settings = {**row.settings, "password": SECRET + "-new"}
    row.enabled = False
    row.save()
    changed = last("source.update")
    assert changed.detail["changed"] == ["settings", "enabled"]
    assert changed.detail["enabled"] == {"from": True, "to": False}
    assert client.delete(f"/api/sources/{row.pk}", headers=auth).status_code == 204
    assert last("source.delete").collection == "docs"
    hook = client.post(
        "/api/curation/webhooks",
        data={"url": "https://hooks.example.org/T000/" + SECRET, "secret": SECRET},
        content_type="application/json",
        headers=auth,
    ).json()
    assert last("webhook.create").target_name == "hooks.example.org"
    assert client.delete(f"/api/curation/webhooks/{hook['id']}", headers=auth).status_code == 204
    assert last("webhook.delete").actor_name == "boss"
    no_secret_anywhere(SECRET)
    assert Webhook.objects.count() == 0


@pytest.mark.django_db
def test_exports_and_bulk_work_are_recorded(
    client: Client, staff: tuple[User, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import searchplus

    class EmptySolr:
        def select(self, collection: str, **params: Any) -> dict[str, Any]:
            return {"response": {"numFound": 0, "docs": []}, "facet_counts": {}}

    monkeypatch.setattr(searchplus, "solr_client", lambda: EmptySolr())
    _, auth = staff
    Collection.objects.create(slug="c", name="c")
    body = {"q": "odysseus", "collection": "c"}
    client.post("/api/search/export.csv", data=body, content_type="application/json", headers=auth)
    entry = last("export.csv")
    assert entry.collection == "c" and entry.detail["q"] == "odysseus"
    from oss_server.pipeline import tasks

    monkeypatch.setattr(tasks.reindex_collection, "delay", lambda *a, **k: None)
    client.post("/api/collections/c/reindex?stages=embed", headers=auth)
    assert last("collection.reindex").detail == {"stages": ["embed"]}


@pytest.mark.django_db
def test_pack_jobs_and_removal_are_recorded(
    client: Client, staff: tuple[User, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.knowledge import tasks as knowledge_tasks
    from oss_server.knowledge.models import Pack

    monkeypatch.setattr(knowledge_tasks.run_pack_job, "delay", lambda *a, **k: None)
    _, auth = staff
    Pack.objects.create(
        pack_id="p",
        version="1",
        title="P",
        base_iri="https://e/p#",
        status="retired",
        manifest_hash="m",
        graph_iri="urn:p",
    )
    assert last("pack.create").detail["version"] == "1"
    resp = client.post("/api/knowledge/packs/p/activate?version=1", headers=auth)
    assert resp.status_code == 202, resp.content
    assert last("pack.activate").target_name == "p@1"
    from oss_server.knowledge import store

    monkeypatch.setattr(store, "drop_graph", lambda *a, **k: None)
    from oss_server.api import knowledge

    class NoSolr:
        def delete_by_query(self, *a: Any) -> None: ...
        def commit(self, *a: Any) -> None: ...
        def count(self, *a: Any) -> int:
            return 0

    import oss_server.services as services

    monkeypatch.setattr(services, "solr_client", lambda: NoSolr())
    assert knowledge is not None
    resp = client.delete("/api/knowledge/packs/p/versions/1", headers=auth)
    assert resp.status_code == 200, resp.content
    assert last("pack.remove").target_name == "p@1"
    assert last("pack.delete").target_name.startswith("p")


@pytest.mark.django_db
def test_entries_are_append_only_and_expire_after_the_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oss_server.audit.log import record
    from oss_server.audit.tasks import purge_expired

    record("test.event", detail={"n": 1})
    entry = last("test.event")
    entry.action = "rewritten"
    with pytest.raises(AppendOnlyError):
        entry.save()
    with pytest.raises(AppendOnlyError):
        entry.delete()
    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.filter(pk=entry.pk).update(action="x")
    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.all().delete()
    AuditEntry.objects.create(action="old.event", at=timezone.now() - timedelta(days=400))
    monkeypatch.setenv("SEMASCOPE_AUDIT_RETENTION_DAYS", "0")
    assert purge_expired() == 0
    monkeypatch.setenv("SEMASCOPE_AUDIT_RETENTION_DAYS", "365")
    assert purge_expired() == 1
    assert "old.event" not in actions() and "test.event" in actions()
    assert last("audit.purge").detail == {"removed": 1, "retention_days": 365}


@pytest.mark.django_db
def test_details_never_hold_credentials() -> None:
    from oss_server.audit.log import record

    record(
        "test.event",
        detail={
            "settings": {"secret_key": SECRET, "headers": {"Authorization": "Bearer " + SECRET}},
            "url": f"https://user:{SECRET}@host.example/x",
        },
    )
    no_secret_anywhere(SECRET)


@pytest.mark.django_db
def test_staff_read_the_log_through_the_api(
    client: Client, staff: tuple[User, dict[str, str]]
) -> None:
    _, auth = staff
    User.objects.create_user("frank")
    issue_token(User.objects.get(username="frank"), "x")
    rows = client.get("/api/audit?action=token.&limit=10", headers=auth).json()
    assert [r["action"] for r in rows] == ["token.create", "token.create"]
    assert rows[0]["id"] > rows[1]["id"]
    older = client.get(f"/api/audit?before={rows[0]['id']}&action=token.", headers=auth).json()
    assert [r["id"] for r in older] == [rows[1]["id"]]
    assert client.get("/api/audit?limit=5000", headers=auth).status_code == 422
    reader = User.objects.create_user("reader")
    other = {"Authorization": f"Bearer {issue_token(reader, 'r')}"}
    assert client.get("/api/audit", headers=other).status_code == 403


@pytest.mark.django_db
def test_the_admin_shows_the_log_read_only(staff: tuple[User, dict[str, str]]) -> None:
    boss, _ = staff
    User.objects.filter(pk=boss.pk).update(is_superuser=True)
    browser = Client()
    browser.force_login(boss)
    entry = last("token.create")
    assert browser.get("/admin/audit/auditentry/").status_code == 200
    assert browser.get(f"/admin/audit/auditentry/{entry.pk}/change/").status_code == 200
    assert browser.get("/admin/audit/auditentry/add/").status_code == 403
    assert (
        browser.post(f"/admin/audit/auditentry/{entry.pk}/delete/", {"post": "yes"}).status_code
        == 403
    )
    assert AuditEntry.objects.filter(pk=entry.pk).exists()
