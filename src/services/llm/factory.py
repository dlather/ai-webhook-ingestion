from src.config import Settings
from src.services.llm.mock import MockLLMService
from src.services.llm.protocol import LLMService


def create_llm_service(settings: Settings) -> LLMService:
    if settings.LLM_PROVIDER == "mock":
        return MockLLMService()
    elif settings.LLM_PROVIDER == "anthropic":
        from src.services.llm.anthropic_service import AnthropicLLMService

        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        return AnthropicLLMService(
            api_key=settings.ANTHROPIC_API_KEY,
            model=settings.LLM_MODEL,
        )
    else:
        raise ValueError(
            f"Unknown LLM provider: {settings.LLM_PROVIDER!r}. Valid options: 'mock', 'anthropic'"
        )
