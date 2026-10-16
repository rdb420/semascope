"""More like this (FUZZ-12): MoreLikeThis on text, kNN on vectors, fused and read-scoped."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server import services as server_services
from oss_server.api import root, similar
from oss_server.api import search as search_mod
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, Source

A, B, C, D = "a" * 32, "b" * 32, "c" * 32, "d" * 32


class SimilarSolr:
    """Answers the four queries more-like-this makes: index size, MLT, kNN and metadata."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.mlt = [B, C]
        self.chunks = [
            {"document_id": A},
            {"document_id": D},
            {"document_id": D},
            {"document_id": B},
        ]

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"collection": collection, **params})
        q = str(params.get("q", ""))
        docs: list[dict[str, Any]]
        if q.startswith("{!mlt"):
            docs = [{"id": i} for i in self.mlt]
        elif q.startswith("{!knn"):
            docs = self.chunks
        elif q.startswith("{!terms"):
            docs = [
                {"id": i, "title_txt": [f"title {i[0]}"], "uri_s": f"u:{i[0]}", "collection_s": "a"}
                for i in q.split("}", 1)[1].split(",")
            ]
        else:
            return {"response": {"numFound": 40, "docs": []}}
        return {"response": {"numFound": len(docs), "docs": docs}}


class Embedder:
    model_name = "bge-small"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[1.0, 0.0] + [0.0] * 382, [0.0, 1.0] + [0.0] * 382][: len(texts)] + [
            [0.0] * 384 for _ in texts[2:]
        ]


@pytest.fixture
def world(db: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    a = Collection.objects.create(slug="a", name="a")
    b = Collection.objects.create(slug="b", name="b")
    reader = User.objects.create_user("reader")
    CollectionRole.objects.create(user=reader, collection=a, role="reader")
    s = Source.objects.create(collection=a, name="s", connector="filesystem")
    sb = Source.objects.create(collection=b, name="s", connector="filesystem")
    doc = Document.objects.create(
        id=A, collection=a, source=s, uri="u:a", content_hash="h", text="word " * 2000
    )
    hidden = Document.objects.create(id=D, collection=b, source=sb, uri="u:d", content_hash="h")
    return {"reader": reader, "doc": doc, "hidden": hidden}


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


@pytest.fixture
def solr(monkeypatch: pytest.MonkeyPatch) -> SimilarSolr:
    fake = SimilarSolr()
    monkeypatch.setattr(server_services, "solr_client", lambda: fake)
    monkeypatch.setattr(root, "solr_client", lambda: fake)
    return fake


# --- more like this ----------------------------------------------------------------------------


def test_mlt_params_drop_words_in_most_documents_and_the_document_itself() -> None:
    p = similar.mlt_params(A, ['collection_s:("a")'], 30, 40)
    assert p["q"].startswith("{!mlt qf=title_txt,text_txt mintf=2 mindf=2 maxdf=20 ")
    assert p["q"].endswith("}" + A) and p["defType"] == "lucene"
    assert p["fq"] == ['collection_s:("a")', f'-id:"{A}"']
    assert "mindf=1 maxdf=1" in similar.mlt_params(A, [], 5, 2)["q"]


def test_passages_spread_over_the_text_and_centroid_is_unit_length() -> None:
    text = "".join(str(i % 10) * 100 for i in range(100))  # 10,000 characters
    parts = similar.passages(text, n=4, size=1000)
    assert len(parts) == 4 and parts[0] == text[:1000] and parts[-1] == text[-1000:]
    assert similar.passages("short", n=4) == ["short"] and similar.passages("  ") == []
    v = similar.centroid([[3.0, 0.0], [0.0, 4.0]])
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9 and v[0] < v[1]


def test_fuse_rewards_documents_both_signals_find() -> None:
    scores = similar.fuse([B, C], [D, B])
    assert sorted(scores, key=lambda k: -scores[k]) == [B, D, C]


def test_similar_fuses_text_and_vectors_inside_the_readable_collections(
    world: dict[str, Any], client: Client, solr: SimilarSolr, monkeypatch: pytest.MonkeyPatch
) -> None:
    embedder = Embedder()
    monkeypatch.setattr(search_mod, "query_embedder", lambda: embedder)
    resp = client.get(f"/api/documents/{A}/similar?rows=5", headers=_auth(world["reader"]))
    assert resp.status_code == 200, resp.content
    out = resp.json()
    assert out["mode"] == "hybrid" and out["text_error"] is None and out["vector_error"] is None
    # B is first in MLT and in the kNN list (after A itself, which is skipped)
    assert [h["id"] for h in out["hits"]][0] == B
    assert out["hits"][0]["text_rank"] == 1 and out["hits"][0]["vector_rank"] == 2
    mlt = next(c for c in solr.calls if str(c["q"]).startswith("{!mlt"))
    knn = next(c for c in solr.calls if str(c["q"]).startswith("{!knn"))
    meta = next(c for c in solr.calls if str(c["q"]).startswith("{!terms"))
    for call in (mlt, knn, meta):
        assert 'collection_s:("a")' in call["fq"]  # never collection b
    assert f'-document_id:"{A}"' in knn["fq"] and 'model_s:"bge-small"' in knn["fq"]
    assert knn["collection"] == "chunks" and "f=vector " in knn["q"]
    assert len(embedder.seen) == similar.PASSAGES


def test_similar_reports_a_missing_signal_and_checks_access(
    world: dict[str, Any], client: Client, solr: SimilarSolr, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> Any:
        raise RuntimeError("no embedding endpoint")

    monkeypatch.setattr(search_mod, "query_embedder", broken)
    auth = _auth(world["reader"])
    out = client.get(f"/api/documents/{A}/similar", headers=auth).json()
    assert "no embedding endpoint" in out["vector_error"]
    assert [h["id"] for h in out["hits"]] == [B, C]  # the text signal still answers
    vector_only = client.get(f"/api/documents/{A}/similar?mode=vector", headers=auth).json()
    assert vector_only["hits"] == [] and vector_only["vector_error"]
    assert client.get(f"/api/documents/{A}/similar?mode=x", headers=auth).status_code == 422
    assert client.get(f"/api/documents/{D}/similar", headers=auth).status_code == 403
    assert client.get(f"/api/documents/{A}/similar?collection=b", headers=auth).status_code == 403
