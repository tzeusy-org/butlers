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
    The public ``EmbeddingEngine`` interface (``embed``, ``embed_batch``, and
    ``model_name``) is stubbed so that callers exercising embedding-aware code
    paths receive well-typed return values instead of bare ``MagicMock`` objects.

    Args:
        model_name: The model name the mock should claim.  Pass the same value
            as ``MemoryModuleConfig.embedding_model`` (default: "all-MiniLM-L6-v2").

    Returns:
        A ``MagicMock`` with ``_model_name`` set and the public
        ``EmbeddingEngine`` interface stubbed.
    """
    engine = MagicMock(name=f"embedding-engine[{model_name}]")
    engine._model_name = model_name
    # Mirror the public EmbeddingEngine interface used by storage.py.
    engine.model_name = model_name
    engine.embed.return_value = [0.0] * 384
    engine.embed_batch.side_effect = lambda texts: [[0.0] * 384 for _ in texts]
    return engine
