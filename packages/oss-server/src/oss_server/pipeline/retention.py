"""What deleting documents removes besides their Postgres row: Solr, the graph and files.

A document's original lives once in the blob store under its content hash, shared by every
document with the same bytes in any collection, and its page previews live under
`previews/<content hash>/`; both go when no document that is not deleted uses them any
more. Deleting a source or a collection takes its documents out of Solr at once, then
removes their graph nodes (curator decisions included: the documents are gone for good),
their files, the source's work area and, for the collection's upload source, the uploaded
files. `gc` sweeps what a crash or an older release left behind: blobs and previews no
document uses, and work files older than a week.
(docs/adr/0013-deletion-and-retention.md)
"""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from django.db.models import Q, QuerySet

from oss_core.config import get_settings
from oss_server.catalogue.models import Collection, Document, Source

log = logging.getLogger(__name__)

BATCH = 500  # documents per purge task
GC_GRACE_S = 24 * 3600  # a blob or preview younger than this may belong to a run in progress
WORK_MAX_AGE_S = 7 * 24 * 3600  # work files older than this were never picked up

# A document node and its edges, curator decisions included, then the entities only these
# documents mentioned (an NER name is personal data); pack-defined entities keep their edges.
GRAPH_PURGE = """
UNWIND $ids AS id
MATCH (d:Document {id: id})
OPTIONAL MATCH (d)-[:MENTIONS]->(e:Entity)
WITH d, collect(e) AS mentioned
DETACH DELETE d
WITH mentioned
UNWIND mentioned AS e
WITH DISTINCT e
WHERE NOT (e)--()
DELETE e
"""


def data_dir() -> Path:
    return Path(get_settings().semascope_data_dir)


def blob_root() -> Path:
    return (data_dir() / "blobs").resolve()


