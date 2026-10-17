"""The tag stage against a fake Solr Tagger that behaves like the real one: UTF-16 offsets,
`tagsLimit`, one request per window. Nothing is lost at the cap or at a window edge, and
spans after astral characters stay on their words."""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.stages import tag as tag_mod
from oss_pipeline.stages.tag import TagStage, utf16_to_index

ENTRIES: dict[str, dict[str, Any]] = {
    "zeus": {
        "id": "zeus",
        "iri": "https://myth/zeus",
        "label": "Zeus",
        "label_type": "pref",
        "kind": "individual",
        "pack_id": "myth",
        "pack_version": "1",
    },
    "hera": {
        "id": "hera",
        "iri": "https://myth/hera",
        "label": "HERA",
        "label_type": "alt",
        "kind": "individual",
        "pack_id": "myth",
        "pack_version": "1",
        "case_sensitive": True,
    },
    "mount": {
        "id": "mount",
        "iri": "https://myth/olympus",
        "label": "Mount Olympus",
        "label_type": "pref",
        "kind": "individual",
        "pack_id": "myth",
        "pack_version": "1",
    },
}


class FakeTagger:
    """Finds the labels case-insensitively in the posted text, reports UTF-16 offsets, and
    stops at `tagsLimit` like Solr's TaggerRequestHandler."""

    def __init__(self, default_limit: int = 5000) -> None:
        self.requests: list[str] = []
        self.default_limit = default_limit  # `tagsLimit` in solrconfig.xml

    def __call__(self, request: httpx.Request) -> httpx.Response:
        window = request.content.decode("utf-8")
        self.requests.append(window)
        limit = int(request.url.params.get("tagsLimit", self.default_limit))
        found: list[dict[str, Any]] = []
        for entry in ENTRIES.values():
            for m in re.finditer(re.escape(str(entry["label"])), window, re.IGNORECASE):
                start16 = len(window[: m.start()].encode("utf-16-le")) // 2
                end16 = len(window[: m.end()].encode("utf-16-le")) // 2
                found.append({"startOffset": start16, "endOffset": end16, "ids": [entry["id"]]})
        found.sort(key=lambda t: t["startOffset"])
        tags = found[:limit]
        docs = [ENTRIES[i] for t in tags for i in t["ids"]]
        return httpx.Response(
            200, json={"tagsCount": len(tags), "tags": tags, "response": {"docs": docs}}
        )


def _run(text: str, **settings: Any) -> tuple[WorkingDocument, FakeTagger]:
    fake = FakeTagger(int(settings.get("tag_limit", 5000)))
    doc = WorkingDocument(
        id="d", collection="c", source_id="1", uri="file:///x", content_hash="h", text=text
    )
    ctx = StageContext(
        collection="c",
        profile="default",
        settings={"solr_url": "http://solr/solr", **settings},
        pack_versions={"myth": "1"},
    )
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return TagStage(http=http).run(doc, ctx), fake


def _spans(doc: WorkingDocument, iri: str) -> list[tuple[int, int]]:
    tag = next(t for t in doc.tags if t.iri == iri)
    return [(s.start, s.end) for s in tag.spans]


def test_utf16_offsets_become_character_offsets() -> None:
    assert utf16_to_index("plain")(3) == 3
    convert = utf16_to_index("\U0001f30aa\U0001f41ab")
    # UTF-16: 🌊=0-2, a=2, 🐚=3-5, b=5
    assert [convert(u) for u in (0, 2, 3, 5, 6)] == [0, 1, 2, 3, 4]


def test_spans_after_emoji_stay_on_their_words() -> None:
    text = "\U0001f30a\U0001f30a Zeus and HERA, \U0001f41a then Zeus and Hera again."
    out, _ = _run(text)
    zeus = _spans(out, "https://myth/zeus")
    assert [text[s:e] for s, e in zeus] == ["Zeus", "Zeus"], zeus
    # a case-sensitive label is checked against the right characters: "HERA" matches,
    # "Hera" does not (with UTF-16 offsets the check would read the wrong text and drop both)
    hera = _spans(out, "https://myth/hera")
    assert [text[s:e] for s, e in hera] == ["HERA"], hera


def test_a_request_that_reaches_the_tag_limit_is_continued() -> None:
    text = " ".join(["Zeus"] * 23) + " Mount Olympus"
    out, fake = _run(text, tag_limit=5)
    assert len(_spans(out, "https://myth/zeus")) == 23, "no match is dropped at the cap"
    assert _spans(out, "https://myth/olympus") == [(len(text) - 13, len(text))]
    assert out.fields["tag_limit_reached"] >= 4 and out.fields["tag_requests"] == len(fake.requests)
    assert "tag_truncated_at" not in out.fields
    starts = [s for s, _ in _spans(out, "https://myth/zeus")]
    assert starts == sorted(set(starts)), "each occurrence once"


def test_a_label_across_a_window_edge_is_found_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tag_mod, "MAX_TEXT", 60)
    monkeypatch.setattr(tag_mod, "OVERLAP", 20)
    text = ("x" * 33 + " Mount Olympus ") * 6  # every window edge cuts a label somewhere
    out, fake = _run(text)
    expected = [m.start() for m in re.finditer("Mount Olympus", text)]
    assert [s for s, _ in _spans(out, "https://myth/olympus")] == expected
    assert len(fake.requests) > 1 and out.fields["tag_requests"] == len(fake.requests)


def test_a_cap_of_one_match_still_moves_forward() -> None:
    text = "Zeus Zeus, Mount Olympus Zeus"
    out, fake = _run(text, tag_limit=1)
    assert [text[s:e] for s, e in _spans(out, "https://myth/zeus")] == ["Zeus"] * 3
    assert _spans(out, "https://myth/olympus") == [(11, 24)]
    assert len(fake.requests) >= 4


def test_an_endless_document_says_where_tagging_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tag_mod, "MAX_REQUESTS", 2)
    text = " ".join(["Zeus"] * 30)
    out, fake = _run(text, tag_limit=5)
    assert len(fake.requests) == 2
    assert out.fields["tag_truncated_at"] > 0
    assert len(_spans(out, "https://myth/zeus")) < 30
