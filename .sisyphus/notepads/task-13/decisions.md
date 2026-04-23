## 2026-04-23
- Implemented only `/health` and `/ingestions/{ingestion_id}` as requested; no readiness or metrics endpoints were added.
- `/health` returns `503` when database connectivity fails and includes queue depth on success.
- `/ingestions/{ingestion_id}` returns a compact JSON payload with `ingestion_id`, `vendor`, `status`, and ISO-formatted `received_at`.
- Kept broad exception boundaries only at the worker loop and pipeline entrypoint; webhook queue notification and worker _process_one now rely on existing outer boundaries instead of local catch-alls.
