from functools import lru_cache
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_file=None, extra="ignore")

    DATABASE_URL: str = "sqlite+aiosqlite:///./webhooks.db"
    LLM_PROVIDER: str = "mock"
    LLM_MODEL: str = "claude-sonnet-4-20250514"
    CONFIDENCE_THRESHOLD: float = 0.7
    ANTHROPIC_API_KEY: str | None = None
    MAX_PAYLOAD_SIZE_BYTES: int = 1_048_576
    OUTBOX_POLL_INTERVAL_SECONDS: float = 2.0
    WORKER_CONCURRENCY: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
