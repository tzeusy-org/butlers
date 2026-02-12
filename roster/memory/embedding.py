"""Embedding engine for the Memory butler.

Wraps sentence-transformers to provide 384-dimensional embeddings
using the all-MiniLM-L6-v2 model.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

_MODEL_NAME = "all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384


class EmbeddingEngine:
    """Loads all-MiniLM-L6-v2 at init and holds it for the process lifetime.

    Provides ``embed`` (single text) and ``embed_batch`` (multiple texts)
    methods that return 384-dimensional float vectors.
    """

    def __init__(self, model_name: str = _MODEL_NAME) -> None:
        self._model = SentenceTransformer(model_name)
        self._dim = _EMBEDDING_DIM

    @property
    def dimension(self) -> int:
        """Return the embedding dimensionality (384)."""
        return self._dim

    def embed(self, text: str) -> list[float]:
        """Embed a single text string into a 384-dimensional vector.

        Args:
            text: The text to embed.  ``None`` and empty strings are
                  normalised to a single space before encoding so that
                  the model always returns a valid fixed-length vector.

        Returns:
            A list of 384 floats.
        """
        text = self._normalise(text)
        vec = self._model.encode(text, show_progress_bar=False)
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in one call for better throughput.

        Args:
            texts: A list of strings to embed.  Each element is individually
                   normalised (see :meth:`embed`).

        Returns:
            A list of 384-dimensional float vectors, one per input text.
        """
        if not texts:
            return []
        normalised = [self._normalise(t) for t in texts]
        vecs = self._model.encode(normalised, show_progress_bar=False)
        return [v.tolist() for v in vecs]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(text: str | None) -> str:
        """Ensure *text* is a non-empty string the model can encode."""
        if text is None or not isinstance(text, str) or text.strip() == "":
            return " "
        return text
