from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from src.schemas.events import ClassificationResult, EventType


@runtime_checkable
class LLMService(Protocol):
    async def classify(self, raw_payload: dict) -> ClassificationResult: ...

    async def extract(
        self,
        raw_payload: dict,
        event_type: EventType,
        schema_class: type[BaseModel],
    ) -> BaseModel: ...
