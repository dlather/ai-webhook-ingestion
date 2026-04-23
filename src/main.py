import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from src.api import health, webhooks
from src.api.deps import set_event_queue, set_session_factory  # pyright: ignore[reportUnknownVariableType]
from src.config import get_settings
from src.db import create_engine, create_session_factory, init_db
from src.pipeline.processor import ProcessingPipeline
from src.services.llm.factory import create_llm_service
from src.services.schema_registry import create_default_registry
from src.worker.processor import EventProcessor
from src.worker.queue import EventQueue
from src.worker.relay import OutboxRelay

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    logger.info("Starting AI Webhook Ingestion Service | LLM provider: %s", settings.LLM_PROVIDER)

    engine = create_engine(settings.DATABASE_URL)
    await init_db(engine)
    session_factory = create_session_factory(engine)

    llm_service = create_llm_service(settings)
    registry = create_default_registry()

    event_queue = EventQueue(maxsize=100)
    relay = OutboxRelay(
        session_factory, event_queue, poll_interval=settings.OUTBOX_POLL_INTERVAL_SECONDS
    )
    pipeline = ProcessingPipeline(
        session_factory=session_factory,
        llm_service=llm_service,
        schema_registry=registry,
        confidence_threshold=settings.CONFIDENCE_THRESHOLD,
    )
    processor = EventProcessor(session_factory, event_queue, pipeline)

    set_session_factory(session_factory)
    set_event_queue(event_queue)

    await relay.recover_stale()

    relay_task = asyncio.create_task(relay.start(), name="outbox-relay")
    processor_task = asyncio.create_task(processor.start(), name="event-processor")

    logger.info("Workers started. Service ready.")

    try:
        yield
    finally:
        logger.info("Shutting down workers...")
        _ = await event_queue.shutdown()
        _ = relay_task.cancel()
        _ = processor_task.cancel()
        try:
            _ = await asyncio.wait_for(
                asyncio.gather(relay_task, processor_task, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Worker shutdown timed out after 5s")

        await engine.dispose()
        logger.info("Service shutdown complete.")


app = FastAPI(
    title="AI Webhook Ingestion Service",
    description="Ingests vendor webhooks, classifies them with LLM, and normalizes to strict schemas.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(webhooks.router)
app.include_router(health.router)
