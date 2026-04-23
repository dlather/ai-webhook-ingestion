import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.config import Settings
from src.schemas.events import EventType, ClassificationResult
from src.schemas.shipment import ShipmentUpdateV1, ShipmentStatus
from src.schemas.invoice import InvoiceV1
from src.services.llm.factory import create_llm_service
from src.services.llm.mock import MockLLMService
from src.services.llm.anthropic_service import AnthropicLLMService


class TestCreateLLMServiceFactory:
    def test_mock_provider_returns_mock_service(self):
        settings = Settings(LLM_PROVIDER="mock")
        svc = create_llm_service(settings)
        assert isinstance(svc, MockLLMService)

    def test_anthropic_provider_returns_anthropic_service(self):
        settings = Settings(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="test-key")
        svc = create_llm_service(settings)
        assert isinstance(svc, AnthropicLLMService)

    def test_unknown_provider_raises_value_error(self):
        settings = Settings(LLM_PROVIDER="unknown_provider")
        with pytest.raises(ValueError, match="unknown_provider"):
            create_llm_service(settings)


class TestAnthropicLLMServiceClassify:
    async def test_classify_calls_instructor_client(self):
        expected = ClassificationResult(
            event_type=EventType.SHIPMENT_UPDATE,
            confidence=0.95,
            reasoning="Contains tracking number",
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)

        svc = AnthropicLLMService.__new__(AnthropicLLMService)
        svc._client = mock_client
        svc._model = "claude-test"

        result = await svc.classify({"tracking_number": "SHIP-001"})

        assert mock_client.chat.completions.create.called
        assert result.event_type == EventType.SHIPMENT_UPDATE
        assert result.confidence == 0.95

    async def test_classify_returns_classification_result(self):
        expected = ClassificationResult(
            event_type=EventType.INVOICE, confidence=0.88, reasoning="Has invoice fields"
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)

        svc = AnthropicLLMService.__new__(AnthropicLLMService)
        svc._client = mock_client
        svc._model = "claude-test"

        result = await svc.classify({"invoice_id": "INV-001", "amount": 100})
        assert isinstance(result, ClassificationResult)
        assert result.event_type == EventType.INVOICE


class TestAnthropicLLMServiceExtract:
    async def test_extract_calls_instructor_with_schema_class(self):
        expected = ShipmentUpdateV1(
            vendor_id="acme",
            tracking_number="SHIP-001",
            status=ShipmentStatus.TRANSIT,
            timestamp="2024-01-15T10:30:00Z",
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=expected)

        svc = AnthropicLLMService.__new__(AnthropicLLMService)
        svc._client = mock_client
        svc._model = "claude-test"

        result = await svc.extract(
            {"vendor_id": "acme", "tracking_number": "SHIP-001"},
            EventType.SHIPMENT_UPDATE,
            ShipmentUpdateV1,
        )

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["response_model"] == ShipmentUpdateV1
        assert isinstance(result, ShipmentUpdateV1)
