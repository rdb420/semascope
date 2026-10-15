"""The runtime providers: windowing, the remote entity contract, the embedding sidecar, MinerU."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.clients.mineru import MineruClient
from oss_pipeline.embedders.sidecar_embedder import SidecarEmbedder
from oss_pipeline.stages.ner import (
    Entity,
    NerStage,
    RemoteProvider,
    facet_for,
    parse_gliner_results,
    windows,
)


def test_windows_cover_the_text_and_cut_on_paragraphs() -> None:
    text = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(30))
    pieces = windows(text, 1000)
    assert "".join(p for _, p in pieces) == text
    assert all(len(p) <= 1000 for _, p in pieces)
    assert [o for o, _ in pieces] == [
        sum(len(p) for _, p in pieces[:i]) for i in range(len(pieces))
    ]
    assert sum(1 for _, p in pieces if p.endswith("\n")) >= len(pieces) - 1


def test_gliner_results_flat_and_nested() -> None:
    flat = [[{"label": "person", "text": "Odysseus", "start": 0, "end": 8, "confidence": 0.93}]]
    nested = [
        {"entities": {"Place": [{"text": "Ithaca", "start": 12, "end": 18, "confidence": 0.8}]}}
    ]
    out = parse_gliner_results(flat, ["Odysseus of Ithaca"])
    assert out == [[Entity("person", "Odysseus", 0, 8, 0.93)]]
    out = parse_gliner_results(nested, ["Odysseus of Ithaca"])
    assert out == [[Entity("place", "Ithaca", 12, 18, 0.8)]]


def test_remote_provider_posts_windows_with_labels_and_token() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"auth": request.headers.get("authorization"), "body": body})
        results = [
            [{"label": "deity", "text": "Zeus", "start": t.find("Zeus"), "end": t.find("Zeus") + 4}]
            if "Zeus" in t
            else []
            for t in body["texts"]
        ]
        return httpx.Response(200, json={"results": results})

    provider = RemoteProvider("http://ner.test", "tok", transport=httpx.MockTransport(handler))
    stage = NerStage(provider)
    text = ("Zeus sat on Olympus. " + "x " * 100 + "\n\n") * 3
    doc = WorkingDocument(
        id="d", collection="c", source_id="s", uri="u", content_hash="h", text=text, language="en"
    )
    ctx = StageContext(
        collection="c",
        profile="default",
        settings={
            "ner_provider": "remote",
            "ner_window_chars": 300,
            "ner_labels": {"deity": {"class": "Deity", "description": "a god"}},
        },
        pack_versions={},
    )
    doc.fields["x_stale_entities_ss"] = ["Old"]
    doc.fields["places"] = ["Nowhere"]
    out = stage.run(doc, ctx)
    assert "x_stale_entities_ss" not in out.fields and "places" not in out.fields
    assert seen and seen[0]["auth"] == "Bearer tok"
    labels = seen[0]["body"]["labels"]  # type: ignore[index]
    assert "person" in labels and labels["deity"] == "a god"
    zeus = [t for t in out.tags if t.label == "Zeus"]
    assert len(zeus) == 1 and zeus[0].iri.endswith("#Deity/zeus")
    assert len(zeus[0].spans) == 3 and [s.start for s in zeus[0].spans] == [
        i for i in range(len(text)) if text.startswith("Zeus", i)
    ]
    assert out.fields["x_deity_entities_ss"] == ["Zeus"]
    assert out.fields["ner_model"] == "remote:http://ner.test"


def test_facets_for_base_and_pack_classes() -> None:
    assert facet_for("person", "Person") == "persons"
    assert facet_for("easement", "Easement") == "x_easement_entities_ss"


def test_sidecar_embedder_records_the_served_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["texts"]
        return httpx.Response(
            200,
            json={"vectors": [[0.1, 0.2] for _ in texts], "model": "mini", "dimensions": 2},
        )

    e = SidecarEmbedder("x", "http://embed.test/", transport=httpx.MockTransport(handler))
    assert e.embed(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
    assert e.model_name == "mini" and e.dimensions == 2
    assert e.embed_query("q") == [0.1, 0.2]


def test_mineru_client_runs_the_job_flow(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/parse/jobs" and request.method == "POST":
            body = json.loads(request.content)
            assert body["files"][0]["source"]["type"] == "inline"
            assert body["output_formats"] == ["markdown"]
            return httpx.Response(202, json={"job_id": "j1", "status": "queued", "files": []})
        if path == "/v1/parse/jobs/j1":
            polls["n"] += 1
            if polls["n"] < 2:
                return httpx.Response(200, json={"job_id": "j1", "status": "running", "files": []})
            return httpx.Response(
                200,
                json={
                    "job_id": "j1",
                    "status": "completed",
                    "files": [
                        {
                            "name": "a.pdf",
                            "status": "completed",
                            "output_files": {"markdown": {"file_id": "f9", "bytes": 7}},
                        }
                    ],
                },
            )
        if path == "/v1/files/f9/content":
            return httpx.Response(200, content=b"# Title\n\nbody")
        return httpx.Response(404)

    client = MineruClient(
        "http://mineru.test", "tok", poll_s=0.0, transport=httpx.MockTransport(handler)
    )
    assert client.parse(pdf, "application/pdf") == "# Title\n\nbody"
    assert polls["n"] == 2
