"""structure: pages and chunks from the extracted text.

Tika marks page boundaries in PDF text with form feeds. Chunks are built by
words (a crude token proxy that needs no model) with overlap, and each chunk
remembers the page it starts on. `fields["page_starts"]` records where each
page starts in the joined text, so later stages can give every span its page.
"""

from __future__ import annotations

import re

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.provenance import PAGE_SEPARATOR

_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


def normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    return _BLANKS.sub("\n\n", text).strip()


def split_pages(text: str) -> list[str]:
    pages = [normalise(p) for p in text.split("\f")]
    pages = [p for p in pages if p]
    return pages or ([normalise(text)] if text.strip() else [])


def chunk_words(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, size - overlap)
    return [" ".join(words[i : i + size]) for i in range(0, max(1, len(words) - overlap), step)]


class StructureStage:
    name = "structure"
    version = "2"
    requires = ("text",)
    provides = ("pages", "chunks")

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        size = int(ctx.settings.get("chunk_words", 350))
        overlap = int(ctx.settings.get("chunk_overlap", 40))
        text = doc.text or ""
        doc.pages = split_pages(text)
        doc.text = PAGE_SEPARATOR.join(doc.pages)
        starts: list[int] = []
        pos = 0
        for page in doc.pages:
            starts.append(pos)
            pos += len(page) + len(PAGE_SEPARATOR)
        doc.fields["page_starts"] = starts  # where each page starts in doc.text (span pages)
        chunks: list[str] = []
        chunk_pages: list[int] = []
        for page_no, page in enumerate(doc.pages, start=1):
            for piece in chunk_words(page, size, overlap):
                chunks.append(piece)
                chunk_pages.append(page_no)
        doc.chunks = chunks
        doc.fields["chunk_pages"] = chunk_pages
        doc.metadata["page_count"] = doc.metadata.get("page_count") or len(doc.pages)
        doc.metadata["word_count"] = len(doc.text.split())
        return doc
