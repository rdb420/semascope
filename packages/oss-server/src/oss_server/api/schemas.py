from __future__ import annotations

from datetime import datetime
from typing import Any

from ninja import Schema
from pydantic import field_validator

from oss_pipeline.connectors.upload import COLLECTION_SLUG


class CollectionIn(Schema):
    slug: str
    name: str | None = None
    description: str = ""
    profile: str = "default"
    settings: dict[str, Any] = {}

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not COLLECTION_SLUG.fullmatch(value):
            raise ValueError("a collection slug is 1 to 64 letters, digits, hyphens or underscores")
        return value

    @field_validator("settings")
    @classmethod
    def _tuning_only(cls, value: dict[str, Any]) -> dict[str, Any]:
        from oss_server.pipeline.overrides import refused_overrides

        refused = refused_overrides(value)
        if refused:
            raise ValueError(
                "settings.pipeline may set only tuning keys (see docs/operations.md); "
                f"not allowed: {', '.join(refused)}"
            )
        return value


class CollectionOut(Schema):
    slug: str
    name: str
    description: str
    profile: str
    settings: dict[str, Any]
    created_at: datetime
    document_count: int = 0


class SourceIn(Schema):
    name: str
    connector: str
    settings: dict[str, Any] = {}
    enabled: bool = True
    schedule: str = ""


class SourceOut(Schema):
    id: int
    collection: str
    name: str
    connector: str
    settings: dict[str, Any]
    enabled: bool
    schedule: str
    status: str
    last_run_at: datetime | None
    last_error: str
    document_count: int = 0


class RunOut(Schema):
    source_id: int
    task_id: str


class DocumentOut(Schema):
    id: str
    collection: str
    source_id: int
    uri: str
    title: str
    media_type: str
    language: str
    size: int | None
    status: str
    graph_status: str = ""  # the Neo4j projection: "", ok, pending (Neo4j was away) or failed
    error: str
    modified_at: datetime | None
    indexed_at: datetime | None
    metadata: dict[str, Any]
    fields: dict[str, Any]
    stage_runs: list[dict[str, Any]]


class DocumentTextOut(Schema):
    id: str
    text: str
    pages: list[str]


class SearchIn(Schema):
    q: str = "*:*"
    collection: str | None = None
    filters: dict[str, list[str]] = {}
    facets: list[str] = ["content_type_group_s", "language_s", "media_type_s", "source_id_s"]
    facet_limit: int = 20
    sort: str | None = None
    page: int = 1
    page_size: int = 10
    highlight: bool = True
    mode: str = "keyword"  # keyword | semantic | hybrid
    histogram: str | None = None  # a date field to bucket, e.g. modified_dt
    histogram_gap: str = "+1YEAR"  # Solr gap: +1DAY, +1MONTH, +1YEAR, +10YEARS


class FacetValue(Schema):
    value: str
    count: int


class SearchHit(Schema):
    id: str
    score: float | None
    title: str
    uri: str
    collection: str
    source_id: str
    media_type: str | None
    content_type_group: str | None
    language: str | None
    size: int | None
    page_count: int | None
    modified_at: str | None
    indexed_at: str | None
    preview_pages: int
    snippets: list[str]
    passages: list[str] = []  # best-matching chunks in semantic and hybrid modes


class SearchOut(Schema):
    total: int
    page: int
    page_size: int
    took_ms: int
    hits: list[SearchHit]
    facets: dict[str, list[FacetValue]]
    histogram: list[FacetValue] = []


class SuggestOut(Schema):
    suggestions: list[str]


class StatsOut(Schema):
    collections: int
    sources: int
    documents: dict[str, int]
    solr_documents: int | None
    chunk_models: dict[str, int] = {}
    embed_model: str = ""
    # indexed documents whose Neo4j projection is `pending` (recovered automatically) or
    # `failed` (needs `oss reindex`); and documents held after their runs kept killing a worker
    graph: dict[str, int] = {}
    interrupted: int = 0
