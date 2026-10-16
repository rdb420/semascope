"""The pack dictionary (Solr `entities`): which entries a collection sees, and which one wins.

`ctx.pack_versions` lists the collection's active packs in precedence order
(the server builds it from `PackBinding.precedence`). When two packs define
the same IRI or the same label, the entry of the earlier pack wins; within a
pack, the better label type (pref, then alt, then a curator's draft, then
hidden) wins. Draft labels carry the collection that proposed them in
`collection_scope` and are seen by that collection only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

LABEL_CONFIDENCE = {"pref": 1.0, "alt": 0.9, "draft": 0.85}
HIDDEN_CONFIDENCE = 0.75  # hidden labels and anything unknown


def label_confidence(label_type: Any) -> float:
    """The confidence a match through a label of this type gets."""

    return LABEL_CONFIDENCE.get(str(label_type), HIDDEN_CONFIDENCE)


def pack_rank(pack_versions: Mapping[str, str]) -> dict[str, int]:
    """Pack id to its precedence position (0 is the highest)."""

    return {pid: i for i, pid in enumerate(pack_versions)}


def entry_rank(entry: Mapping[str, Any], ranks: Mapping[str, int]) -> tuple[int, str, float, str]:
    """Sort key: earlier pack first, then the better label type, then the IRI (a total order)."""

    pid = str(entry.get("pack_id") or "")
    return (
        ranks.get(pid, len(ranks)),
        pid,
        -label_confidence(entry.get("label_type")),
        str(entry.get("iri") or ""),
    )


def dictionary_fq(pack_versions: Mapping[str, str], collection: str) -> str:
    """Filter for the entries a collection tags with: its pack versions, minus other
    collections' draft labels."""

    packs = " OR ".join(
        f'(pack_id:"{pid}" AND pack_version:"{ver}")' for pid, ver in pack_versions.items()
    )
    slug = collection.replace("\\", "\\\\").replace('"', '\\"')
    scope = f'((*:* -collection_scope:[* TO *]) OR collection_scope:"{slug}")'
    return f"({packs}) AND {scope}"
