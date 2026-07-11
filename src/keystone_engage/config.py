"""Configuration for Keystone Engage.

Loads from environment variables with KEYSTONE_ prefix, or from .env file.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "qwen2.5:7b-instruct"
    ollama_embed_model: str = "nomic-embed-text"
    corpus_dir: str = "data/corpus"
    retrieval_top_k: int = 5
    confidence_threshold: float = 0.50
    env: str = "development"
    multi_agent: bool = False  # True = coordinator pipeline, False = v1 orchestrator
    database_url: str = ""  # postgresql://user:pass@host:5432/db (empty = in-memory)

    model_config = {"env_prefix": "KEYSTONE_", "env_file": ".env", "extra": "ignore"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