def _in_blob_root(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(blob_root())
    except OSError:
        return False


def blob_in_use(blob_path: str, content_hash: str) -> bool:
    live = Document.objects.exclude(status=Document.Status.DELETED)
    query = Q(blob_path=blob_path) if blob_path else Q()
    if content_hash:
        query |= Q(content_hash=content_hash)
    return bool(query) and live.filter(query).exists()


def previews_in_use(content_hash: str) -> bool:
    return (
        Document.objects.exclude(status=Document.Status.DELETED)
        .filter(content_hash=content_hash)
        .exists()
    )


def remove_files(entries: Iterable[tuple[str, str]]) -> int:
    """Remove the blobs and previews of (blob path, content hash) pairs nothing else uses."""

    removed = 0
    previews = blob_root() / "previews"
    for blob_path, content_hash in entries:
        mine = blob_path and _in_blob_root(Path(blob_path))
        if mine and not blob_in_use(blob_path, content_hash):
            try:
                Path(blob_path).unlink()
                removed += 1
            except FileNotFoundError:
                pass
        if content_hash and "/" not in content_hash and not previews_in_use(content_hash):
            folder = previews / content_hash
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
    return removed


# the source and collection nodes a purge left without documents
GRAPH_EMPTY = "MATCH (n) WHERE (n:Source OR n:Collection) AND NOT (n)--() DELETE n"


def purge_graph(ids: list[str]) -> None:
    from oss_server.services import graph_client

    graph = graph_client()
    graph.execute(GRAPH_PURGE, ids=ids)
    graph.execute(GRAPH_EMPTY)


def _entries(docs: QuerySet[Document]) -> list[tuple[str, list[tuple[str, str]]]]:
    """(id, [(blob path, content hash)]) per document, before the rows go."""

    return [(i, [(b, h)]) for i, b, h in docs.values_list("id", "blob_path", "content_hash")]


def _queue(rows: list[tuple[str, list[tuple[str, str]]]]) -> int:
    from oss_server.pipeline.tasks import purge_documents

    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        purge_documents.delay(
            [doc_id for doc_id, _ in chunk], [e for _, files in chunk for e in files]
        )
    return len(rows)


def purge_source(source: Source) -> int:
    """Before a source's row is deleted: take its documents out of search now and queue the
    removal of their graph nodes and files, its work area and (for uploads) the uploads."""

    from oss_server.pipeline.tasks import purge_area
    from oss_server.services import solr_client, solr_documents_collection

    docs = Document.objects.filter(source=source)
    rows = _entries(docs)
    # marked deleted first, so the file tasks never see them as users, whenever the rows go
    docs.update(status=Document.Status.DELETED)
    term = _quote(str(source.pk))
    solr_client().delete_by_query(solr_documents_collection(), f"source_id_s:{term}")
    for i in range(0, len(rows), BATCH):
        purge_solr_chunks([doc_id for doc_id, _ in rows[i : i + BATCH]])
    _queue(rows)
    purge_area.delay(source.collection.slug, str(source.pk), uploads=source.connector == "upload")
    return len(rows)


def purge_collection(collection: Collection) -> int:
    """Before a collection's row is deleted: as `purge_source`, for every source in it."""

    from oss_server.pipeline.tasks import purge_area
    from oss_server.services import solr_chunks_collection, solr_client, solr_documents_collection

    docs = Document.objects.filter(collection=collection)
    rows = _entries(docs)
    docs.update(status=Document.Status.DELETED)
    term = _quote(collection.slug)
    solr_client().delete_by_query(solr_documents_collection(), f"collection_s:{term}")
    solr_client().delete_by_query(solr_chunks_collection(), f"collection_s:{term}")
    _queue(rows)
    purge_area.delay(collection.slug, None, uploads=True)
    return len(rows)


def purge_solr_chunks(ids: list[str]) -> None:
    from oss_pipeline.clients.solr import quote_term
    from oss_server.services import solr_chunks_collection, solr_client

    if ids:
        terms = " OR ".join(quote_term(i) for i in ids)
        solr_client().delete_by_query(solr_chunks_collection(), f"document_id:({terms})")


def _quote(value: str) -> str:
    from oss_pipeline.clients.solr import quote_term

    return quote_term(value)


def remove_area(collection: str, source_id: str | None, *, uploads: bool) -> None:
    """A source's work area (or a collection's), and the collection's uploads if asked."""

    from oss_pipeline.connectors.base import remove_collection_work, remove_source_work
    from oss_pipeline.connectors.upload import upload_root

    if source_id is None:
        remove_collection_work(collection)
    else:
        remove_source_work(collection, source_id)
    if uploads:
        try:
            shutil.rmtree(upload_root(data_dir(), collection), ignore_errors=True)
        except ValueError:  # not a collection slug: there is no such folder to remove
            pass


def gc(now: float | None = None) -> dict[str, int]:
    """Blobs and previews no document uses, and stale work files; young files are kept."""

    from oss_pipeline.connectors.base import work_root

    now = time.time() if now is None else now
    root = blob_root()
    out = {"blobs": 0, "previews": 0, "work": 0}
    if root.is_dir():
        for shard in root.iterdir():
            if not shard.is_dir() or len(shard.name) != 2:
                continue
            for blob in shard.iterdir():
                if not blob.is_file() or _young(blob, now, GC_GRACE_S):
                    continue
                digest = blob.name.split(".", 1)[0]
                if not blob_in_use(str(blob), digest):
                    blob.unlink(missing_ok=True)
                    out["blobs"] += 1
        previews = root / "previews"
        if previews.is_dir():
            for folder in previews.iterdir():
                if folder.is_dir() and not _young(folder, now, GC_GRACE_S):
                    if not previews_in_use(folder.name):
                        shutil.rmtree(folder, ignore_errors=True)
                        out["previews"] += 1
    work = work_root()
    if work.is_dir():
        for file in work.rglob("*"):
            if file.is_file() and not _young(file, now, WORK_MAX_AGE_S):
                file.unlink(missing_ok=True)
                out["work"] += 1
    return out


def _young(path: Path, now: float, seconds: float) -> bool:
    """Written or used (the fetch stage touches a blob it reuses) within `seconds`."""

    try:
        return now - path.stat().st_mtime < seconds
    except OSError:
        return True


def tombstone() -> dict[str, Any]:
    """The fields a deleted document keeps empty: only its id, URI and hash remain."""

    return {
        "status": Document.Status.DELETED,
        "title": "",
        "text": "",
        "pages": [],
        "metadata": {},
        "fields": {},
        "tags": [],
        "stage_runs": [],
        "blob_path": "",
        "item": {},
        "error": "",
        "fingerprint": "",
        "indexed_at": None,
        "processing_since": None,
        "attempts": 0,
        "graph_status": Document.GraphStatus.NONE,
    }
