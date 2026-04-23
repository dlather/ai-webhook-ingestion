# AI Webhook Ingestion Service

A FastAPI service that accepts arbitrary vendor webhook payloads, uses an LLM to classify and
normalize them into typed schemas, and stores the results asynchronously. Built for supply chain
integrations where every vendor sends a different JSON structure. The architecture centres on a
transactional outbox that guarantees durability before returning 202, and an async worker layer
that keeps LLM latency off the request path entirely.

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

## Architecture

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

### Why These Patterns

**202 + outbox, not synchronous processing.** Vendors expect sub-second acknowledgment; real LLM
calls take 1-3 seconds -- you cannot block the webhook handler. The outbox pattern writes
RawEvent + OutboxEvent atomically in a single DB transaction before returning 202, so the vendor
gets its acknowledgment in under 10ms regardless of downstream latency. The alternative --
fire-and-forget background task -- loses the event if the process crashes before the task runs;
with the outbox, the relay recovers stale DISPATCHED rows on startup.

**Two LLM passes, not one.** A combined call forces the LLM to produce a union type: "output a
ShipmentUpdate or an Invoice depending on what you think this is." Structured output via Instructor
requires the target schema to be known before the call, so a union response is not parseable.
Classifying first resolves the schema; each extraction prompt is then precisely targeted --
shipment prompts enumerate status enums and tracking constraints, invoice prompts specify currency
codes and amount rules. Trade-off: roughly 2x LLM cost per event, acceptable because extraction
is async and output reliability matters more than cost at this scale.

**Quarantine, not retry.** When the LLM returns confidence below 0.7, the event is quarantined --
not retried with a different prompt, not silently dropped. Structurally valid but semantically
wrong data written to the normalized records table is extremely hard to detect and clean up
afterward. A quarantine row is visible, reviewable, and replayable once a human has confirmed the
correct interpretation. The threshold is configurable via CONFIDENCE_THRESHOLD.

**Schema registry for extensibility.** The ProcessingPipeline contains no hardcoded type
switches. Classification reads EventType enum values directly to build its prompt -- adding a new
value automatically includes it in the next classification call. Extraction schemas are resolved
from the registry at runtime. Adding a new event type is 4 files, zero changes to existing code.
(See "Adding a New Schema Type" below.)

---

## Project Structure

```
src/
├── config.py              # 8 env vars via pydantic-settings
├── main.py                # FastAPI lifespan: startup/shutdown wiring
├── schemas/               # Pydantic v2 domain models (no DB coupling)
│   ├── events.py          # EventType enum, ClassificationResult
│   ├── shipment.py        # ShipmentUpdateV1 with field validators
│   ├── invoice.py         # InvoiceV1 with field validators
│   └── extraction.py      # ExtractionResult wrapper
├── models/                # SQLAlchemy 2.0 ORM (no business logic)
│   ├── base.py            # DeclarativeBase + TimestampMixin
│   ├── raw_event.py       # Immutable raw payload storage
│   ├── outbox_event.py    # Transactional outbox for async dispatch
│   ├── processing_attempt.py  # Audit trail per pipeline stage
│   ├── normalized_record.py   # Validated output storage
│   └── quarantine_event.py    # Failed/uncertain events
├── services/              # Business logic (no HTTP or DB knowledge)
│   ├── dedup.py           # Strong (event ID) + weak (payload hash) dedup
│   ├── quarantine.py      # Write-only quarantine + status update
│   ├── schema_registry.py # EventType -> Pydantic schema + prompt builder
│   ├── prompts.py         # LLM prompt templates
│   └── llm/
│       ├── protocol.py    # typing.Protocol -- the only interface the pipeline sees
│       ├── mock.py        # Deterministic keyword heuristics (no API key)
│       ├── anthropic_service.py  # Real LLM via Instructor library
│       └── factory.py     # Config-driven provider selection
├── pipeline/
│   └── processor.py       # Two-pass orchestration: classify -> extract -> persist
├── worker/
│   ├── queue.py           # asyncio.Queue wrapper (notification channel)
│   ├── relay.py           # Polls outbox -> dispatches to queue
│   └── processor.py       # Consumes queue -> runs pipeline
└── api/
    ├── deps.py            # Module-level DI state
    ├── webhooks.py        # POST /webhooks/{vendor}
    └── health.py          # GET /health, GET /ingestions/{id}
```

