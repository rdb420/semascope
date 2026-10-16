#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Failure and recovery (measure 7 of docs/assess/benchmarks.md).

Runs the six scenarios of the protocol against a benchmark stack, each on its own
collection of `--docs` files taken from the mixed-1k corpus, and compares the result with
a clean run of the same files:

  kill-worker   SIGKILL the worker's pool processes mid-document, then (second collection)
                the whole worker container; wait; then try `oss reindex` and a source re-run
  valkey        restart Valkey while documents are being indexed
  solr, neo4j   stop the store while documents are being indexed, start it, `oss reindex`
  remote        the embed stage pointed at a service that answers 503; then put it back and
                `oss reindex --stage embed`
  restore       `./oss backup --with-graph`, a fresh compose project, `./oss restore`, compare
  embed-model   switch the embedding model, see what semantic search returns, then
                `oss reindex --stage embed` and compare with the old results

For each: time to recover, and whether any document was lost (never indexed), duplicated
(indexed twice without a reindex), or half-written (in PostgreSQL but not in Solr or Neo4j).

It stops and starts services of the stack under test and creates a second project for the
restore, so it refuses the compose project `semascope`. It needs the host's `docker`, and
the corpus folder inside the stack's import folder (`SEMASCOPE_IMPORT_DIR`).

    uv run scripts/bench_recovery.py --project sem-bench --env-file .bench/sem-bench.env \\
        --compose-file compose.yaml --compose-file .bench/override.yaml \\
        --corpus packs/demo/derived/mixed-1k
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchlib import (  # noqa: E402
    ROOT,
    Compose,
    add_stack_args,
    api_from_args,
    compose_from_args,
    hardware_block,
    table,
)

SCENARIOS = ("kill-worker", "valkey", "solr", "neo4j", "remote", "restore", "embed-model")
FORMATS = ("html", "docx", "pdf", "eml", "odt", "epub", "csv")  # no OCR: keeps runs short
QUERIES = ("flood", "hero returns home after the war", "sacrifice to the gods")
ALT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # 384 dimensions, like the default


@dataclass
class Outcome:
    scenario: str
    expected: str
    observed: list[str] = field(default_factory=list)
    recovery_s: float | None = None
    lost: int | None = None
    duplicated: int | None = None
    half_written: int | None = None
    passed: bool | None = None

    def row(self) -> list[str]:
        def n(v: int | None) -> str:
            return "" if v is None else str(v)

        verdict = {True: "pass", False: "**fail**", None: "n/a"}[self.passed]
        rec = "" if self.recovery_s is None else f"{self.recovery_s:.0f} s"
        return [
            self.scenario,
            self.expected,
            "; ".join(self.observed),
            rec,
            n(self.lost),
            n(self.duplicated),
            n(self.half_written),
            verdict,
        ]


def run_cmd(cmd: list[str], env: dict[str, str], timeout: float) -> str:
    """Run a host command; on failure raise with the end of its output, not just the status."""

    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        raise RuntimeError(f"{' '.join(cmd[:3])}… exited {proc.returncode}: {' | '.join(tail)}")
    return proc.stdout


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


