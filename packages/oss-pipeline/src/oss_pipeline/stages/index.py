"""index: write the document to the Solr `documents` collection."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from oss_core.contracts import StageContext, Tag, WorkingDocument
from oss_pipeline.clients.solr import SolrClient
from oss_pipeline.languages import all_language_fields, language_fields
from oss_pipeline.provenance import curated_tags
from oss_pipeline.stages.base import solr_key

ENTITY_FIELDS = (
    "emails",
    "urls",
    "phones",
    "money",
    "hashtags",
    "persons",
    "organisations",
    "places",
    "events",
)

# Custom fields: `x_<name>` from connectors (table columns, an RDF subject), the tag stage
# (`x_<scheme>_paths`, scheme slugs may hold hyphens) and zero-shot NER, indexed as <name>.
CUSTOM_FIELD = re.compile(r"x_[a-z0-9][a-z0-9_-]{0,127}")
# The index's own fields (the explicit fields of the documents schema, plus what this stage
# writes): a custom field never replaces one, so nothing a source supplies can change a
# document's id, collection, source or text.
RESERVED_FIELDS = frozenset(
    {
        "id",
        "_version_",
        "collection_s",
        "source_id_s",
        "uri_s",
        "content_hash_s",
        "parent_id_s",
        "title_txt",
        "title_s",
        "text_txt",
        *all_language_fields(),  # title_txt_<lang>, text_txt_<lang> (oss_pipeline.languages)
        "language_s",
        "media_type_s",
        "content_type_group_s",
        "size_l",
        "page_count_i",
        "ocr_b",
        "preview_pages_i",
        "modified_dt",
        "created_dt",
        "indexed_dt",
        "dates_dts",
        "concept_iris_ss",
        "concept_labels_ss",
        "entity_iris_ss",
        "entity_labels_ss",
        "pack_versions_ss",
        "curated_ss",
        *(f"{name}_ss" for name in ENTITY_FIELDS),
    }
)


def custom_field_name(key: object) -> str | None:
    """The index field a custom `x_` key becomes, or None when the key is not allowed."""

    if not isinstance(key, str) or not CUSTOM_FIELD.fullmatch(key):
        return None
    name = key[2:]
    return None if name in RESERVED_FIELDS else name


def custom_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The allowed custom `x_` fields of a mapping (e.g. a connector's metadata `fields`)."""

    return {k: v for k, v in fields.items() if custom_field_name(k) is not None}


def effective_tags(doc: WorkingDocument) -> list[Tag]:
    """The document's tags after curation: rejected ones dropped, accepted additions and links
    applied (`oss_pipeline.provenance.curated_tags`)."""

    return curated_tags(doc.tags, doc.fields.get("curated"))


def to_solr_document(doc: WorkingDocument, indexed_at: datetime | None = None) -> dict[str, Any]:
    meta = doc.metadata
    tags = effective_tags(doc)
    curated: dict[str, Any] = dict(doc.fields.get("curated") or {})
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
    # the stemmed copies for the document's language (docs/search.md#languages)
    out.update(language_fields(doc.language, out["title_txt"], out["text_txt"]))
    if meta.get("modified_at"):
        out["modified_dt"] = _iso(meta["modified_at"])
    for name in ENTITY_FIELDS:
        values = doc.fields.get(name)
        if values:
            out[f"{name}_ss"] = list(values)
    concept = [t for t in tags if t.kind == "concept"]
    if concept:
        out["concept_iris_ss"] = sorted({t.iri for t in concept})
        out["concept_labels_ss"] = sorted({t.label for t in concept})
    linked = [t for t in tags if t.kind == "entity" and t.pack_id]
    if linked:
        out["entity_iris_ss"] = sorted({t.iri for t in linked})
        out["entity_labels_ss"] = sorted({t.label for t in linked})
    packs = {f"{t.pack_id}@{t.pack_version}" for t in tags if t.pack_id}
    if packs:
        out["pack_versions_ss"] = sorted(packs)
    if curated:
        out["curated_ss"] = sorted(f"{iri}:{v.get('decision')}" for iri, v in curated.items())
    tree = curated_path_fields(doc.fields, tags)
    for key, value in doc.fields.items():
        field = custom_field_name(key)  # pre-suffixed custom fields: x_owner_s -> owner_s
        if field in tree:
            continue  # a scheme's tag paths come from the curated view
        if field and isinstance(value, str | int | float | bool | list):
            if field.endswith("_paths") and isinstance(value, list):
                value = expand_paths([str(v) for v in value])
            out[field] = value
    out.update(tree)
    return {k: v for k, v in out.items() if v is not None}


