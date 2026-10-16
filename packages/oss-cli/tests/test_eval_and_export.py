"""`oss eval` (ANL-16, APP-07): judgement files, P@k, R@k, nDCG@k and MRR; `oss export`."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from typer.testing import CliRunner

import oss_cli.main as cli
from oss_cli import evaluate
from oss_cli.main import app

REPO = Path(__file__).resolve().parents[3]
runner = CliRunner(env={"COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb"})

DOCS = {f"{c * 32}": f"file:///corpus/{name}.txt" for c, name in zip("abcd", "wxyz", strict=True)}
BY_NAME = {uri.rsplit("/", 1)[1]: did for did, uri in DOCS.items()}


def _hit(name: str) -> dict[str, Any]:
    did = BY_NAME[name]
    return {"id": did, "uri": DOCS[did]}


def fake_api(
    rankings: dict[tuple[str, str], list[str]], calls: list[dict[str, Any]]
) -> httpx.Client:
    """The search API: `*:*` lists the collection; other queries answer from `rankings`."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if body["q"] == "*:*":
            start = (body["page"] - 1) * body["page_size"]
            hits = [{"id": d, "uri": u} for d, u in list(DOCS.items())[start : start + 100]]
        else:
            hits = [_hit(n) for n in rankings.get((body["q"], body["mode"]), [])]
            hits = hits[: body["page_size"]]
        return httpx.Response(200, json={"hits": hits, "total": len(hits)})

    return httpx.Client(base_url="http://api/api", transport=httpx.MockTransport(handler))


def judgements(tmp_path: Path, queries: list[dict[str, Any]], **extra: Any) -> Path:
    path = tmp_path / "j.yaml"
    path.write_text(
        yaml.safe_dump(
            {"format": evaluate.FORMAT, "collection": "demo", "k": 3, "queries": queries, **extra}
        )
    )
    return path


def test_scores_follow_their_definitions() -> None:
    # ranked grades 0, 3, 1 against judged grades 3, 1, 2
    s = evaluate.score([0, 3, 1], [3, 1, 2], k=3)
    assert s.precision == pytest.approx(2 / 3)
    assert s.recall == pytest.approx(2 / 3)
    assert s.rr == pytest.approx(1 / 2)
    dcg = 7 / math.log2(3) + 1 / math.log2(4)
    ideal = 7 / math.log2(2) + 3 / math.log2(3) + 1 / math.log2(4)
    assert s.ndcg == pytest.approx(dcg / ideal)
    perfect = evaluate.score([3, 2, 1], [1, 2, 3], k=3)
    assert (perfect.precision, perfect.recall, perfect.ndcg, perfect.rr) == (1, 1, 1, 1)
    nothing = evaluate.score([0, 0], [2], k=2)
    assert (nothing.precision, nothing.recall, nothing.ndcg, nothing.rr) == (0, 0, 0, 0)


def test_keys_match_by_id_full_uri_or_uri_ending() -> None:
    did, uri = "a" * 32, "file:///corpus/w.txt"
    assert evaluate.matches(did, did, uri) and evaluate.matches(uri, did, uri)
    assert evaluate.matches("w.txt", did, uri) and evaluate.matches("corpus/w.txt", did, uri)
    assert not evaluate.matches("w.tx", did, uri) and not evaluate.matches("aw.txt", did, uri)


def test_load_rejects_what_is_not_a_judgement_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("queries: []\n")
    with pytest.raises(evaluate.JudgementError, match="not a judgement file"):
        evaluate.load(bad)
    with pytest.raises(evaluate.JudgementError, match="grade"):
        evaluate.load(judgements(tmp_path, [{"id": "q", "query": "x", "relevant": {"w.txt": 5}}]))
    with pytest.raises(evaluate.JudgementError, match="twice"):
        evaluate.load(judgements(tmp_path, [{"id": "q", "query": "x"}, {"id": "q", "query": "y"}]))


