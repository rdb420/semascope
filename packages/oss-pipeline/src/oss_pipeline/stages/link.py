"""link: attach NER entities to pack individuals by label.

An NER tag whose normalised text equals a taggable individual's label (pref,
alt or hidden) in an active pack is re-pointed at that individual's IRI with
the pack version recorded, so the graph and the facets use the curated
identity instead of a free-text name. The tag keeps the NER entity it was
(`linked_from`: IRI, surface label, score and model) and the individual's
label it matched (`matched_label`, `label_type`); its `stage` stays `ner`.

The dictionary comes from the Solr `entities` collection and is kept per
process for each pack set and collection, together with a fingerprint of the
entries it was built from (how many there are and the newest `_version_`).
Every run asks Solr for the fingerprint first, one cheap request, and fetches
the dictionary again when it changed: a re-sync of the same pack version, a
curator's draft label or its removal is used by the next document, without a
worker restart. When two packs give an individual the same label, the pack
with the higher binding precedence wins (`oss_pipeline.dictionary`), whatever
order Solr returns the entries in.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

import httpx

from oss_core.contracts import LinkedFrom, StageContext, Tag, WorkingDocument
from oss_pipeline.dictionary import dictionary_fq, entry_rank, pack_rank
from oss_pipeline.stages.base import solr_key

CACHE_SIZE = 8  # dictionaries kept per process (pack sets and collections)
Fingerprint = tuple[int, Any]
_cache: OrderedDict[tuple[Any, ...], tuple[Fingerprint, dict[str, dict[str, Any]]]] = OrderedDict()


def clear_cache() -> None:
    _cache.clear()


def normalise(value: str) -> str:
    return " ".join(value.lower().split())


def _fingerprint(client: httpx.Client, url: str, fq: str) -> Fingerprint:
    """How many individual entries the filter sees, and the newest `_version_` among them:
    any add, replacement or deletion changes one of the two."""

    resp = client.get(
        url,
        params={
            "q": "kind:individual",
            "fq": fq,
            "rows": 0,
            "wt": "json",
            "json.facet": json.dumps({"newest": "max(_version_)"}),
        },
    )
    resp.raise_for_status()
    body = resp.json()
    return (
        int(body.get("response", {}).get("numFound") or 0),
        (body.get("facets") or {}).get("newest"),
    )


def _fetch(
    client: httpx.Client, url: str, fq: str, ranks: dict[str, int]
) -> dict[str, dict[str, Any]]:
    """Normalised label to the winning entry."""

    out: dict[str, dict[str, Any]] = {}
    start = 0
    while True:
        resp = client.get(
            url,
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


def dictionary(
    client: httpx.Client, base: str, collection: str, pack_versions: dict[str, str], scope: str
) -> dict[str, dict[str, Any]]:
    """The collection's individual labels, from the process cache while Solr's fingerprint
    of them is unchanged. `pack_versions` is in precedence order."""

    url = f"{base}/{collection}/select"
    fq = dictionary_fq(pack_versions, scope)
    key = (url, tuple(pack_versions.items()), scope)
    current = _fingerprint(client, url, fq)
    cached = _cache.get(key)
    if cached is not None and cached[0] == current:
        _cache.move_to_end(key)
        return cached[1]
    found = _fetch(client, url, fq, pack_rank(pack_versions))
    _cache[key] = (current, found)
    _cache.move_to_end(key)
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return found


class LinkStage:
    name = "link"
    version = "2"
    requires = ("tags",)
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
            self._http = httpx.Client(timeout=60.0, auth=auth)
            self._http_key = key
        return self._http

    def close(self) -> None:
        if self._http is not None and self._http_key is not None:
            self._http.close()  # only a client this stage built
            self._http = None

    def run(self, doc: WorkingDocument, ctx: StageContext) -> WorkingDocument:
        if not ctx.pack_versions:
            return doc
        if not any(tag.stage == "ner" for tag in doc.tags):
            doc.fields["linked_count"] = 0
            return doc  # nothing to link: no need to ask Solr
        base = str(ctx.settings["solr_url"]).rstrip("/")
        collection = str(ctx.settings.get("solr_entities", "entities"))
        labels = dictionary(
            self._client(ctx), base, collection, dict(ctx.pack_versions), ctx.collection
        )
        if not labels:
            return doc
        linked = 0
        by_iri: dict[str, Tag] = {t.iri: t for t in doc.tags}
        for tag in list(doc.tags):
            if tag.stage != "ner":
                continue
            entry = labels.get(normalise(tag.label))
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