Schemas know nothing about the database. Models know nothing about business logic. Services know
nothing about HTTP. The pipeline orchestrates services. The API calls the pipeline through the
worker layer. This layering means any component can be tested in isolation without standing up a
full stack.

The LLM service is defined as a `typing.Protocol`, not an ABC. The pipeline depends on the
protocol, not on any concrete implementation. The mock and Anthropic services satisfy the protocol
through structural subtyping -- no inheritance required. Swapping providers is a factory decision,
not a refactor.

---

## Edge Cases Handled

| Reality | Edge Case | How It's Handled | Where in Code |
|---------|-----------|------------------|---------------|
| "Vendors expect sub-second acknowledgments" | Slow LLM on request path | 202 + outbox; LLM runs async in background worker | `api/webhooks.py` -> `worker/` |
| "Notorious for firing the same payload multiple times" | Duplicate webhooks | Two-tier dedup: strong (vendor + `X-Event-ID`) unique index; weak (vendor + SHA-256 payload hash) composite unique constraint | `services/dedup.py`, `models/raw_event.py` |
| "Massive spikes in traffic" | Burst of webhooks | Bounded asyncio.Queue (maxsize=100); 1 MB payload size limit; relay is poll-based, not push | `worker/queue.py`, `config.py` |
| "LLMs are slow" | Processing latency | Async worker decouples ingestion from processing; vendor gets 202 in <10ms regardless of LLM latency | `main.py` lifespan, `worker/` |
| "Prone to hallucinations" | LLM invents field values | Pydantic v2 strict validation; prompts explicitly prohibit invented values; confidence threshold gates extraction | `schemas/`, `services/prompts.py` |
| "Fail to return the exact structure you ask for" | Malformed LLM output | Instructor enforces Pydantic schema on response; auto-retries with validation error context; unrecoverable -> FAILED_TERMINAL | `services/llm/anthropic_service.py` |
| Crash between 202 and processing | Lost events | Relay resets DISPATCHED rows older than 5 min to PENDING on startup; idempotency guard prevents double-processing | `worker/relay.py`, `pipeline/processor.py` |
| Concurrent identical requests | Race condition duplicates | Composite unique constraint on (vendor, weak_payload_hash); IntegrityError -> return duplicate response | `models/raw_event.py`, `api/webhooks.py` |
| Unknown event type | Unclassifiable payload | UNCLASSIFIED -> COMPLETED with no extraction; no normalized record written | `pipeline/processor.py` |
| Classified but unregistered type | Registry miss | Quarantine with UNKNOWN_TYPE reason code | `pipeline/processor.py` |

---

## LLM Prompting Strategy

The pipeline makes two sequential LLM calls per webhook.

**Pass 1 -- Classification**

The classification prompt presents the raw payload and asks the LLM to select from the registered
EventType values, returning a confidence score between 0.0 and 1.0 with brief reasoning. It
does not ask for field extraction -- asking the model to do two things in one call consistently
degrades both.

**Pass 2 -- Extraction**

Once the event type is resolved, the schema registry provides a prompt_builder for that type.
Each extraction prompt lists the exact target fields, their types, accepted enum values, and
explicit mapping rules (e.g., "in transit" -> TRANSIT). Every extraction prompt ends with:

> Only extract values that clearly exist in the payload. Do NOT invent or infer values not present
> in the payload.

This is the primary hallucination guard at the prompt level. Pydantic v2 field validators are the
second layer -- they reject structurally wrong values even if the prompt instruction was ignored.

**Structured output via Instructor**

Extraction uses the Instructor library to wrap the LLM call. Instructor enforces that the response
conforms to the target Pydantic schema. If the model returns malformed JSON or a field fails
validation, Instructor retries automatically with the validation error appended to the prompt --
removing the need for manual JSON parsing and hand-rolled retry loops.

**Confidence threshold**

