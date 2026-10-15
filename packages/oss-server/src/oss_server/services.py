"""Clients the server holds for the lifetime of the process."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from oss_core.config import get_settings
from oss_pipeline.clients.solr import SolrClient

log = logging.getLogger(__name__)

CONFIGSETS = Path(__file__).resolve().parents[4] / "deploy" / "solr" / "configsets"


def solr_documents_collection() -> str:
    return "documents"


def solr_entities_collection() -> str:
    return "entities"


@lru_cache(maxsize=1)
def solr_client() -> SolrClient:
    s = get_settings()
    return SolrClient(
        s.solr_url,
        user=s.solr_user,
        password=s.solr_password.get_secret_value() if s.solr_password else None,
    )


def configset_dir(name: str) -> Path:
    for candidate in (CONFIGSETS, Path("/app/deploy/solr/configsets")):
        if (candidate / name / "conf").is_dir():
            return candidate / name / "conf"
    raise FileNotFoundError(f"configset {name} not found under {CONFIGSETS}")


def bootstrap_solr() -> list[str]:
    """Upload every configset, reload existing collections, create missing ones."""

    created: list[str] = []
    client = solr_client()
    for name in ("documents", "entities"):
        if client.ensure_collection(name, f"semascope-{name}", configset_dir(name)):
            created.append(name)
            log.info("solr: created collection %s", name)
        else:
            log.info("solr: configset semascope-%s uploaded and collection reloaded", name)
    return created


def sync_dictionary(pack_id: str, version: str) -> int:
    """Replace the Solr dictionary entries of one pack version. Returns the entry count."""

    from oss_server.knowledge.importer import dictionary_entries
    from oss_server.knowledge.models import Pack

    pack = Pack.objects.get(pack_id=pack_id, version=version)
    client = solr_client()
    collection = solr_entities_collection()
    entries = dictionary_entries(pack)
    client.delete_by_query(collection, f'pack_id:"{pack_id}" AND pack_version:"{version}"')
    for i in range(0, len(entries), 500):
        client.add(collection, entries[i : i + 500], commit_within_ms=1000)
    client.commit(collection)
    log.info("solr: dictionary for %s@%s has %d entries", pack_id, version, len(entries))
    return len(entries)


def import_bundled_packs() -> list[str]:
    """Import the packs shipped with the product (packs/base, packs/demo/pack) when present."""

    from oss_server.knowledge.importer import import_pack
    from oss_server.knowledge.models import Pack

    imported: list[str] = []
    for candidate in (CONFIGSETS.parents[1] / "packs" / "base", Path("/app/packs/base")):
        if (candidate / "requirements.yaml").exists():
            before = Pack.objects.filter(status=Pack.Status.ACTIVE).count()
            pack = import_pack(candidate)
            if Pack.objects.filter(
                status=Pack.Status.ACTIVE
            ).count() != before or not _dictionary_present(pack.pack_id, pack.version):
                sync_dictionary(pack.pack_id, pack.version)
            imported.append(str(pack))
            break
    return imported


def _dictionary_present(pack_id: str, version: str) -> bool:
    try:
        return (
            solr_client().count(
                solr_entities_collection(), f'pack_id:"{pack_id}" AND pack_version:"{version}"'
            )
            > 0
        )
    except Exception:  # noqa: BLE001
        return False
