"""tag: dictionary tagging against the Solr `entities` collection (Tagger handler).

Every match becomes a Tag with the concept or individual IRI, the pack
version it came from and character spans. Facet fields are derived per
scheme: `<scheme>_paths` carries the expanded ancestor paths of each matched
concept (every path in a polyhierarchy), so the UI can show a hierarchical
facet without any schema change. `fields["paths_by_tag"]` keeps each tag's own
paths, from which the index stage builds the facet of the curated view.

When two active packs define the same IRI, the tag is attributed to the pack
with the higher binding precedence (see `oss_pipeline.dictionary`), and a
tag's confidence is that of the best label type it matched through anywhere
in the text, not only at its first occurrence. The tag records that entry
(`matched_label`, `label_type`, `lang`), each span the label it matched, and
the page it is on. Curators' draft labels are used only in the collection
that proposed them.

Long texts are sent in windows of `MAX_TEXT` characters; each window starts at
a space at least `OVERLAP` characters before the end of the one before and tags
that stretch again, so a label across a window edge is found whole, once. A
request that returns `tag_limit` matches (the tagger's cap, 5,000 by default)
is continued from the last match instead of losing the rest of its window;
`fields["tag_requests"]` and `fields["tag_limit_reached"]` say how often that
happened. The tagger reports offsets in UTF-16 code units, which
are converted to the character offsets the rest of the record uses, so spans
after an emoji or another character outside the Basic Multilingual Plane stay
on their words.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import httpx

from oss_core.contracts import Span, StageContext, Tag, WorkingDocument
from oss_pipeline.dictionary import dictionary_fq, entry_rank, label_confidence, pack_rank
from oss_pipeline.provenance import assign_pages
from oss_pipeline.stages.base import solr_key

MAX_TEXT = 2_000_000  # characters sent per request; longer texts are tagged in windows
OVERLAP = 1_000  # characters a window repeats from the one before: longer than any label
TAG_LIMIT = 5_000  # matches per request (`tag_limit`); a request that reaches it is continued
MAX_REQUESTS = 1_000  # per document; past it the rest of the text is left untagged, and said so
ASTRAL = re.compile("[\U00010000-\U0010ffff]")

log = logging.getLogger(__name__)


def utf16_to_index(text: str) -> Callable[[int], int]:
    """Map the tagger's offsets (UTF-16 code units, as Java counts) to indexes into `text`.

    Every character outside the Basic Multilingual Plane is two UTF-16 units and one
    Python character, so an offset after one of them is one too far without this.
    """

    starts = [m.start() + k for k, m in enumerate(ASTRAL.finditer(text))]
    if not starts:
        return lambda offset: offset
    return lambda offset: offset - bisect_left(starts, offset)


def _explain(tag: Tag, entry: dict[str, Any], label: str) -> None:
    """Record the dictionary entry behind the tag's confidence: label, label type, language."""

    tag.matched_label = label
    tag.label_type = str(entry.get("label_type") or "hidden")
    tag.lang = str(entry["lang"]) if entry.get("lang") else None


