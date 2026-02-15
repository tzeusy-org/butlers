"""Tests for the Memory butler EmbeddingEngine."""

from __future__ import annotations

import importlib.util
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load the embedding module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------
_EMBEDDING_PATH = MEMORY_MODULE_PATH / "embedding.py"


def _load_embedding_module():
    spec = importlib.util.spec_from_file_location("embedding", _EMBEDDING_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_embedding_module()
EmbeddingEngine = _mod.EmbeddingEngine

# ---------------------------------------------------------------------------
# Unit tests -- mock the model for speed
# ---------------------------------------------------------------------------


def _fake_encode(texts, **_kwargs):
    """Return deterministic fake vectors matching model output shape."""
    if isinstance(texts, str):
        return np.zeros(384, dtype=np.float32)
    return np.zeros((len(texts), 384), dtype=np.float32)


@pytest.fixture()
def engine():
    """Return an EmbeddingEngine with the model replaced by a mock."""
    with patch.object(_mod, "SentenceTransformer") as mock_cls:
        mock_model = MagicMock()
        mock_model.encode = MagicMock(side_effect=_fake_encode)
        mock_cls.return_value = mock_model
        eng = EmbeddingEngine()
        yield eng


@pytest.mark.unit
class TestEmbedUnit:
    def test_embed_returns_list_of_floats(self, engine):
        result = engine.embed("hello world")
        assert isinstance(result, list)
        assert len(result) == 384
        assert all(isinstance(v, float) for v in result)

    def test_embed_empty_string(self, engine):
        """Empty strings should not crash; they get normalised to a space."""
        result = engine.embed("")
        assert isinstance(result, list)
        assert len(result) == 384

    def test_embed_none(self, engine):
        """None should be handled gracefully."""
        result = engine.embed(None)
        assert isinstance(result, list)
        assert len(result) == 384

    def test_embed_whitespace_only(self, engine):
        result = engine.embed("   ")
        assert len(result) == 384

    def test_embed_disables_progress_bar(self, engine):
        engine.embed("hello world")
        engine._model.encode.assert_called_once_with("hello world", show_progress_bar=False)


@pytest.mark.unit
class TestEmbedBatchUnit:
    def test_batch_returns_correct_count(self, engine):
        texts = ["one", "two", "three"]
        result = engine.embed_batch(texts)
        assert len(result) == 3
        assert all(len(v) == 384 for v in result)

    def test_batch_empty_list(self, engine):
        result = engine.embed_batch([])
        assert result == []

    def test_batch_with_none_elements(self, engine):
        result = engine.embed_batch([None, "hello", ""])
        assert len(result) == 3
        assert all(len(v) == 384 for v in result)

    def test_batch_single_item(self, engine):
        result = engine.embed_batch(["solo"])
        assert len(result) == 1
        assert len(result[0]) == 384

    def test_batch_disables_progress_bar(self, engine):
        texts = ["one", "two", "three"]
        engine.embed_batch(texts)
        engine._model.encode.assert_called_once_with(texts, show_progress_bar=False)


@pytest.mark.unit
class TestDimension:
    def test_dimension_property(self, engine):
        assert engine.dimension == 384


# ---------------------------------------------------------------------------
# Additional unit edge-case tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalise:
    """Test the _normalise static method edge cases."""

    def test_normalise_none(self):
        assert EmbeddingEngine._normalise(None) == " "

    def test_normalise_empty_string(self):
        assert EmbeddingEngine._normalise("") == " "

    def test_normalise_whitespace_only(self):
        assert EmbeddingEngine._normalise("   \t\n  ") == " "

    def test_normalise_normal_text_passthrough(self):
        assert EmbeddingEngine._normalise("hello world") == "hello world"

    def test_normalise_integer_input(self):
        """Non-string, non-None inputs should be normalised to a space."""
        assert EmbeddingEngine._normalise(42) == " "

    def test_normalise_list_input(self):
        """Non-string, non-None inputs should be normalised to a space."""
        assert EmbeddingEngine._normalise([1, 2, 3]) == " "

    def test_normalise_preserves_non_empty_text(self):
        text = "a short sentence"
        assert EmbeddingEngine._normalise(text) == text


@pytest.mark.unit
class TestEmbedConsistency:
    """Test that batch embedding results are consistent with single embed."""

    def test_batch_of_one_matches_single_embed(self, engine):
        """embed_batch(['text']) should produce same result as [embed('text')]."""
        single = engine.embed("hello")
        batch = engine.embed_batch(["hello"])
        assert len(batch) == 1
        assert len(single) == len(batch[0])
        # Both should be lists of floats with the same length
        assert all(isinstance(v, float) for v in batch[0])

    def test_batch_preserves_order(self, engine):
        """Results from embed_batch should maintain input order."""
        texts = ["alpha", "beta", "gamma"]
        result = engine.embed_batch(texts)
        assert len(result) == 3
        # Each result should be a 384-dim vector
        for vec in result:
            assert len(vec) == 384

    def test_embed_long_text(self, engine):
        """Very long text should still produce a 384-dim vector."""
        long_text = "word " * 10000
        result = engine.embed(long_text)
        assert len(result) == 384
        assert all(isinstance(v, float) for v in result)

    def test_embed_unicode_text(self, engine):
        """Unicode text should be handled without error."""
        result = engine.embed("cafe\u0301 \u00fc\u00f1\u00ee\u00e7\u00f6\u00f0\u00e9")
        assert len(result) == 384

    def test_embed_batch_large(self, engine):
        """A larger batch should work correctly."""
        texts = [f"text number {i}" for i in range(50)]
        result = engine.embed_batch(texts)
        assert len(result) == 50
        assert all(len(v) == 384 for v in result)


@pytest.mark.unit
class TestModelInit:
    """Test EmbeddingEngine initialization."""

    def test_default_model_name(self):
        """Default model name should be all-MiniLM-L6-v2."""
        assert _mod._MODEL_NAME == "all-MiniLM-L6-v2"

    def test_default_embedding_dim(self):
        """Default embedding dimension should be 384."""
        assert _mod._EMBEDDING_DIM == 384

    def test_custom_model_name_passed_to_transformer(self):
        """EmbeddingEngine should pass custom model name to SentenceTransformer."""
        with patch.object(_mod, "SentenceTransformer") as mock_cls:
            mock_cls.return_value = MagicMock()
            EmbeddingEngine(model_name="custom-model")
            mock_cls.assert_called_once_with("custom-model")


# ---------------------------------------------------------------------------
# Integration tests -- load the real model (slow, needs download)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEmbedIntegration:
    @pytest.fixture(scope="class")
    def real_engine(self):
        """Load the real model once per test class."""
        return EmbeddingEngine()

    def test_real_embed_dimension(self, real_engine):
        vec = real_engine.embed("The quick brown fox jumps over the lazy dog.")
        assert len(vec) == 384
        assert all(isinstance(v, float) for v in vec)

    def test_real_embed_different_texts_differ(self, real_engine):
        v1 = real_engine.embed("cats")
        v2 = real_engine.embed("quantum mechanics")
        assert v1 != v2

    def test_real_embed_batch(self, real_engine):
        vecs = real_engine.embed_batch(["alpha", "beta", "gamma"])
        assert len(vecs) == 3
        assert all(len(v) == 384 for v in vecs)

    def test_real_embed_empty_string(self, real_engine):
        vec = real_engine.embed("")
        assert len(vec) == 384
