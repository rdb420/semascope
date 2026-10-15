"""index: write the document to the Solr `documents` collection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.clients.solr import SolrClient

ENTITY_FIELDS = (
    "emails",
    "urls",
    "phones",
    "money",
    "hashtags",
    "persons",
    "organisations",
    "places",
)


def to_solr_document(doc: WorkingDocument, indexed_at: datetime | None = None) -> dict[str, Any]:
    meta = doc.metadata
    out: dict[str, Any] = {
        "id": doc.id,
        "collection_s": doc.collection,
        "source_id_s": doc.source_id,
        "uri_s": doc.uri,
        "content_hash_s": doc.content_hash,
        "title_txt": doc.title or doc.uri.rsplit("/", 1)[-1],
        "text_txt": doc.text or "",
        "language_s": doc.language or "und",
        "media_type_s": doc.media_type or "application/octet-stream",
        "content_type_group_s": meta.get("content_type_group") or "text",
        "size_l": meta.get("size"),
        "page_count_i": meta.get("page_count"),
        "ocr_b": bool(meta.get("ocr", False)),
        "preview_pages_i": int(doc.fields.get("preview_pages") or 0),
        "indexed_dt": _iso(indexed_at or datetime.now(UTC)),
    }
    if meta.get("modified_at"):
        out["modified_dt"] = _iso(meta["modified_at"])
    for name in ENTITY_FIELDS:
        values = doc.fields.get(name)
        if values:
            out[f"{name}_ss"] = list(values)
    concept = [t for t in doc.tags if t.kind == "concept"]
    if concept:
        out["concept_iris_ss"] = sorted({t.iri for t in concept})
        out["concept_labels_ss"] = sorted({t.label for t in concept})
    linked = [t for t in doc.tags if t.kind == "entity" and t.pack_id]
    if linked:
        out["entity_iris_ss"] = sorted({t.iri for t in linked})
        out["entity_labels_ss"] = sorted({t.label for t in linked})
    packs = {f"{t.pack_id}@{t.pack_version}" for t in doc.tags if t.pack_id}
    if packs:
        out["pack_versions_ss"] = sorted(packs)
    for key, value in doc.fields.items():
        if key.startswith("x_") and isinstance(value, str | int | float | bool | list):
            name = key[2:]  # pre-suffixed custom fields from connectors: x_owner_s -> owner_s
            if name.endswith("_paths") and isinstance(value, list):
                value = expand_paths([str(v) for v in value])
            out[name] = value
    return {k: v for k, v in out.items() if v is not None}


def expand_paths(paths: list[str]) -> list[str]:
    """ "a/b/c" -> ["a", "a/b", "a/b/c"], deduplicated and ordered."""

    out: list[str] = []
    for path in paths:
        parts = [p for p in path.split("/") if p]
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            if prefix not in out:
                out.append(prefix)
    return out


def _iso(value: Any) -> str | None:
    """Solr wants `YYYY-MM-DDTHH:MM:SSZ`; accept datetimes and ISO strings, drop the rest."""

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, datetime):
        stamp: datetime = value if value.tzinfo else value.replace(tzinfo=UTC)
        return stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


class IndexStage:
    name = "index"
    version = "1"
    queue = "io"
    requires = ("text",)
    provides = ("indexed",)

    def __init__(self, solr: SolrClient | None = None) -> None:
        self._solr = solr

    def _client(self, ctx: StageContext) -> SolrClient:
        if self._solr is None:
            self._solr = SolrClient(
                str(ctx.settings["solr_url"]),
                user=ctx.settings.get("solr_user"),
                password=ctx.settings.get("solr_password"),
            )
        return self._solr

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        collection = str(ctx.settings.get("solr_documents", "documents"))
        self._client(ctx).add(collection, [to_solr_document(doc)])
        doc.fields["indexed"] = True
        return doc
