## 2026-04-23
- FastAPI package imports can expose a new router module directly via `from src.api import health` once the module exists.
- File-level pyright config comments are a quick way to keep async SQLAlchemy/httpx tests clean when the type checker has limited inference.
- Consolidating LLM classify/extract failures to the pipeline outer safety net removes redundant broad exception handlers without changing terminal failure behavior.
