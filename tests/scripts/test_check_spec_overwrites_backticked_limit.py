"""The 0.85 coverage threshold misses a dropped backticked list item (bu-flpugx, PR #4654).

This pins a documented limitation, not a desired behaviour: dropping 2 of the 15 backticked field
names of a long THEN list still reads as carried forward.  If the checker later gains a
token-survival rule this test fails and should be replaced by its inverse.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "check_spec_overwrites_limit",
    Path(__file__).resolve().parents[2] / "scripts" / "check_spec_overwrites.py",
)
cso = importlib.util.module_from_spec(spec)
sys.modules["check_spec_overwrites_limit"] = cso
spec.loader.exec_module(cso)

_FIELDS = (
    "id, conversation_id, role, content, created_at, session_id, model_name, input_tokens, "
    "output_tokens, duration_ms, tool_calls, error, request_id, page_context, captured_at"
)
BASELINE = "- **THEN** each entry includes: " + ", ".join(f"`{f}`" for f in _FIELDS.split(", "))
BLOCK = "- **THEN** each entry includes: " + ", ".join(
    f"`{f}`" for f in _FIELDS.split(", ") if f not in {"page_context", "captured_at"}
)


def test_fixture_matches_the_4654_shape() -> None:
    assert BASELINE.count("`") // 2 == 15
    assert BLOCK.count("`") // 2 == 13


def test_two_dropped_of_fifteen_backticked_names_still_score_above_the_threshold() -> None:
    covered, contiguous = cso.survival(cso.normalize_clause(BASELINE), cso.normalize_clause(BLOCK))
    assert cso.COVERAGE_THRESHOLD <= covered < 0.9
    assert contiguous >= cso.CONTIGUITY_THRESHOLD


def test_the_dropped_names_are_reported_as_carried_forward_today() -> None:
    """Known limitation: no loss is reported although `page_context` and `captured_at` vanished."""
    assert cso.dropped_clauses([BASELINE], [BLOCK]) == []
