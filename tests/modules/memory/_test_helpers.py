"""Test helpers for memory module tests."""

from pathlib import Path
from unittest.mock import MagicMock

# Base path to memory module source files
MEMORY_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "src" / "butlers" / "modules" / "memory"
)


def make_embedding_engine_mock(model_name: str = "all-MiniLM-L6-v2") -> MagicMock:
    """Return a MagicMock that passes ``MemoryModule._get_embedding_engine()``'s guard.

    ``_get_embedding_engine()`` checks ``engine._model_name`` against the
    configured embedding model.  When they differ it discards the cached engine
    and constructs the *real* sentence-transformers model, which triggers a
    HuggingFace download and HTTP 429 in CI.

    This factory always sets ``_model_name`` to the supplied ``model_name`` so
    that the guard accepts the mock and never falls through to the real engine.
    The ``encode`` method is stubbed to return a deterministic fake vector of 384
    floats (matching the all-MiniLM-L6-v2 dimension) so callers that pass the
    engine to embedding-aware functions don't error on missing methods.

    Args:
        model_name: The model name the mock should claim.  Pass the same value
            as ``MemoryModuleConfig.embedding_model`` (default: "all-MiniLM-L6-v2").

    Returns:
        A ``MagicMock`` with ``_model_name`` set and ``encode`` stubbed.
    """
    engine = MagicMock(name=f"embedding-engine[{model_name}]")
    engine._model_name = model_name
    # Stub encode to return a deterministic zero-vector of the standard dimension.
    engine.encode.return_value = [0.0] * 384
    return engine
