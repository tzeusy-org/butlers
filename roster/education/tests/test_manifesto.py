"""Contract tests for the Education butler's governing identity document."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MANIFESTO = Path(__file__).resolve().parents[1] / "MANIFESTO.md"


def test_manifesto_preserves_source_grounding_commitment_and_boundaries() -> None:
    """REQ-butler-education-007: source grounding stays scoped and conversation-first."""
    manifesto = " ".join(_MANIFESTO.read_text(encoding="utf-8").lower().split())

    assert "source material is owner-provided or model-recalled" in manifesto
    assert "never autonomously fetch or scrape external references" in manifesto
    assert "cite sources and suggest reading pathways" in manifesto
    assert "evidence-based teaching techniques" in manifesto
    assert "make those choices transparent" in manifesto

    for preserved_boundary in (
        "a video platform",
        "a classroom tool",
        "a certification authority",
        "an lms integration",
    ):
        assert preserved_boundary in manifesto
