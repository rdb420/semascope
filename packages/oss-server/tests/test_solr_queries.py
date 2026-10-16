"""Request values never change the shape of a Solr query.

Field names a request names (filters, facets, the histogram) must be plain field
names of an allowed kind; every value is one quoted term, the collection slug in
a delete-by-query included; collection slugs are Django slugs.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_pipeline.clients.solr import quote_term
from oss_pipeline.dictionary import dictionary_fq
from oss_server.api.auth import issue_token
from oss_server.api.schemas import SearchIn
from oss_server.api.search import build_params
from oss_server.catalogue.models import Collection

WIDEN = 'x" OR *:* OR "y'
FIELD_PAYLOADS = [
    "x_s} OR *:* {!tag=y_s",
    "{!lucene}*:*_s",
    "collection_s:* OR x_s",
    "x_ss}{!terms f=id}a",
    "language_s OR collection_s",
    "a b_paths",
    "x_s\n",
]


class RecordingSolr:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.selects: list[dict[str, Any]] = []

    def delete_by_query(self, collection: str, query: str) -> None:
        self.deleted.append(query)

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.selects.append(params)
        return {"response": {"numFound": 0, "docs": []}, "facet_counts": {}}


@pytest.fixture
def solr(monkeypatch: pytest.MonkeyPatch) -> RecordingSolr:
    from oss_server import services
    from oss_server.api import root

    fake = RecordingSolr()
    for module in (services, root):
        monkeypatch.setattr(module, "solr_client", lambda: fake)
    return fake


@pytest.fixture
def admin(db: None) -> dict[str, str]:
    user = User.objects.create_user("root", password="x" * 16, is_staff=True)
    return {"Authorization": f"Bearer {issue_token(user, 'test')}"}


def test_quote_term_keeps_a_value_one_term() -> None:
    assert quote_term(WIDEN) == '"x\\" OR *:* OR \\"y"'
    assert quote_term('a\\"') == '"a\\\\\\""'  # a trailing backslash cannot eat the quote
    assert quote_term("plain") == '"plain"'


@pytest.mark.parametrize("field", FIELD_PAYLOADS)
def test_filter_field_names_cannot_inject(field: str) -> None:
    params = build_params(SearchIn(filters={field: ["v"], "language_s": ["en"]}), ["a"])
    assert params["fq"] == ['collection_s:("a")', '{!tag=language_s}language_s:("en")']


@pytest.mark.parametrize("field", FIELD_PAYLOADS)
def test_facet_and_histogram_field_names_cannot_inject(field: str) -> None:
    params = build_params(SearchIn(facets=[field, "language_s"], histogram=field + "_dt"))
    assert params["facet.field"] == ["{!ex=language_s}language_s"]
    assert "facet.range" not in params


def test_dynamic_fields_of_the_allowed_kinds_still_work() -> None:
    req = SearchIn(
        filters={"x_owner_s": ["o"], "dewey-classes_paths": ["a/b"]},
        facets=["x_deity_entities_ss", "dewey-classes_paths"],
        histogram="modified_dt",
    )
    params = build_params(req)
    assert '{!tag=x_owner_s}x_owner_s:("o")' in params["fq"]
    assert '{!tag=dewey-classes_paths}dewey-classes_paths:("a/b")' in params["fq"]
    assert params["facet.field"] == [
        "{!ex=x_deity_entities_ss}x_deity_entities_ss",
        "{!ex=dewey-classes_paths}dewey-classes_paths",
    ]
    assert params["facet.range"] == "modified_dt"


def test_filter_values_and_the_collection_are_quoted() -> None:
    params = build_params(SearchIn(collection=WIDEN, filters={"language_s": [WIDEN]}))
    assert f"collection_s:{quote_term(WIDEN)}" in params["fq"]
    assert f"{{!tag=language_s}}language_s:({quote_term(WIDEN)})" in params["fq"]


def test_the_dictionary_filter_quotes_the_collection() -> None:
    fq = dictionary_fq({"p": "1"}, WIDEN)
    assert f"collection_scope:{quote_term(WIDEN)}" in fq


@pytest.mark.parametrize("slug", [WIDEN, "../uploads", "a/b", "a b", "", "x" * 65, "a}"])
def test_collection_slugs_must_be_slugs(client: Client, admin: dict[str, str], slug: str) -> None:
    r = client.post(
        "/api/collections", data={"slug": slug}, content_type="application/json", headers=admin
    )
    assert r.status_code == 422, (slug, r.content)
    assert not Collection.objects.exists()


def test_a_slug_still_works(client: Client, admin: dict[str, str]) -> None:
    r = client.post(
        "/api/collections",
        data={"slug": "Case_files-2026"},
        content_type="application/json",
        headers=admin,
    )
    assert r.status_code == 201, r.content


def test_deleting_a_collection_quotes_its_slug(
    client: Client, admin: dict[str, str], solr: RecordingSolr
) -> None:
    Collection.objects.create(slug=WIDEN, name="made before slugs were checked")
    r = client.delete(f"/api/collections/{quote(WIDEN, safe='')}", headers=admin)
    assert r.status_code == 204, r.content
    assert solr.deleted == [f"collection_s:{quote_term(WIDEN)}"] * 2


def test_concept_documents_quote_the_iri(
    client: Client, admin: dict[str, str], solr: RecordingSolr
) -> None:
    r = client.get("/api/knowledge/concepts/documents", {"iri": WIDEN}, headers=admin)
    assert r.status_code == 200, r.content
    (call,) = solr.selects
    assert (
        call["fq"][0]
        == f"concept_iris_ss:{quote_term(WIDEN)} OR entity_iris_ss:{quote_term(WIDEN)}"
    )


@pytest.mark.parametrize("slug", ["..", ".", "a/b", "../x", "a\\b", "", "x" * 65, "a b"])
def test_an_upload_root_is_built_from_slugs_only(tmp_path: Any, slug: str) -> None:
    from oss_pipeline.connectors.guard import SourceNotAllowed
    from oss_pipeline.connectors.upload import upload_root

    with pytest.raises(SourceNotAllowed):
        upload_root(tmp_path, slug)


def test_an_uploaded_file_name_stays_in_its_folder(tmp_path: Any) -> None:
    from oss_pipeline.connectors.upload import store_upload

    for name in ("..", ".", "../../escape.txt", "/etc/passwd"):
        path = store_upload(tmp_path, "c", name, b"x")
        assert path.is_file() and path.parent.parent == tmp_path / "uploads" / "c"


@pytest.mark.parametrize("slug", ["..", "."])
def test_uploads_to_a_dot_slug_cannot_leave_the_upload_area(
    client: Client,
    admin: dict[str, str],
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    slug: str,
) -> None:
    from django.core.files.uploadedfile import SimpleUploadedFile

    from oss_server.pipeline import tasks

    data = tmp_path / "data"
    monkeypatch.setenv("SEMASCOPE_DATA_DIR", str(data))
    monkeypatch.setattr(tasks.process_document, "delay", lambda *a, **k: None)
    Collection.objects.create(slug=slug, name="made before slugs were checked")
    r = client.post(
        f"/api/collections/{quote(slug, safe='')}/upload",
        {"files": SimpleUploadedFile("note.txt", b"hello")},
        headers=admin,
    )
    assert r.status_code == 400, r.content
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == [], written


def test_places_quote_the_collection(
    client: Client, admin: dict[str, str], solr: RecordingSolr, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import knowledge
    from oss_server.knowledge.models import Concept, Pack

    pack = Pack.objects.create(
        pack_id="p", version="1", title="P", base_iri="https://x/p#", manifest_hash="h",
        graph_iri="g:p", status=Pack.Status.ACTIVE,
    )  # fmt: skip
    Concept.objects.create(
        pack=pack, iri="https://x/p#troy", kind=Concept.Kind.INDIVIDUAL, pref_label="Troy",
        extra={"lat": 39.9, "lon": 26.2},
    )  # fmt: skip
    monkeypatch.setattr(knowledge, "read_scope", lambda request, collection=None: None)
    client.get("/api/knowledge/places", {"collection": WIDEN}, headers=admin)
    assert solr.selects, "places did not query Solr"
    assert f"collection_s:{quote_term(WIDEN)}" in solr.selects[0]["fq"]


def test_list_search_keeps_each_name_one_phrase(
    client: Client, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oss_server.api import search as search_mod
    from oss_server.api.schemas import SearchOut

    seen: list[str] = []

    def fake(client: Any, coll: str, req: SearchIn, allowed: Any) -> SearchOut:
        seen.append(req.q)
        return SearchOut(total=0, page=1, page_size=1, took_ms=0, hits=[], facets={})

    monkeypatch.setattr(search_mod, "search", fake)
    r = client.post(
        "/api/search/list",
        data={"names": [WIDEN]},
        content_type="application/json",
        headers=admin,
    )
    assert r.status_code == 200, r.content
    assert seen == [quote_term(WIDEN)]
