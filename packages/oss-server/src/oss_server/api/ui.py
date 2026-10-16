"""Settings the browser UI needs from the server's environment."""

from __future__ import annotations

from django.http import HttpRequest
from ninja import Router, Schema

from oss_core.config import get_settings

router = Router(tags=["ui"])


class UiConfigOut(Schema):
    map_tile_url: str  # XYZ raster template; empty = no base map
    map_tile_attribution: str


@router.get("/config", response=UiConfigOut)
def ui_config(request: HttpRequest) -> UiConfigOut:
    s = get_settings()
    return UiConfigOut(
        map_tile_url=s.semascope_map_tile_url.strip(),
        map_tile_attribution=s.semascope_map_tile_attribution,
    )
