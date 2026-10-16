"""W3C Web Annotations (ANN-07): notes and decisions out, W3C and Hypothesis annotations in."""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from oss_server import services as server_services
from oss_server.api import annotations_w3c
from oss_server.api.auth import issue_token
from oss_server.catalogue.models import Collection, CollectionRole, Document, Source
from oss_server.curation import services
from oss_server.curation.models import Annotation, Proposal
from oss_server.exports import w3c

TEXT = (
    "Tell me, O Muse, of that ingenious hero who travelled far and wide.\n\n"
    "The hero   went home. Then the hero slept."
)
FLOOD = "https://p/themes#flood"
ODYSSEUS = "https://semascope.dev/ontology/base#Person/odysseus"


class Stores:
    def add(self, *args: Any, **kwargs: Any) -> None:
        return None

    def execute(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _stores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_services, "solr_client", lambda: Stores())
    monkeypatch.setattr(server_services, "graph_client", lambda: Stores())


@pytest.fixture
def world(db: None, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("SEMASCOPE_DEFAULT_ROLE", "none")
    a = Collection.objects.create(slug="a", name="a")
    b = Collection.objects.create(slug="b", name="b", settings={"pseudonymise": True})
    curator = User.objects.create_user("cura")
    reader = User.objects.create_user("reed")
    CollectionRole.objects.create(user=curator, collection=a, role="curator")
    CollectionRole.objects.create(user=reader, collection=a, role="reader")
    docs = {}
    for c in (a, b):
        s = Source.objects.create(collection=c, name="s", connector="filesystem")
        docs[c.slug] = Document.objects.create(
            id=c.slug * 32,
            collection=c,
            source=s,
            uri="https://example.org/odyssey book 1.html",
            content_hash="h",
            title="Odyssey",
            text=TEXT,
            status=Document.Status.INDEXED,
            tags=[
                {"iri": FLOOD, "label": "Flood", "kind": "concept", "stage": "tag"},
                {"iri": ODYSSEUS, "label": "Odysseus", "kind": "entity", "stage": "ner"},
            ],
        )
    return {"curator": curator, "reader": reader, "docs": docs}


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user, 't')}"}


def _page(client: Client, auth: dict[str, str], query: str) -> dict[str, Any]:
    resp = client.get(f"/api/annotations/w3c?{query}", headers=auth)
    assert resp.status_code == 200, resp.content
    assert resp["Content-Type"] == w3c.CONTENT_TYPE
    body: dict[str, Any] = resp.json()
    return body


def _import(client: Client, auth: dict[str, str], payload: Any, query: str = "collection=a") -> Any:
    return client.post(
        f"/api/annotations/w3c/import?{query}",
        data=json.dumps(payload),
        content_type="application/json",
        headers=auth,
    )


# --- parsing and anchoring ---------------------------------------------------------------------


def test_payload_shapes_all_yield_their_annotations() -> None:
    one = {"type": "Annotation", "target": "https://x"}
    assert w3c.annotation_items(one) == [one]
    assert w3c.annotation_items([one, one, "junk"]) == [one, one]
    assert w3c.annotation_items({"type": "AnnotationPage", "items": [one]}) == [one]
    assert w3c.annotation_items({"type": "AnnotationCollection", "first": {"items": [one]}}) == [
        one
    ]
    assert w3c.annotation_items({"total": 1, "rows": [one]}) == [one]
    assert w3c.annotation_items("nope") == []


