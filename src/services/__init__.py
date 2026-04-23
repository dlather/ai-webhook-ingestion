# pyright: reportMissingImports=false, reportUnknownVariableType=false
"""Services package."""

from src.services.dedup import (
    DeduplicationService,
    canonicalize_json,
    compute_payload_hash,
    derive_strong_dedupe_key,
    derive_weak_dedupe_key,
)

__all__ = [
    "canonicalize_json",
    "compute_payload_hash",
    "derive_strong_dedupe_key",
    "derive_weak_dedupe_key",
    "DeduplicationService",
]
