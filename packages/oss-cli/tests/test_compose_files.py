"""The compose files: health checks, network split, observability wiring and Docker secrets.

Read as YAML (anchors and merge keys resolved), so this needs no Docker; CI's compose job
runs `docker compose config` on every overlay as well.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from oss_cli.main import FILE_SECRETS

REPO = Path(__file__).resolve().parents[3]


def compose(name: str) -> dict[str, Any]:
    data = yaml.safe_load((REPO / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def networks_of(service: dict[str, Any]) -> set[str]:
    nets = service.get("networks") or ["default"]
    return set(nets) if isinstance(nets, list) else set(nets)


def test_every_long_running_service_has_a_health_check() -> None:
    services = compose("compose.yaml")["services"]
    missing = [name for name, s in services.items() if name != "cli" and "healthcheck" not in s]
    assert missing == []
    # migrations on a first start must not count against the server's retries
    assert services["server"]["healthcheck"]["start_period"] == "10m"
    worker = services["worker"]["healthcheck"]["test"]
    assert worker[-2:] == ["health", "worker"]
    assert services["beat"]["healthcheck"]["test"][-2:] == ["health", "beat"]


def test_tika_and_caddy_sit_on_their_own_networks() -> None:
    data = compose("compose.yaml")
    services = data["services"]
    assert data["networks"]["extract"] == {"internal": True}
    assert networks_of(services["tika"]) == {"extract"}
    assert networks_of(services["caddy"]) == {"edge"}
    on_extract = {n for n, s in services.items() if "extract" in networks_of(s)}
    assert on_extract == {"tika", "worker", "cli"}
    on_edge = {n for n, s in services.items() if "edge" in networks_of(s)}
    assert on_edge == {"caddy", "server"}
    published = {n for n, s in services.items() if s.get("ports")}
    assert published == {"caddy"}


def test_solr_runs_in_solrcloud_mode_explicitly() -> None:
    solr = compose("compose.yaml")["services"]["solr"]
    assert solr["command"] == ["solr-foreground"]  # never --user-managed


def test_observe_exports_beat_and_keeps_traces() -> None:
    data = compose("compose.observe.yaml")
    services = data["services"]
    for name in ("server", "worker", "beat"):
        env = services[name]["environment"]
        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4318", name
        assert env["OTEL_SERVICE_NAME"] == f"semascope-{name}"
    collector = yaml.safe_load((REPO / "deploy/observe/otel-collector.yaml").read_text())
    assert collector["service"]["pipelines"]["traces"]["exporters"] == ["otlp/jaeger"]
    assert collector["exporters"]["otlp/jaeger"]["endpoint"] == "jaeger:4317"
    sources = yaml.safe_load((REPO / "deploy/observe/grafana-datasources.yaml").read_text())
    assert {d["type"] for d in sources["datasources"]} == {"prometheus", "jaeger"}
    assert services["grafana"]["ports"] == ["127.0.0.1:3000:3000"]
    assert services["flower"]["ports"] == ["127.0.0.1:5555:5555"]
    assert services["flower"]["environment"]["FLOWER_BASIC_AUTH"].startswith("admin:${")


def test_prometheus_scrapes_solr_with_its_password() -> None:
    data = compose("compose.observe.yaml")
    prometheus = yaml.safe_load((REPO / "deploy/observe/prometheus.yaml").read_text())
    (solr,) = [j for j in prometheus["scrape_configs"] if j["job_name"] == "solr"]
    target = solr["basic_auth"]["password_file"]
    mounted = {c["target"]: c["source"] for c in data["services"]["prometheus"]["configs"]}
    assert data["configs"][mounted[target]]["content"] == "${SOLR_PASSWORD:-}"


def test_the_dashboard_is_provisioned() -> None:
    import json

    provider = yaml.safe_load((REPO / "deploy/observe/grafana-dashboards.yaml").read_text())
    path = provider["providers"][0]["options"]["path"]
    mounts = compose("compose.observe.yaml")["services"]["grafana"]["volumes"]
    assert f"./deploy/observe/dashboards:{path}:ro" in mounts
    (dashboard,) = (REPO / "deploy/observe/dashboards").glob("*.json")
    panels = json.loads(dashboard.read_text())["panels"]
    assert panels and all(p["targets"] for p in panels if p["type"] != "row")


def test_secrets_overlay_gives_every_app_service_every_secret_file() -> None:
    data = compose("compose.secrets.yaml")
    declared = set(data["secrets"])
    assert declared == {k.lower() for k in FILE_SECRETS}
    for name in ("server", "worker", "beat", "cli"):
        service = data["services"][name]
        assert set(service["secrets"]) == declared, name
        env = service["environment"]
        for key in FILE_SECRETS:
            assert env[f"{key}_FILE"] == f"/run/secrets/{key.lower()}", (name, key)
    stores = data["services"]
    assert stores["postgres"]["environment"]["POSTGRES_PASSWORD_FILE"]
    assert stores["valkey"]["environment"]["VALKEY_PASSWORD_FILE"]
    assert stores["solr"]["environment"]["SEMASCOPE_SOLR_PASSWORD_FILE"]
    assert "/run/secrets/neo4j_password" in " ".join(stores["neo4j"]["entrypoint"])