def curated_path_fields(fields: Mapping[str, Any], tags: Iterable[Tag]) -> dict[str, Any]:
    """`<scheme>_paths` for every scheme the document's tags or decisions touch, from the
    curated view: the paths of the tags in `tags` (the curated view, rejected tags left
    out), expanded to their ancestors, or None for a scheme left with no path (an atomic
    update then removes the field).

    Each tag's paths come from the tag stage (`fields["paths_by_tag"]`) or, for a tag a curator
    added, from the decision (`curated[iri]["paths"]`). A document tagged before the tag
    stage kept `paths_by_tag` falls back to the tag stage's `x_<scheme>_paths` less the paths
    of rejected concepts plus those of accepted ones; a reindex replaces that with the exact
    computation.
    """

    curated: Mapping[str, Any] = fields.get("curated") or {}
    decisions = {str(k): v for k, v in curated.items() if isinstance(v, Mapping)}
    schemes: set[str] = {str(s) for s in fields.get("tagged_schemes") or []}
    for decision in decisions.values():
        schemes.update(str(s) for s in (decision.get("paths") or {}))
    leaves: dict[str, set[str]] = defaultdict(set)
    by_iri = fields.get("paths_by_tag")
    if isinstance(by_iri, Mapping):
        for tag in tags:
            own = by_iri.get(tag.iri) or (decisions.get(tag.iri) or {}).get("paths") or {}
            for scheme, values in own.items():
                leaves[str(scheme)].update(str(v) for v in values or [])
    else:
        for scheme in schemes:
            values = fields.get(f"x_{scheme}_paths")
            if isinstance(values, list):
                leaves[scheme].update(str(v) for v in values)
        kept = {tag.iri for tag in tags}
        for iri, decision in decisions.items():
            for scheme, values in (decision.get("paths") or {}).items():
                if decision.get("decision") == "reject":
                    leaves[str(scheme)].difference_update(str(v) for v in values or [])
                elif decision.get("decision") == "accept" and iri in kept:
                    leaves[str(scheme)].update(str(v) for v in values or [])
    schemes.update(leaves)
    out: dict[str, Any] = {}
    for scheme in sorted(schemes):
        field = custom_field_name(f"x_{scheme}_paths")
        if field is not None:
            out[field] = expand_paths(sorted(leaves[scheme])) or None
    return out


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
    version = "3"
    requires = ("text",)
    provides = ("indexed",)

    def __init__(self, solr: SolrClient | None = None) -> None:
        self._solr = solr
        self._solr_key: tuple[str, ...] | None = None  # None: a client handed in

    def _client(self, ctx: StageContext) -> SolrClient:
        key = solr_key(ctx)
        if self._solr is None or (self._solr_key is not None and self._solr_key != key):
            self.close()
            self._solr = SolrClient(
                str(ctx.settings["solr_url"]),
                user=ctx.settings.get("solr_user"),
                password=ctx.settings.get("solr_password"),
            )
            self._solr_key = key
        return self._solr

    def close(self) -> None:
        if self._solr is not None and self._solr_key is not None:
            self._solr.close()  # only a client this stage built
            self._solr = None

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        collection = str(ctx.settings.get("solr_documents", "documents"))
        self._client(ctx).add(collection, [to_solr_document(doc)])
        doc.fields["indexed"] = True
        return doc
