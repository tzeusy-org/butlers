"""Regression guard for the curriculum-request drain wiring (bu-99iek).

The dashboard's "Request curriculum" button POSTs to
``/api/education/curriculum-requests``, which stores the request under the
``pending_curriculum_request`` state key. For a long time *nothing* consumed
that key — no schedule, job, or skill — so the success toast lied and the
new-user first action was a silent no-op.

The fix is a scheduled ``drain-curriculum-request`` prompt task in
``roster/education/butler.toml`` that reads the key, calls
``teaching_flow_start`` to actually start the curriculum, then clears the key.

These tests assert that the drain wiring exists and is coherent so the no-op
regression cannot silently return (e.g. if someone deletes the schedule, or the
endpoint's state key drifts from the one the drain consumes).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import ScheduleDispatchMode, load_config

pytestmark = pytest.mark.unit

# roster/education/tests/test_*.py -> parents[3] == repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EDUCATION_CONFIG_DIR = _REPO_ROOT / "roster" / "education"

# Must match roster/education/api/router.py::_CURRICULUM_REQUEST_KEY
_CURRICULUM_REQUEST_KEY = "pending_curriculum_request"
_DRAIN_SCHEDULE_NAME = "drain-curriculum-request"


def _drain_schedule():
    cfg = load_config(_EDUCATION_CONFIG_DIR)
    matches = [s for s in cfg.schedules if s.name == _DRAIN_SCHEDULE_NAME]
    assert matches, (
        f"No {_DRAIN_SCHEDULE_NAME!r} schedule found in roster/education/butler.toml — "
        "the curriculum-request key would never be drained (no-op regression)."
    )
    assert len(matches) == 1, f"Duplicate {_DRAIN_SCHEDULE_NAME!r} schedule entries"
    return matches[0]


def test_drain_schedule_exists_and_is_a_prompt_task():
    """A drain task must exist and run as an ephemeral prompt session (not a job)."""
    drain = _drain_schedule()
    assert drain.dispatch_mode is ScheduleDispatchMode.PROMPT, (
        "Drain must be a prompt task so the LLM can call teaching_flow_start + "
        "state_delete; a job dispatch cannot run those MCP tools."
    )
    assert drain.prompt is not None and drain.prompt.strip(), (
        "Drain prompt must be non-empty — it carries the entire drain logic."
    )


def test_drain_prompt_consumes_the_request_key():
    """The drain prompt must read AND clear the exact state key the endpoint writes."""
    drain = _drain_schedule()
    prompt = drain.prompt or ""

    # Reads the key the endpoint wrote.
    assert _CURRICULUM_REQUEST_KEY in prompt, (
        f"Drain prompt does not reference state key {_CURRICULUM_REQUEST_KEY!r}; "
        "it would drain the wrong key and leave requests stuck pending."
    )
    assert "state_get" in prompt, "Drain prompt must read the pending request via state_get"
    # Clears the key so the one-pending-at-a-time 409 guard releases.
    assert "state_delete" in prompt, (
        "Drain prompt must clear the key via state_delete, otherwise the request is "
        "stuck pending forever and the endpoint keeps returning 409."
    )


def test_drain_prompt_actually_starts_the_curriculum():
    """The drain must take real action, not just acknowledge — call teaching_flow_start."""
    drain = _drain_schedule()
    prompt = drain.prompt or ""
    assert "teaching_flow_start" in prompt, (
        "Drain prompt must call teaching_flow_start to actually create the mind map "
        "and begin the curriculum — otherwise the success toast still lies."
    )


def test_endpoint_state_key_matches_drain_key():
    """Guard against the endpoint's key drifting from the drained key."""
    router_src = (_EDUCATION_CONFIG_DIR / "api" / "router.py").read_text()
    assert f'_CURRICULUM_REQUEST_KEY = "{_CURRICULUM_REQUEST_KEY}"' in router_src, (
        "router.py _CURRICULUM_REQUEST_KEY drifted from the key the drain consumes; "
        "update both the endpoint and the drain schedule together."
    )