After classification, if confidence is below CONFIDENCE_THRESHOLD (default 0.7), the event is
quarantined rather than extracted. Extraction on a low-confidence classification risks producing
records that are structurally valid but semantically wrong.

**Trade-off: 2 calls vs 1**

Two LLM calls per webhook roughly doubles cost and latency versus a single combined call. This is
acceptable because extraction is async (the vendor already received 202) and output reliability
matters more than cost at this scale. If cost becomes a concern, low-confidence events could be
short-circuited before the extraction call.

---

## Adding a New Schema Type

Suppose you receive a new webhook type: Purchase Orders. Here is exactly what you would do.

**Step 1** -- Add `PURCHASE_ORDER` to the EventType enum in `src/schemas/events.py` (1 line).
The classification prompt reads from this enum automatically -- no prompt edits required.

**Step 2** -- Create `src/schemas/purchase_order.py` with a PurchaseOrderV1 Pydantic model. Use
invoice.py as a template.

**Step 3** -- Add `build_purchase_order_extraction_prompt()` in `src/services/prompts.py`. Copy
the structure of an existing prompt builder; customize the field names, enum values, and mapping
rules.

**Step 4** -- Register in `src/services/schema_registry.py`:

```python
registry.register(
    EventType.PURCHASE_ORDER,
    SchemaRegistryEntry(
        schema_class=PurchaseOrderV1,
        prompt_builder=build_purchase_order_extraction_prompt,
        version="1.0",
    ),
)
```

That is it. No changes to the pipeline, API, worker, or database. The pipeline resolves the
extraction schema from the registry at runtime, so the new type is live the moment the service
restarts.

---

## What's Hacked / Tradeoffs

This service was built in a time-boxed session. Below is an honest accounting of every shortcut
taken, why each is safe for a demo but not for production, and the exact upgrade path.

**1. SQLite instead of PostgreSQL**

DATABASE_URL defaults to sqlite+aiosqlite:///./webhooks.db -- zero external dependencies to
run. The limitation: SQLite has a single-writer lock and does not support FOR UPDATE SKIP LOCKED,
so multiple relay workers cannot safely compete for outbox rows. Production upgrade: set
DATABASE_URL to a PostgreSQL asyncpg connection string. No application code changes required --
SQLAlchemy abstracts the driver. Add Alembic for schema migrations.

**2. In-memory asyncio.Queue instead of an external broker**

The webhook handler pushes raw_event_id into an in-process asyncio.Queue. If the process
crashes after the 202 is returned but before the queue item is consumed, that item is lost from
memory. The outbox relay's startup recovery handles this -- but only after restart. Production
upgrade: replace the EventQueue abstraction with SQS, RabbitMQ, or Kafka. The outbox pattern is
already in place; connecting it to a real broker is a swap at the relay layer.

**3. Single relay worker**

WORKER_CONCURRENCY defaults to 1. Throughput is bounded by single-worker LLM latency (~1-3
seconds per event with a real LLM). Increasing beyond 1 is unsafe with SQLite due to write-lock
contention. Production upgrade: switch to PostgreSQL with FOR UPDATE SKIP LOCKED, then set
WORKER_CONCURRENCY to match available LLM rate limit budget.

**4. No vendor authentication or signature verification**

The service accepts any POST to /webhooks/{vendor} with no authentication. In production each
vendor signs differently -- Shopify uses X-Shopify-Hmac-Sha256, Stripe uses Stripe-Signature.
Production upgrade: add per-vendor auth middleware that reads a vendor config (HMAC secret or
bearer token) and validates the signature before reading the payload. Reject with 401 on failure.

**5. create_all() instead of Alembic**

Base.metadata.create_all() runs on startup. There is no migration history. Adding a column in
production requires manual intervention. Production upgrade: alembic init, set
target_metadata = Base.metadata, generate revisions with --autogenerate, apply with
alembic upgrade head.

**6. No circuit breaker for the LLM provider**

If the LLM API is down, every event fails and is marked FAILED_TERMINAL. There is no backoff or
open-circuit state. Production upgrade: wrap LLM calls in a circuit breaker (pybreaker). After N
consecutive failures, open the circuit and route events directly to quarantine. Reset after a
cooldown period.

