"""fetch: store the original under its content hash in the blob directory."""

from __future__ import annotations

import shutil
from pathlib import Path

from oss_core.contracts import StageContext, WorkingDocument


class FetchStage:
    name = "fetch"
    version = "1"
    requires = ("uri", "content_hash")
    provides = ("blob_path",)

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        blob_root = Path(str(ctx.settings["blob_dir"]))
        src = doc.fields.get("local_path")
        if not src:
            raise FileNotFoundError("fetch needs fields.local_path from the connector")
        suffix = Path(str(src)).suffix.lower()[:16]
        dest = blob_root / doc.content_hash[:2] / f"{doc.content_hash}{suffix}"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), dest)
        doc.fields["blob_path"] = str(dest)
        doc.metadata.setdefault("size", dest.stat().st_size)
        return doc
