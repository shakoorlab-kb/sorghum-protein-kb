"""Settings, all from the environment (see deploy/.env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # Read-only Sorghum KB checkout (papers + structured extraction).
    kb_dir: Path = field(default_factory=lambda: Path(_env("SI_KB_DIR", "/kb")))
    # App-owned data: SQLite, uploads, page render cache.
    data_dir: Path = field(default_factory=lambda: Path(_env("SI_DATA_DIR", "/data")))

    neo4j_uri: str = field(default_factory=lambda: _env("NEO4J_URI", "bolt://neo4j:7687"))
    neo4j_user: str = field(default_factory=lambda: _env("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: _env("NEO4J_PASSWORD"))

    # OpenRouter (OpenAI-compatible chat completions).
    llm_base_url: str = field(default_factory=lambda: _env("SI_LLM_BASE_URL", "https://openrouter.ai/api/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    chat_model: str = field(default_factory=lambda: _env("SI_CHAT_MODEL", "anthropic/claude-haiku-5.5"))
    extract_model: str = field(default_factory=lambda: _env("SI_EXTRACT_MODEL", "anthropic/claude-haiku-5.5"))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("SI_LLM_TIMEOUT_S", "120")))
    # How the knowledge graph is built from indexed documents:
    #   claude      by Claude Code agents writing extraction files, loaded with `claude-import`
    #   openrouter  automatically after indexing, with SI_EXTRACT_MODEL (costs API credit)
    extract_mode: str = field(default_factory=lambda: _env("SI_EXTRACT_MODE", "claude").lower())
    extract_workers: int = field(default_factory=lambda: int(_env("SI_EXTRACT_WORKERS", "12")))

    embed_model: str = field(default_factory=lambda: _env("SI_EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
    embed_dim: int = field(default_factory=lambda: int(_env("SI_EMBED_DIM", "384")))

    admin_user: str = field(default_factory=lambda: _env("SI_ADMIN_USER", "admin"))
    admin_password: str = field(default_factory=lambda: _env("SI_ADMIN_PASSWORD"))
    session_secret: str = field(default_factory=lambda: _env("SI_SESSION_SECRET"))
    session_days: int = field(default_factory=lambda: int(_env("SI_SESSION_DAYS", "14")))

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def render_cache(self) -> Path:
        return self.data_dir / "render_cache"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.sqlite3"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()