def test_parse_w3c_bodies_selectors_and_creator() -> None:
    item = w3c.parse(
        {
            "id": "https://anno.example/1",
            "type": "Annotation",
            "motivation": "commenting",
            "creator": {"id": "https://people.example/ann", "name": "Ann", "email": "x@y"},
            "created": "2026-01-02T03:04:05Z",
            "body": [
                {"type": "TextualBody", "value": "Who is he?", "purpose": "commenting"},
                {"type": "TextualBody", "value": "hero", "purpose": "tagging"},
                {"type": "SpecificResource", "source": "https://ignored"},
            ],
            "target": {
                "source": "https://example.org/doc",
                "selector": [
                    {
                        "type": "TextQuoteSelector",
                        "exact": "hero",
                        "prefix": "Then the ",
                        "suffix": " slept",
                    },
                    {"type": "TextPositionSelector", "start": 5, "end": 9},
                ],
            },
        }
    )
    assert item is not None
    assert (item.id, item.source, item.text, item.tags) == (
        "https://anno.example/1",
        "https://example.org/doc",
        "Who is he?",
        ["hero"],
    )
    assert (item.exact, item.prefix, item.suffix, item.start, item.end) == (
        "hero",
        "Then the ",
        " slept",
        5,
        9,
    )
    assert item.creator == {"id": "https://people.example/ann", "name": "Ann"}  # no email kept
    assert w3c.parse({"type": "Annotation", "body": "x"}) is None  # no target


def test_parse_hypothesis_rows() -> None:
    item = w3c.parse(
        {
            "id": "hyp-1",
            "uri": "https://example.org/doc",
            "user": "acct:ann@hypothes.is",
            "text": "a note",
            "tags": ["t1", "  ", "t2"],
            "target": [
                {
                    "source": "https://example.org/doc",
                    "selector": [{"type": "TextQuoteSelector", "exact": "hero"}],
                }
            ],
        }
    )
    assert item is not None and item.format == "hypothesis"
    assert (item.text, item.tags, item.exact) == ("a note", ["t1", "t2"], "hero")
    assert item.creator == {"id": "acct:ann@hypothes.is"}


def test_locate_prefers_the_quote_and_uses_context_to_choose() -> None:
    def at(
        exact: str,
        prefix: str = "",
        suffix: str = "",
        start: int | None = None,
        end: int | None = None,
    ) -> dict[str, int] | None:
        return w3c.locate(
            TEXT,
            w3c.Incoming(
                id="x", source="s", exact=exact, prefix=prefix, suffix=suffix, start=start, end=end
            ),
        )

    last = TEXT.rindex("hero")
    assert at("hero", prefix="Then the ", suffix=" slept") == {"start": last, "end": last + 4}
    first = TEXT.index("hero")
    assert at("hero", prefix="ingenious ") == {"start": first, "end": first + 4}
    # a position that agrees with the quote is used; one that does not is ignored
    assert at("hero", start=last, end=last + 4) == {"start": last, "end": last + 4}
    assert at("hero", prefix="ingenious ", start=0, end=4) == {"start": first, "end": first + 4}
    # whitespace differs between a rendered page and the extracted text
    spaced = TEXT.index("hero   went")
    assert at("hero went home") == {"start": spaced, "end": spaced + len("hero   went home")}
    assert at("not in the text") is None
    assert w3c.locate(TEXT, w3c.Incoming(id="x", source="s", start=2, end=6)) == {
        "start": 2,
        "end": 6,
    }
    assert w3c.locate(TEXT, w3c.Incoming(id="x", source="s", start=2, end=10**6)) is None


def test_document_key_from_urn_or_page() -> None:
    k = "a" * 32
    assert w3c.document_key(f"urn:semascope:doc:{k}") == k
    assert w3c.document_key(f"https://host:8443/documents/{k}?q=1") == k
    assert w3c.document_key("https://example.org/documents/abc") is None


# --- export ------------------------------------------------------------------------------------


