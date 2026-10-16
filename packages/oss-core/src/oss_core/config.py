"""Application settings.

Every setting comes from the environment (or a `.env` file), is validated once
at start-up, and is never generated as code. Runtime-editable settings live in
the database and are a separate concern of oss-server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # Identity and sizing
    semascope_host: str = "localhost"
    semascope_https_port: int = 8443  # host port Caddy listens on (oss init --https-port)
    semascope_size: Literal["small", "medium", "large"] = "small"
    semascope_data_dir: Path = Path("./data")
    semascope_secret_key: SecretStr = Field(default=SecretStr("change-me"))
    semascope_debug: bool = False
    semascope_admin_password: SecretStr | None = None
    semascope_api_token: SecretStr | None = None
    semascope_api_url: str = "http://server:8000"
    # role of a signed-in user on a collection they have no role on: reader (open reading)
    # or none (each collection readable only by the users given a role on it)
    semascope_default_role: str = "reader"
    # where file-reading sources may read (comma-separated paths inside the containers):
    # the import mount and the demo corpus shipped in the image
    semascope_source_roots: str = "/import,/app/packs/demo/corpus"
    # private hosts or CIDR ranges that URL sources (web, feed, SPARQL, S3) may reach
    semascope_allowed_private_hosts: str = ""

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
    database_url: str = "postgresql://semascope:change-me@localhost:5432/semascope"
    postgres_password: SecretStr | None = None  # compose builds DATABASE_URL from it
    valkey_url: str = "redis://localhost:6379/0"
    # Valkey's requirepass; added to VALKEY_URL when that URL carries no password
    valkey_password: SecretStr | None = None
    solr_url: str = "http://localhost:8983/solr"
    solr_user: str = "semascope"
    solr_password: SecretStr = Field(default=SecretStr("change-me"))
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = Field(default=SecretStr("change-me"))
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

    # Map background: an XYZ raster tile URL template, shown to the UI. Empty means no base map
    # (offline and air-gapped installs); the places are drawn either way.
    semascope_map_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    semascope_map_tile_attribution: str = "© OpenStreetMap contributors"

    # Optional answer generation through any OpenAI-compatible endpoint
    semascope_llm_base_url: str | None = None
    semascope_llm_api_key: SecretStr | None = None
    semascope_llm_model: str | None = None

    @model_validator(mode="after")
    def _valkey_auth(self) -> Settings:
        """Put VALKEY_PASSWORD into VALKEY_URL when the URL has none of its own."""

        password = self.valkey_password.get_secret_value() if self.valkey_password else ""
        if password and not _url_password(self.valkey_url):
            parts = urlsplit(self.valkey_url)
            host = parts.netloc.rpartition("@")[2]
            netloc = f":{quote(password, safe='')}@{host}"
            self.valkey_url = urlunsplit(parts._replace(netloc=netloc))
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
        return [name for name, secret in checks.items() if is_placeholder(secret)]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.semascope_llm_base_url and self.semascope_llm_model)

    def remote_key(self, stage: Literal["embed", "ner", "extract"]) -> str:
        """The bearer token for a remote stage: its own key, else the shared remote key."""

        own = getattr(self, f"semascope_{stage}_api_key")
        key = own or self.semascope_remote_api_key
        return key.get_secret_value() if key else ""


def get_settings() -> Settings:
    return Settings()