**7. No metrics, tracing, or structured logging**

Logging uses plain logging.info/error. No Prometheus counters, no distributed traces, no
correlation IDs. Production upgrade: Prometheus counters for webhook rate, LLM latency histogram,
quarantine rate, queue depth gauge; OpenTelemetry traces per request; structured logging with
ingestion_id and vendor in every record.

---

## Configuration

All configuration is read from environment variables at startup.

| Variable | Default | Description |
|---|---|---|
| DATABASE_URL | sqlite+aiosqlite:///./webhooks.db | SQLAlchemy async connection string |
| LLM_PROVIDER | mock | LLM backend: mock or anthropic |
| LLM_MODEL | claude-sonnet-4-20250514 | Model name passed to the provider |
| CONFIDENCE_THRESHOLD | 0.7 | Events classified below this score go to quarantine |
| ANTHROPIC_API_KEY | _(none)_ | Required when LLM_PROVIDER=anthropic |
| MAX_PAYLOAD_SIZE_BYTES | 1048576 | Reject payloads larger than this (1 MB default) |
| OUTBOX_POLL_INTERVAL_SECONDS | 2.0 | How often the relay polls for pending outbox rows |
| WORKER_CONCURRENCY | 1 | Number of concurrent pipeline workers |

---

## API Reference

### POST /webhooks/{vendor}

Accepts a webhook payload from the named vendor. Returns immediately.

Request headers:
- Content-Type: application/json (required)
- X-Event-ID: string (optional; enables strong deduplication by vendor + event ID)

Request body: any valid JSON object.

Response 202 -- accepted:
```json
{"ingestion_id": "ing_a3f1b2c4d5e6", "status": "accepted"}
```

Response 200 -- duplicate detected:
```json
{"ingestion_id": "ing_a3f1b2c4d5e6", "status": "duplicate"}
```

Response 400 -- invalid JSON, 413 -- payload too large, 415 -- wrong content type.

### GET /ingestions/{ingestion_id}

Polls the processing status of a previously accepted webhook.

Response 200:
```json
{
  "ingestion_id": "ing_a3f1b2c4d5e6",
  "vendor": "my-vendor",
  "status": "COMPLETED",
  "received_at": "2024-01-15T10:30:01.234567+00:00",
  "record_type": "SHIPMENT_UPDATE"
}
```

Possible status values: RECEIVED, PROCESSING, COMPLETED, QUARANTINED, FAILED_TERMINAL.

When status is COMPLETED, the response includes record_type (e.g., SHIPMENT_UPDATE, INVOICE).
When status is QUARANTINED, the response includes reason_code (e.g., LOW_CONFIDENCE).

Response 404 if the ingestion_id does not exist.

### GET /health

Response 200:
```json
{"status": "healthy", "db": "connected", "queue_depth": 0}
```

Response 503 if the database is unreachable.

---

## Running Tests

```bash
uv run pytest                      # all 131 tests
uv run pytest tests/unit/          # unit tests only
uv run pytest tests/integration/   # integration tests only
```

131 tests total:
- 121 unit tests (TDD -- written before implementation)
- 10 integration tests (full pipeline: HTTP -> DB -> worker -> LLM -> normalize)

Tests use the mock LLM. No API keys or external services required.

---

## LLM Integration

### Mock mode (default)

No API key required. The mock LLM uses keyword heuristics to classify payloads and returns a
configurable delay to simulate latency. Deterministic by default (seeded RNG). Suitable for
development and all tests.

```bash
uv run uvicorn src.main:app  # LLM_PROVIDER defaults to mock
```

### Anthropic mode

```bash
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... uv run uvicorn src.main:app
```

Uses claude-sonnet-4-20250514 by default. Override with LLM_MODEL.

### Adding a new provider

1. src/services/llm/protocol.py -- implement the LLMService Protocol (classify and extract);
   structural subtyping means no inheritance is required
2. src/services/llm/factory.py -- add a branch for your provider in create_llm_service()
3. src/config.py -- add any new config fields (e.g., API key, base URL)
