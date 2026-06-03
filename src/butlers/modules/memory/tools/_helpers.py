"""Shared helpers for memory butler tools."""

from __future__ import annotations

import importlib.util
import threading
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
# Embedding engine (loaded once per model name, shared across all tool invocations)
# ---------------------------------------------------------------------------

_embedding_mod = _load_module("embedding")
EmbeddingEngine = _embedding_mod.EmbeddingEngine

_DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Singleton cache keyed by model name.  A new entry is created whenever a
# different model name is requested for the first time.  Changing the
# embedding_model config field produces a fresh engine (and therefore a fresh
# model load) without evicting engines still in use by other callers.
_embedding_engines: dict[str, Any] = {}
_embedding_engines_lock = threading.Lock()


def get_embedding_engine(model_name: str = _DEFAULT_EMBEDDING_MODEL) -> Any:
    """Get or create the shared EmbeddingEngine singleton for *model_name*.

    Engines are cached by model name.  Requesting a new model name produces a
    fresh ``EmbeddingEngine`` instance; subsequent calls with the same name
    return the cached instance.

    Args:
        model_name: Sentence-transformers model identifier (default
            ``"all-MiniLM-L6-v2"``).  Matches the default of
            ``MemoryModuleConfig.embedding_model``.

    Returns:
        The ``EmbeddingEngine`` instance for the requested model.
    """
    with _embedding_engines_lock:
        if model_name not in _embedding_engines:
            _embedding_engines[model_name] = EmbeddingEngine(model_name)
        return _embedding_engines[model_name]


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
