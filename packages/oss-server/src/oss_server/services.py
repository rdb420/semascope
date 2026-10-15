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
    for name in ("documents",):
        if client.ensure_collection(name, f"semascope-{name}", configset_dir(name)):
            created.append(name)
            log.info("solr: created collection %s", name)
        else:
            log.info("solr: configset semascope-%s uploaded and collection reloaded", name)
    return created
