from datetime import UTC, datetime

from oss_core.contracts import (
    Profile,
    Stage,
    StageContext,
    StageRun,
    WorkingDocument,
)


class _Stage:
    name: str
    version: str = "1"
    requires: tuple[str, ...]
    provides: tuple[str, ...]

    def __init__(self, name: str, requires: tuple[str, ...], provides: tuple[str, ...]) -> None:
        self.name = name
        self.requires = requires
        self.provides = provides

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        return doc


def test_profile_accepts_ordered_dag() -> None:
    stages: dict[str, Stage] = {
        "extract": _Stage("extract", ("uri",), ("text", "media_type")),
        "patterns": _Stage("patterns", ("text",), ("tags",)),
    }
    assert Profile(name="p", stages=["extract", "patterns"]).validate_against(stages) == []


def test_profile_reports_missing_requirements_and_unknown_stages() -> None:
    stages: dict[str, Stage] = {"patterns": _Stage("patterns", ("text",), ("tags",))}
    problems = Profile(name="p", stages=["patterns", "nope"]).validate_against(stages)
    assert any("requires ['text']" in p for p in problems)
    assert "unknown stage: nope" in problems


def test_working_document_round_trips() -> None:
    doc = WorkingDocument(
        id="d1", collection="c", source_id="s", uri="file:///a", content_hash="abc"
    )
    doc.stage_runs.append(
        StageRun(
            stage="extract",
            version="1",
            started_at=datetime.now(UTC),
            duration_ms=3,
            outcome="ok",
        )
    )
    assert WorkingDocument.model_validate(doc.model_dump()).stage_runs[0].stage == "extract"
