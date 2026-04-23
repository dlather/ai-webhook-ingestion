import logging

import instructor
from pydantic import BaseModel

from src.schemas.events import ClassificationResult, EventType
from src.services.prompts import build_classification_prompt

logger = logging.getLogger(__name__)

_MAX_TOKENS_CLASSIFY = 500
_MAX_TOKENS_EXTRACT = 1000


class AnthropicLLMService:
    def __init__(self, api_key: str, model: str, max_retries: int = 2) -> None:
        self._model = model
        self._client = instructor.from_provider(
            f"anthropic/{model}",
            async_client=True,
            api_key=api_key,
        )
        self._max_retries = max_retries

    async def classify(self, raw_payload: dict) -> ClassificationResult:
        prompt = build_classification_prompt(raw_payload)
        result: ClassificationResult = await self._client.chat.completions.create(
            response_model=ClassificationResult,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=_MAX_TOKENS_CLASSIFY,
            max_retries=getattr(self, "_max_retries", 2),
        )
        logger.info(f"Classified as {result.event_type} with confidence {result.confidence:.2f}")
        return result

    async def extract(
        self,
        raw_payload: dict,
        event_type: EventType,
        schema_class: type[BaseModel],
    ) -> BaseModel:
        from src.services.schema_registry import create_default_registry

        registry = create_default_registry()
        entry = registry.get(event_type)
        if entry is None:
            raise ValueError(f"No schema registered for event type: {event_type}")

        prompt = entry.prompt_builder(raw_payload)
        result: BaseModel = await self._client.chat.completions.create(
            response_model=schema_class,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=_MAX_TOKENS_EXTRACT,
            max_retries=getattr(self, "_max_retries", 2),
        )
        return result
