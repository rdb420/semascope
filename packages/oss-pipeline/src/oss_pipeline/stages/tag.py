"""tag: dictionary tagging against the Solr `entities` collection (Tagger handler).

Every match becomes a Tag with the concept or individual IRI, the pack
version it came from and character spans. Facet fields are derived per
scheme: `<scheme>_paths` carries the expanded ancestor path of each matched
concept, so the UI can show a hierarchical facet without any schema change.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import httpx

from oss_core.contracts import Span, StageContext, Tag, WorkingDocument

MAX_TEXT = 2_000_000  # characters sent per request; longer texts are tagged in windows


class TagStage:
    name = "tag"
    version = "1"
    queue = "cpu"
    requires = ("text",)
    provides = ("tags",)

    def __init__(self, http: httpx.Client | None = None) -> None:
        self._http = http

    def _client(self, ctx: StageContext) -> httpx.Client:
        if self._http is None:
            auth = None
            if ctx.settings.get("solr_user") and ctx.settings.get("solr_password"):
                auth = (str(ctx.settings["solr_user"]), str(ctx.settings["solr_password"]))
            self._http = httpx.Client(timeout=120.0, auth=auth)
        return self._http

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        text = doc.text or ""
        if not text.strip():
            return doc
        base = str(ctx.settings["solr_url"]).rstrip("/")
        collection = str(ctx.settings.get("solr_entities", "entities"))
        packs: dict[str, str] = dict(ctx.pack_versions)
        if not packs:
            doc.fields["tag_skipped"] = "no active packs"
            return doc
        fq = " OR ".join(
            f'(pack_id:"{pid}" AND pack_version:"{ver}")' for pid, ver in packs.items()
        )
        client = self._client(ctx)
        found: dict[str, Tag] = {}
        paths: dict[str, set[str]] = defaultdict(set)
        labels: dict[str, set[str]] = defaultdict(set)
        for offset in range(0, len(text), MAX_TEXT):
            window = text[offset : offset + MAX_TEXT]
            resp = client.post(
                f"{base}/{collection}/tag",
                params={"wt": "json", "fq": fq, "matchText": "false"},
                content=window.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            entries = {d["id"]: d for d in body.get("response", {}).get("docs", [])}
            for hit in body.get("tags", []):
                start, end = int(hit["startOffset"]) + offset, int(hit["endOffset"]) + offset
                matched = text[start:end]
                for entry_id in hit.get("ids", []):
                    entry = entries.get(entry_id)
                    if entry is None:
                        continue
                    if entry.get("case_sensitive") and matched != entry.get("label"):
                        continue
                    iri = str(entry["iri"])
                    tag = found.get(iri)
                    if tag is None:
                        tag = Tag(
                            iri=iri,
                            label=str(entry.get("pref_label") or entry.get("label") or matched),
                            kind="concept" if entry.get("kind") == "concept" else "entity",
                            pack_id=str(entry.get("pack_id")),
                            pack_version=str(entry.get("pack_version")),
                            stage=self.name,
                            confidence=1.0 if entry.get("label_type") == "pref" else 0.9,
                        )
                        found[iri] = tag
                    tag.spans.append(Span(start=start, end=end))
                    scheme = str(entry.get("scheme") or "")
                    if scheme and entry.get("path"):
                        paths[scheme].add(str(entry["path"]))
                        labels[scheme].add(str(entry.get("pref_label") or matched))
        doc.tags.extend(found.values())
        for scheme, values in paths.items():
            doc.fields[f"x_{scheme}_paths"] = sorted(values)
        doc.fields["tag_count"] = len(found)
        doc.fields["tagged_schemes"] = sorted(paths)
        return doc
