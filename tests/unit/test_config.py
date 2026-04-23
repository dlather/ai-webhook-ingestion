import pytest

from src.config import Settings, get_settings


def test_default_database_url():
    s = Settings()
    assert s.DATABASE_URL == "sqlite+aiosqlite:///./webhooks.db"


def test_default_llm_provider():
    s = Settings()
    assert s.LLM_PROVIDER == "mock"


def test_default_confidence_threshold():
    s = Settings()
    assert s.CONFIDENCE_THRESHOLD == 0.7


def test_default_max_payload_size():
    s = Settings()
    assert s.MAX_PAYLOAD_SIZE_BYTES == 1_048_576


def test_default_anthropic_api_key_is_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings()
    assert s.ANTHROPIC_API_KEY is None


def test_get_settings_returns_settings_instance():
    s = get_settings()
    assert isinstance(s, Settings)
