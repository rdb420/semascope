"""Pack precedence: which active pack wins when two define the same IRI or label.

A collection with pack bindings uses its own order (`PackBinding.precedence`,
lowest first, then pack id). Everywhere else (a collection without bindings,
or a lookup outside any collection) the active packs are ordered by the
lowest precedence any collection binds them with (100 when unbound), then
by pack id. The order is total, so the answer never depends on database or
Solr result order.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.db.models import Min

if TYPE_CHECKING:
    from oss_server.catalogue.models import Collection
    from oss_server.knowledge.models import Concept

DEFAULT_PRECEDENCE = 100


def pack_order(collection: Collection | None = None) -> list[str]:
    """Active pack ids in precedence order for a collection (or globally)."""

    from oss_server.knowledge.models import Pack, PackBinding

    active = set(Pack.objects.filter(status=Pack.Status.ACTIVE).values_list("pack_id", flat=True))
    if collection is not None:
        bound = [
            pid
            for pid in PackBinding.objects.filter(collection=collection, enabled=True)
            .order_by("precedence", "pack_id")
            .values_list("pack_id", flat=True)
        ]
        if bound:
            return [pid for pid in bound if pid in active]
    lowest = dict(
        PackBinding.objects.filter(enabled=True)
        .values("pack_id")
        .annotate(p=Min("precedence"))
        .values_list("pack_id", "p")
    )
    return sorted(active, key=lambda pid: (lowest.get(pid, DEFAULT_PRECEDENCE), pid))


def preferred(concepts: Iterable[Concept], order: list[str]) -> Concept | None:
    """The concept from the highest-precedence pack (packs outside `order` come last)."""

    rank = {pid: i for i, pid in enumerate(order)}
    rows = list(concepts)
    if not rows:
        return None
    return min(rows, key=lambda c: (rank.get(c.pack.pack_id, len(rank)), c.pack.pack_id))
