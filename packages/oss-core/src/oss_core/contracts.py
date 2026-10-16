"""Plugin contracts.

These protocols are the stable surface between the product and its plugins.
A change here needs an architecture decision record (docs/adr/).

Plugins register through Python entry points:

    [project.entry-points."oss.connectors"]
    filesystem = "oss_pipeline.connectors.filesystem:FilesystemConnector"

    [project.entry-points."oss.stages"]
    extract = "oss_pipeline.stages.extract:ExtractStage"
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Cursor(BaseModel):
    """Opaque resume point a connector hands back after a discovery pass."""

    value: str
    issued_at: datetime


class SourceConfig(BaseModel):
    """One configured source inside a collection."""

    id: str
    collection: str
    connector: str
    settings: dict[str, Any] = Field(default_factory=dict)


class SourceItem(BaseModel):
    """Something a connector found that may need (re)indexing."""

    uri: str
    content_hash: str | None = None
    modified_at: datetime | None = None
    size: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FetchedItem(BaseModel):
    """The bytes (or a path to them) and metadata for one source item."""

    item: SourceItem
    path: str | None = None
    content: bytes | None = None
    media_type: str | None = None


class Span(BaseModel):
    start: int
    end: int
    page: int | None = None


class Tag(BaseModel):
    """A concept or entity found in a document, with provenance."""

    iri: str
    label: str
    kind: Literal["concept", "entity"]
    pack_id: str | None = None
    pack_version: str | None = None
    stage: str
    confidence: float = 1.0
    spans: list[Span] = Field(default_factory=list)


class StageRun(BaseModel):
    stage: str
    version: str
    started_at: datetime
    duration_ms: int
    outcome: Literal["ok", "skipped", "failed"]
    message: str | None = None


class WorkingDocument(BaseModel):
    """The document as it moves through the pipeline.

    Stages read the fields they declare in `requires` and write the ones in
    `provides`. Unknown keys live in `fields` so a plugin can add data without
    changing the core model.
    """

    id: str
    collection: str
    source_id: str
    uri: str
    content_hash: str
    media_type: str | None = None
    language: str | None = None
    title: str | None = None
    text: str | None = None
    pages: list[str] = Field(default_factory=list)
    chunks: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    fields: dict[str, Any] = Field(default_factory=dict)
    tags: list[Tag] = Field(default_factory=list)
    embeddings: dict[str, list[list[float]]] = Field(default_factory=dict)
    stage_runs: list[StageRun] = Field(default_factory=list)


class StageContext(BaseModel):
    """What a stage may use besides the document: settings and the active packs."""

    collection: str
    profile: str
    settings: dict[str, Any] = Field(default_factory=dict)
    pack_versions: dict[str, str] = Field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    name: str

    def discover(self, source: SourceConfig, since: Cursor | None) -> Iterable[SourceItem]: ...

    def fetch(self, item: SourceItem) -> FetchedItem: ...

    def deleted(self, source: SourceConfig, since: Cursor) -> Iterable[str]: ...


@runtime_checkable
class Stage(Protocol):
    """One step of a profile. A document's whole profile runs in one task on the `cpu`
    queue, so a stage has no queue of its own (docs/adr/0009-one-task-per-document.md)."""

    name: str
    version: str
    requires: tuple[str, ...]
    provides: tuple[str, ...]

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument: ...


@runtime_checkable
class Embedder(Protocol):
    name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class IndexBackend(Protocol):
    name: str

    def index(self, doc: WorkingDocument) -> None: ...

    def delete(self, document_id: str) -> None: ...


class Profile(BaseModel):
    """An ordered list of stages for a collection, validated as a DAG."""

    name: str
    stages: list[str]

    def validate_against(self, stages: dict[str, Stage]) -> list[str]:
        """Return problems: unknown stages and `requires` not provided earlier."""

        problems: list[str] = []
        available: set[str] = {"id", "collection", "source_id", "uri", "content_hash"}
        for name in self.stages:
            stage = stages.get(name)
            if stage is None:
                problems.append(f"unknown stage: {name}")
                continue
            missing = [field for field in stage.requires if field not in available]
            if missing:
                problems.append(f"{name} requires {missing} before any stage provides them")
            available.update(stage.provides)
        return problems
