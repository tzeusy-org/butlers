"""Unit tests for contact_resolve threshold decision logic."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from butlers.testing.fake_pool import fake_pool

RESOLVE_PATH = Path(__file__).parent.parent.parent / "roster/relationship/tools/resolve.py"


def _load_resolve_module():
    mod = importlib.import_module("butlers.tools.relationship.resolve")
    assert mod.__spec__ is not None and mod.__spec__.loader is not None
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


async def test_resolve_confidence_decisions():
    """NONE when no candidates; HIGH for single match; MEDIUM for tied; HIGH when gap ≥30."""
    # No matches → NONE
    # fetch order: exact match, partial match (only after no exact), then salience queries.
    pool = fake_pool(fetch=AsyncMock(side_effect=[[], [], [], [], [], [], []]))
    result = await contact_resolve(pool, "Nonexistent Person")
    assert result["confidence"] == CONFIDENCE_NONE
    assert result["contact_id"] is None
    assert result["candidates"] == []

    # Single exact match → HIGH
    pool = fake_pool(
        fetch=AsyncMock(side_effect=[[_make_contact_row("uuid-1")], [], [], [], [], []])
    )
    result = await contact_resolve(pool, "John Smith")
    assert result["confidence"] == CONFIDENCE_HIGH
    assert result["contact_id"] == "uuid-1"

    # Multiple equal-score matches → MEDIUM
    tied = [_make_contact_row("uuid-1"), _make_contact_row("uuid-2")]
    pool = fake_pool(fetch=AsyncMock(side_effect=[tied, [], [], [], [], []]))
    result = await contact_resolve(pool, "John Smith")
    assert result["confidence"] == CONFIDENCE_MEDIUM
    assert result["contact_id"] is None
    assert len(result["candidates"]) == 2

    # Large gap → HIGH
    pool = fake_pool(
        fetch=AsyncMock(
            side_effect=[
                [_make_contact_row("uuid-1"), _make_contact_row("uuid-2")],
                [{"id": "uuid-1", "stay_in_touch_days": 7}],
                [{"contact_id": "uuid-1", "forward_label": "spouse"}],
                [],
                [],
                [],
            ]
        )
    )
    result = await contact_resolve(pool, "John Smith")
    assert result["confidence"] == CONFIDENCE_HIGH
    assert result["contact_id"] == "uuid-1"


async def test_resolve_result_fields():
    """Result includes inferred and salience fields; each candidate has salience."""
    tied = [_make_contact_row("uuid-1"), _make_contact_row("uuid-2")]
    pool = fake_pool(fetch=AsyncMock(side_effect=[tied, [], [], [], [], []]))
    result = await contact_resolve(pool, "John Smith")
    assert "inferred" in result
    for candidate in result["candidates"]:
        assert "salience" in candidate