class Bench:
    def __init__(self, args: Any, c: Compose) -> None:
        self.args = args
        self.c = c
        self.api = api_from_args(args, c)
        self.import_dir = Path(c.env.get("SEMASCOPE_IMPORT_DIR", "./data/import")).resolve()
        self.work = Path(tempfile.mkdtemp(prefix="bench-recovery-"))
        self.log_lines: list[str] = []
        self.stuck: dict[str, tuple[float, list[str]]] = {}  # collection -> (kill time, ids)
        self.prefix = f"rec{args.run_id or time.strftime('%m%d%H%M')}"  # fresh collections per run
        self.clean_s = 0.0
        self.copies: list[Path] = []  # subsets copied into the import folder

    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.log_lines.append(line)
        print(line, file=sys.stderr, flush=True)

    # --- corpus -------------------------------------------------------------------------------

    def subset(self, name: str) -> tuple[str, int]:
        """Copy `--docs` corpus files into the import folder; returns the in-stack path."""

        src = Path(self.args.corpus).resolve()
        files = sorted(p for fmt in FORMATS for p in (src / fmt).glob("*") if p.is_file())
        step = max(1, len(files) // self.args.docs)
        chosen = files[::step][: self.args.docs]
        dest = self.import_dir / "derived" / "recovery" / name
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        self.copies.append(dest)
        for f in chosen:
            shutil.copy2(f, dest / f"{f.parent.name}-{f.name}")
        return f"/import/{dest.relative_to(self.import_dir).as_posix()}", len(chosen)

    # --- API and stores -----------------------------------------------------------------------

    def index(self, collection: str, path: str) -> int:
        if self.api.get(f"/collections/{collection}").status_code == 404:
            self.api.post("/collections", json={"slug": collection}).raise_for_status()
        resp = self.api.post(
            f"/collections/{collection}/sources",
            json={"name": collection, "connector": "filesystem", "settings": {"path": path}},
        )
        resp.raise_for_status()
        source = int(resp.json()["id"])
        self.api.post(f"/sources/{source}/run").raise_for_status()
        return source

    def statuses(self, collection: str) -> dict[str, int]:
        rows = self.c.psql(
            "SELECT d.status, count(*) FROM catalogue_document d JOIN catalogue_collection c "
            f"ON c.id = d.collection_id WHERE c.slug = {q(collection)} GROUP BY 1"
        )
        return {r[0]: int(r[1]) for r in rows}

    def wait_until(self, cond: Callable[[], bool], timeout: float, every: float = 2) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if cond():
                    return True
            except Exception as exc:  # noqa: BLE001 - a store may be down on purpose
                self.log(f"  poll: {type(exc).__name__}: {exc}"[:200])
            time.sleep(every)
        return False

    def wait_fraction(self, collection: str, total: int, fraction: float) -> None:
        self.wait_until(
            lambda: self.statuses(collection).get("indexed", 0) >= total * fraction, 900
        )

    def wait_drained(self, collection: str, total: int, timeout: float) -> bool:
        """Every document is in, none queued or processing, and no retry is still due.

        A document indexed with a transiently failed stage keeps `indexed` while its retry
        waits; its error then ends with "retry n/m in Ns" until the last run.
        """

        def done() -> bool:
            s = self.statuses(collection)
            if sum(s.values()) < total or s.get("queued") or s.get("processing"):
                return False
            due = self.c.psql(
                "SELECT count(*) FROM catalogue_document d JOIN catalogue_collection c ON "
                f"c.id = d.collection_id WHERE c.slug = {q(collection)} AND d.error ~ "
                "'; retry [0-9]+/[0-9]+ in [0-9]+s'"
            )
            return due[0] == ["0"]

        return self.wait_until(done, timeout)

    def counts(self, collection: str) -> dict[str, Any]:
        time.sleep(5)  # let Solr's soft commit (2 to 3 s) make the last updates visible
        s = self.statuses(collection)
        fq = quote(f'collection_s:"{collection}"')
        docs = self.c.solr(f"documents/select?q=*:*&rows=0&fq={fq}")["response"]["numFound"]
        chunks = self.c.solr(
            f"chunks/select?q=*:*&rows=0&fq={fq}&facet=true&facet.field=model_s&facet.mincount=1"
        )
        flat = chunks["facet_counts"]["facet_fields"]["model_s"]
        models = dict(zip(flat[::2], flat[1::2], strict=True))
        graph = self.c.cypher(
            f"MATCH (d:Document) WHERE d.collection = {json.dumps(collection)} RETURN count(d)"
        )
        tags = self.c.psql(
            "SELECT coalesce(sum(jsonb_array_length(d.tags)), 0) FROM catalogue_document d "
            f"JOIN catalogue_collection c ON c.id = d.collection_id WHERE c.slug = {q(collection)} "
            "AND d.status = 'indexed'"
        )
        dupes = self.c.psql(
            "SELECT count(*) FROM (SELECT subject FROM curation_event WHERE kind = "
            f"'document.indexed' AND collection = {q(collection)} GROUP BY subject "
            "HAVING count(*) > 1) s"
        )
        failed_stages = self.c.psql(
            "SELECT r->>'stage', count(*) FROM catalogue_document d JOIN catalogue_collection c "
            "ON c.id = d.collection_id, jsonb_array_elements(d.stage_runs) r "
            f"WHERE c.slug = {q(collection)} AND r->>'outcome' = 'failed' GROUP BY 1"
        )
        return {
            "status": s,
            "solr_docs": int(docs),
            "chunks": int(chunks["response"]["numFound"]),
            "chunk_models": models,
            "graph_docs": int(graph[0][0]) if graph else 0,
            "tags": int(tags[0][0]),
            "duplicated": int(dupes[0][0]),
            "failed_stages": {r[0]: int(r[1]) for r in failed_stages},
        }

    def reindex(self, collection: str, stages: str | None = None) -> None:
        params = {"stages": stages} if stages else {}
        self.api.post(f"/collections/{collection}/reindex", params=params).raise_for_status()

    def wait_reindexed(self, collection: str, since: float, timeout: float) -> bool:
        """Every indexed-or-failed document has a stage run that started after `since`."""

        def done() -> bool:
            rows = self.c.psql(
                "SELECT count(*) FILTER (WHERE d.status = 'processing'), count(*) FILTER (WHERE "
                "NOT EXISTS (SELECT 1 FROM jsonb_array_elements(d.stage_runs) r WHERE "
                f"(r->>'started_at')::timestamptz > to_timestamp({since}))) "
                "FROM catalogue_document d JOIN catalogue_collection c ON c.id = d.collection_id "
                f"WHERE c.slug = {q(collection)} AND d.text <> ''"
            )
            return rows[0] == ["0", "0"]

        return self.wait_until(done, timeout, every=3)

    def compare(self, out: Outcome, got: dict[str, Any], clean: dict[str, Any]) -> None:
        indexed = got["status"].get("indexed", 0)
        total = clean["status"].get("indexed", 0)
        out.lost = total - indexed
        out.half_written = abs(indexed - got["solr_docs"]) + abs(indexed - got["graph_docs"])
        same = (
            indexed == total
            and got["solr_docs"] == clean["solr_docs"]
            and got["chunks"] == clean["chunks"]
            and got["graph_docs"] == clean["graph_docs"]
            and got["tags"] == clean["tags"]
        )
        out.observed.append(
            f"indexed {indexed}/{total}, Solr {got['solr_docs']}/{clean['solr_docs']}, "
            f"chunks {got['chunks']}/{clean['chunks']}, graph {got['graph_docs']}/"
            f"{clean['graph_docs']}, tags {got['tags']}/{clean['tags']}"
        )
        out.passed = same and not out.duplicated

    def with_files(self, *extra: Path) -> Compose:
        return Compose(
            self.c.project,
            files=[*(self.c.files or ["compose.yaml"]), *map(str, extra)],
            env_file=self.c.env_file,
        )

    def override(self, name: str, services: dict[str, Any]) -> Path:
        path = self.work / f"{name}.override.json"  # JSON is YAML; compose reads it
        path.write_text(json.dumps({"services": services}), encoding="utf-8")
        return path

    # --- scenarios ----------------------------------------------------------------------------

    def clean(self) -> tuple[dict[str, Any], int]:
        path, n = self.subset("clean")
        self.log(f"clean run: {n} documents")
        t0 = time.monotonic()
        self.index(f"{self.prefix}-clean", path)
        self.wait_drained(f"{self.prefix}-clean", n, 1800)
        self.clean_s = time.monotonic() - t0
        got = self.counts(f"{self.prefix}-clean")
        self.log(f"clean run done in {self.clean_s:.0f} s: {got}")
        return got, n

    def kill_worker(self, clean: dict[str, Any], n: int) -> list[Outcome]:
        outs = []
        for variant in ("pool", "container"):
            coll = f"{self.prefix}-kill-{variant}"
            out = Outcome(
                f"kill worker ({variant})",
                "late ack: Valkey redelivers the task; every document indexed exactly once",
            )
            path, total = self.subset(coll)
            self.index(coll, path)
            self.wait_fraction(coll, total, 0.25)
            in_flight = self.c.psql(
                "SELECT d.id FROM catalogue_document d JOIN catalogue_collection c ON "
                f"c.id = d.collection_id WHERE c.slug = {q(coll)} AND d.status = 'processing'"
            )
            killed_at = time.monotonic()
            if variant == "pool":
                pids = self.c.exec(
                    "worker",
                    "python",
                    "-c",
                    "import os, signal\n"
                    "for p in os.listdir('/proc'):\n"
                    "    if p.isdigit() and p != '1':\n"
                    "        try:\n"
                    "            st = open(f'/proc/{p}/status').read()\n"
                    "        except OSError:\n"
                    "            continue\n"
                    "        if '\\nPPid:\\t1\\n' in st:\n"
                    "            os.kill(int(p), signal.SIGKILL); print(p)",
                )
                out.observed.append(
                    f"killed {len(pids.split())} pool processes, "
                    f"{len(in_flight)} documents in flight"
                )
            else:
                self.c.run("kill", "-s", "KILL", "worker")
                self.c.run("up", "-d", "--no-deps", "worker")
                out.observed.append(f"killed the worker container with {len(in_flight)} in flight")
            drained = self.wait_drained(coll, total, self.args.redelivery_wait)
            s = self.statuses(coll)
            stuck = s.get("processing", 0) + s.get("queued", 0)
            got = self.counts(coll)
            out.duplicated = got["duplicated"]
            if drained:
                out.recovery_s = time.monotonic() - killed_at
                out.observed.append("queue drained without intervention")
            else:
                out.observed.append(
                    f"after {self.args.redelivery_wait:.0f} s: {stuck} documents still "
                    f"processing or queued ({s})"
                )
                # what an operator can do: reindex (indexed and failed documents only) and a
                # re-run of the source (requeues failed and queued documents)
                self.reindex(coll)
                src = next(x for x in self.api.get(f"/collections/{coll}/sources").json())
                self.api.post(f"/sources/{src['id']}/run").raise_for_status()
                self.wait_drained(coll, total, 300)
                s = self.statuses(coll)
                left = s.get("processing", 0) + s.get("queued", 0)
                out.observed.append(f"after reindex and a source re-run: {left} still stuck")
                if left == 0:
                    out.recovery_s = time.monotonic() - killed_at
            self.stuck[coll] = (killed_at, [r[0] for r in in_flight])
            self.compare(out, self.counts(coll), clean)
            outs.append(out)
        return outs

    def valkey(self, clean: dict[str, Any], n: int) -> Outcome:
        out = Outcome("restart Valkey", "append-only file keeps queued tasks; none lost")
        path, total = self.subset(f"{self.prefix}-valkey")
        self.index(f"{self.prefix}-valkey", path)
        self.wait_fraction(f"{self.prefix}-valkey", total, 0.25)
        before = self.statuses(f"{self.prefix}-valkey")
        t0 = time.monotonic()
        self.c.run("restart", "valkey")
        out.observed.append(f"restarted with {before.get('queued', 0)} queued")
        drained = self.wait_drained(f"{self.prefix}-valkey", total, self.args.redelivery_wait)
        if drained:
            out.recovery_s = time.monotonic() - t0
        got = self.counts(f"{self.prefix}-valkey")
        out.duplicated = got["duplicated"]
        if not drained:
            out.observed.append(
                f"not drained after {self.args.redelivery_wait:.0f} s: {got['status']}"
            )
        self.compare(out, got, clean)
        return out

    def store_down(self, service: str, clean: dict[str, Any], n: int) -> Outcome:
        coll = f"{self.prefix}-{service}"
        out = Outcome(
            f"stop {service.capitalize()} during indexing",
            "stage fails, document marked failed; `oss reindex` retries; counts match a clean run",
        )
        path, total = self.subset(coll)
        self.index(coll, path)
        self.wait_fraction(coll, total, 0.25)
        self.c.run("stop", service)
        down_at = time.monotonic()
        time.sleep(self.args.outage)
        self.c.run("up", "-d", "--wait", "--no-deps", service, timeout=900)
        up_at = time.monotonic()
        self.wait_drained(coll, total, 1800)
        got = self.counts(coll)
        out.observed.append(
            f"{self.args.outage:.0f} s outage: {got['status']}, "
            f"failed stages {got['failed_stages']}"
        )
        since = time.time()
        self.reindex(coll)
        self.wait_reindexed(coll, since, 1800)
        out.recovery_s = time.monotonic() - up_at
        out.observed.append(f"outage plus restart {up_at - down_at:.0f} s")
        self.compare(out, self.counts(coll), clean)
        return out

    def remote(self, clean: dict[str, Any], n: int) -> Outcome:
        out = Outcome(
            "remote service answers errors",
            "embed fails, no fallback to the CPU model; reindex recovers; no mixed-model chunks",
        )
        image = self.c.run("config", "--images").stdout
        caddy = next(i for i in image.split() if i.startswith("caddy:"))
        failing = self.override(
            "remote",
            {
                "failing-remote": {
                    "image": caddy,
                    "command": [
                        "caddy",
                        "respond",
                        "--listen",
                        ":8080",
                        "--status",
                        "503",
                        "--body",
                        "unavailable",
                    ],
                },
                "worker": {
                    "environment": {
                        "SEMASCOPE_EMBEDDER": "openai",
                        "SEMASCOPE_EMBED_BASE_URL": "http://failing-remote:8080/v1",
                        "SEMASCOPE_EMBED_MODEL": "bench-remote-model",
                        "SEMASCOPE_EMBED_API_KEY": "bench",
                    }
                },
            },
        )
        broken = self.with_files(failing)
        try:
            broken.run("up", "-d", "--wait", "failing-remote", "worker", timeout=900)
            path, total = self.subset(f"{self.prefix}-remote")
            self.index(f"{self.prefix}-remote", path)
            self.wait_drained(f"{self.prefix}-remote", total, 1800)
            got = self.counts(f"{self.prefix}-remote")
            out.observed.append(
                f"with the failing service: {got['status']}, failed stages {got['failed_stages']}, "
                f"chunks {got['chunk_models'] or 'none'}"
            )
            fallback = sum(got["chunk_models"].values())
        finally:
            broken.run("rm", "-sf", "failing-remote", check=False)
            self.c.run("up", "-d", "--wait", "--no-deps", "worker", timeout=900)
        t0 = time.monotonic()
        since = time.time()
        self.reindex(f"{self.prefix}-remote", "embed")
        self.wait_reindexed(f"{self.prefix}-remote", since, 1800)
        out.recovery_s = time.monotonic() - t0
        got = self.counts(f"{self.prefix}-remote")
        out.observed.append(f"after reindex --stage embed: chunks by model {got['chunk_models']}")
        self.compare(out, got, clean)
        out.passed = bool(out.passed) and fallback == 0 and len(got["chunk_models"]) == 1
        return out

    def restore(self, clean: dict[str, Any], n: int) -> Outcome:
        out = Outcome(
            "restore a backup onto a fresh project",
            "`./oss restore`: search, facets, review decisions and the outbox cursor match",
        )
        backup = self.work / "backup"
        env = dict(os.environ)
        env.update(
            COMPOSE_PROJECT_NAME=self.c.project,
            COMPOSE_FILE=os.pathsep.join(
                str((ROOT / f).resolve()) for f in self.c.files or ["compose.yaml"]
            ),
        )
        if self.c.env_file:
            env["COMPOSE_ENV_FILES"] = str(self.c.env_file)
        t0 = time.monotonic()
        run_cmd([str(ROOT / "oss"), "backup", str(backup), "--with-graph"], env, 3600)
        backup_s = time.monotonic() - t0
        size = sum(p.stat().st_size for p in backup.rglob("*") if p.is_file())
        source = self.snapshot(self.c, self.api)
        target_project = f"{self.c.project}-restore"
        port = str(int(self.c.env.get("SEMASCOPE_HTTPS_PORT", "8443")) + 1)
        env.update(COMPOSE_PROJECT_NAME=target_project, SEMASCOPE_HTTPS_PORT=port)
        target = Compose(target_project, files=self.c.files, env_file=self.c.env_file)
        try:
            run_cmd([*target.base(), "up", "-d", "--wait"], env, 1800)
            t1 = time.monotonic()
            run_cmd([str(ROOT / "oss"), "restore", str(backup)], env, 3600)
            restore_s = time.monotonic() - t1
            target_args = argparse.Namespace(
                **{**vars(self.args), "api_url": f"https://localhost:{port}"}
            )
            with api_from_args(target_args, target) as api:
                self.wait_until(lambda: api.get("/stats").status_code == 200, 300)
                time.sleep(5)  # Solr's soft commit after the restore
                restored = self.snapshot(target, api)
        finally:
            if not self.args.keep_restore:
                subprocess.run(  # noqa: S603
                    [*target.base(), "down", "-v"],
                    cwd=ROOT,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=900,
                )
        out.recovery_s = backup_s + restore_s
        diffs = [k for k in source if source[k] != restored.get(k)]
        out.observed.append(
            f"backup {backup_s:.0f} s ({size / 1e6:.0f} MB), restore {restore_s:.0f} s; "
            f"{len(source)} checks, differences: {', '.join(diffs) or 'none'}"
        )
        for k in diffs[:6]:
            out.observed.append(f"{k}: {source[k]} -> {restored.get(k)}")
        out.passed = not diffs
        return out

    def snapshot(self, c: Compose, api: Any) -> dict[str, Any]:
        """What must survive a restore: hits, facets, decisions, outbox cursor, graph size."""

        snap: dict[str, Any] = {}
        for coll in sorted(x["slug"] for x in api.get("/collections").json()):
            for query in QUERIES:
                for mode in ("keyword", "semantic"):
                    body = api.post(
                        "/search", json={"q": query, "collection": coll, "mode": mode}
                    ).json()
                    snap[f"{coll} {mode} '{query}' hits"] = body.get("total")
            body = api.post("/search", json={"q": "*:*", "collection": coll}).json()
            snap[f"{coll} facet content_type_group_s"] = {
                f["value"]: f["count"]
                for f in body.get("facets", {}).get("content_type_group_s", [])
            }
        snap["reviews by status"] = dict(
            c.psql("SELECT status, count(*) FROM curation_review GROUP BY 1 ORDER BY 1")
        )
        snap["proposals"] = c.psql(
            "SELECT count(*), count(*) FILTER (WHERE applied) FROM curation_proposal"
        )
        snap["outbox: last event id"] = c.psql("SELECT coalesce(max(id), 0) FROM curation_event")
        snap["outbox: webhook cursors"] = c.psql(
            "SELECT id, last_event_id FROM curation_webhook ORDER BY id"
        )
        snap["graph nodes"] = c.cypher("MATCH (n) RETURN count(n)")
        snap["graph relationships"] = c.cypher("MATCH ()-[r]->() RETURN count(r)")
        return snap

    def embed_model(self, clean: dict[str, Any], n: int) -> Outcome:
        out = Outcome(
            "replace the embedding model",
            "old model's chunks no longer searched; `reindex --stage embed` restores recall",
        )

        def top(coll: str) -> dict[str, list[str]]:
            res = {}
            for query in QUERIES:
                body = self.api.post(
                    "/search", json={"q": query, "collection": coll, "mode": "semantic"}
                ).json()
                res[query] = [h["id"] for h in body.get("hits", [])]
            return res

        before = top(f"{self.prefix}-clean")
        swap = self.override(
            "model",
            {
                svc: {
                    "environment": {
                        "SEMASCOPE_EMBED_MODEL": ALT_MODEL,
                        "FASTEMBED_CACHE_PATH": "/data/models",  # the image's cache is read-only
                    }
                }
                for svc in ("server", "worker")
            },
        )
        swapped = self.with_files(swap)
        try:
            swapped.run("up", "-d", "--wait", "--no-deps", "server", "worker", timeout=900)
            self.wait_until(lambda: self.api.get("/stats").status_code == 200, 300)
            during = top(f"{self.prefix}-clean")
            out.observed.append(
                "right after the switch, semantic hits per query: "
                + ", ".join(str(len(v)) for v in during.values())
            )
            t0 = time.monotonic()
            since = time.time()
            self.reindex(f"{self.prefix}-clean", "embed")
            ok = self.wait_reindexed(f"{self.prefix}-clean", since, 3600)
            out.recovery_s = time.monotonic() - t0
            after = top(f"{self.prefix}-clean")
            got = self.counts(f"{self.prefix}-clean")
            overlap = [len(set(before[k]) & set(after[k])) / max(1, len(before[k])) for k in before]
            out.observed.append(
                f"reindex {'finished' if ok else 'timed out'}; "
                f"chunks by model {got['chunk_models']}; "
                "top-10 overlap with the old model: "
                + ", ".join(f"{o:.0%}" for o in overlap)
                + "; hits per query: "
                + ", ".join(str(len(v)) for v in after.values())
            )
            out.passed = (
                ok
                and list(got["chunk_models"]) == [ALT_MODEL]
                and all(len(after[k]) >= len(before[k]) for k in before)
            )
        finally:
            self.c.run("up", "-d", "--wait", "--no-deps", "server", "worker", timeout=900)
        return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_stack_args(ap)
    ap.add_argument("--corpus", default=str(ROOT / "packs/demo/derived/mixed-1k"))
    ap.add_argument("--docs", type=int, default=60, help="documents per scenario")
    ap.add_argument("--scenario", action="append", choices=SCENARIOS, help="default: all")
    ap.add_argument(
        "--redelivery-wait", type=float, default=600, help="seconds to wait for redelivery"
    )
    ap.add_argument("--outage", type=float, default=30, help="seconds a store stays down")
    ap.add_argument("--keep-restore", action="store_true", help="keep the restored project")
    ap.add_argument("--run-id", default="", help="collection name suffix (default: the time)")
    ap.add_argument("--corpus-manifest", action="append", default=[], type=Path)
    args = ap.parse_args(argv)
    c = compose_from_args(args)
    if not c.files:
        c.files = ["compose.yaml"]
    bench = Bench(args, c)
    wanted = args.scenario or list(SCENARIOS)
    clean, n = bench.clean()
    outcomes: list[Outcome] = []
    runners: dict[str, Callable[[], list[Outcome]]] = {
        "kill-worker": lambda: bench.kill_worker(clean, n),
        "valkey": lambda: [bench.valkey(clean, n)],
        "solr": lambda: [bench.store_down("solr", clean, n)],
        "neo4j": lambda: [bench.store_down("neo4j", clean, n)],
        "remote": lambda: [bench.remote(clean, n)],
        "restore": lambda: [bench.restore(clean, n)],
        "embed-model": lambda: [bench.embed_model(clean, n)],  # last: it re-embeds rec-clean
    }
    for name in SCENARIOS:
        if name not in wanted:
            continue
        bench.log(f"scenario {name}")
        try:
            outcomes += runners[name]()
        except Exception as exc:  # noqa: BLE001 - report and go on with the next scenario
            bench.log(f"scenario {name} raised {type(exc).__name__}: {exc}")
            outcomes.append(
                Outcome(name, "", [f"the benchmark raised {type(exc).__name__}: {exc}"[:300]])
            )
        bench.log(f"done {name}: {outcomes[-1].row()}")
    for coll, (killed_at, ids) in bench.stuck.items():
        if ids:
            rows = c.psql(
                "SELECT status, count(*) FROM catalogue_document WHERE id IN ("
                + ", ".join(q(i) for i in ids)
                + ") GROUP BY 1"
            )
            outcomes.append(
                Outcome(
                    f"recheck {coll}",
                    "documents in flight at the kill",
                    [f"{time.monotonic() - killed_at:.0f} s after the kill: {dict(rows)}"],
                )
            )
    for path in [bench.work, *bench.copies]:  # overrides, the backup, the copied subsets
        shutil.rmtree(path, ignore_errors=True)
    print(table(["item", "value"], hardware_block(c, args.corpus_manifest)))
    print(f"\nClean run: {n} documents ({', '.join(FORMATS)}) in {bench.clean_s:.0f} s.\n")
    print(
        table(
            [
                "scenario",
                "expected",
                "observed",
                "recovery",
                "lost",
                "duplicated",
                "half-written",
                "result",
            ],
            [o.row() for o in outcomes],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
