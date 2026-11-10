from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sentinel_env: str = "dev"
    log_level: str = "INFO"
    database_url: str = "postgresql://sentinel:change-me-locally@localhost:5432/sentinel"
    redis_url: str = "redis://localhost:6379/0"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 3
    traces_dir: Path = Path("traces")


@lru_cache
def get_settings() -> Settings:
    return Settings()
