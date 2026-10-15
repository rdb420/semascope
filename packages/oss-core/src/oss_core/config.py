"""Application settings.

Every setting comes from the environment (or a `.env` file), is validated once
at start-up, and is never generated as code. Runtime-editable settings live in
the database and are a separate concern of oss-server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # Identity and sizing
    semascope_host: str = "localhost"
    semascope_size: Literal["small", "medium", "large"] = "small"
    semascope_data_dir: Path = Path("./data")
    semascope_secret_key: SecretStr = Field(default=SecretStr("change-me"))
    semascope_debug: bool = False
    semascope_admin_password: SecretStr | None = None
    semascope_api_token: SecretStr | None = None
    semascope_api_url: str = "http://server:8000"
    semascope_default_role: str = "reader"  # role of a signed-in user with no explicit roles

    # Stores
    database_url: str = "postgresql://semascope:change-me@localhost:5432/semascope"
    valkey_url: str = "redis://localhost:6379/0"
    solr_url: str = "http://localhost:8983/solr"
    solr_user: str = "semascope"
    solr_password: SecretStr = Field(default=SecretStr("change-me"))
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = Field(default=SecretStr("change-me"))
    tika_url: str = "http://localhost:9998"

    # Embeddings: fastembed (bundled, CPU) or openai (any /embeddings endpoint)
    semascope_embedder: Literal["fastembed", "openai"] = "fastembed"
    semascope_embed_model: str = "BAAI/bge-small-en-v1.5"
    semascope_model_cache: Path = Path("/app/models")

    # Optional answer generation through any OpenAI-compatible endpoint
    semascope_llm_base_url: str | None = None
    semascope_llm_api_key: SecretStr | None = None
    semascope_llm_model: str | None = None

    @property
    def llm_enabled(self) -> bool:
        return bool(self.semascope_llm_base_url and self.semascope_llm_model)


def get_settings() -> Settings:
    return Settings()
