"""fetch: store the original under its content hash in the blob directory.

The blob is named by the SHA-256 of the bytes this stage read, computed while it copies
them, never by what discovery reported: a file that changed between discovery and this
stage is stored, and indexed, as what was read (the document's content hash follows), and
an S3 object is keyed by its content, not its ETag. When a blob for the document's content
hash is already stored (another document with the same bytes, or a retried run), it is
reused without reading the source again.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

from oss_core.contracts import StageContext, WorkingDocument

SHA256 = re.compile(r"[0-9a-f]{64}")
CHUNK = 1 << 20


def blob_for(blob_root: Path, digest: str, suffix: str) -> Path:
    return blob_root / digest[:2] / f"{digest}{suffix}"


def copy_hashing(src: Path, blob_root: Path) -> tuple[str, Path]:
    """Copy `src` into the blob directory's incoming area; its SHA-256 and the copy's path."""

    incoming = blob_root / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    part = incoming / uuid.uuid4().hex
    h = hashlib.sha256()
    try:
        with src.open("rb") as fh, part.open("wb") as out:
            while chunk := fh.read(CHUNK):
                h.update(chunk)
                out.write(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return h.hexdigest(), part


class FetchStage:
    name = "fetch"
    version = "1"  # same output: documents are not reprocessed for it
    requires = ("uri", "content_hash")
    provides = ("blob_path",)

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        blob_root = Path(str(ctx.settings["blob_dir"]))
        src = doc.fields.get("local_path")
        if not src:
            raise FileNotFoundError("fetch needs fields.local_path from the connector")
        suffix = Path(str(src)).suffix.lower()[:16]
        claimed = doc.content_hash if SHA256.fullmatch(doc.content_hash or "") else ""
        dest = blob_for(blob_root, claimed, suffix) if claimed else None
        if dest is None or not dest.exists():
            digest, part = copy_hashing(Path(str(src)), blob_root)
            dest = blob_for(blob_root, digest, suffix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                part.unlink(missing_ok=True)
            else:
                os.replace(part, dest)
            if claimed and digest != claimed:
                doc.content_hash = digest  # changed since discovery: it is what was read
        os.utime(dest)  # in use now: the blob sweep leaves recently used blobs alone
        doc.fields["blob_path"] = str(dest)
        doc.metadata.setdefault("size", dest.stat().st_size)
        return doc
