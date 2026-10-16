"""Semantic and hybrid search apply every filter to the vector candidates and count after it."""

from __future__ import annotations

from typing import Any

import pytest

from oss_server.api import search as search_mod
from oss_server.api.schemas import SearchIn

DOCS = {f"d{i:02d}": ("en" if i % 5 not in (1, 3) else "fr") for i in range(15)}  # 9 en, 6 fr


class FakeSolr:
    """Chunks rank d00..d14 by similarity; documents filter on language and collection."""

    def __init__(self, join_works: bool) -> None:
        self.join_works = join_works
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _passes(doc_id: str, fq: list[str]) -> bool:
        for f in fq:
            if "language_s" in f and f'"{DOCS[doc_id]}"' not in f:
                return False
            if f.startswith("collection_s:(") and '"a"' not in f:
                return False
        return True

    def select(self, collection: str, **params: Any) -> dict[str, Any]:
        self.calls.append({"collection": collection, **params})
        fq = list(params.get("fq") or [])
        if collection == "chunks":
            ids = list(DOCS)
            if any("{!join" in f for f in fq):
                if not self.join_works:
                    raise RuntimeError("join not available")
                ids = [d for d in ids if f'"{DOCS[d]}"' in params["docfq"]]
            docs = [{"id": f"{d}-0", "document_id": d, "text_txt": d} for d in ids]
            return {"response": {"numFound": len(docs), "docs": docs}}
        q = str(params["q"])
        if q.startswith("{!terms f=id}"):
            ids = [d for d in q.removeprefix("{!terms f=id}").split(",") if self._passes(d, fq)]
        else:  # the keyword half of hybrid
            ids = [d for d in DOCS if self._passes(d, fq)]
        docs = [{"id": d, "title_txt": d, "collection_s": "a", "language_s": DOCS[d]} for d in ids]
        facets = {"language_s": [DOCS[ids[0]], len(ids)]} if ids and params.get("facet") else {}
        return {
            "response": {"numFound": len(docs), "docs": docs},
            "facet_counts": {"facet_fields": facets},
        }


class Embedder:
    model_name = "m"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2]]


@pytest.mark.django_db
@pytest.mark.parametrize("join_works", [True, False])
@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
def test_filtered_semantic_pages_are_full_and_totals_count_the_filtered_set(
    join_works: bool, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search_mod, "query_embedder", lambda: Embedder())
    solr = FakeSolr(join_works)
    req = SearchIn(
        q="flood",
        mode=mode,
        filters={"language_s": ["en"]},
        facets=["language_s"],
        page_size=5,
    )
    run: Any = search_mod.search
    first = run(solr, "documents", req, ["a"])
    second = run(solr, "documents", req.model_copy(update={"page": 2}), ["a"])
    assert first.total == 9 and second.total == 9
    assert len(first.hits) == 5 and len(second.hits) == 4
    ids = [h.id for h in first.hits + second.hits]
    assert len(set(ids)) == 9 and all(DOCS[i] == "en" for i in ids)
    assert first.facets["language_s"][0].count == 9
    chunk_calls = [c for c in solr.calls if c["collection"] == "chunks"]
    assert all('collection_s:("a")' in c["fq"] for c in chunk_calls)
    if join_works:
        assert any("language_s" in str(c.get("docfq")) for c in chunk_calls)
