"""Pack conflicts resolve by binding precedence; draft labels stay scoped and survive a sync."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server import services as server_services
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection
from oss_server.knowledge.models import Concept, Pack, PackBinding, PackDelta
from oss_server.knowledge.precedence import pack_order
from oss_server.pipeline.tasks import pack_versions_for

IRI = "https://shared/ithaca"


def _pack(pack_id: str, label: str) -> Pack:
    pack = Pack.objects.create(
        pack_id=pack_id,
        version="1",
        title=pack_id,
        base_iri=f"https://{pack_id}/",
        status=Pack.Status.ACTIVE,
        manifest_hash="x",
        graph_iri=f"urn:{pack_id}",
    )
    Concept.objects.create(pack=pack, iri=IRI, kind="individual", pref_label=label, taggable=True)
    return pack


@pytest.fixture
def auth(db: None) -> dict[str, str]:
    user = User.objects.create_user("prec", password="v" * 16, is_staff=True)
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


@pytest.mark.django_db
def test_pack_order_by_binding_precedence() -> None:
    _pack("zeta", "Ithaca (zeta)")
    _pack("alpha", "Ithaca (alpha)")
    _pack("mid", "Ithaca (mid)")
    texts = Collection.objects.create(slug="texts", name="texts")
    other = Collection.objects.create(slug="other", name="other")
    assert pack_order() == ["alpha", "mid", "zeta"], "no bindings: by pack id"
    PackBinding.objects.create(collection=texts, pack_id="zeta", precedence=10)
    PackBinding.objects.create(collection=texts, pack_id="alpha", precedence=20)
    assert pack_order(texts) == ["zeta", "alpha"]
    assert list(pack_versions_for(texts)) == ["zeta", "alpha"]
    # unbound collections and global lookups use the lowest precedence any binding gives
    assert pack_order(other) == ["zeta", "alpha", "mid"] == list(pack_versions_for(other))
    assert pack_order() == ["zeta", "alpha", "mid"]


@pytest.mark.django_db
def test_concept_lookup_with_the_iri_in_two_active_packs(
    client: Client, auth: dict[str, str]
) -> None:
    _pack("alpha", "Ithaca (alpha)")
    _pack("beta", "Ithaca (beta)")
    texts = Collection.objects.create(slug="texts", name="texts")
    url = f"/api/knowledge/concepts?iri={IRI}"
    r = client.get(url, headers=auth)
    assert r.status_code == 200 and r.json()["pack_id"] == "alpha", r.content
    PackBinding.objects.create(collection=texts, pack_id="beta", precedence=1)
    PackBinding.objects.create(collection=texts, pack_id="alpha", precedence=2)
    assert client.get(url, headers=auth).json()["pack_id"] == "beta"
    assert client.get(url + "&collection=texts", headers=auth).json()["pack_id"] == "beta"
    assert client.get("/api/knowledge/concepts?iri=https://nope", headers=auth).status_code == 404
    assert client.get(f"/api/reconcile/preview?iri={IRI}").json()["label"] == "Ithaca (beta)"


class FakeSolr:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []

    def add(self, collection: str, docs: list[dict[str, Any]], **_: Any) -> None:
        self.docs.update({d["id"]: d for d in docs})

    def delete_by_query(self, collection: str, query: str) -> None:
        self.deleted.append(query)
        self.docs.clear()  # the only pack version in these tests

    def commit(self, collection: str) -> None:
        pass


@pytest.mark.django_db
def test_draft_labels_are_scoped_and_survive_a_dictionary_sync(
    client: Client, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    solr = FakeSolr()
    monkeypatch.setattr(server_services, "solr_client", lambda: solr)
    _pack("alpha", "Ithaca")
    Collection.objects.create(slug="texts", name="texts")
    Collection.objects.create(slug="other", name="other")
    r = client.post(
        "/api/knowledge/packs/alpha/deltas",
        data={
            "collection": "texts",
            "kind": "add_label",
            "params": {"iri": IRI, "label": "Ithake"},
        },
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 201, r.content
    drafts = [d for d in solr.docs.values() if d["label_type"] == "draft"]
    assert len(drafts) == 1 and drafts[0]["collection_scope"] == "texts"
    first_id = drafts[0]["id"]

    assert server_services.sync_dictionary("alpha", "1") == 2  # the pref label and the draft
    assert solr.deleted, "the sync replaced the pack version's entries"
    kept = solr.docs[first_id]
    assert kept["collection_scope"] == "texts" and kept["label"] == "Ithake"
    assert all(
        "collection_scope" not in d for d in solr.docs.values() if d["label_type"] != "draft"
    )

    PackDelta.objects.update(status=PackDelta.Status.REJECTED)
    assert server_services.sync_dictionary("alpha", "1") == 1, "a decided delta is dropped"