def test_export_notes_and_decisions_in_force(world: dict[str, Any], client: Client) -> None:
    doc, curator = world["docs"]["a"], world["curator"]
    start = TEXT.index("ingenious hero")
    Annotation.objects.create(
        document=doc,
        author=curator,
        text="who?",
        tags=["q"],
        span={"start": start, "end": start + 14},
    )
    services.propose(
        doc.collection,
        Proposal.Kind.ACCEPT_TAG,
        FLOOD,
        document=doc,
        user=curator,
        params={"label": "Flood", "kind": "concept"},
        reason="clearly",
    )
    services.propose(
        doc.collection,
        Proposal.Kind.REJECT_TAG,
        FLOOD,
        document=doc,
        user=curator,
        params={"label": "Flood", "kind": "concept"},
    )
    page = _page(client, _auth(world["reader"]), f"document={doc.id}")
    assert page["@context"] == w3c.ANNO_CONTEXT and page["type"] == "AnnotationPage"
    assert page["partOf"]["total"] == 2 and "next" not in page
    note, decision = page["items"]
    assert note["motivation"] == "commenting" and note["creator"]["nickname"] == "cura"
    assert note["target"]["source"] == "https://example.org/odyssey%20book%201.html"
    assert note["target"]["scope"].endswith(f"/documents/{doc.id}")
    quote = next(s for s in note["target"]["selector"] if s["type"] == "TextQuoteSelector")
    assert quote["exact"] == "ingenious hero" and quote["prefix"].endswith("of that ")
    assert {b["purpose"] for b in note["body"]} == {"commenting", "tagging"}
    # only the reject is in force; the accept before it shows with history=true
    assert decision["motivation"] == "assessing"
    assert {"type": "SpecificResource", "source": FLOOD} in decision["body"]
    history = _page(client, _auth(world["reader"]), f"document={doc.id}&history=true")
    accepted = [i for i in history["items"] if i["motivation"] == "classifying"]
    assert len(accepted) == 1
    assert {"type": "TextualBody", "value": "clearly", "purpose": "commenting"} in accepted[0][
        "body"
    ]


