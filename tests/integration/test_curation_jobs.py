"""Curation and pack work on the live stack: the tree facet follows decisions (also after a
reindex), bulk tagging runs as a job, a plain list imports as a pack through a job, and the
sweep that re-applies decisions is registered with the worker.

Every test puts the demo collection back as it found it (a decision undone by the opposite
decision, an imported version removed), so the modules after this one see the same corpus.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx
import pytest
from _stack import collection, compose, run_task, wait_for

TRADITIONS = "https://semascope.dev/ontology/demo/traditions#"
TEXT_TYPES = "https://semascope.dev/ontology/demo/text-types#"
TIMEOUT = float(os.environ.get("SEMASCOPE_IT_TIMEOUT", "900"))


def in_facet(api: httpx.Client, field: str, value: str, q: str = "*:*") -> set[str]:
    body = {"q": q, "collection": collection(), "filters": {field: [value]}, "page_size": 100}
    return {h["id"] for h in api.post("/search", json=body).raise_for_status().json()["hits"]}


def decide(api: httpx.Client, doc_id: str, iri: str, decision: str, label: str) -> dict[str, Any]:
    r = api.post(
        f"/curation/documents/{doc_id}/tags",
        json={"iri": iri, "decision": decision, "label": label, "kind": "concept"},
    )
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    assert out["applied"], out
    return out


def test_a_rejected_concept_leaves_the_tree_facet_and_stays_out_after_reindex(
    api: httpx.Client, docs_by_slug: dict[str, dict[str, Any]]
) -> None:
    """SEMA-030 on the stack: the live Solr update and the index stage both build
    `traditions_paths` from the curated view."""

    if "odyssey" not in docs_by_slug:
        pytest.skip("the odyssey is not in the demo subset")
    doc = docs_by_slug["odyssey"]["id"]
    greek = TRADITIONS + "greek"
    assert doc in in_facet(api, "traditions_paths", "european/greek"), "precondition"
    decide(api, doc, greek, "reject", "Greek")
    wait_for(
        lambda: doc not in in_facet(api, "traditions_paths", "european/greek"),
        timeout=60,
        every=1.0,
        what="the rejected concept to leave the tree facet",
    )
    before = datetime.fromisoformat(api.get(f"/documents/{doc}").json()["indexed_at"])
    assert api.post(f"/documents/{doc}/reindex").status_code == 202

    def reindexed() -> bool:
        d = api.get(f"/documents/{doc}").json()
        stamp = d.get("indexed_at")
        return d["status"] == "indexed" and bool(stamp) and datetime.fromisoformat(stamp) > before

    wait_for(reindexed, timeout=TIMEOUT, every=3.0, what="the document's reindex")
    fields = api.get(f"/documents/{doc}").json()["fields"]
    assert greek in (fields.get("paths_by_tag") or {}), "the tag stage records each tag's paths"
    assert doc not in in_facet(api, "traditions_paths", "european/greek"), "back after reindex"

    decide(api, doc, greek, "accept", "Greek")  # put it back for the modules after this one
    wait_for(
        lambda: doc in in_facet(api, "traditions_paths", "european/greek"),
        timeout=60,
        every=1.0,
        what="the accepted concept to return to the tree facet",
    )


def follow_bulk(api: httpx.Client, job_id: int) -> dict[str, Any]:
    def finished() -> dict[str, Any] | None:
        job: dict[str, Any] = api.get(f"/curation/bulk-tags/{job_id}").raise_for_status().json()
        return job if job["status"] in ("done", "failed") else None

    out: dict[str, Any] = wait_for(finished, timeout=300, every=2.0, what=f"bulk job {job_id}")
    return out


def test_bulk_tagging_runs_as_a_job_and_feeds_the_tree_facet(api: httpx.Client) -> None:
    """ANN-04: one concept accepted on every document of a search, then rejected again."""

    fable = TEXT_TYPES + "fable"
    query = {"collection": collection(), "q": "Ithaca", "iri": fable}
    count = api.post("/curation/bulk-tags", json={**query, "decision": "accept", "dry_run": True})
    assert count.status_code == 200, count.text
    matched = count.json()["matched"]
    if matched == 0:
        pytest.skip("no demo text mentions Ithaca")
    if in_facet(api, "text-types_paths", "fable", q="Ithaca"):
        pytest.skip("a document of the search is already a fable; the undo would reject it")
    start = api.post(
        "/curation/bulk-tags", json={**query, "decision": "accept", "reason": "integration"}
    )
    assert start.status_code == 202, start.text
    job = follow_bulk(api, start.json()["id"])
    assert job["status"] == "done", job
    assert job["decided"] + job["unchanged"] == matched and job["unapplied"] == 0, job
    found = wait_for(
        lambda: len(in_facet(api, "text-types_paths", "fable", q="Ithaca")) == matched,
        timeout=60,
        every=1.0,
        what="the bulk decision in the tree facet",
    )
    assert found
    proposals = api.get(
        "/curation/proposals", params={"collection": collection(), "kind": "add_tag", "limit": 1000}
    ).json()
    mine = [p for p in proposals if p["reason"].startswith(f"bulk tagging job {job['id']}")]
    assert len(mine) == job["decided"], proposals[-5:]

    undo = api.post("/curation/bulk-tags", json={**query, "decision": "reject"})
    assert undo.status_code == 202, undo.text
    assert follow_bulk(api, undo.json()["id"])["status"] == "done"
    wait_for(
        lambda: not in_facet(api, "text-types_paths", "fable", q="Ithaca"),
        timeout=60,
        every=1.0,
        what="the bulk reject to leave the tree facet",
    )


def test_a_plain_list_imports_as_a_pack_through_a_job(api: httpx.Client) -> None:
    """LIST-05 and SEMA-038 (import part): the request answers 202 with a job the client
    follows; the version is then removed, so the demo keeps its own vocabulary."""

    r = api.post(
        "/knowledge/packs/import-list",
        json={
            "pack_id": "it-names",
            "title": "Integration names",
            "scheme": "Names",
            "text": "Penelope | Penelopeia\nTelemachus\n",
            "activate": False,
            "reindex": False,
        },
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    def finished() -> dict[str, Any] | None:
        job: dict[str, Any] = api.get(f"/knowledge/pack-jobs/{job_id}").raise_for_status().json()
        return job if job["status"] in ("done", "failed") else None

    job = wait_for(finished, timeout=300, every=2.0, what=f"pack job {job_id}")
    assert job["status"] == "done", job
    assert job["result"]["status"] == "imported" and job["result"]["dictionary_entries"] == 3
    packs = api.get("/knowledge/packs", params={"status": "imported"}).json()
    assert any(p["pack_id"] == "it-names" for p in packs), packs
    removed = api.delete("/knowledge/packs/it-names/versions/1.0.0")
    assert removed.status_code == 200, removed.text
    assert removed.json()["dictionary_entries_removed"]


def test_the_worker_runs_the_proposal_sweep(api: httpx.Client) -> None:
    """SEMA-031: the periodic sweep is registered with the worker and runs."""

    run_task("semascope.retry_proposals")

    def ran() -> bool:
        logs = compose("logs", "--no-color", "--since", "10m", "worker", timeout=120).stdout
        return any(
            "semascope.retry_proposals" in line and "succeeded" in line
            for line in logs.splitlines()
        )

    wait_for(ran, timeout=120, every=3.0, what="the sweep task in the worker log")
