import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server.api.auth import issue_token
from oss_server.api.searchplus import _stopwords


@pytest.mark.django_db
def test_saved_searches_round_trip(client: Client) -> None:
    user = User.objects.create_user("ss", password="q" * 16)
    auth = {"Authorization": f"Bearer {issue_token(user, 't')}"}
    r = client.post(
        "/api/search/saved",
        data={"name": "floods", "query": {"q": "flood", "collection": "demo"}, "alert": True},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 201 and r.json()["alert"] is True
    bad = client.post(
        "/api/search/saved",
        data={"name": "x", "query": {"page_size": "nope"}},
        content_type="application/json",
        headers=auth,
    )
    assert bad.status_code == 422
    listed = client.get("/api/search/saved", headers=auth).json()
    assert [s["name"] for s in listed] == ["floods"]
    assert client.delete(f"/api/search/saved/{listed[0]['id']}", headers=auth).status_code == 204
    assert client.get("/api/search/saved", headers=auth).json() == []


def test_stopwords_include_solr_list_and_extras() -> None:
    words = _stopwords()
    assert {"the", "and", "thou", "unto"} <= words
