from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    llm_provider: Literal["ollama", "openai"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:9b"
    embed_model: str = "nomic-embed-text"

    openai_api_key: str = ""
    openai_model: str = ""

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "***REMOVED***"

    # Pipeline limits
    max_cypher_attempts: int = 3
    max_result_rows: int = 50
    query_timeout_s: float = 10.0
    entity_candidates: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
