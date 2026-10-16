"""link: attach NER entities to pack individuals by label.

An NER tag whose normalised text equals a taggable individual's label (pref,
alt or hidden) in an active pack is re-pointed at that individual's IRI with
the pack version recorded, so the graph and the facets use the curated
identity instead of a free-text name. The tag keeps the NER entity it was
(`linked_from`: IRI, surface label, score and model) and the individual's
label it matched (`matched_label`, `label_type`); its `stage` stays `ner`.

The dictionary comes from the Solr `entities` collection, fetched once per
process per pack set and collection. When two packs give an individual the
same label, the pack with the higher binding precedence wins
(`oss_pipeline.dictionary`), whatever order Solr returns the entries in.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx

from oss_core.contracts import LinkedFrom, StageContext, Tag, WorkingDocument
from oss_pipeline.dictionary import dictionary_fq, entry_rank, pack_rank


def normalise(value: str) -> str:
    return " ".join(value.lower().split())


@lru_cache(maxsize=8)
def _dictionary(
    base: str,
    collection: str,
    pack_key: tuple[tuple[str, str], ...],
    auth: tuple[str, str] | None,
    scope: str = "",
) -> dict[str, dict[str, Any]]:
    """Normalised label to the winning entry. `pack_key` is in precedence order."""

    packs = dict(pack_key)
    fq = dictionary_fq(packs, scope)
    ranks = pack_rank(packs)
    out: dict[str, dict[str, Any]] = {}
    with httpx.Client(timeout=60.0, auth=auth) as client:
        start = 0
        while True:
            resp = client.get(
                f"{base}/{collection}/select",
                params={
                    "q": "kind:individual",
                    "fq": fq,
                    "rows": 1000,
                    "start": start,
                    "sort": "id asc",
                    "wt": "json",
                    "fl": "iri,label,label_type,pref_label,pack_id,pack_version,type_iri",
                },
            )
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
            for d in docs:
                key = normalise(str(d["label"]))
                if key not in out or entry_rank(d, ranks) < entry_rank(out[key], ranks):
                    out[key] = d
            if len(docs) < 1000:
                break
            start += 1000
    return out


class LinkStage:
    name = "link"
    version = "2"
    queue = "cpu"
    requires = ("tags",)
    provides = ("tags",)

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        if not ctx.pack_versions:
            return doc
        base = str(ctx.settings["solr_url"]).rstrip("/")
        collection = str(ctx.settings.get("solr_entities", "entities"))
        auth = None
        if ctx.settings.get("solr_user") and ctx.settings.get("solr_password"):
            auth = (str(ctx.settings["solr_user"]), str(ctx.settings["solr_password"]))
        dictionary = _dictionary(
            base, collection, tuple(ctx.pack_versions.items()), auth, ctx.collection
        )
        if not dictionary:
            return doc
        linked = 0
        by_iri: dict[str, Tag] = {t.iri: t for t in doc.tags}
        for tag in list(doc.tags):
            if tag.stage != "ner":
                continue
            entry = dictionary.get(normalise(tag.label))
            if entry is None:
                continue
            target = str(entry["iri"])
            origin = LinkedFrom(
                iri=tag.iri,
                label=tag.label,
                confidence=tag.confidence,
                model=tag.model,
                link_version=self.version,
            )
            existing = by_iri.get(target)
            if existing is not None and existing is not tag:
                # the individual was also found another way (usually the dictionary): its
                # tag keeps its own provenance and gains the NER occurrences
                existing.spans.extend(tag.spans)
                existing.linked_from = existing.linked_from or origin
                doc.tags.remove(tag)
            else:
                tag.iri = target
                tag.label = str(entry.get("pref_label") or tag.label)
                tag.pack_id = str(entry.get("pack_id"))
                tag.pack_version = str(entry.get("pack_version"))
                tag.confidence = max(tag.confidence, 0.85)
                tag.matched_label = str(entry.get("label") or tag.label)
                tag.label_type = str(entry.get("label_type") or "hidden")
                tag.linked_from = origin
                by_iri[target] = tag
            linked += 1
        doc.fields["linked_count"] = linked
        return doc
