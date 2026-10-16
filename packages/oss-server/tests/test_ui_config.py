import pytest
from django.contrib.auth.models import User
from django.test import Client

from oss_server.api.auth import issue_token


@pytest.fixture
def auth(db: None) -> dict[str, str]:
    user = User.objects.create_user("viewer", password="x" * 16)
    return {"Authorization": f"Bearer {issue_token(user, 'test')}"}


def test_map_tiles_default_to_openstreetmap(client: Client, auth: dict[str, str]) -> None:
    body = client.get("/api/ui/config", headers=auth).json()
    assert body["map_tile_url"] == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert "OpenStreetMap" in body["map_tile_attribution"]


def test_empty_tile_url_means_no_base_map(
    client: Client, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEMASCOPE_MAP_TILE_URL", "")
    assert client.get("/api/ui/config", headers=auth).json()["map_tile_url"] == ""


def test_ui_config_needs_sign_in(client: Client, db: None) -> None:
    assert client.get("/api/ui/config").status_code == 401
