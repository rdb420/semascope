"""Application settings.

Every setting comes from the environment (or a `.env` file), is validated once
at start-up, and is never generated as code. Runtime-editable settings live in
the database and are a separate concern of oss-server.

Any setting can instead be read from a file named by `<NAME>_FILE` (for example
`POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password`), which is how Docker and
Kubernetes secrets reach a container; see `compose.secrets.yaml`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from pydantic import Field, SecretStr, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

MAX_RETRY_COUNTDOWN_S = 600  # a document task never waits longer than this for its retry


# Values that are not secrets: the .env.example placeholders and their usual cousins.
# Compared case-insensitively; anything starting with "change-me" counts too.
PLACEHOLDER_SECRETS = frozenset(
    {"", "change-me", "changeme", "change_me", "changeit", "secret", "password", "example"}
    | {"placeholder", "xxx", "todo", "none", "null", "default", "admin", "semascope"}
)


def is_placeholder(value: str | None) -> bool:
    """True when a secret is empty or a known placeholder."""

    text = (value or "").strip().lower()
    return text in PLACEHOLDER_SECRETS or text.startswith(("change-me", "changeme"))


def _url_password(url: str) -> str:
    try:
        return unquote(urlsplit(url).password or "")
    except ValueError:
        return ""


def _with_password(url: str, password: str) -> str:
    """`url` with `password` in its user part (the user name kept), when it has none."""

    if not password or _url_password(url):
        return url
    parts = urlsplit(url)
    userinfo, _, host = parts.netloc.rpartition("@")
    user = userinfo.partition(":")[0]
    netloc = f"{user}:{quote(password, safe='')}@{host}"
    return urlunsplit(parts._replace(netloc=netloc))


class SettingsFileError(ValueError):
    """A `<NAME>_FILE` setting names a file that cannot be read."""


class FileSettingsSource(PydanticBaseSettingsSource):
    """Settings read from the files that `<NAME>_FILE` environment variables name.

    The Docker convention for secrets (`docker compose` `secrets:`, Kubernetes secret
    volumes): the value is the file's content without its trailing line break. A file
    setting wins over the plain variable and the `.env` file.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False  # unused: __call__ reads every field at once

    def __call__(self) -> dict[str, Any]:
        environ = {k.upper(): v for k, v in os.environ.items()}
        out: dict[str, Any] = {}
        for name in self.settings_cls.model_fields:
            variable = f"{name.upper()}_FILE"
            path = environ.get(variable, "").strip()
            if not path:
                continue
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError as exc:
                raise SettingsFileError(
                    f"{variable}: cannot read {path} ({exc.strerror or exc})"
                ) from exc
            out[name] = text.rstrip("\r\n")
        return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Explicit values, then `<NAME>_FILE`, then the environment, then `.env`."""

        files = FileSettingsSource(settings_cls)
        return init_settings, files, env_settings, dotenv_settings, file_secret_settings

    # Identity and sizing
    semascope_host: str = "localhost"
    semascope_https_port: int = 8443  # host port Caddy listens on (oss init --https-port)
    semascope_size: Literal["small", "medium", "large"] = "small"
    semascope_data_dir: Path = Path("./data")
    # secrets have no usable default: empty until .env sets them (oss init generates them),
    # and the server refuses to start without them outside debug mode
    semascope_secret_key: SecretStr = Field(default=SecretStr(""))
    semascope_debug: bool = False
    semascope_admin_password: SecretStr | None = None
    semascope_api_token: SecretStr | None = None
    semascope_api_url: str = "http://server:8000"
    # key of the keyed hash that pseudonymises entity keys in the graph (a collection's
    # `pseudonymise` setting). Kept when other secrets are rotated, because a new key gives
    # every entity a new pseudonym; empty means SEMASCOPE_SECRET_KEY (installs before rc.4)
    semascope_pseudonym_key: SecretStr | None = None
    # role of a signed-in user on a collection they have no role on: none (the default: each
    # collection readable only by the users given a role on it) or reader (open reading)
    semascope_default_role: str = "none"
    # where file-reading sources may read (comma-separated paths inside the containers):
    # the import mount and the demo corpus shipped in the image
    semascope_source_roots: str = "/import,/app/packs/demo/corpus"
    # private hosts or CIDR ranges that URL sources (web, feed, SPARQL, S3) may reach
    semascope_allowed_private_hosts: str = ""

    # Request limits and the audit log (oss_server.ratelimit, oss_server.audit; docs/security.md)
    # failed sign-ins before an account, or a client address, is refused for the lockout time
    semascope_login_max_failures: int = 5
    semascope_login_max_failures_per_ip: int = 50
    semascope_login_lockout_s: int = 900
    # per-user (per-token) rate limits of the expensive API routes: `scope=count/period`
    # pairs that replace the defaults (`export=10/h,console=5/m`), or `off`
    semascope_rate_limits: str = ""
    # reverse proxies in front of the server (Caddy: 1); the client address used by the
    # sign-in lockout and the anonymous limits is the one the outermost of them saw
    semascope_trusted_proxies: int = 1
    semascope_upload_max_mb: int = 512  # one uploaded file; Caddy caps the whole request
    semascope_request_max_mb: int = 10  # any other request body (JSON, forms)
    # the Reconciliation API without signing in (OpenRefine cannot send a token); off by
    # default, because list packs can name people and clients
    semascope_reconcile_public: bool = False
    semascope_audit_retention_days: int = 365  # 0 keeps audit entries for ever

    # Optional OpenID Connect sign-in (any provider); local accounts always work
    semascope_oidc_name: str = ""  # label on the sign-in button, e.g. "Company SSO"
    semascope_oidc_server_url: str = ""  # issuer, e.g. https://login.example.com/realms/x
    semascope_oidc_client_id: str = ""
    semascope_oidc_client_secret: SecretStr | None = None
    # a first sign-in through the provider creates the account (false: only accounts an
    # administrator made can sign in that way); local sign-up is always closed
    semascope_oidc_auto_signup: bool = True
    # global role given to accounts created by single sign-on: none (the default: they get
    # SEMASCOPE_DEFAULT_ROLE like everyone else), reader, curator, editor or admin
    semascope_oidc_default_role: str = "none"

    # Stores
    database_url: str = "postgresql://semascope@localhost:5432/semascope"
    postgres_password: SecretStr | None = None  # compose builds DATABASE_URL from it
    valkey_url: str = "redis://localhost:6379/0"
    # Valkey's requirepass; added to VALKEY_URL when that URL carries no password
    valkey_password: SecretStr | None = None
    solr_url: str = "http://localhost:8983/solr"
    solr_user: str = "semascope"
    solr_password: SecretStr = Field(default=SecretStr(""))
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = Field(default=SecretStr(""))
    tika_url: str = "http://localhost:9998"

    # Runtime: which models run where. `cpu` is the bundled default, `gpu` the same models on
    # a local NVIDIA card (compose.gpu.yaml), `remote` HTTP services for the heavy stages. The
    # per-stage provider settings below are what the code reads; the runtime only sets their
    # defaults at `oss init` and picks the compose overlay in `./oss up`.
    semascope_runtime: Literal["cpu", "gpu", "remote"] = "cpu"
    semascope_remote_api_key: SecretStr | None = None  # bearer token every remote service gets

    # Embeddings: fastembed (bundled; CUDA with SEMASCOPE_EMBED_PROVIDERS), openai (any
    # /embeddings endpoint) or sidecar (POST /embed/dense {"texts": [...]} -> {"vectors": [...]})
    semascope_embedder: Literal["fastembed", "openai", "sidecar"] = "fastembed"
    semascope_embed_model: str = "BAAI/bge-small-en-v1.5"
    semascope_embed_base_url: str | None = None
    semascope_embed_api_key: SecretStr | None = None
    # the model's vector length, for `oss doctor` when it cannot tell (oss_core.vectors)
    semascope_embed_dimensions: int | None = None
    semascope_model_cache: Path = Path("/app/models")

    # Named entities: spacy (bundled), gliner (GLiNER2 in-process, needs the gpu image) or
    # remote (POST /entities {"texts", "labels", "threshold"} -> {"results": [[span, ...]]})
    semascope_ner_provider: Literal["spacy", "gliner", "remote"] = "spacy"
    semascope_ner_model: str = "fastino/gliner2-large-v1"
    semascope_ner_base_url: str | None = None
    semascope_ner_api_key: SecretStr | None = None

    # Text extraction: tika (bundled, with Tesseract OCR) or mineru (a MinerU parse server for
    # PDF, Office and images; Tika still reads metadata and every other format)
    semascope_extract_provider: Literal["tika", "mineru"] = "tika"
    semascope_extract_base_url: str | None = None
    semascope_extract_api_key: SecretStr | None = None

    # Worker reliability (docs/operations.md#crashes-and-recovery); 0 means the default
    semascope_task_time_limit_s: int = 0  # hard limit of one document task: 1800, MinerU 5400
    semascope_visibility_timeout_s: int = 0  # Valkey redelivery of an unacked task: limit + 900
    semascope_max_attempts: int = 3  # interrupted starts before a document is marked failed
    semascope_stale_after_s: int = 300  # `processing` this long and on no worker: requeued
    semascope_worker_max_memory_mb: int = 0  # a pool process above this is replaced; 0: from cap

    # Map background: an XYZ raster tile URL template, shown to the UI. Empty means no base map
    # (offline and air-gapped installs); the places are drawn either way.
    semascope_map_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    semascope_map_tile_attribution: str = "© OpenStreetMap contributors"

    # Optional answer generation through any OpenAI-compatible endpoint
    semascope_llm_base_url: str | None = None
    semascope_llm_api_key: SecretStr | None = None
    semascope_llm_model: str | None = None

    @model_validator(mode="after")
    def _store_auth(self) -> Settings:
        """Put VALKEY_PASSWORD into VALKEY_URL, and POSTGRES_PASSWORD into DATABASE_URL,
        when the URL has no password of its own (compose builds both URLs from the
        variables; with Docker secrets those are empty and the files fill them in)."""

        valkey = self.valkey_password.get_secret_value() if self.valkey_password else ""
        self.valkey_url = _with_password(self.valkey_url, valkey)
        postgres = self.postgres_password.get_secret_value() if self.postgres_password else ""
        if not self.database_url.startswith("sqlite"):
            self.database_url = _with_password(self.database_url, postgres)
        return self

    def insecure_secrets(self) -> list[str]:
        """The required secrets that are empty or a known placeholder (environment names).

        The server, worker and beat refuse to start with any of these outside debug mode
        (`oss_server.settings`). Each is checked where the process reads it: the Postgres
        and Valkey passwords also count when given only inside DATABASE_URL or VALKEY_URL.
        """

        def value(secret: SecretStr | None) -> str:
            return secret.get_secret_value() if secret else ""

        checks = {
            "SEMASCOPE_SECRET_KEY": value(self.semascope_secret_key),
            "POSTGRES_PASSWORD": value(self.postgres_password) or _url_password(self.database_url),
            "NEO4J_PASSWORD": value(self.neo4j_password),
            "SOLR_PASSWORD": value(self.solr_password),
            "VALKEY_PASSWORD": value(self.valkey_password) or _url_password(self.valkey_url),
            "SEMASCOPE_ADMIN_PASSWORD": value(self.semascope_admin_password),
        }
        weak = [name for name, secret in checks.items() if is_placeholder(secret)]
        # optional, but a placeholder (`oss_change-me` in .env.example) is not a token, and a
        # placeholder pseudonym key would make every pseudonym guessable
        token = value(self.semascope_api_token)
        if token and is_placeholder(token.removeprefix("oss_")):
            weak.append("SEMASCOPE_API_TOKEN")
        if self.semascope_pseudonym_key and is_placeholder(value(self.semascope_pseudonym_key)):
            weak.append("SEMASCOPE_PSEUDONYM_KEY")
        return weak

    @property
    def pseudonym_key(self) -> str:
        """The key that pseudonymises graph entity keys: its own setting, else the secret key."""

        own = (
            self.semascope_pseudonym_key.get_secret_value() if self.semascope_pseudonym_key else ""
        )
        return own or self.semascope_secret_key.get_secret_value()

    @property
    def llm_enabled(self) -> bool:
        return bool(self.semascope_llm_base_url and self.semascope_llm_model)

    @property
    def task_time_limit(self) -> int:
        """Seconds a document task may run before its process is killed.

        The slowest document that can still succeed on the CPU runtime takes about
        20 minutes: an extract that times out at Tika's 300 s twice and then succeeds,
        then embedding a whole book. A MinerU job may take up to an hour on its own.
        """

        if self.semascope_task_time_limit_s > 0:
            return self.semascope_task_time_limit_s
        return 1800 + (3600 if self.semascope_extract_provider == "mineru" else 0)

    @property
    def visibility_timeout(self) -> int:
        """Seconds Valkey waits before redelivering an unacknowledged task.

        Longer than any task that is still running or waiting for its retry: the time
        limit, plus the longest retry countdown (`MAX_RETRY_COUNTDOWN_S`), plus a margin.
        """

        if self.semascope_visibility_timeout_s > 0:
            return self.semascope_visibility_timeout_s
        return self.task_time_limit + MAX_RETRY_COUNTDOWN_S + 300

    def remote_key(self, stage: Literal["embed", "ner", "extract"]) -> str:
        """The bearer token for a remote stage: its own key, else the shared remote key."""

        own = getattr(self, f"semascope_{stage}_api_key")
        key = own or self.semascope_remote_api_key
        return key.get_secret_value() if key else ""


def get_settings() -> Settings:
    return Settings()
