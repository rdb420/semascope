"""`oss eval`: relevance of search against judged queries (parity rows ANL-16, APP-07).

A judgement file (YAML, `format: semascope-judgements/1`) lists queries and,
for each, the documents that are relevant to it with a grade (1 partly, 2
relevant, 3 highly relevant; anything unlisted is not relevant):

    format: semascope-judgements/1
    collection: demo          # searched unless --collection says otherwise
    k: 10                     # cut-off unless --k says otherwise
    queries:
      - id: q01
        query: Odysseus
        relevant:
          odyssey.txt: 3      # a document id, its full URI, or the end of its URI
          iliad.txt: 3

Every query runs once per search mode through the API (`POST /api/search`)
and is scored on its top k hits:

- P@k: the share of the k hits that are relevant (grade 1 or more)
- R@k: the share of the relevant documents found in the top k
- nDCG@k: graded gain (2^grade - 1) discounted by log2(rank + 1), divided by
  the best possible ordering of the judged documents
- MRR: 1 / the rank of the first relevant hit (0 if none in the top k)

Judged documents the collection does not hold are left out (and reported), so
a judgement set for a whole corpus can score a subset of it; a query with no
judged document left is skipped. The report is Markdown.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml

FORMAT = "semascope-judgements/1"
MODES = ("keyword", "semantic", "hybrid")
LIST_LIMIT = 10000  # documents listed to find which judged ones the collection holds


@dataclass
class Query:
    id: str
    query: str
    relevant: dict[str, int]


@dataclass
class Judgements:
    queries: list[Query]
    collection: str | None = None
    k: int = 10
    name: str = ""
    sha256: str = ""


@dataclass
class Scores:
    precision: float
    recall: float
    ndcg: float
    rr: float


@dataclass
class ModeResult:
    mode: str
    per_query: dict[str, Scores] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def mean(self, attr: str) -> float:
        values = [getattr(s, attr) for s in self.per_query.values()]
        return sum(values) / len(values) if values else 0.0


@dataclass
class Report:
    judgements: Judgements
    collection: str | None
    k: int
    modes: list[ModeResult]
    absent: dict[str, list[str]]  # query id -> judged documents the collection does not hold
    skipped: list[str]  # queries with no judged document left
    documents: int | None  # documents in the collection, when listed


class JudgementError(ValueError):
    pass


def load(path: Path) -> Judgements:
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise JudgementError(f"{path}: not a judgement file (format: {FORMAT} expected)")
    queries: list[Query] = []
    seen: set[str] = set()
    for i, q in enumerate(data.get("queries") or []):
        qid = str(q.get("id") or f"q{i + 1}")
        if qid in seen:
            raise JudgementError(f"{path}: query id {qid} appears twice")
        seen.add(qid)
        if not str(q.get("query") or "").strip():
            raise JudgementError(f"{path}: query {qid} has no text")
        relevant: dict[str, int] = {}
        for key, grade in (q.get("relevant") or {}).items():
            if not isinstance(grade, int) or not 0 <= grade <= 3:
                raise JudgementError(f"{path}: {qid}: grade of {key} must be 0 to 3")
            if grade:
                relevant[str(key)] = grade
        queries.append(Query(id=qid, query=str(q["query"]), relevant=relevant))
    if not queries:
        raise JudgementError(f"{path}: no queries")
    return Judgements(
        queries=queries,
        collection=data.get("collection"),
        k=int(data.get("k") or 10),
        name=str(data.get("name") or path.stem),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def matches(key: str, doc_id: str, uri: str) -> bool:
    """A judgement key names a hit by id, by full URI, or by the end of its URI."""

    return key in (doc_id, uri) or uri.endswith("/" + key.lstrip("/"))


def grades_of(hits: list[dict[str, Any]], relevant: dict[str, int]) -> list[int]:
    """The grade of each hit, in rank order (0 for unjudged)."""

    out: list[int] = []
    for h in hits:
        out.append(
            max(
                (g for key, g in relevant.items() if matches(key, h["id"], h.get("uri", ""))),
                default=0,
            )
        )
    return out


def dcg(grades: Iterable[int]) -> float:
    return float(sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(grades)))


def score(ranked: list[int], judged: list[int], k: int) -> Scores:
    """Scores of one ranked list of grades against the judged grades (all > 0)."""

    top = ranked[:k]
    found = sum(1 for g in top if g > 0)
    ideal = dcg(sorted(judged, reverse=True)[:k])
    first = next((i + 1 for i, g in enumerate(top) if g > 0), None)
    return Scores(
        precision=found / k,
        recall=found / len(judged) if judged else 0.0,
        ndcg=dcg(top) / ideal if ideal else 0.0,
        rr=1.0 / first if first else 0.0,
    )


def _check(resp: httpx.Response) -> Any:
    if resp.status_code >= 400:
        raise RuntimeError(
            f"{resp.request.method} {resp.request.url.path}: {resp.status_code} {resp.text[:300]}"
        )
    return resp.json()


def held_documents(api: httpx.Client, collection: str | None) -> tuple[list[tuple[str, str]], bool]:
    """(id, URI) of the collection's documents, and whether the list is complete."""

    out: list[tuple[str, str]] = []
    page = 1
    while len(out) < LIST_LIMIT:
        body: dict[str, Any] = {
            "q": "*:*",
            "collection": collection,
            "page": page,
            "page_size": 100,
            "facets": [],
            "highlight": False,
        }
        res = _check(api.post("/search", json=body))
        out.extend((h["id"], h.get("uri", "")) for h in res["hits"])
        if len(res["hits"]) < 100:
            return out, True
        page += 1
    return out, False


