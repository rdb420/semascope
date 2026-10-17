"""Documents and their entities as tables: CSV, JSON Lines or Parquet, streamed.

Two tables (parity rows ETL-EXP-08, DM-20, ANL-17):

- **documents**, one row per document: its catalogue fields, its concepts in
  force (labels) and how many entities it holds; with `text_chars`, the start of
  its text.
- **entities**, one row per document and tag in force (the curated view, as in
  search): the concept or entity's export IRI, kind, class, label, confidence,
  the stage and pack that produced it, its mentions and the NER entity a link
  replaced. A collection that pseudonymises its graph exports named entities by
  their HMAC key and without their labels, as the RDF export does.

Rows are written in batches; Parquet gets one row group per batch, so the file
is streamed and never held whole. Column types are fixed (`*_COLUMNS`), so a
table loads into DuckDB, Spark, pandas or a SQL database without inference.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime
from typing import Any

from oss_pipeline.media import content_type_group
from oss_pipeline.provenance import curated_tags
from oss_server.catalogue.models import Document
from oss_server.exports import ids

FORMATS = {
    "csv": ("text/csv; charset=utf-8", "csv"),
    "jsonl": ("application/x-ndjson; charset=utf-8", "jsonl"),
    "parquet": ("application/vnd.apache.parquet", "parquet"),
}
LIST_SEPARATOR = "; "  # list cells in CSV

# column -> type: string, int, float, timestamp, strings (a list of strings)
DOCUMENT_COLUMNS: dict[str, str] = {
    "id": "string",
    "collection": "string",
    "source_id": "int",
    "title": "string",
    "uri": "string",
    "media_type": "string",
    "content_type_group": "string",
    "language": "string",
    "size": "int",
    "pages": "int",
    "status": "string",
    "content_hash": "string",
    "modified_at": "timestamp",
    "indexed_at": "timestamp",
    "concepts": "strings",
    "concept_iris": "strings",
    "entities": "int",
    "page_url": "string",
}
TEXT_COLUMN = ("text", "string")
ENTITY_COLUMNS: dict[str, str] = {
    "document_id": "string",
    "collection": "string",
    "kind": "string",
    "class": "string",
    "iri": "string",
    "label": "string",
    "confidence": "float",
    "stage": "string",
    "pack_id": "string",
    "pack_version": "string",
    "mentions": "int",
    "linked_from": "string",
}

Row = dict[str, Any]


def document_columns(text: bool) -> dict[str, str]:
    return {**DOCUMENT_COLUMNS, **(dict([TEXT_COLUMN]) if text else {})}


def document_row(doc: Document, *, site: str, text_chars: int = 0) -> Row:
    tags = curated_tags(doc.tags or [], (doc.fields or {}).get("curated") or {})
    concepts = [t for t in tags if t.kind == "concept"]
    pages = len(doc.pages or []) or int((doc.metadata or {}).get("page_count") or 0)
    row: Row = {
        "id": doc.id,
        "collection": doc.collection.slug,
        "source_id": doc.source_id,
        "title": doc.title or "",
        "uri": doc.uri,
        "media_type": doc.media_type or "",
        "content_type_group": content_type_group(doc.media_type) if doc.media_type else "",
        "language": doc.language or "",
        "size": doc.size,
        "pages": pages or None,
        "status": doc.status,
        "content_hash": doc.content_hash,
        "modified_at": doc.modified_at,
        "indexed_at": doc.indexed_at,
        "concepts": sorted({t.label for t in concepts if t.label}),
        "concept_iris": sorted({ids.safe_iri(t.iri) for t in concepts}),
        "entities": len(tags) - len(concepts),
        "page_url": ids.page_url(site, doc.id),
    }
    if text_chars:
        row["text"] = (doc.text or "")[:text_chars]
    return row


def entity_rows(doc: Document, secret: str | None) -> Iterator[Row]:
    for tag in curated_tags(doc.tags or [], (doc.fields or {}).get("curated") or {}):
        iri, hidden = ids.target_iri(tag.iri, secret)
        linked = ""
        if tag.linked_from is not None:
            linked, _ = ids.target_iri(tag.linked_from.iri, secret)
        yield {
            "document_id": doc.id,
            "collection": doc.collection.slug,
            "kind": tag.kind,
            "class": ids.entity_class(tag.iri) if ids.is_ner_entity(tag.iri) else "",
            "iri": iri,
            "label": "" if hidden else tag.label,
            "confidence": round(float(tag.confidence), 4),
            "stage": tag.stage,
            "pack_id": tag.pack_id or "",
            "pack_version": tag.pack_version or "",
            "mentions": len(tag.spans),
            "linked_from": linked,
        }


# --- serialisation ---------------------------------------------------------------------------


def _cell(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return LIST_SEPARATOR.join(str(v) for v in value)
    return "" if value is None else value


def csv_stream(batches: Iterable[list[Row]], columns: dict[str, str]) -> Iterator[bytes]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(list(columns))
    yield buf.getvalue().encode("utf-8")
    for batch in batches:
        buf.seek(0)
        buf.truncate()
        for row in batch:
            writer.writerow([_cell(row.get(c)) for c in columns])
        yield buf.getvalue().encode("utf-8")


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def jsonl_stream(batches: Iterable[list[Row]], columns: dict[str, str]) -> Iterator[bytes]:
    for batch in batches:
        lines = (
            json.dumps({c: _json_value(row.get(c)) for c in columns}, ensure_ascii=False) + "\n"
            for row in batch
        )
        yield "".join(lines).encode("utf-8")


class _Chunks:
    """A write-only file that hands back what was written since it was last drained."""

    def __init__(self) -> None:
        self.parts: list[bytes] = []
        self.position = 0
        self.closed = False

    def write(self, data: bytes | bytearray | memoryview) -> int:
        chunk = bytes(data)
        self.parts.append(chunk)
        self.position += len(chunk)
        return len(chunk)

    def tell(self) -> int:
        return self.position

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readable(self) -> bool:
        return False

    def drain(self) -> bytes:
        out = b"".join(self.parts)
        self.parts = []
        return out


def arrow_schema(columns: dict[str, str]) -> Any:
    import pyarrow as pa

    types = {
        "string": pa.string(),
        "int": pa.int64(),
        "float": pa.float64(),
        "timestamp": pa.timestamp("us", tz="UTC"),
        "strings": pa.list_(pa.string()),
    }
    return pa.schema([pa.field(name, types[kind]) for name, kind in columns.items()])


def parquet_stream(batches: Iterable[list[Row]], columns: dict[str, str]) -> Iterator[bytes]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = arrow_schema(columns)
    sink = _Chunks()
    writer = pq.ParquetWriter(pa.PythonFile(sink, mode="w"), schema, compression="zstd")
    try:
        for batch in batches:
            if not batch:
                continue
            data = {c: [row.get(c) for row in batch] for c in columns}
            writer.write_table(pa.Table.from_pydict(data, schema=schema))
            chunk = sink.drain()
            if chunk:
                yield chunk
    finally:
        writer.close()
    yield sink.drain()


STREAMS: dict[str, Callable[[Iterable[list[Row]], dict[str, str]], Iterator[bytes]]] = {
    "csv": csv_stream,
    "jsonl": jsonl_stream,
    "parquet": parquet_stream,
}


def batched(rows: Iterable[Row], size: int = 500) -> Iterator[list[Row]]:
    batch: list[Row] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
