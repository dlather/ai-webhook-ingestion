# AI Webhook Ingestion Service

A FastAPI service that accepts arbitrary vendor webhook payloads, uses an LLM to classify and normalize them into typed schemas, and stores the results asynchronously. Built for supply chain integrations where every vendor sends a different JSON structure.

---

## Quick Start

Prerequisites: Python 3.11+, uv (https://docs.astral.sh/uv/)

```bash
uv sync
uv run uvicorn src.main:app
```

```bash
# Submit a webhook
curl -X POST http://localhost:8000/webhooks/my-vendor \
  -H "Content-Type: application/json" \
  -H "X-Event-ID: evt-123" \
  -d '{"tracking_number":"SHIP-001","status":"TRANSIT","vendor_id":"acme","timestamp":"2024-01-15T10:30:00Z"}'

# Check status
curl http://localhost:8000/ingestions/{ingestion_id}

# Run tests
uv run pytest
```

---

## Architecture Overview

```
POST /webhooks/{vendor}
        |
        v
 [Validate + Size Check]
        |
        v
 [Deduplication Check] -----> 200 duplicate (if seen before)
        |
        v
 [Write RawEvent + OutboxEvent to DB]
        |
        v
 202 Accepted  <--- response returned here (sub-millisecond path)
        |
        v (async, via asyncio.Queue relay)
 [Outbox Relay Worker]
        |
        v
 [ProcessingPipeline]
        |
        +---> [LLM classify()] --> confidence < threshold --> [Quarantine]
        |
        +---> [LLM extract()]  --> Pydantic validation fail --> [Quarantine]
        |
        +---> [Write NormalizedRecord] --> status = COMPLETED
```

Key design decisions:

- **Outbox pattern**: RawEvent and OutboxEvent are written in a single DB transaction before the 202 is returned. The relay picks up pending outbox rows on startup, so no events are silently dropped on crash.
- **Two-pass LLM pipeline**: Classification and extraction are separate calls. This avoids forcing the LLM to produce a union type in one shot, and allows extraction prompts to be schema-specific (shipment vs invoice instructions differ).
- **Quarantine over retry**: Events that fail classification or fall below the confidence threshold are quarantined rather than retried blindly. Bad data in normalized records is harder to detect and clean up than a quarantine row.
- **Idempotency guard**: Before processing, the pipeline checks whether the raw event is already in a terminal state (COMPLETED, QUARANTINED, FAILED_TERMINAL). Safe to re-dispatch from the relay.

---

## API Reference

### POST /webhooks/{vendor}

Accepts a webhook payload from the named vendor. Returns immediately.

Request headers:
- `Content-Type: application/json` (required)
- `X-Event-ID: <string>` (optional; enables strong deduplication by vendor + event ID)

Request body: any valid JSON object.

Response 202 — accepted:
```json
{"ingestion_id": "ing_a3f1b2c4d5e6", "status": "accepted"}
```

Response 200 — duplicate detected:
```json
{"ingestion_id": "ing_a3f1b2c4d5e6", "status": "duplicate"}
```

Response 400 — invalid JSON, 413 — payload too large, 415 — wrong content type.

---

### GET /ingestions/{ingestion_id}

Polls the processing status of a previously accepted webhook.

Response 200:
```json
{
  "ingestion_id": "ing_a3f1b2c4d5e6",
  "vendor": "my-vendor",
  "status": "COMPLETED",
  "received_at": "2024-01-15T10:30:01.234567+00:00"
}
```

Possible status values:
- `RECEIVED` — written to DB, not yet picked up by the pipeline
- `PROCESSING` — pipeline has started
- `COMPLETED` — normalized record written (or event was UNCLASSIFIED)
- `QUARANTINED` — low confidence or extraction failure; see quarantine table
- `FAILED_TERMINAL` — unexpected pipeline crash

Response 404 if the ingestion_id does not exist.

---

### GET /health

Returns DB connectivity and current in-memory queue depth.

Response 200:
```json
{"status": "healthy", "db": "connected", "queue_depth": 0}
```

Response 503 if the database is unreachable.

---

## Configuration

All configuration is read from environment variables at startup.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./webhooks.db` | SQLAlchemy async connection string |
| `LLM_PROVIDER` | `mock` | LLM backend: `mock` or `anthropic` |
| `LLM_MODEL` | `claude-sonnet-4-20250514` | Model name passed to the provider |
| `CONFIDENCE_THRESHOLD` | `0.7` | Events classified below this score go to quarantine |
| `ANTHROPIC_API_KEY` | _(none)_ | Required when `LLM_PROVIDER=anthropic` |
| `MAX_PAYLOAD_SIZE_BYTES` | `1048576` | Reject payloads larger than this (1 MB default) |
| `OUTBOX_POLL_INTERVAL_SECONDS` | `2.0` | How often the relay polls for pending outbox rows |
| `WORKER_CONCURRENCY` | `1` | Number of concurrent pipeline workers |

---

## LLM Integration

### Mock mode (default)

No API key required. The mock LLM uses keyword heuristics to classify payloads and returns a fixed delay to simulate latency. Suitable for development and all tests.

```bash
uv run uvicorn src.main:app  # LLM_PROVIDER defaults to mock
```

### Anthropic mode

```bash
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... uv run uvicorn src.main:app
```

Uses `claude-sonnet-4-20250514` by default. Override with `LLM_MODEL`.

### Adding a new provider

Three files to touch:

1. `src/services/llm/protocol.py` — the `LLMService` Protocol defines `classify()` and `extract()`; implement it
2. `src/services/llm/factory.py` — add a branch for your provider name in `create_llm_service()`
3. `src/config.py` — add any new config fields (e.g., API key, base URL)

---

## LLM Prompting Strategy

The pipeline makes two sequential LLM calls per webhook.

**Pass 1 — Classification**

The classification prompt asks the LLM to pick from three labels (SHIPMENT_UPDATE, INVOICE, UNCLASSIFIED) and return a confidence score between 0.0 and 1.0 with brief reasoning. It does not ask for any field extraction.

Rationale: Combining classification and extraction into one call forces the LLM to produce a union type response — either a ShipmentUpdate or an Invoice depending on its own classification. This is unreliable because the output schema must be known before the response is parsed. Separating the calls lets each extraction prompt be precisely targeted to its schema.

**Pass 2 — Extraction**

Once the type is known, a schema-specific prompt is built. Each extraction prompt lists the exact target fields, their types, accepted enum values, and explicit mapping rules (e.g., "in transit" -> TRANSIT). The prompt ends with:

> Only extract values that clearly exist in the payload. Do NOT invent or infer values not present in the payload.

This is the primary hallucination guard. The LLM is given the raw JSON and told explicitly what it may not do.

**Structured output via Instructor**

Extraction uses the Instructor library to wrap the LLM call. Instructor enforces that the response matches the target Pydantic schema. If the model returns malformed JSON or a field fails validation, Instructor automatically retries the call with the validation error appended to the prompt. This removes the need for manual JSON parsing and try/except retry loops.

**Confidence threshold**

After classification, if the returned confidence is below `CONFIDENCE_THRESHOLD` (default 0.7), the event is quarantined rather than extracted. Extraction on a low-confidence classification risks writing structurally valid but semantically wrong records.

**Trade-off: 2 calls vs 1**

Two LLM calls per webhook roughly doubles the LLM cost and latency compared to a single combined call. This is acceptable here because the extraction is async (vendor already received 202), and reliability of normalized output matters more than cost at this scale. If cost becomes a concern, the confidence score from pass 1 could be used to short-circuit events likely to be UNCLASSIFIED before making the extraction call.

---

## Schema Registry

The schema registry maps an EventType enum value to a Pydantic schema class and a version string.

Current registered types:

**ShipmentUpdateV1**
- `vendor_id` (string)
- `tracking_number` (string, required, non-empty)
- `status` (enum: TRANSIT | DELIVERED | EXCEPTION)
- `timestamp` (ISO 8601 datetime)

**InvoiceV1**
- `vendor_id` (string)
- `invoice_id` (string)
- `amount` (float, must be > 0)
- `currency` (3-letter ISO 4217 uppercase, e.g. USD)

### Adding a new event type

1. Add a new value to `EventType` in `src/schemas/events.py`
2. Create a Pydantic model in `src/schemas/` (follow ShipmentUpdateV1 or InvoiceV1 as reference)
3. Add a prompt builder function in `src/services/prompts.py`
4. Register the type in `src/services/schema_registry.py` — one line: `registry.register(EventType.YOUR_TYPE, YourSchema, "v1")`

---

## What's Hacked / Tradeoffs

These are the corners that were explicitly cut for scope. Each item describes what was done, why, and the production upgrade path.

**1. SQLite instead of PostgreSQL**

What: `DATABASE_URL` defaults to `sqlite+aiosqlite:///./webhooks.db`. Zero external dependencies to run the service.

Limitation: SQLite has a single-writer lock. It does not support `FOR UPDATE SKIP LOCKED`, which means multiple relay workers cannot safely compete for outbox rows without risk of double-processing.

Production upgrade: Set `DATABASE_URL=postgresql+asyncpg://user:pass@host/db`. Add Alembic for schema migrations (`alembic init`, `alembic revision --autogenerate`). No application code changes required — SQLAlchemy abstracts the driver.

**2. In-memory asyncio.Queue instead of an external message broker**

What: After the DB write, the webhook handler pushes the raw_event_id into an in-process `asyncio.Queue`. The relay worker reads from this queue and drives the processing pipeline.

Limitation: If the process crashes after the 202 is returned but before the queue item is consumed, that item is lost in memory. Recovery depends on the outbox relay polling for `PENDING` outbox rows on startup, which handles this case — but only if the crash happens before the outbox row is marked processed.

Production upgrade: Replace the `EventQueue` abstraction with an SQS, RabbitMQ, or Kafka client. The outbox row write and the broker publish can be made transactionally consistent using the transactional outbox pattern (already partially implemented here).

**3. Single relay worker**

What: `WORKER_CONCURRENCY` defaults to 1. Only one event is processed at a time.

Limitation: Throughput is bounded by single-worker LLM latency (~1-3 seconds per event with a real LLM). Increasing `WORKER_CONCURRENCY` beyond 1 is unsafe with SQLite due to the write-lock contention.

Production upgrade: Switch to PostgreSQL. `FOR UPDATE SKIP LOCKED` allows multiple workers to safely pull from the outbox without double-processing. Set `WORKER_CONCURRENCY` to match available LLM rate limit budget.

**4. No vendor-specific authentication or signature verification**

What: The service accepts any POST to `/webhooks/{vendor}` with no authentication.

Limitation: In production, each vendor signs their webhooks differently. Shopify uses `X-Shopify-Hmac-Sha256`, Stripe uses `Stripe-Signature`, others use bearer tokens.

Production upgrade: Add a per-vendor auth middleware that reads a vendor config (HMAC secret, token) and validates the signature before the payload is read. Reject with 401 if verification fails. This prevents replay attacks and spoofed payloads.

**5. `create_all()` instead of Alembic**

What: On startup, `Base.metadata.create_all()` creates tables if they don't exist. There is no migration history.

Limitation: Adding a column in production requires manual intervention or a destructive drop-and-recreate.

Production upgrade: `alembic init alembic`, set `target_metadata = Base.metadata`, generate migrations with `alembic revision --autogenerate`, apply with `alembic upgrade head` in the deployment step.

**6. No circuit breaker for the LLM provider**

What: If the LLM API is down or rate-limited, every event that reaches the pipeline will fail and be quarantined. There is no backoff or open-circuit state.

Production upgrade: Wrap LLM calls in a circuit breaker (e.g., `pybreaker`). After N consecutive failures, open the circuit and route all incoming events directly to quarantine rather than attempting LLM calls. Reset after a cooldown period. This prevents a LLM outage from cascading into a queue backlog.

**7. No metrics, tracing, or structured logging**

What: Logging uses plain `logging.info/error` calls. There are no Prometheus counters, no distributed traces, and no correlation IDs linking an ingestion_id across log lines.

Production upgrade:
- Prometheus counters: webhook rate by vendor, LLM latency histogram, quarantine rate, queue depth gauge
- OpenTelemetry traces: span per webhook request, child spans for dedup check, LLM classify, LLM extract, DB write
- Structured logging with `ingestion_id` and `vendor` in every log record for log aggregation queries

---

## Running Tests

```bash
uv run pytest                      # all 131 tests
uv run pytest tests/unit/          # unit tests only (121 tests)
uv run pytest tests/integration/   # integration tests only (10 tests)
```

Tests use the mock LLM by default. No API keys or external services required.