def evaluate(
    api: httpx.Client,
    judgements: Judgements,
    *,
    modes: Iterable[str] = MODES,
    k: int | None = None,
    collection: str | None = None,
) -> Report:
    k = k or judgements.k
    collection = collection or judgements.collection
    held, complete = held_documents(api, collection)
    absent: dict[str, list[str]] = {}
    queries: list[tuple[Query, dict[str, int]]] = []
    skipped: list[str] = []
    for q in judgements.queries:
        present = {
            key: g
            for key, g in q.relevant.items()
            if not complete or any(matches(key, i, u) for i, u in held)
        }
        missing = sorted(set(q.relevant) - set(present))
        if missing:
            absent[q.id] = missing
        if present:
            queries.append((q, present))
        else:
            skipped.append(q.id)
    results: list[ModeResult] = []
    for mode in modes:
        if mode not in MODES:
            raise JudgementError(f"unknown mode {mode}; known: {', '.join(MODES)}")
        result = ModeResult(mode=mode)
        for q, present in queries:
            body: dict[str, Any] = {
                "q": q.query,
                "collection": collection,
                "page_size": k,
                "mode": mode,
                "facets": [],
                "highlight": False,
            }
            try:
                hits = _check(api.post("/search", json=body))["hits"]
            except (RuntimeError, httpx.HTTPError) as exc:
                result.errors[q.id] = str(exc)[:300]
                continue
            result.per_query[q.id] = score(grades_of(hits, present), list(present.values()), k)
        results.append(result)
    return Report(
        judgements=judgements,
        collection=collection,
        k=k,
        modes=results,
        absent=absent,
        skipped=skipped,
        documents=len(held) if complete else None,
    )


def markdown(report: Report, *, per_query: bool = True) -> str:
    j, k = report.judgements, report.k
    scored = len(j.queries) - len(report.skipped)
    lines = [
        f"## Relevance: {j.name} on {report.collection or 'every readable collection'}",
        "",
        f"{scored} of {len(j.queries)} queries scored at k = {k}; judgements sha256 "
        f"`{j.sha256[:12]}`"
        + (f"; the collection holds {report.documents} documents" if report.documents else "")
        + ".",
        "",
        f"| mode | P@{k} | R@{k} | nDCG@{k} | MRR@{k} | errors |",
        "|---|---|---|---|---|---|",
    ]
    for m in report.modes:
        lines.append(
            f"| {m.mode} | {m.mean('precision'):.3f} | {m.mean('recall'):.3f} | "
            f"{m.mean('ndcg'):.3f} | {m.mean('rr'):.3f} | {len(m.errors)} |"
        )
    if per_query and report.modes:
        lines += ["", f"nDCG@{k} per query:", ""]
        lines.append("| query | " + " | ".join(m.mode for m in report.modes) + " |")
        lines.append("|---" * (len(report.modes) + 1) + "|")
        by_id = {q.id: q for q in j.queries}
        for qid in [q.id for q in j.queries if q.id not in report.skipped]:
            cells = []
            for m in report.modes:
                s = m.per_query.get(qid)
                cells.append(f"{s.ndcg:.2f}" if s else "error")
            lines.append(f"| {qid} {by_id[qid].query} | " + " | ".join(cells) + " |")
    if report.absent:
        lines += ["", "Judged documents the collection does not hold (left out):", ""]
        for qid, keys in report.absent.items():
            lines.append(f"- {qid}: {', '.join(keys)}")
    if report.skipped:
        lines += ["", f"Skipped (nothing judged is held): {', '.join(report.skipped)}"]
    for m in report.modes:
        for qid, err in m.errors.items():
            lines.append(f"- {m.mode} {qid}: {err}")
    return "\n".join(lines) + "\n"


def command(
    judgements: Path = typer.Argument(..., exists=True, dir_okay=False, help="Judgement file"),
    collection: str | None = typer.Option(None, help="Collection to search (default: the file's)"),
    k: int | None = typer.Option(None, help="Cut-off rank (default: the file's, else 10)"),
    mode: list[str] = typer.Option(list(MODES), help="Search modes to score (repeatable)"),
    out: Path | None = typer.Option(None, help="Also write the Markdown report to this file"),
    per_query: bool = typer.Option(True, help="Include the nDCG of every query"),
) -> None:
    """Score search relevance against judged queries: P@k, R@k, nDCG@k and MRR per mode."""

    from oss_cli.main import _api, console

    try:
        loaded = load(judgements)
        with _api() as api:
            report = evaluate(api, loaded, modes=mode, k=k, collection=collection)
    except (JudgementError, RuntimeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    text = markdown(report, per_query=per_query)
    print(text, end="")  # Markdown on stdout, unstyled, so it can be redirected
    if out is not None:
        out.write_text(text, encoding="utf-8")
