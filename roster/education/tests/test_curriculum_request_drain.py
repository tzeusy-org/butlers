"""Regression guard for the curriculum-request wiring (bu-99iek).

The dashboard's "Request curriculum" button POSTs to
``/api/education/curriculum-requests``, which stores the request under the
``pending_curriculum_request`` state key. For a long time *nothing* consumed
that key — no schedule, job, or skill — so the success toast lied and the
new-user first action was a silent no-op.

The original fix polled the key every 5 minutes via a ``drain-curriculum-request``
schedule, which burned a full ephemeral session each tick even when nothing was
pending. That polling was replaced with an **event-driven trigger**: the endpoint
spawns an education session immediately (via the butler's ``trigger`` MCP tool),
which starts the curriculum and clears the key. See ``submit_curriculum_request``
and ``_trigger_curriculum_drain`` in ``roster/education/api/router.py``.

These tests assert that the wiring stays coherent so the no-op regression cannot
silently return:
- the polling schedule must NOT come back (it was the token-burn we removed);
- the endpoint must trigger a session and still own the exact state key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import load_config

pytestmark = pytest.mark.unit

# roster/education/tests/test_*.py -> parents[3] == repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EDUCATION_CONFIG_DIR = _REPO_ROOT / "roster" / "education"

# Must match roster/education/api/router.py::_CURRICULUM_REQUEST_KEY
_CURRICULUM_REQUEST_KEY = "pending_curriculum_request"
_DRAIN_SCHEDULE_NAME = "drain-curriculum-request"

_ROUTER_SRC = (_EDUCATION_CONFIG_DIR / "api" / "router.py").read_text()


def test_no_polling_drain_schedule():
    """The 5-minute polling schedule must stay gone — it spawned a session every
    tick even when nothing was pending (the token burn we removed)."""
    cfg = load_config(_EDUCATION_CONFIG_DIR)
    matches = [s for s in cfg.schedules if s.name == _DRAIN_SCHEDULE_NAME]
    assert not matches, (
        f"{_DRAIN_SCHEDULE_NAME!r} schedule is back in roster/education/butler.toml — "
        "curriculum requests are now event-driven (triggered on submit); a polling "
        "drain re-introduces the per-tick token burn."
    )


def test_endpoint_triggers_a_session():
    """Submitting a request must trigger an ephemeral education session via the
    butler's `trigger` MCP tool — not just write a key and hope something polls it."""
    assert "get_mcp_manager" in _ROUTER_SRC, (
        "router.py must depend on the MCP manager to trigger a session on submit."
    )
    assert '"trigger"' in _ROUTER_SRC, (
        "router.py must call the butler's `trigger` MCP tool to start the curriculum "
        "immediately; otherwise the request is written but never acted on (no-op toast)."
    )
    assert "teaching_flow_start" in _ROUTER_SRC, (
        "the triggered session prompt must call teaching_flow_start to actually create "
        "the mind map and begin the curriculum — otherwise the success toast still lies."
    )


def test_trigger_clears_request_key_on_failure():
    """If the session cannot be spawned, the endpoint must clear the lock so the user
    is not wedged behind a permanent 409 (there is no polling fallback to retry)."""
    assert "state_delete" in _ROUTER_SRC, (
        "router.py must clear the pending key via state_delete when the trigger fails; "
        "without the old polling drain there is no other path to release the 409 guard."
    )


def test_endpoint_state_key_is_stable():
    """Guard against the endpoint's key drifting from the documented contract."""
    assert f'_CURRICULUM_REQUEST_KEY = "{_CURRICULUM_REQUEST_KEY}"' in _ROUTER_SRC, (
        "router.py _CURRICULUM_REQUEST_KEY drifted from the documented key; the trigger "
        "prompt and the 409 guard both depend on this exact value."
    )
    # The triggered session prompt clears the same key it guards on.
    assert _CURRICULUM_REQUEST_KEY in _ROUTER_SRC
