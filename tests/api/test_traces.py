"""Tests for span tree assembly and trace status logic.

The /api/traces endpoints have been removed and replaced by /api/ingestion/events.
This file retains the pure unit tests for assemble_span_tree and
_determine_trace_status helper functions which live in the (now route-less)
traces module.

Issues: butlers-26h.7.1, 7.2, 7.3, 7.4
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from butlers.api.routers.traces import (
    _determine_trace_status,
    assemble_span_tree,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_TRACE_ID = "trace-abc-123"


def _make_session_row(
    *,
    session_id=None,
    prompt="test prompt",
    trigger_source="schedule",
    success=True,
    started_at=None,
    completed_at=None,
    duration_ms=1000,
    model="claude-opus-4-20250514",
    input_tokens=100,
    output_tokens=200,
    parent_session_id=None,
    trace_id=_TRACE_ID,
):
    """Create a dict mimicking an asyncpg Record for session columns."""
    return {
        "id": session_id or uuid4(),
        "prompt": prompt,
        "trigger_source": trigger_source,
        "success": success,
        "started_at": started_at or _NOW,
        "completed_at": completed_at or _NOW,
        "duration_ms": duration_ms,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "parent_session_id": parent_session_id,
        "trace_id": trace_id,
    }


# ---------------------------------------------------------------------------
# Unit tests: assemble_span_tree
# ---------------------------------------------------------------------------


class TestAssembleSpanTree:
    def test_single_root_span(self):
        """A single session with no parent should be a lone root."""
        sid = uuid4()
        sessions = [
            {
                "id": sid,
                "butler": "atlas",
                "prompt": "root task",
                "trigger_source": "schedule",
                "success": True,
                "started_at": _NOW,
                "completed_at": _NOW,
                "duration_ms": 500,
                "model": "claude-opus-4-20250514",
                "input_tokens": 100,
                "output_tokens": 50,
                "parent_session_id": None,
            }
        ]
        roots = assemble_span_tree(sessions)
        assert len(roots) == 1
        assert roots[0].id == sid
        assert roots[0].butler == "atlas"
        assert roots[0].children == []

    def test_parent_child_nesting(self):
        """Children should be nested under their parent."""
        parent_id = uuid4()
        child1_id = uuid4()
        child2_id = uuid4()

        sessions = [
            {
                "id": parent_id,
                "butler": "atlas",
                "prompt": "parent",
                "trigger_source": "schedule",
                "success": True,
                "started_at": _NOW,
                "completed_at": _NOW + timedelta(seconds=10),
                "duration_ms": 10000,
                "model": None,
                "input_tokens": None,
                "output_tokens": None,
                "parent_session_id": None,
            },
            {
                "id": child1_id,
                "butler": "switchboard",
                "prompt": "child 1",
                "trigger_source": "mcp",
                "success": True,
                "started_at": _NOW + timedelta(seconds=1),
                "completed_at": _NOW + timedelta(seconds=3),
                "duration_ms": 2000,
                "model": None,
                "input_tokens": None,
                "output_tokens": None,
                "parent_session_id": parent_id,
            },
            {
                "id": child2_id,
                "butler": "atlas",
                "prompt": "child 2",
                "trigger_source": "mcp",
                "success": True,
                "started_at": _NOW + timedelta(seconds=4),
                "completed_at": _NOW + timedelta(seconds=6),
                "duration_ms": 2000,
                "model": None,
                "input_tokens": None,
                "output_tokens": None,
                "parent_session_id": parent_id,
            },
        ]

        roots = assemble_span_tree(sessions)
        assert len(roots) == 1
        root = roots[0]
        assert root.id == parent_id
        assert len(root.children) == 2
        assert root.children[0].id == child1_id
        assert root.children[1].id == child2_id

    def test_children_sorted_by_started_at(self):
        """Children should be sorted by started_at, not insertion order."""
        parent_id = uuid4()
        early_id = uuid4()
        late_id = uuid4()

        sessions = [
            {
                "id": parent_id,
                "butler": "atlas",
                "prompt": "parent",
                "trigger_source": "schedule",
                "started_at": _NOW,
                "parent_session_id": None,
            },
            {
                "id": late_id,
                "butler": "atlas",
                "prompt": "late child",
                "trigger_source": "mcp",
                "started_at": _NOW + timedelta(seconds=10),
                "parent_session_id": parent_id,
            },
            {
                "id": early_id,
                "butler": "switchboard",
                "prompt": "early child",
                "trigger_source": "mcp",
                "started_at": _NOW + timedelta(seconds=1),
                "parent_session_id": parent_id,
            },
        ]

        roots = assemble_span_tree(sessions)
        assert len(roots) == 1
        children = roots[0].children
        assert len(children) == 2
        assert children[0].id == early_id
        assert children[1].id == late_id

    def test_deep_nesting(self):
        """Grandchildren should also be properly nested."""
        root_id = uuid4()
        child_id = uuid4()
        grandchild_id = uuid4()

        sessions = [
            {
                "id": root_id,
                "butler": "atlas",
                "prompt": "root",
                "trigger_source": "schedule",
                "started_at": _NOW,
                "parent_session_id": None,
            },
            {
                "id": child_id,
                "butler": "switchboard",
                "prompt": "child",
                "trigger_source": "mcp",
                "started_at": _NOW + timedelta(seconds=1),
                "parent_session_id": root_id,
            },
            {
                "id": grandchild_id,
                "butler": "atlas",
                "prompt": "grandchild",
                "trigger_source": "mcp",
                "started_at": _NOW + timedelta(seconds=2),
                "parent_session_id": child_id,
            },
        ]

        roots = assemble_span_tree(sessions)
        assert len(roots) == 1
        assert roots[0].id == root_id
        assert len(roots[0].children) == 1
        assert roots[0].children[0].id == child_id
        assert len(roots[0].children[0].children) == 1
        assert roots[0].children[0].children[0].id == grandchild_id

    def test_orphan_becomes_root(self):
        """A session whose parent is not in the trace should become a root."""
        orphan_id = uuid4()
        missing_parent_id = uuid4()

        sessions = [
            {
                "id": orphan_id,
                "butler": "atlas",
                "prompt": "orphan",
                "trigger_source": "mcp",
                "started_at": _NOW,
                "parent_session_id": missing_parent_id,
            },
        ]

        roots = assemble_span_tree(sessions)
        assert len(roots) == 1
        assert roots[0].id == orphan_id

    def test_empty_sessions(self):
        """Empty session list should return empty roots."""
        roots = assemble_span_tree([])
        assert roots == []

    def test_multiple_roots(self):
        """Multiple sessions with no parent should all be root spans."""
        root1_id = uuid4()
        root2_id = uuid4()

        sessions = [
            {
                "id": root1_id,
                "butler": "atlas",
                "prompt": "root 1",
                "trigger_source": "schedule",
                "started_at": _NOW + timedelta(seconds=5),
                "parent_session_id": None,
            },
            {
                "id": root2_id,
                "butler": "switchboard",
                "prompt": "root 2",
                "trigger_source": "schedule",
                "started_at": _NOW,
                "parent_session_id": None,
            },
        ]

        roots = assemble_span_tree(sessions)
        assert len(roots) == 2
        # Sorted by started_at
        assert roots[0].id == root2_id
        assert roots[1].id == root1_id


# ---------------------------------------------------------------------------
# Unit tests: _determine_trace_status
# ---------------------------------------------------------------------------


class TestDetermineTraceStatus:
    def test_all_success(self):
        spans = [{"success": True}, {"success": True}]
        assert _determine_trace_status(spans) == "success"

    def test_all_failed(self):
        spans = [{"success": False}, {"success": False}]
        assert _determine_trace_status(spans) == "failed"

    def test_mixed_partial(self):
        spans = [{"success": True}, {"success": False}]
        assert _determine_trace_status(spans) == "partial"

    def test_all_running(self):
        spans = [{"success": None}, {"success": None}]
        assert _determine_trace_status(spans) == "running"

    def test_some_running_no_failure(self):
        spans = [{"success": True}, {"success": None}]
        assert _determine_trace_status(spans) == "running"

    def test_some_running_with_failure(self):
        spans = [{"success": False}, {"success": None}]
        assert _determine_trace_status(spans) == "partial"

    def test_empty_spans(self):
        assert _determine_trace_status([]) == "running"
