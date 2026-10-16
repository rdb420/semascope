"""The Django admin: staff reach every registered model's pages; everyone else is sent away."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth.models import User
from django.test import Client


@pytest.fixture
def staff(db: None) -> Client:
    client = Client()
    client.force_login(User.objects.create_superuser("root", password="x" * 16))
    return client


def test_admin_index_lists_the_semascope_apps(staff: Client) -> None:
    resp = staff.get("/admin/")
    assert resp.status_code == 200
    body = resp.content.decode()
    for app in ("catalogue", "curation", "knowledge"):
        assert f"/admin/{app}/" in body, app


def test_every_registered_model_has_working_pages(staff: Client) -> None:
    models = [m for m in admin.site._registry if m._meta.app_label != "auth"]  # noqa: SLF001
    assert len(models) >= 6, models
    for model in models:
        base = f"/admin/{model._meta.app_label}/{model._meta.model_name}/"
        assert staff.get(base).status_code == 200, base
        assert staff.get(base + "?q=x").status_code == 200, base
        assert staff.get(base + "add/").status_code in (200, 403), base


def test_the_admin_needs_a_staff_account(db: None) -> None:
    anonymous = Client().get("/admin/")
    assert anonymous.status_code == 302 and "/admin/login/" in anonymous["Location"]
    plain = Client()
    plain.force_login(User.objects.create_user("plain", password="x" * 16))
    assert plain.get("/admin/catalogue/document/").status_code == 302
