"""Dictionary conflicts resolve by pack precedence; confidence is the best label type seen."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from oss_core.contracts import StageContext, Tag, WorkingDocument
from oss_pipeline.dictionary import dictionary_fq
from oss_pipeline.stages import link as link_mod
from oss_pipeline.stages.link import LinkStage
from oss_pipeline.stages.tag import TagStage

TEXT = "Ithaca. Later, ITH again."
IRI = "https://shared/ithaca"


def _entry(
    eid: str, pack: str, label_type: str = "pref", label: str = "Ithaca", **extra: Any
) -> dict[str, Any]:
    return {
        "id": eid,
        "iri": IRI,
        "label": label,
        "pref_label": f"Ithaca ({pack})",
        "label_type": label_type,
        "kind": "individual",
        "pack_id": pack,
        "pack_version": "1",
        "scheme": f"{pack}-places",
        "path": "places/ithaca",
        **extra,
    }


def _tagger(body: dict[str, Any], seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _doc() -> WorkingDocument:
    return WorkingDocument(
        id="d", collection="texts", source_id="1", uri="file:///x", content_hash="h", text=TEXT
    )


def _ctx(packs: dict[str, str]) -> StageContext:
    return StageContext(
        collection="texts",
        profile="standard",
        settings={"solr_url": "http://solr/solr"},
        pack_versions=packs,
    )


# the same IRI in two packs, both entries returned for one occurrence (Solr order: a, b)
TWO_PACKS = {
    "response": {"docs": [_entry("a1", "pack-a"), _entry("b1", "pack-b")]},
    "tags": [{"startOffset": 0, "endOffset": 6, "ids": ["a1", "b1"]}],
}


@pytest.mark.parametrize(
    ("packs", "winner"),
    [({"pack-b": "1", "pack-a": "1"}, "pack-b"), ({"pack-a": "1", "pack-b": "1"}, "pack-a")],
)
def test_tag_attributes_a_shared_iri_to_the_higher_precedence_pack(
    packs: dict[str, str], winner: str
) -> None:
    seen: list[httpx.Request] = []
    out = TagStage(http=_tagger(TWO_PACKS, seen)).run(_doc(), _ctx(packs))
    assert len(out.tags) == 1
    tag = out.tags[0]
    assert tag.pack_id == winner and tag.label == f"Ithaca ({winner})"
    assert len(tag.spans) == 1, "one occurrence, one span, whatever the number of packs"
    assert out.fields[f"x_{winner}-places_paths"] == ["places/ithaca"]
    assert not any(k.endswith("_paths") and winner not in k for k in out.fields)


def test_tag_confidence_is_the_best_label_type_over_all_occurrences() -> None:
    body: dict[str, Any] = {
        "response": {
            "docs": [
                _entry("h", "pack-a", "hidden", "ITH", case_sensitive=True),
                _entry("p", "pack-a", "pref"),
            ]
        },
        # the hidden label comes first in the result, the pref label later in the text
        "tags": [
            {"startOffset": 15, "endOffset": 18, "ids": ["h"]},
            {"startOffset": 0, "endOffset": 6, "ids": ["p"]},
        ],
    }
    out = TagStage(http=_tagger(body, [])).run(_doc(), _ctx({"pack-a": "1"}))
    assert out.tags[0].confidence == 1.0 and len(out.tags[0].spans) == 2
    only_hidden = {"response": body["response"], "tags": body["tags"][:1]}
    out = TagStage(http=_tagger(only_hidden, [])).run(_doc(), _ctx({"pack-a": "1"}))
    assert out.tags[0].confidence == 0.75


def test_tag_asks_only_for_this_collections_drafts() -> None:
    seen: list[httpx.Request] = []
    TagStage(http=_tagger({"response": {"docs": []}, "tags": []}, seen)).run(
        _doc(), _ctx({"pack-a": "1"})
    )
    fq = seen[0].url.params["fq"]
    assert fq == dictionary_fq({"pack-a": "1"}, "texts")
    assert 'collection_scope:"texts"' in fq and "-collection_scope:[* TO *]" in fq


@pytest.mark.parametrize(
    ("packs", "winner"),
    [({"pack-b": "1", "pack-a": "1"}, "pack-b"), ({"pack-a": "1", "pack-b": "1"}, "pack-a")],
)
def test_link_picks_the_higher_precedence_pack_for_a_shared_label(
    monkeypatch: pytest.MonkeyPatch, packs: dict[str, str], winner: str
) -> None:
    docs = [
        {**_entry("a1", "pack-a"), "iri": "https://a/ithaca"},
        {**_entry("b1", "pack-b"), "iri": "https://b/ithaca"},
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=json.dumps({"response": {"docs": docs}}).encode())

    real = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    link_mod._dictionary.cache_clear()
    doc = _doc()
    doc.tags = [Tag(iri="base#Place/ithaca", label="Ithaca", kind="entity", stage="ner")]
    out = LinkStage().run(doc, _ctx(packs))
    assert out.tags[0].pack_id == winner
    assert out.tags[0].iri == f"https://{winner[-1]}/ithaca"
    assert out.tags[0].label == f"Ithaca ({winner})", "the pref label is fetched and used"
    assert 'collection_scope:"texts"' in requests[0].url.params["fq"]
    link_mod._dictionary.cache_clear()


def test_tag_records_every_path_of_a_tag_and_drops_stale_schemes() -> None:
    body = {
        "response": {"docs": [_entry("a1", "pack-a", paths=["places/ithaca", "islands/ithaca"])]},
        "tags": [{"startOffset": 0, "endOffset": 6, "ids": ["a1"]}],
    }
    doc = _doc()
    doc.fields["tagged_schemes"] = ["gone"]
    doc.fields["x_gone_paths"] = ["old/path"]
    out = TagStage(http=_tagger(body, [])).run(doc, _ctx({"pack-a": "1"}))
    assert out.fields["x_pack-a-places_paths"] == ["islands/ithaca", "places/ithaca"]
    assert out.fields["paths_by_tag"] == {
        IRI: {"pack-a-places": ["islands/ithaca", "places/ithaca"]}
    }
    assert "x_gone_paths" not in out.fields