def test_export_pages_and_is_read_scoped(
    world: dict[str, Any], client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(annotations_w3c, "PAGE_SIZE", 2)
    for i in range(3):
        Annotation.objects.create(document=world["docs"]["a"], text=f"note {i}")
    Annotation.objects.create(document=world["docs"]["b"], text="secret")
    auth = _auth(world["reader"])
    first = _page(client, auth, "")
    assert first["partOf"]["total"] == 3  # collection b is not readable
    assert [i["body"][0]["value"] for i in first["items"]] == ["note 0", "note 1"]
    assert first["next"].endswith("page=1")
    second = _page(client, auth, "page=1")
    assert [i["body"][0]["value"] for i in second["items"]] == ["note 2"] and "next" not in second
    assert client.get("/api/annotations/w3c?collection=b", headers=auth).status_code == 403
    doc_b = world["docs"]["b"].id
    assert client.get(f"/api/annotations/w3c?document={doc_b}", headers=auth).status_code == 403


def test_export_pseudonymises_entity_decisions(world: dict[str, Any], client: Client) -> None:
    doc = world["docs"]["b"]
    staff = User.objects.create_user("boss", is_staff=True)
    services.propose(
        doc.collection,
        Proposal.Kind.REJECT_TAG,
        ODYSSEUS,
        document=doc,
        user=staff,
        params={"label": "Odysseus", "kind": "entity"},
    )
    page = _page(client, _auth(staff), "collection=b")
    text = json.dumps(page)
    assert "dysseus" not in text
    assert "urn:semascope:entity:h:" in text


# --- import ------------------------------------------------------------------------------------


def _w3c(
    id_: str,
    exact: str | None = "hero",
    source: str = "https://example.org/odyssey book 1.html",
    **extra: Any,
) -> dict[str, Any]:
    target: dict[str, Any] = {"source": source}
    if exact:
        target["selector"] = {"type": "TextQuoteSelector", "exact": exact, "prefix": "Then the "}
    return {
        "id": id_,
        "type": "Annotation",
        "creator": "Ann",
        "created": "2026-01-01T00:00:00Z",
        "body": {"type": "TextualBody", "value": f"note {id_}"},
        "target": target,
        **extra,
    }


def test_import_creates_notes_records_them_and_is_idempotent(
    world: dict[str, Any], client: Client
) -> None:
    doc, auth = world["docs"]["a"], _auth(world["curator"])
    payload = {
        "type": "AnnotationCollection",
        "first": {
            "items": [
                _w3c("x1"),
                _w3c("x2", exact="words that are not there"),
                _w3c("x3", source="https://example.org/elsewhere"),
                _w3c("x4", exact=None, source=f"urn:semascope:doc:{doc.id}"),
                {"type": "Annotation", "body": "no target"},
            ]
        },
    }
    out = _import(client, auth, payload).json()
    assert out | {"problems": []} == {
        "received": 5,
        "imported": 3,
        "already_imported": 0,
        "unmatched": 2,
        "anchored": 1,
        "unanchored": 2,
        "dry_run": False,
        "problems": [],
    }
    reasons = {p["id"]: p["reason"] for p in out["problems"]}
    assert "no document" in reasons["x3"] and "quote not found" in reasons["x2"]
    notes = {n.text: n for n in Annotation.objects.filter(document=doc)}
    last = TEXT.rindex("hero")
    assert notes["note x1"].span == {"start": last, "end": last + 4}
    assert notes["note x2"].span is None and notes["note x1"].author == world["curator"]
    recorded = Proposal.objects.get(target=f"annotation:{notes['note x1'].pk}")
    assert recorded.kind == Proposal.Kind.ADD_NOTE
    assert recorded.params["imported"]["id"] == "x1"
    assert recorded.params["imported"]["creator"] == {"type": "Person", "nickname": "Ann"}
    # importing the same file again adds nothing
    again = _import(client, auth, payload).json()
    assert (again["imported"], again["already_imported"]) == (0, 3)
    assert Annotation.objects.filter(document=doc).count() == 3
    # the export names the original id and creator
    page = _page(client, auth, f"document={doc.id}&decisions=false")
    first = next(i for i in page["items"] if i["id"].endswith(str(notes["note x1"].pk)))
    assert first["via"] == "x1" and first["creator"] == {"type": "Person", "nickname": "Ann"}
    assert first["created"] == "2026-01-01T00:00:00Z"


def test_import_round_trips_an_export(world: dict[str, Any], client: Client) -> None:
    src, auth = world["docs"]["a"], _auth(world["curator"])
    start = TEXT.index("ingenious")
    Annotation.objects.create(document=src, text="round", span={"start": start, "end": start + 9})
    exported = _page(client, auth, f"document={src.id}&decisions=false")
    Annotation.objects.all().delete()
    out = _import(client, auth, exported).json()
    assert (out["imported"], out["anchored"]) == (1, 1)
    note = Annotation.objects.get(document=src)
    assert (note.text, note.span) == ("round", {"start": start, "end": start + 9})


def test_import_needs_curator_matches_only_its_collection_and_dry_runs(
    world: dict[str, Any], client: Client
) -> None:
    reader, curator = _auth(world["reader"]), _auth(world["curator"])
    assert _import(client, reader, [_w3c("y1")]).status_code == 403
    assert _import(client, curator, [_w3c("y1")], "collection=b").status_code == 403
    # a document id from another collection does not match here
    other = _w3c("y2", exact=None, source=f"urn:semascope:doc:{world['docs']['b'].id}")
    out = _import(client, curator, [other, _w3c("y3")], "collection=a&dry_run=true").json()
    assert (out["imported"], out["unmatched"], out["dry_run"]) == (1, 1, True)
    assert not Annotation.objects.exists()
    bad = client.post(
        "/api/annotations/w3c/import?collection=a",
        data="{",
        content_type="application/json",
        headers=curator,
    )
    assert bad.status_code == 422


def test_import_takes_a_hypothesis_export_as_an_uploaded_file(
    world: dict[str, Any], client: Client
) -> None:
    rows = {
        "total": 1,
        "rows": [
            {
                "id": "h1",
                "uri": "https://example.org/odyssey%20book%201.html",
                "user": "acct:a@hypothes.is",
                "text": "from hypothesis",
                "tags": ["muse"],
                "target": [
                    {
                        "source": "https://example.org/odyssey%20book%201.html",
                        "selector": [{"type": "TextQuoteSelector", "exact": "O Muse"}],
                    }
                ],
            }
        ],
    }
    upload = SimpleUploadedFile("export.json", json.dumps(rows).encode(), "application/json")
    resp = client.post(
        "/api/annotations/w3c/import?collection=a",
        data={"file": upload},
        headers=_auth(world["curator"]),
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["anchored"] == 1
    note = Annotation.objects.get()
    assert (note.text, note.tags, note.span) == (
        "from hypothesis",
        ["muse"],
        {"start": 9, "end": 15},
    )
