"""`oss stats` and `oss doctor` count documents waiting for their OCR pass (`ocr: later`)."""

from __future__ import annotations

import httpx
from pydantic import SecretStr

from oss_cli.main import document_notes, document_report
from oss_core.config import Settings


def test_pending_ocr_is_a_note_and_part_of_the_doctor_summary() -> None:
    notes = document_notes({"graph": {}, "interrupted": 0, "ocr_pending": 7})
    assert len(notes) == 1 and notes[0].startswith("7 documents are searchable by their text")
    body = {"graph": {}, "interrupted": 0, "ocr_pending": 7}
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    summary, _ = document_report(Settings(semascope_api_token=SecretStr("t")), client)
    assert summary.endswith(", ocr pending 7")
