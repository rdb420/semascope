"""preview: page images for PDFs and thumbnails for images, stored beside the blob.

PDF pages are rendered with pypdfium2 (first `preview_pages` pages, default
20, at `preview_width` pixels, default 1200), images are resized with Pillow.
Files land in `<blob_dir>/previews/<content_hash>/<page>.webp`; the API serves
them by document id and page number. Other formats get no preview.
"""

from __future__ import annotations

from pathlib import Path

from oss_core.contracts import StageContext, WorkingDocument


class PreviewStage:
    name = "preview"
    version = "1"
    requires = ("blob_path", "media_type")
    provides = ("preview_pages",)

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        blob = Path(str(doc.fields["blob_path"]))
        out_dir = Path(str(ctx.settings["blob_dir"])) / "previews" / doc.content_hash
        max_pages = int(ctx.settings.get("preview_pages", 20))
        width = int(ctx.settings.get("preview_width", 1200))
        media_type = (doc.media_type or "").lower()
        count = 0
        if media_type == "application/pdf":
            count = render_pdf(blob, out_dir, max_pages=max_pages, width=width)
        elif media_type.startswith("image/"):
            count = render_image(blob, out_dir, width=width)
        doc.fields["preview_pages"] = count
        if count:
            doc.fields["preview_dir"] = str(out_dir)
        return doc


def render_pdf(blob: Path, out_dir: Path, *, max_pages: int, width: int) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(blob))
    try:
        n = min(len(pdf), max_pages)
        if n == 0:
            return 0
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            target = out_dir / f"{i + 1}.webp"
            if target.exists():
                continue
            page = pdf[i]
            scale = width / max(page.get_width(), 1)
            image = page.render(scale=scale).to_pil()
            image.save(target, "WEBP", quality=80)
            page.close()
        return n
    finally:
        pdf.close()


def render_image(blob: Path, out_dir: Path, *, width: int) -> int:
    from PIL import Image

    target = out_dir / "1.webp"
    if target.exists():
        return 1
    with Image.open(blob) as source:
        image = source.convert("RGB")
    if image.width > width:
        image = image.resize((width, int(image.height * width / image.width)))
    out_dir.mkdir(parents=True, exist_ok=True)
    image.save(target, "WEBP", quality=80)
    return 1