def test_evaluate_scores_each_mode_and_leaves_out_documents_not_held(tmp_path: Path) -> None:
    path = judgements(
        tmp_path,
        [
            {"id": "q1", "query": "odysseus", "relevant": {"w.txt": 3, "x.txt": 1, "gone.txt": 2}},
            {"id": "q2", "query": "flood", "relevant": {"y.txt": 2}},
            {"id": "q3", "query": "missing", "relevant": {"gone.txt": 3}},
        ],
    )
    rankings = {
        ("odysseus", "keyword"): ["x.txt", "z.txt", "w.txt"],
        ("odysseus", "semantic"): ["w.txt", "x.txt"],
        ("flood", "keyword"): ["y.txt"],
        ("flood", "semantic"): ["z.txt"],
    }
    calls: list[dict[str, Any]] = []
    report = evaluate.evaluate(
        fake_api(rankings, calls), evaluate.load(path), modes=["keyword", "semantic"]
    )
    assert report.absent == {"q1": ["gone.txt"], "q3": ["gone.txt"]} and report.skipped == ["q3"]
    assert report.documents == 4
    keyword, semantic = report.modes
    assert semantic.per_query["q1"].ndcg == pytest.approx(1.0)  # the best order of what is held
    assert keyword.per_query["q1"].rr == 1.0 and keyword.per_query["q1"].ndcg < 1.0
    assert semantic.per_query["q2"].precision == 0.0 and keyword.mean("recall") == 1.0
    searches = [c for c in calls if c["q"] != "*:*"]
    assert {(c["collection"], c["page_size"]) for c in searches} == {("demo", 3)}
    md = evaluate.markdown(report)
    assert "| keyword | " in md and "| semantic | " in md and "q3" in md and "gone.txt" in md


def test_eval_command_prints_markdown_and_writes_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = judgements(tmp_path, [{"id": "q1", "query": "odysseus", "relevant": {"w.txt": 3}}])
    rankings = {("odysseus", m): ["w.txt"] for m in evaluate.MODES}
    monkeypatch.setattr(cli, "_api", lambda: fake_api(rankings, []))
    out = tmp_path / "report.md"
    result = runner.invoke(app, ["eval", str(path), "--k", "5", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "| hybrid | 0.200 | 1.000 | 1.000 | 1.000 | 0 |" in result.output
    assert out.read_text() == result.output


def test_the_demo_judgements_load_and_name_corpus_texts() -> None:
    loaded = evaluate.load(REPO / "packs" / "demo" / "judgements.yaml")
    sources = yaml.safe_load((REPO / "packs" / "demo" / "sources.yaml").read_text())
    slugs = {f"{t['slug']}.txt" for t in sources["texts"]}
    assert len(loaded.queries) >= 20 and loaded.collection == "demo"
    for q in loaded.queries:
        assert q.relevant and set(q.relevant) <= slugs, q.id


def test_export_annotations_follows_pages_into_one_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "0"))
        body: dict[str, Any] = {
            "@context": "http://www.w3.org/ns/anno.jsonld",
            "type": "AnnotationPage",
            "partOf": {"id": "http://h/api/annotations/w3c?collection=demo", "label": "demo"},
            "items": [{"id": f"urn:semascope:annotation:{page}"}],
        }
        if page == 0:
            assert request.url.params["collection"] == "demo"
            body["next"] = "http://h/api/annotations/w3c?collection=demo&page=1"
        return httpx.Response(200, json=body)

    monkeypatch.setattr(
        cli,
        "_api",
        lambda: httpx.Client(base_url="http://h/api", transport=httpx.MockTransport(handler)),
    )
    out = tmp_path / "a.jsonld"
    result = runner.invoke(
        app, ["export", "annotations", "--collection", "demo", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(out.read_text())
    assert doc["type"] == "AnnotationCollection" and doc["total"] == 2
    assert [i["id"] for i in doc["first"]["items"]] == [
        "urn:semascope:annotation:0",
        "urn:semascope:annotation:1",
    ]


def test_export_search_streams_to_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"@prefix base: <x> .\n")

    monkeypatch.setattr(
        cli,
        "_api",
        lambda: httpx.Client(base_url="http://h/api", transport=httpx.MockTransport(handler)),
    )
    out = tmp_path / "s.ttl"
    result = runner.invoke(
        app, ["export", "search", "flood", "--collection", "demo", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"@prefix base: <x> .\n"
    assert seen[0].url.params["format"] == "turtle" and json.loads(seen[0].content)["q"] == "flood"
