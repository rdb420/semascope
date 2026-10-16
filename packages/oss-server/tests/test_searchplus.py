from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server.api.auth import issue_token
from oss_server.api.searchplus import _stopwords


@pytest.mark.django_db
def test_saved_searches_round_trip(client: Client) -> None:
    from oss_server.catalogue.models import Collection, CollectionRole

    user = User.objects.create_user("ss", password="q" * 16)
    demo = Collection.objects.create(slug="demo", name="demo")
    CollectionRole.objects.create(user=user, collection=demo, role="reader")
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


@pytest.mark.django_db
def test_list_search_cascades_from_the_phrase_to_near_spellings(
    client: Client, monkeypatch: Any
) -> None:
    from oss_server.api import search as search_mod
    from oss_server.api.schemas import SearchHit, SearchIn, SearchOut

    asked: list[tuple[str, str | None, bool, dict[str, list[str]]]] = []
    found_by = {'"Odysseus"': 3, '"Pallas" "Athena"': 2, "odyseus~1": 1}

    def fake(_client: Any, _coll: str, req: SearchIn, _allowed: Any) -> SearchOut:
        asked.append((req.q, req.operator, req.stemming, req.filters))
        total = found_by.get(req.q, 0)
        hit = SearchHit(
            id="d",
            score=float(total),
            title="t",
            uri="u",
            collection="c",
            source_id="1",
            media_type=None,
            content_type_group=None,
            language=None,
            size=None,
            page_count=None,
            modified_at=None,
            indexed_at=None,
            preview_pages=0,
            snippets=[],
        )
        hits = [hit] if total else []
        return SearchOut(total=total, page=1, page_size=1, took_ms=0, hits=hits, facets={})

    monkeypatch.setattr(search_mod, "search", fake)
    user = User.objects.create_user("lister", password="x" * 16)
    client.force_login(user)
    body = {
        "names": ["Odysseus", "Pallas Athena", "Odyseus", "Nobody Atall"],
        "filters": {"language_s": ["en"]},
    }
    out = client.post("/api/search/list", data=body, content_type="application/json").json()
    assert {n: (r["method"], r["total"], r["best_score"]) for n, r in out.items()} == {
        "Odysseus": ("phrase", 3, 3.0),
        "Pallas Athena": ("words", 2, 2.0),
        "Odyseus": ("fuzzy", 1, 1.0),
        "Nobody Atall": (None, 0, None),
    }
    assert all(f == {"language_s": ["en"]} for *_, f in asked)  # filters reach every query
    assert ('"Pallas" "Athena"', "all", True, {"language_s": ["en"]}) in asked
    assert ("odyseus~1", "all", False, {"language_s": ["en"]}) in asked
    # only the phrase, when asked for
    asked.clear()
    body = {"names": ["Odyseus"], "methods": ["phrase"]}
    out = client.post("/api/search/list", data=body, content_type="application/json").json()
    assert out["Odyseus"]["total"] == 0 and len(asked) == 1


@pytest.mark.django_db
def test_terms_stand_out_against_the_readable_background(client: Client, monkeypatch: Any) -> None:
    from oss_server.api import search as search_mod
    from oss_server.api import searchplus
    from oss_server.api.schemas import SearchHit, SearchIn, SearchOut
    from oss_server.catalogue.models import Collection, CollectionRole, Document, Source

    demo = Collection.objects.create(slug="demo", name="demo")
    source = Source.objects.create(collection=demo, name="s", connector="upload")
    texts = {
        "a": "The cyclops roared. Cyclops and ships and sea.",
        "b": "A cyclops cave by the sea; ships waited.",
    }
    for i, text in texts.items():
        Document.objects.create(
            id=i, collection=demo, source=source, uri=i, content_hash=i, language="en", text=text
        )

    def fake(_c: Any, _coll: str, req: SearchIn, _allowed: Any) -> SearchOut:
        hits = [
            SearchHit(
                id=i,
                score=1.0,
                title=i,
                uri=i,
                collection="demo",
                source_id="1",
                media_type=None,
                content_type_group=None,
                language="en",
                size=None,
                page_count=None,
                modified_at=None,
                indexed_at=None,
                preview_pages=0,
                snippets=[],
            )
            for i in texts
        ]
        return SearchOut(total=2, page=1, page_size=2, took_ms=0, hits=hits, facets={})

    class Background:
        params: dict[str, Any] = {}

        def select(self, collection: str, **params: Any) -> dict[str, Any]:
            Background.params = params
            # 100 documents: "sea" and "ships" are everywhere, "cyclops" only in the two hits
            df = {"cyclops": 2, "sea": 90, "ships": 80, "cave": 1, "roared": 1, "waited": 30}
            counts = {q: df[q.split("}", 1)[1]] for q in params["facet.query"]}
            return {"response": {"numFound": 100}, "facet_counts": {"facet_queries": counts}}

    monkeypatch.setattr(search_mod, "search", fake)
    monkeypatch.setattr(searchplus, "solr_client", lambda: Background())
    user = User.objects.create_user("reader", password="q" * 16)
    CollectionRole.objects.create(user=user, collection=demo, role="reader")
    auth = {"Authorization": f"Bearer {issue_token(user, 't')}"}
    r = client.post(
        "/api/search/terms",
        data={"q": "cyclops", "collection": "demo"},
        content_type="application/json",
        headers=auth,
    )
    assert r.status_code == 200, r.content
    out = r.json()
    ranked = [t["term"] for t in out]
    # frequent in the hits but common everywhere ranks below the rare word of the hits
    assert ranked[0] == "cyclops" and ranked.index("sea") > ranked.index("cave")
    assert next(t for t in out if t["term"] == "sea")["background"] == 90
    # the background is the readable collection searched; words are term queries
    assert 'collection_s:"demo"' in Background.params["fq"]
    assert all(q.startswith("{!term f=text_txt}") for q in Background.params["facet.query"])
    assert "the" not in ranked and "and" not in ranked
