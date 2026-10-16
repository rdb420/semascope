#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6,<7", "pillow==12.3.0"]
# ///
"""Derive the `mixed-1k` benchmark corpus from the demo texts.

Cuts the sixteen texts that `build.py` wrote to `corpus/` into about 1,000
chapters and renders each chapter in one format, so every extraction path has
demo data under the texts' public-domain status (ADR-0005):

    html  docx  odt  epub  pdf (text layer)  scanned pdf (page images, no text
    layer)  png (page image)  eml  mbox  csv  ttl  zip

A scanned PDF holds the chapter's first four pages and a PNG its first page,
to bound the OCR time; every other format holds the whole chapter.

Output goes to `derived/mixed-1k/<format>/<slug>-<NNN>.<ext>`, with an index of
which chapter each file holds in `derived/mixed-1k.index.csv` (outside the corpus
folder, so it is not indexed with it).

The output is deterministic: no clocks, no randomness without a fixed seed,
zip members stored with a fixed date, PDFs and images written by a pinned
Pillow. `mixed-1k.sha256` (committed, `sha256sum -c` format) pins every file;
`derive.py` checks the input texts against `sources.yaml` before it starts and
its output against that manifest when it finishes, and fails on a difference
unless `--update-manifest` is given.

Libraries: the standard library, PyYAML (MIT) and Pillow (MIT-CMU, an
MIT-family licence). Page images use Pillow's built-in Aileron font (CC0).

Usage: uv run packs/demo/derive.py [--update-manifest] [--check-only]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import random
import re
import shutil
import sys
import textwrap
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import yaml

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"
DERIVED = HERE / "derived"
NAME = "mixed-1k"
MANIFEST = HERE / f"{NAME}.sha256"
TARGET_DOCS = 1000

# One cycle of 25 slots decides the format of each chapter in corpus order. Office and web
# formats dominate, as in a real share; scanned PDFs and page images exercise OCR.
CYCLE = (
    ["html"] * 4
    + ["docx"] * 3
    + ["pdf"] * 3
    + ["eml"] * 3
    + ["csv"] * 2
    + ["scan"] * 2
    + ["odt"] * 2
    + ["epub"] * 2
    + ["mbox", "ttl", "zip", "png"]
)
CYCLE = [CYCLE[(i * 7) % len(CYCLE)] for i in range(len(CYCLE))]  # interleave, still 25 slots
EXT = {"scan": "pdf"}
SCAN_PAGES = 4  # a scanned PDF carries the chapter's first pages; OCR cost grows per page
FIXED_DATE = datetime(2026, 10, 15, tzinfo=UTC)
ZIP_DATE = (2026, 10, 15, 0, 0, 0)
SENDER = "Semascope demo <demo@example.org>"
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HEADING = re.compile(
    r"^(BOOK|Book|CHAPTER|Chapter|PART|Part|CANTO|Canto|RUNE|Rune|SURA|Sura|SECTION|Section|"
    r"PSALM|Psalm|FABLE|Fable|STORY|Story|[IVXLC]+\.)\b"
)


@dataclass(frozen=True)
class Chapter:
    slug: str
    number: int
    title: str
    paragraphs: tuple[str, ...]
    source_title: str
    creator: str

    @property
    def name(self) -> str:
        return f"{self.slug}-{self.number:03d}"

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)


# --- splitting ----------------------------------------------------------------------------------


def paragraphs(text: str) -> list[str]:
    text = CONTROL.sub("", text.replace("\r\n", "\n"))
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def is_heading(p: str) -> bool:
    if "\n" in p or len(p) > 80 or p.endswith((".", ",", ";", ":")) and not HEADING.match(p):
        return False
    letters = [c for c in p if c.isalpha()]
    return bool(HEADING.match(p)) or (len(letters) >= 4 and all(c.isupper() for c in letters))


def cut(paras: list[str], target: int) -> list[list[str]]:
    """Pack paragraphs into chapters near `target` characters, breaking before headings."""

    out: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for p in paras:
        if cur and ((is_heading(p) and size >= target // 2) or size >= target * 8 // 5):
            out.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p) + 2
    if cur:
        if out and size < target // 4:
            out[-1].extend(cur)
        else:
            out.append(cur)
    return out


def chapters(texts: list[dict[str, str]], wanted: int = TARGET_DOCS) -> list[Chapter]:
    paras = {t["slug"]: paragraphs((CORPUS / f"{t['slug']}.txt").read_text("utf-8")) for t in texts}
    total = sum(len(p) + 2 for ps in paras.values() for p in ps)
    target = total // wanted
    for _ in range(12):  # settle the target so the corpus has about `wanted` chapters
        n = sum(len(cut(ps, target)) for ps in paras.values())
        if abs(n - wanted) <= wanted // 50:
            break
        target = max(500, target * n // wanted)
    out: list[Chapter] = []
    for t in texts:
        for i, chunk in enumerate(cut(paras[t["slug"]], target), start=1):
            head = next((p for p in chunk[:3] if is_heading(p)), None)
            title = (
                f"{t['title']}: {head.title() if head and head.isupper() else head}"
                if head
                else (f"{t['title']}, part {i}")
            )
            out.append(
                Chapter(t["slug"], i, title, tuple(chunk), t["title"], str(t.get("creator", "")))
            )
    return out


# --- renderers ----------------------------------------------------------------------------------


def x(s: str) -> str:
    return xml_escape(s, {'"': "&quot;"})


def render_html(c: Chapter) -> bytes:
    body = "\n".join(f"<p>{html.escape(p)}</p>" for p in c.paragraphs)
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(c.title)}</title>\n"
        f'<meta name="author" content="{html.escape(c.creator)}">\n'
        f'<meta name="dc.source" content="{html.escape(c.source_title)}">\n'
        f"</head>\n<body>\n<h1>{html.escape(c.title)}</h1>\n{body}\n</body>\n</html>\n"
    ).encode()


def _zip(members: Iterable[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return buf.getvalue()


def render_docx(c: Chapter) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paras = [
        f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>{x(c.title)}</w:t></w:r></w:p>'
    ]
    paras += [
        f'<w:p><w:r><w:t xml:space="preserve">{x(p.replace(chr(10), " "))}</w:t></w:r></w:p>'
        for p in c.paragraphs
    ]
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document xmlns:w="{w}">'
        f"<w:body>{''.join(paras)}</w:body></w:document>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/'
        'relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-'
        'package.core-properties+xml"/></Types>'
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
        'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{x(c.title)}</dc:title><dc:creator>{x(c.creator)}</dc:creator>"
        f"<dc:source>{x(c.source_title)}</dc:source></cp:coreProperties>"
    )
    return _zip(
        [
            ("[Content_Types].xml", types.encode()),
            ("_rels/.rels", rels.encode()),
            ("word/document.xml", document.encode()),
            ("docProps/core.xml", core.encode()),
        ]
    )


def render_odt(c: Chapter) -> bytes:
    ns = (
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"'
    )
    body = f'<text:h text:outline-level="1">{x(c.title)}</text:h>' + "".join(
        f"<text:p>{x(p.replace(chr(10), ' '))}</text:p>" for p in c.paragraphs
    )
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content {ns} '
        f'office:version="1.3"><office:body><office:text>{body}</office:text></office:body>'
        "</office:document-content>"
    )
    meta = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-meta {ns} office:version="1.3">'
        f"<office:meta><dc:title>{x(c.title)}</dc:title><meta:initial-creator>{x(c.creator)}"
        "</meta:initial-creator></office:meta></office:document-meta>"
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<manifest:manifest xmlns:manifest="urn:oasis:'
        'names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">'
        '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/'
        'vnd.oasis.opendocument.text"/><manifest:file-entry manifest:full-path="content.xml" '
        'manifest:media-type="text/xml"/><manifest:file-entry manifest:full-path="meta.xml" '
        'manifest:media-type="text/xml"/></manifest:manifest>'
    )
    return _zip(
        [
            ("mimetype", b"application/vnd.oasis.opendocument.text"),
            ("META-INF/manifest.xml", manifest.encode()),
            ("meta.xml", meta.encode()),
            ("content.xml", content.encode()),
        ]
    )


def render_epub(c: Chapter) -> bytes:
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<container version="1.0" xmlns="urn:oasis:'
        'names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/'
        'content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="id">urn:semascope-demo:{c.name}</dc:identifier>'
        f"<dc:title>{x(c.title)}</dc:title><dc:creator>{x(c.creator)}</dc:creator>"
        "<dc:language>en</dc:language>"
        f'<meta property="dcterms:modified">{FIXED_DATE:%Y-%m-%dT%H:%M:%SZ}</meta></metadata>'
        '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/><item id="ch" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="ch"/></spine></package>'
    )

    def xhtml(title: str, body: str) -> bytes:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org'
            '/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en"><head>'
            f"<title>{x(title)}</title></head><body>{body}</body></html>"
        ).encode()

    nav = xhtml(
        c.title,
        f'<nav epub:type="toc"><ol><li><a href="chapter.xhtml">{x(c.title)}</a></li></ol></nav>',
    )
    chapter = xhtml(
        c.title, f"<h1>{x(c.title)}</h1>" + "".join(f"<p>{x(p)}</p>" for p in c.paragraphs)
    )
    return _zip(
        [
            ("mimetype", b"application/epub+zip"),
            ("META-INF/container.xml", container.encode()),
            ("OEBPS/content.opf", opf.encode()),
            ("OEBPS/nav.xhtml", nav),
            ("OEBPS/chapter.xhtml", chapter),
        ]
    )


def _pdf_string(s: str) -> bytes:
    raw = s.encode("cp1252", "replace")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def render_pdf(c: Chapter) -> bytes:
    """A text-layer PDF: Helvetica in WinAnsi encoding, US Letter, 50 lines a page."""

    lines: list[tuple[str, str]] = [("F2", c.title), ("F1", "")]
    for p in c.paragraphs:
        for line in textwrap.wrap(p.replace("\n", " "), 82) or [""]:
            lines.append(("F1", line))
        lines.append(("F1", ""))
    pages = [lines[i : i + 50] for i in range(0, len(lines), 50)]
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # pages, filled below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    ]
    kids = []
    for page in pages:
        ops = [b"BT 14 TL 72 730 Td"]
        for font, line in page:
            ops.append(
                b"/"
                + font.encode()
                + (b" 13 Tf " if font == "F2" else b" 11 Tf ")
                + _pdf_string(line)
                + b" Tj T*"
            )
        ops.append(b"ET")
        stream = b"\n".join(ops)
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_ref = len(objs)
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % content_ref
        )
        kids.append(len(objs))
    return _assemble_pdf(objs, kids, c)


def _assemble_pdf(objs: list[bytes], kids: list[int], c: Chapter) -> bytes:
    """Number the objects, add the page tree and the document info, write the xref."""

    objs[1] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (
        len(kids),
        b" ".join(b"%d 0 R" % k for k in kids),
    )
    objs.append(
        b"<< /Title "
        + _pdf_string(c.title)
        + b" /Author "
        + _pdf_string(c.creator)
        + b" /Producer (Semascope demo derive.py) >>"
    )
    info = len(objs)
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        info,
        xref,
    )
    return bytes(out)


_FONT: object = None


def _page_images(c: Chapter, max_pages: int) -> list[object]:
    """Render the chapter as 200 dpi bilevel pages that look scanned (skew and specks)."""

    from PIL import Image, ImageDraw, ImageFont

    global _FONT
    if _FONT is None:
        _FONT = ImageFont.load_default(size=30)  # Aileron Regular, bundled with Pillow (CC0)
    font = _FONT
    width, height, margin, leading = 1700, 2200, 150, 42
    per_page = (height - 2 * margin) // leading
    lines: list[str] = [c.title, ""]
    for p in c.paragraphs:
        words = p.split()
        line = ""
        for word in words:
            trial = f"{line} {word}".strip()
            if font.getlength(trial) > width - 2 * margin and line:  # type: ignore[attr-defined]
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        lines.append("")
        if len(lines) > per_page * max_pages:
            break
    rng = random.Random(c.name)  # noqa: S311 - a fixed seed for reproducible noise, not crypto
    pages = []
    for start in range(0, min(len(lines), per_page * max_pages), per_page):
        im = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(im)
        for i, line in enumerate(lines[start : start + per_page]):
            draw.text((margin, margin + i * leading), line, font=font, fill=0)
        for _ in range(1500):
            draw.point((rng.randrange(width), rng.randrange(height)), fill=0)
        im = im.rotate(rng.uniform(-0.8, 0.8), resample=Image.Resampling.BICUBIC, fillcolor=255)
        pages.append(
            im.point(lambda v: 255 if v > 150 else 0).convert("1", dither=Image.Dither.NONE)
        )
    return pages


def _g4(page: object) -> bytes:
    """CCITT Group 4 data of a bilevel page, as libtiff encodes it into a one-strip TIFF."""

    from PIL import Image

    im = page  # a mode "1" PIL image
    width, height = im.size  # type: ignore[attr-defined]
    buf = io.BytesIO()
    im.save(buf, "TIFF", compression="group4", strip_size=(width + 7) // 8 * height + 1)  # type: ignore[attr-defined]
    with Image.open(io.BytesIO(buf.getvalue())) as tif:
        offsets, counts = tif.tag_v2[273], tif.tag_v2[279]  # type: ignore[attr-defined]
    if len(offsets) != 1:
        raise RuntimeError("expected a single TIFF strip")
    return buf.getvalue()[offsets[0] : offsets[0] + counts[0]]


def render_scan(c: Chapter) -> bytes:
    """Page images only, no text layer: what a scanner without OCR produces."""

    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for page in _page_images(c, SCAN_PAGES):
        width, height = page.size  # type: ignore[attr-defined]
        data = _g4(page)
        objs.append(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
            b"/BitsPerComponent 1 /Filter /CCITTFaxDecode /DecodeParms << /K -1 /BlackIs1 true "
            b"/Columns %d /Rows %d >> /Length %d >>\nstream\n"
            % (width, height, width, height, len(data))
            + data
            + b"\nendstream"
        )
        image = len(objs)
        stream = b"q 612 0 0 792 0 0 cm /Im0 Do Q"
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content = len(objs)
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /XObject << /Im0 %d 0 R >> >> >>" % (content, image)
        )
        kids.append(len(objs))
    return _assemble_pdf(objs, kids, c)


def render_png(c: Chapter) -> bytes:
    buf = io.BytesIO()
    _page_images(c, 1)[0].save(buf, "PNG", dpi=(200, 200))  # type: ignore[attr-defined]
    return buf.getvalue()


def _message(
    c: Chapter, subject: str, body: str, n: int, reply_to: str | None = None
) -> EmailMessage:
    m = EmailMessage(policy=SMTP.clone(linesep="\n"))
    m["From"] = SENDER
    m["To"] = "Readers <readers@example.org>"
    m["Subject"] = subject
    m["Date"] = format_datetime(FIXED_DATE + timedelta(minutes=n))
    m["Message-ID"] = f"<{c.name}.{n}@demo.example.org>"
    if reply_to:
        m["In-Reply-To"] = reply_to
        m["References"] = reply_to
    m.set_content(body, cte="quoted-printable")
    return m


def render_eml(c: Chapter) -> bytes:
    return _message(c, c.title, c.text + "\n", 0).as_bytes()


def render_mbox(c: Chapter) -> bytes:
    """The chapter as a thread of up to five messages in one mbox (mboxrd quoting)."""

    size = max(1, -(-len(c.paragraphs) // 5))
    parts = [c.paragraphs[i : i + size] for i in range(0, len(c.paragraphs), size)]
    out = bytearray()
    first_id = f"<{c.name}.0@demo.example.org>"
    for n, part in enumerate(parts):
        subject = c.title if n == 0 else f"Re: {c.title}"
        m = _message(c, subject, "\n\n".join(part) + "\n", n, None if n == 0 else first_id)
        out += b"From demo@example.org Thu Oct 15 00:%02d:00 2026\n" % n
        for line in m.as_bytes().split(b"\n"):
            out += (b">" + line if re.match(rb"^>*From ", line) else line) + b"\n"
    return bytes(out)


def render_csv(c: Chapter) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["work", "chapter", "paragraph", "text"])
    for i, p in enumerate(c.paragraphs, start=1):
        w.writerow([c.source_title, c.title, i, p.replace("\n", " ")])
    return buf.getvalue().encode()


def _ttl(s: str) -> str:
    return '"""' + s.replace("\\", "\\\\").replace('"', '\\"') + '"""@en'


def render_ttl(c: Chapter) -> bytes:
    iri = f"<urn:semascope-demo:{c.name}>"
    lines = [
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix schema: <https://schema.org/> .",
        "",
        f"{iri} a schema:Chapter ;",
        f"    dcterms:title {_ttl(c.title)} ;",
        f"    dcterms:creator {_ttl(c.creator)} ;",
        f"    dcterms:isPartOf <urn:semascope-demo:{c.slug}> .",
        "",
    ]
    for i, p in enumerate(c.paragraphs, start=1):
        lines += [
            f"<urn:semascope-demo:{c.name}#p{i}> a schema:CreativeWork ;",
            f"    schema:isPartOf {iri} ;",
            f"    schema:position {i} ;",
            f"    schema:text {_ttl(p)} .",
            "",
        ]
    return "\n".join(lines).encode()


def render_zip(c: Chapter) -> bytes:
    size = max(1, -(-len(c.paragraphs) // 3))
    parts = [c.paragraphs[i : i + size] for i in range(0, len(c.paragraphs), size)]
    members = [("README.txt", f"{c.title}\n{c.creator}\n".encode())]
    members += [
        (f"part-{n}.txt", ("\n\n".join(part) + "\n").encode()) for n, part in enumerate(parts, 1)
    ]
    return _zip(members)


RENDERERS: dict[str, Callable[[Chapter], bytes]] = {
    "html": render_html,
    "docx": render_docx,
    "odt": render_odt,
    "epub": render_epub,
    "pdf": render_pdf,
    "scan": render_scan,
    "png": render_png,
    "eml": render_eml,
    "mbox": render_mbox,
    "csv": render_csv,
    "ttl": render_ttl,
    "zip": render_zip,
}


# --- manifest -----------------------------------------------------------------------------------


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_inputs(texts: list[dict[str, object]]) -> list[str]:
    """Every corpus text must exist and match the hash `build.py` recorded in sources.yaml."""

    problems = []
    for t in texts:
        path = CORPUS / f"{t['slug']}.txt"
        if not path.exists():
            problems.append(f"{path.name}: missing (run build.py)")
        elif not t.get("sha256"):
            problems.append(f"{path.name}: no sha256 in sources.yaml")
        elif sha256(path.read_bytes()) != t["sha256"]:
            problems.append(f"{path.name}: sha256 differs from sources.yaml")
    return problems


def read_manifest(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text("utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                digest, name = line.split(maxsplit=1)
                out[name.lstrip("*")] = digest
    return out


def write_manifest(path: Path, files: dict[str, str]) -> None:
    path.write_text("".join(f"{d}  {n}\n" for n, d in sorted(files.items())), encoding="utf-8")


def diff_manifests(want: dict[str, str], got: dict[str, str]) -> list[str]:
    out = [f"missing: {n}" for n in sorted(want.keys() - got.keys())]
    out += [f"unexpected: {n}" for n in sorted(got.keys() - want.keys())]
    out += [f"changed: {n}" for n in sorted(want.keys() & got.keys()) if want[n] != got[n]]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--update-manifest", action="store_true", help=f"rewrite {MANIFEST.name}")
    ap.add_argument(
        "--check-only", action="store_true", help="verify existing output against the manifest"
    )
    args = ap.parse_args(argv)

    sources = yaml.safe_load((HERE / "sources.yaml").read_text(encoding="utf-8"))
    texts = sources["texts"]
    problems = check_inputs(texts)
    if problems:
        print("input texts do not match sources.yaml:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    out_dir = DERIVED / NAME
    if args.check_only:
        got = {
            p.relative_to(out_dir).as_posix(): sha256(p.read_bytes())
            for p in sorted(out_dir.rglob("*"))
            if p.is_file()
        }
        diffs = diff_manifests(read_manifest(MANIFEST), got)
        print("\n".join(diffs) if diffs else f"{NAME}: {len(got)} files match {MANIFEST.name}")
        return 1 if diffs else 0

    started = time.monotonic()
    chs = chapters(texts)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    files: dict[str, str] = {}
    counts: dict[str, int] = {}
    index_rows = []
    for i, c in enumerate(chs):
        fmt = CYCLE[i % len(CYCLE)]
        rel = f"{fmt}/{c.name}.{EXT.get(fmt, fmt)}"
        data = RENDERERS[fmt](c)
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[rel] = sha256(data)
        counts[fmt] = counts.get(fmt, 0) + 1
        index_rows.append([rel, c.slug, c.number, c.title, len(c.text), len(data)])
    with (DERIVED / f"{NAME}.index.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["file", "slug", "chapter", "title", "text_chars", "bytes"])
        w.writerows(index_rows)
    total = sum((out_dir / n).stat().st_size for n in files)
    print(
        f"{NAME}: {len(files)} files, {total / 1e6:.1f} MB in {time.monotonic() - started:.0f} s; "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    )
    if args.update_manifest or not MANIFEST.exists():
        write_manifest(MANIFEST, files)
        print(f"wrote {MANIFEST.name} (sha256 {sha256(MANIFEST.read_bytes())})")
        return 0
    diffs = diff_manifests(read_manifest(MANIFEST), files)
    if diffs:
        print(
            f"output differs from {MANIFEST.name} ({len(diffs)} files):\n  "
            + "\n  ".join(diffs[:20])
            + "\nrerun with --update-manifest if the change is intended",
            file=sys.stderr,
        )
        return 1
    print(f"matches {MANIFEST.name} (sha256 {sha256(MANIFEST.read_bytes())})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