class TagStage:
    name = "tag"
    version = "3"
    requires = ("text",)
    provides = ("tags",)

    def __init__(self, http: httpx.Client | None = None) -> None:
        self._http = http
        self._http_key: tuple[str, ...] | None = None  # None: a client handed in

    def _client(self, ctx: StageContext) -> httpx.Client:
        key = solr_key(ctx)
        if self._http is None or (self._http_key is not None and self._http_key != key):
            self.close()
            auth = None
            if ctx.settings.get("solr_user") and ctx.settings.get("solr_password"):
                auth = (str(ctx.settings["solr_user"]), str(ctx.settings["solr_password"]))
            self._http = httpx.Client(timeout=120.0, auth=auth)
            self._http_key = key
        return self._http

    def close(self) -> None:
        if self._http is not None and self._http_key is not None:
            self._http.close()  # only a client this stage built
            self._http = None

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        for key in ("tag_skipped", "tag_truncated_at"):  # an earlier run's notes
            doc.fields.pop(key, None)
        text = doc.text or ""
        if not text.strip():
            return doc
        base = str(ctx.settings["solr_url"]).rstrip("/")
        collection = str(ctx.settings.get("solr_entities", "entities"))
        packs: dict[str, str] = dict(ctx.pack_versions)
        if not packs:
            doc.fields["tag_skipped"] = "no active packs"
            return doc
        fq = dictionary_fq(packs, ctx.collection)
        ranks = pack_rank(packs)
        client = self._client(ctx)
        found: dict[str, Tag] = {}
        owner: dict[str, dict[str, Any]] = {}  # the entry each tag is attributed to
        limit = max(1, int(ctx.settings.get("tag_limit", TAG_LIMIT)))
        hits: list[tuple[int, int, list[str]]] = []
        entries: dict[str, dict[str, Any]] = {}
        cursor, requests, limited = 0, 0, 0
        while cursor < len(text):
            if requests >= MAX_REQUESTS:
                doc.fields["tag_truncated_at"] = cursor
                log.warning("document %s: tagging stopped at character %d", doc.id, cursor)
                break
            start = cursor  # always a token boundary: the text's start, a space or a match
            end = min(start + MAX_TEXT, len(text))
            window = text[start:end]
            resp = client.post(
                f"{base}/{collection}/tag",
                params={"wt": "json", "fq": fq, "matchText": "false", "tagsLimit": limit},
                content=window.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
            resp.raise_for_status()
            requests += 1
            body: dict[str, Any] = resp.json()
            entries.update((d["id"], d) for d in body.get("response", {}).get("docs", []))
            index = utf16_to_index(window)
            found_here = [
                (index(int(h["startOffset"])) + start, index(int(h["endOffset"])) + start, h)
                for h in body.get("tags", [])
            ]
            # the matches this request answers for, and where the next one starts:
            # - at the end of the text, everything;
            # - before a window edge, those starting before the last space at least OVERLAP
            #   characters before the edge (a label is never longer, so they are whole); the
            #   next window starts after that space and tags the rest again;
            # - when the tagger stopped at its limit, those before its last match, where the
            #   next request carries on (after that match when it is the only way forward)
            if end >= len(text):
                until = len(text)
            else:
                edge = end - OVERLAP
                space = max(text.rfind(" ", cursor + 1, edge), text.rfind("\n", cursor + 1, edge))
                until = space + 1 if space > cursor else edge
            if len(found_here) >= limit:
                limited += 1
                last = max(hit[0] for hit in found_here)
                if last > cursor:
                    until = min(until, last)
                else:
                    until = max(hit[1] for hit in found_here)
            hits.extend(
                (s_, e_, list(h.get("ids", []))) for s_, e_, h in found_here if cursor <= s_ < until
            )
            cursor = until
        for start, end, ids in hits:
            matched = text[start:end]
            # one candidate per IRI for this occurrence: the best-ranked entry
            best: dict[str, dict[str, Any]] = {}
            for entry_id in ids:
                entry = entries.get(entry_id)
                if entry is None:
                    continue
                if entry.get("case_sensitive") and matched != entry.get("label"):
                    continue
                iri = str(entry["iri"])
                if iri not in best or entry_rank(entry, ranks) < entry_rank(best[iri], ranks):
                    best[iri] = entry
            for iri, entry in best.items():
                confidence = label_confidence(entry.get("label_type"))
                label = str(entry.get("label") or matched)
                tag = found.get(iri)
                if tag is None:
                    tag = Tag(
                        iri=iri,
                        label=str(entry.get("pref_label") or entry.get("label") or matched),
                        kind="concept" if entry.get("kind") == "concept" else "entity",
                        pack_id=str(entry.get("pack_id")),
                        pack_version=str(entry.get("pack_version")),
                        stage=self.name,
                        stage_version=self.version,
                        confidence=confidence,
                    )
                    _explain(tag, entry, label)
                    found[iri] = tag
                    owner[iri] = entry
                else:
                    if confidence > tag.confidence:
                        # the confidence is that of the best label type seen: say which
                        tag.confidence = confidence
                        _explain(tag, entry, label)
                    if entry_rank(entry, ranks)[:2] < entry_rank(owner[iri], ranks)[:2]:
                        # a higher-precedence pack defines the same IRI
                        tag.pack_id = str(entry.get("pack_id"))
                        tag.pack_version = str(entry.get("pack_version"))
                        tag.label = str(entry.get("pref_label") or tag.label)
                        owner[iri] = entry
                tag.spans.append(Span(start=start, end=end, matched=label))
        paths: dict[str, set[str]] = defaultdict(set)
        paths_by_tag: dict[str, dict[str, list[str]]] = {}
        for iri, entry in owner.items():
            scheme = str(entry.get("scheme") or "")
            # every path of a concept in a polyhierarchy; `path` alone in older dictionaries
            values = entry.get("paths") or ([entry["path"]] if entry.get("path") else [])
            if isinstance(values, str):
                values = [values]
            if scheme and values:
                paths[scheme].update(str(v) for v in values)
                paths_by_tag[iri] = {scheme: sorted(str(v) for v in values)}
        assign_pages(found.values(), doc)
        doc.tags.extend(found.values())
        # the schemes an earlier run tagged lose their paths unless this run tags them again
        for scheme in doc.fields.get("tagged_schemes") or []:
            doc.fields.pop(f"x_{scheme}_paths", None)
        for scheme, values in paths.items():
            doc.fields[f"x_{scheme}_paths"] = sorted(values)
        # the paths of each tag, so the index stage can build the facet from the curated view
        doc.fields["paths_by_tag"] = paths_by_tag
        doc.fields["tag_count"] = len(found)
        doc.fields["tag_requests"] = requests
        doc.fields["tag_limit_reached"] = limited
        doc.fields["tagged_schemes"] = sorted(paths)
        return doc
