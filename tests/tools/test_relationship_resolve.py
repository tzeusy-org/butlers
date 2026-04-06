"""Unit tests for contact_resolve threshold decision logic."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
        "id": id,
        "first_name": "John",
        "last_name": "Smith",
        "nickname": None,
        "company": None,
        "job_title": None,
        "metadata": {},
        **kwargs,
    }


def _make_pool(exact_rows=None, partial_rows=None, extra_queries=None):
    pool = MagicMock()
    rows = []
    if exact_rows is not None:
        rows.append(exact_rows)
        if not exact_rows:
            rows.append(partial_rows or [])
    else:
        rows.append([])
        rows.append(partial_rows or [])
    rows.extend(extra_queries or [[], [], [], [], []])
    pool.fetch = AsyncMock(side_effect=rows)
    return pool


async def test_resolve_confidence_decisions():
    """NONE when no candidates; HIGH for single match; MEDIUM for tied; HIGH when gap ≥30."""
    # No matches → NONE
    pool = _make_pool(exact_rows=[], partial_rows=[])
    result = await contact_resolve(pool, "Nonexistent Person")
    assert result["confidence"] == CONFIDENCE_NONE
    assert result["contact_id"] is None
    assert result["candidates"] == []

    # Single exact match → HIGH
    pool = _make_pool(exact_rows=[_make_contact_row("uuid-1")])
    result = await contact_resolve(pool, "John Smith")
    assert result["confidence"] == CONFIDENCE_HIGH
    assert result["contact_id"] == "uuid-1"

    # Multiple equal-score matches → MEDIUM
    pool = _make_pool(exact_rows=[_make_contact_row("uuid-1"), _make_contact_row("uuid-2")])
    result = await contact_resolve(pool, "John Smith")
    assert result["confidence"] == CONFIDENCE_MEDIUM
    assert result["contact_id"] is None
    assert len(result["candidates"]) == 2

    # Large gap → HIGH
    pool = _make_pool(
        exact_rows=[_make_contact_row("uuid-1"), _make_contact_row("uuid-2")],
        extra_queries=[
            [{"id": "uuid-1", "stay_in_touch_days": 7}],
            [{"contact_id": "uuid-1", "forward_label": "spouse"}],
            [],
            [],
            [],
        ],
    )
    result = await contact_resolve(pool, "John Smith")
    assert result["confidence"] == CONFIDENCE_HIGH
    assert result["contact_id"] == "uuid-1"


async def test_resolve_result_fields():
    """Result includes inferred and salience fields; each candidate has salience."""
    pool = _make_pool(exact_rows=[_make_contact_row("uuid-1"), _make_contact_row("uuid-2")])
    result = await contact_resolve(pool, "John Smith")
    assert "inferred" in result
    for candidate in result["candidates"]:
        assert "salience" in candidate
