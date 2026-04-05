"""Shared helpers for memory butler tools."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Valid tenant IDs — "shared" + butler roster names
# ---------------------------------------------------------------------------

VALID_TENANT_IDS = frozenset(
    {
        "shared",
        "education",
        "finance",
        "general",
        "health",
        "home",
        "lifestyle",
        "messenger",
        "qa",
        "relationship",
        "switchboard",
        "travel",
    }
)


def validate_tenant_id(tenant_id: str) -> str:
    """Validate that *tenant_id* is in the allowed set.

    Returns the tenant_id unchanged on success; raises ``ValueError`` with an
    actionable message listing valid values on failure.
    """
    if tenant_id not in VALID_TENANT_IDS:
        raise ValueError(
            f"Invalid tenant_id '{tenant_id}'. Must be one of: {sorted(VALID_TENANT_IDS)}"
        )
    return tenant_id


# ---------------------------------------------------------------------------
# Load sibling modules from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent.parent


def _load_module(name: str):
    path = _MODULE_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_storage = _load_module("storage")
_search = _load_module("search")

# ---------------------------------------------------------------------------
# Embedding engine (loaded once, shared across all tool invocations)
# ---------------------------------------------------------------------------

_embedding_mod = _load_module("embedding")
EmbeddingEngine = _embedding_mod.EmbeddingEngine


def get_embedding_engine() -> Any:
    """Get or create the shared EmbeddingEngine singleton.

    Lazy-loads the model on first call.
    """
    global _embedding_engine
    if _embedding_engine is None:
        _embedding_engine = EmbeddingEngine()
    return _embedding_engine


_embedding_engine: Any = None


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------


def _serialize_row(row: dict) -> dict[str, Any]:
    """Convert a row dict to JSON-serializable format."""
    result = {}
    for key, value in row.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result
