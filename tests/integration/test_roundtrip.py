"""Backup, wipe every volume, restore: the stack comes back with the same state.

Destructive, so it only runs with SEMASCOPE_IT_ROUNDTRIP=1 and a compose
project other than the default one (COMPOSE_PROJECT_NAME, e.g. sem-ci).
`scripts/integration.sh roundtrip` sets both.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tarfile
import time
from typing import Any

import httpx
import pytest
from _stack import ROOT, collection, make_client, oss, wait_for

PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("SEMASCOPE_IT_ROUNDTRIP") != "1" or PROJECT in ("", "semascope"),
    reason="destructive: needs SEMASCOPE_IT_ROUNDTRIP=1 and a throwaway COMPOSE_PROJECT_NAME",
)


def compose(*args: str) -> None:
    cmd = ["docker", "compose", "-p", PROJECT, *args]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)  # noqa: S603
    print(f"$ {' '.join(cmd)}\n{proc.stdout}{proc.stderr}")
    assert proc.returncode == 0, f"{' '.join(cmd)} exited {proc.returncode}: {proc.stderr}"


def answering() -> bool:
    try:
        with make_client() as c:
            return c.get("/stats").status_code == 200
    except httpx.HTTPError:
        return False


def last_event_id(api: httpx.Client) -> int:
    cursor = 0
    while page := api.get("/curation/events", params={"since_id": cursor, "limit": 1000}).json():
        cursor = page[-1]["id"]
    return cursor


def snapshot(api: httpx.Client) -> dict[str, Any]:
    """Everything the restore must bring back, in comparable form."""

    stats = api.get("/stats").raise_for_status().json()

    def search(q: str, mode: str) -> list[str]:
        body = {"q": q, "collection": collection(), "mode": mode, "page_size": 10}
        return [h["id"] for h in api.post("/search", json=body).json()["hits"]]

    decided = [
        (r["id"], r["status"], r["target"], r["document_id"])
        for status in ("accepted", "rejected", "deferred")
        for r in api.get(
            "/curation/reviews", params={"status": status, "limit": 500, "collection": collection()}
        ).json()
    ]
    proposals = [
        (p["id"], p["kind"], p["target"], p["document_id"], p["external_ref"])
        for p in api.get("/curation/proposals", params={"limit": 1000}).json()
    ]
    curated: dict[str, list[str]] = {}
    for _, _, _, doc_id, _ in proposals:
        if doc_id and doc_id not in curated:
            tags = api.get(f"/curation/documents/{doc_id}/tags").json()
            curated[doc_id] = sorted(
                f"{t['iri']}:{t['curated']['decision']}" for t in tags if t["curated"]
            )
    edges: dict[str, list[str]] = {}
    for doc_id in curated:
        out = api.get(
            "/graph/neighbours",
            params={"id": f"Document:{doc_id}", "kinds": "Concept,Entity", "limit": 1000},
        ).json()
        edges[doc_id] = sorted(
            f"{e['target']}:{e['properties'].get('decision')}"
            for e in out["edges"]
            if e["type"] == "CURATED"
        )
    packs = sorted(
        (p["pack_id"], p["version"], p["status"]) for p in api.get("/knowledge/packs").json()
    )
    return {
        "documents": stats["documents"],
        "solr_documents": stats["solr_documents"],
        "chunks": stats["chunk_models"],
        "keyword": search("Penelope", "keyword"),
        "semantic": search("a king sails home after the war", "semantic"),
        "reviews_open": api.get(
            "/curation/reviews/summary", params={"collection": collection()}
        ).json(),
        "reviews_decided": sorted(decided),
        "proposals": proposals,
        "curated_tags": curated,
        "curated_edges": edges,
        "packs": packs,
        "outbox_cursor": last_event_id(api),
    }


def test_backup_wipe_restore(api: httpx.Client, indexed: dict[str, Any]) -> None:
    before = snapshot(api)
    assert before["proposals"], "no decisions to carry across; run the curation tests first"
    assert before["keyword"] and before["semantic"]
    assert any(before["curated_edges"].values()), "no CURATED edge to carry across"

    dest = ROOT / "data" / "backups" / f"it-{PROJECT}-{int(time.time())}"
    oss("backup", str(dest), "--with-graph")
    for name in ("postgres.sql.gz", "data.tar.gz", "neo4j.dump", "solr/documents.tar"):
        assert (dest / name).stat().st_size > 0, f"empty {name} in the backup"

    compose("down", "--volumes", "--remove-orphans")
    compose("up", "--detach", "--wait")
    wait_for(answering, timeout=300, what="the fresh stack")
    with make_client() as fresh:
        empty = fresh.get("/stats").json()
        assert not empty["documents"], f"the volumes were not wiped: {empty}"

    oss("restore", str(dest))
    wait_for(answering, timeout=300, what="the restored stack")
    with make_client() as restored:
        after = wait_for(
            lambda: (s := snapshot(restored))["solr_documents"] == before["solr_documents"] and s,
            timeout=120,
            what="the restored Solr collections",
        )
        for key, value in before.items():
            assert after[key] == value, f"{key} differs after restore"
        assert_live_collections_reload()

        # the outbox continues after the restored cursor, not from 1
        tags = restored.get(f"/curation/documents/{next(iter(before['curated_tags']))}/tags")
        tag = next(t for t in tags.json() if not t["curated"])
        doc_id = next(iter(before["curated_tags"]))
        decision = restored.post(
            f"/curation/documents/{doc_id}/tags",
            json={"iri": tag["iri"], "decision": "accept", "label": tag["label"]},
        )
        assert decision.status_code == 200, decision.text
        new = restored.get("/curation/events", params={"since_id": before["outbox_cursor"]}).json()
        assert [e["kind"] for e in new] == ["proposal.accept_tag"], new
        assert new[0]["id"] > before["outbox_cursor"]


def failing_oss(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ./oss expecting a non-zero exit; returns the finished process."""

    cmd = [*shlex.split(os.environ.get("SEMASCOPE_IT_OSS", "./oss")), *args]
    proc = subprocess.run(  # noqa: S603 - the project's own entry point
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=900, check=False
    )
    print(f"$ {shlex.join(cmd)}\n{proc.stdout}{proc.stderr}")
    assert proc.returncode != 0, f"{shlex.join(cmd)} succeeded on a corrupt backup"
    return proc


