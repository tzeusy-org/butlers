"""Wiring guards for the curriculum-request spine (bu-99iek, bu-6jv4m.10).

The dashboard's "Request curriculum" button POSTs to
``/api/education/curriculum-requests``. Two regressions have already been paid
for on this seam and neither may silently return:

1. **The no-op toast** (bu-99iek). The endpoint wrote a
   ``pending_curriculum_request`` KV key that *nothing* consumed — no schedule,
   job, or skill — so the success toast lied. The first fix polled the key every
   5 minutes, burning a full ephemeral session per tick; that polling was
   replaced with an event-driven trigger fired from the endpoint itself.

2. **The lock with no owner** (bu-6jv4m.10). The event-driven fix still left the
   *lifecycle* ownerless: a KV lock that the triggered LLM session had to
   remember to ``state_delete``, a detached trigger whose failure was logged and
   swallowed, and no durable status for the owner to read. A crashed API process
   stranded the lock behind a permanent 409. That lifecycle now lives in
   ``education.curriculum_requests`` (migration ``education_004``), and the
   backend — not the session — owns the guard.

These tests assert the wiring stays coherent. They read the router source
because what is being guarded is *which components own the lifecycle*, which no
runtime assertion on a single request can show. Behaviour is covered by
``roster/education/tests/test_api.py`` and
``tests/config/test_education_curriculum_receipt_db.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import load_config

pytestmark = pytest.mark.unit

# roster/education/tests/test_*.py -> parents[3] == repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EDUCATION_CONFIG_DIR = _REPO_ROOT / "roster" / "education"

_DRAIN_SCHEDULE_NAME = "drain-curriculum-request"

# Must match roster/education/migrations/004_curriculum_request_receipts.py
_RECEIPT_TABLE = "education.curriculum_requests"
_PENDING_GUARD_INDEX = "uq_curriculum_requests_one_open"

_ROUTER_SRC = (_EDUCATION_CONFIG_DIR / "api" / "router.py").read_text()
_MIGRATION_SRC = (
    _EDUCATION_CONFIG_DIR / "migrations" / "004_curriculum_request_receipts.py"
).read_text()


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
    butler's `trigger` MCP tool — not just write a row and hope something polls it."""
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


def test_request_persists_a_durable_receipt():
    """Accepted work must be recorded before the detached task starts."""
    assert _RECEIPT_TABLE in _ROUTER_SRC, (
        f"router.py must persist accepted requests in {_RECEIPT_TABLE}; a detached task "
        "with no durable row leaves a trigger failure invisible to the owner."
    )
    assert _RECEIPT_TABLE in _MIGRATION_SRC, (
        f"migration 004 must create {_RECEIPT_TABLE} — the router writes to it."
    )


def test_pending_guard_is_backend_owned_not_kv():
    """The one-pending guard must be a database constraint, not a KV key an LLM clears.

    The old ``pending_curriculum_request`` key was released by the *triggered
    session* calling ``state_delete``. A session that died, timed out, or simply
    forgot left the owner behind a permanent 409 with no way to retry.
    """
    assert _PENDING_GUARD_INDEX in _MIGRATION_SRC, (
        f"migration 004 must create the {_PENDING_GUARD_INDEX} partial unique index — "
        "it is the single one-pending-at-a-time guard."
    )
    assert "pending_curriculum_request" not in _ROUTER_SRC, (
        "the KV pending lock is gone; keeping it alongside the receipt row means two "
        "guards that can disagree, and the KV one has no owner when a session dies."
    )
    assert "state_delete" not in _ROUTER_SRC, (
        "releasing the guard must be a backend write (settling the receipt), never a "
        "state_delete the triggered LLM session has to remember to call."
    )


def test_detached_work_always_settles_a_terminal_state():
    """Every exit path of the detached task must land a terminal receipt state."""
    for marker in (
        "_FAILURE_TRIGGER_UNREACHABLE",
        "_FAILURE_SESSION_ERROR",
        "_FAILURE_NO_CURRICULUM",
        "_FAILURE_TIMED_OUT",
    ):
        assert marker in _ROUTER_SRC, (
            f"router.py must define {marker}: a swallowed trigger failure with no "
            "terminal reason is exactly the gap the receipt exists to close."
        )
    assert "_sweep_abandoned_receipts" in _ROUTER_SRC, (
        "a receipt whose owning task died with the API process must be swept to a "
        "terminal state, or the pending guard strands the owner across a restart."
    )


def test_status_read_is_exposed():
    """The owner must be able to read the outcome, not just submit and hope."""
    assert "/curriculum-requests/{request_id}" in _ROUTER_SRC, (
        "router.py must expose a read-only receipt endpoint; a 202 with no status read "
        "means the UI can only guess at completion."
    )
    assert "receipts_available" in _ROUTER_SRC, (
        "the status read must distinguish 'store unreadable' from 'nothing in flight' — "
        "an unavailable store rendered as empty is fabricated calm."
    )
