"""Unit tests for contact_resolve threshold decision logic.

Tests key confidence-level decisions:
- HIGH: single candidate OR top leads by ≥30 points
- MEDIUM: multiple candidates within 30-point gap
- NONE: no matches found

Entity resolution integration and salience fields are also verified.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

RESOLVE_PATH = Path(__file__).parent.parent.parent / "roster/relationship/tools/resolve.py"


def _load_resolve_module():
    spec = importlib.util.spec_from_file_location("resolve", RESOLVE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["resolve"] = mod
    spec.loader.exec_module(mod)
    return mod


resolve_mod = _load_resolve_module()
contact_resolve = resolve_mod.contact_resolve
CONFIDENCE_HIGH = resolve_mod.CONFIDENCE_HIGH
CONFIDENCE_MEDIUM = resolve_mod.CONFIDENCE_MEDIUM
CONFIDENCE_NONE = resolve_mod.CONFIDENCE_NONE

pytestmark = pytest.mark.unit


def _make_contact_row(id: str, **kwargs) -> dict:
    return {
        "id": id, "first_name": "John", "last_name": "Smith",
        "nickname": None, "company": None, "job_title": None, "metadata": {},
        **kwargs,
    }


def _make_pool(exact_rows=None, partial_rows=None, extra_queries=None):
    """Build a mock pool with configurable query responses."""
    pool = MagicMock()
    # The order of fetch calls: exact, [partial if no exact], salience queries
    rows = []
    if exact_rows is not None:
        rows.append(exact_rows)
        if not exact_rows:
            rows.append(partial_rows or [])
    else:
        rows.append([])
        rows.append(partial_rows or [])
    # Salience/scoring sub-queries (stay_in_touch, relationships, interactions, etc.)
    rows.extend(extra_queries or [[], [], [], [], []])
    pool.fetch = AsyncMock(side_effect=rows)
    return pool


# ---------------------------------------------------------------------------
# Confidence level decisions
# ---------------------------------------------------------------------------


async def test_no_matches_returns_none_confidence():
    """contact_resolve returns NONE confidence when no candidates found."""
    pool = _make_pool(exact_rows=[], partial_rows=[], extra_queries=[[], [], [], [], []])
    result = await contact_resolve(pool, "Nonexistent Person")
    assert result["confidence"] == CONFIDENCE_NONE
    assert result["contact_id"] is None
    assert result["candidates"] == []


async def test_single_exact_match_returns_high_confidence():
    """A single exact match returns HIGH confidence with the contact selected."""
    pool = _make_pool(
        exact_rows=[_make_contact_row("uuid-1")],
        extra_queries=[[], [], [], [], []],
    )
    result = await contact_resolve(pool, "John Smith")
    assert result["confidence"] == CONFIDENCE_HIGH
    assert result["contact_id"] == "uuid-1"


async def test_multiple_exact_same_score_returns_medium():
    """Multiple exact matches with equal scores return MEDIUM confidence."""
    pool = _make_pool(
        exact_rows=[_make_contact_row("uuid-1"), _make_contact_row("uuid-2")],
        extra_queries=[[], [], [], [], []],
    )
    result = await contact_resolve(pool, "John Smith")
    assert result["confidence"] == CONFIDENCE_MEDIUM
    assert result["contact_id"] is None
    assert len(result["candidates"]) == 2


async def test_multiple_matches_large_gap_returns_high():
    """Multiple exact matches with top leading by ≥30 points returns HIGH confidence."""
    pool = _make_pool(
        exact_rows=[_make_contact_row("uuid-1"), _make_contact_row("uuid-2")],
        extra_queries=[
            [{"id": "uuid-1", "stay_in_touch_days": 7}],  # +10 points for uuid-1
            [{"contact_id": "uuid-1", "forward_label": "spouse"}],  # +50 points for uuid-1
            [],  # interactions
            [],  # facts
            [],  # groups
        ],
    )
    result = await contact_resolve(pool, "John Smith")
    # uuid-1 should dominate with 60+ point gap
    assert result["confidence"] == CONFIDENCE_HIGH
    assert result["contact_id"] == "uuid-1"


# ---------------------------------------------------------------------------
# Inferred fields contract
# ---------------------------------------------------------------------------


async def test_result_includes_inferred_field():
    """contact_resolve result includes inferred boolean field."""
    pool = _make_pool(exact_rows=[_make_contact_row("uuid-1")], extra_queries=[[], [], [], [], []])
    result = await contact_resolve(pool, "John Smith")
    assert "inferred" in result


async def test_candidates_include_salience_field():
    """Each candidate in result includes salience score."""
    pool = _make_pool(
        exact_rows=[_make_contact_row("uuid-1"), _make_contact_row("uuid-2")],
        extra_queries=[[], [], [], [], []],
    )
    result = await contact_resolve(pool, "John Smith")
    for candidate in result["candidates"]:
        assert "salience" in candidate