def solr_admin(query: str) -> dict[str, Any]:
    """A Collections API call inside the Solr container, as the shim makes it."""

    call = (
        'curl -sS -u "semascope:$SEMASCOPE_SOLR_PASSWORD" '
        '"http://localhost:8983/solr/admin/collections?$1&wt=json"'
    )
    cmd = ["docker", "compose", "-p", PROJECT, "exec", "-T", "solr", "sh", "-c", call, "sh", query]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)  # noqa: S603
    body: dict[str, Any] = json.loads(proc.stdout)
    return body


def solr_collections() -> set[str]:
    return set(solr_admin("action=LIST")["collections"])


def assert_live_collections_reload() -> None:
    """Each live collection reloads: its configset in ZooKeeper is complete.

    A Solr RESTORE also writes the backup's copy of the configset, and a backup keeps
    only the configset's top-level files; a core whose configset lost its subfolders
    fails to reload ("Can't find resource 'lang/...'"). The restore puts the
    configsets back from the image, on success and on failure.
    """

    for name in ("documents", "entities", "chunks"):
        body = solr_admin(f"action=RELOAD&name={name}")
        assert body["responseHeader"]["status"] == 0 and "failure" not in body, (name, body)


def test_a_corrupt_backup_leaves_the_data_as_it_was(
    api: httpx.Client, indexed: dict[str, Any]
) -> None:
    before = snapshot(api)
    good = ROOT / "data" / "backups" / f"it-{PROJECT}-good-{int(time.time())}"
    oss("backup", str(good))

    # a truncated Postgres dump: refused before anything is stopped
    truncated = good.with_name(good.name.replace("-good-", "-truncated-"))
    shutil.copytree(good, truncated)
    data = (truncated / "postgres.sql.gz").read_bytes()
    (truncated / "postgres.sql.gz").write_bytes(data[: len(data) // 2])
    proc = failing_oss("restore", str(truncated))
    assert "postgres.sql.gz is corrupt" in proc.stderr
    assert snapshot(api) == before

    # a Solr backup that is a readable archive but not a backup: Solr refuses to restore it,
    # after the app services were stopped; the live collections are kept and the app restarts
    bogus = good.with_name(good.name.replace("-good-", "-bogus-"))
    shutil.copytree(good, bogus)
    with tarfile.open(bogus / "solr" / "documents.tar", "w") as tar:
        junk = bogus / "junk.txt"
        junk.write_text("not a Solr backup\n", encoding="utf-8")
        tar.add(junk, arcname="./documents/junk.txt")
    proc = failing_oss("restore", str(bogus))
    assert "restore FAILED" in proc.stderr and "nothing was changed" in proc.stderr
    wait_for(answering, timeout=300, what="the app after the refused restore")
    with make_client() as after:
        assert snapshot(after) == before
    assert not {c for c in solr_collections() if c.endswith("_restoring")}
    assert_live_collections_reload()
