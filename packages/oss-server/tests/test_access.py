import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server.api.access import role_for
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole


@pytest.mark.django_db
def test_roles_resolve_per_collection_with_default(client: Client) -> None:
    c = Collection.objects.create(slug="a", name="a")
    reader = User.objects.create_user("r", password="p" * 16)
    curator = User.objects.create_user("c", password="p" * 16)
    CollectionRole.objects.create(user=curator, collection=c, role="curator")
    staff = User.objects.create_user("s", password="p" * 16, is_staff=True)
    assert role_for(reader, "a") == "reader" and role_for(curator, "a") == "curator"
    assert role_for(curator, "other") == "reader" and role_for(staff, "a") == "admin"
    auth = {"Authorization": f"Bearer {issue_token(reader, 't')}"}
    assert (
        client.post(
            "/api/collections", data={"slug": "b"}, content_type="application/json", headers=auth
        ).status_code
        == 403
    )
    auth_c = {"Authorization": f"Bearer {issue_token(curator, 't')}"}
    assert (
        client.post(
            "/api/collections", data={"slug": "b"}, content_type="application/json", headers=auth_c
        ).status_code
        == 403
    )
    auth_s = {"Authorization": f"Bearer {issue_token(staff, 't')}"}
    assert (
        client.post(
            "/api/collections", data={"slug": "b"}, content_type="application/json", headers=auth_s
        ).status_code
        == 201
    )
    assert client.get("/api/auth/me", headers=auth_c).json()["role"] == "reader"
