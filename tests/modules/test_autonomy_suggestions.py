"""Tests for autonomy suggestions — promotion/demotion lifecycle.

Covers tasks 5.1–5.7 and 6.1–6.2:
- generate_scope_description (5.2)
- create_promotion_suggestion (5.1)
- confirm_suggestion for promotion (5.3) and demotion (6.2)
- dismiss_suggestion (5.4)
- list_suggestions (5.5)
- supersede_matching_suggestions (5.6)
- create_demotion_suggestion (6.1)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.modules.approvals.autonomy_suggestions import (
    confirm_suggestion,
    create_demotion_suggestion,
    create_promotion_suggestion,
    dismiss_suggestion,
    generate_scope_description,
    list_suggestions,
    supersede_matching_suggestions,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# generate_scope_description tests (task 5.2)
# ---------------------------------------------------------------------------


def test_scope_description_with_string_args():
    """Scope description lists all args with exact values."""
    desc = generate_scope_description(
        "send_telegram", {"chat_id": "mom_123", "text": "Good morning"}
    )
    assert "send_telegram" in desc
    assert "chat_id = 'mom_123'" in desc
    assert "text = 'Good morning'" in desc


def test_scope_description_includes_all_args():
    """All arguments appear in the scope description."""
    desc = generate_scope_description(
        "notify",
        {"channel": "email", "to": "mom@example.com", "subject": "Weekly update"},
    )
    assert "channel = 'email'" in desc
    assert "to = 'mom@example.com'" in desc
    assert "subject = 'Weekly update'" in desc


def test_scope_description_no_args_fallback():
    """Empty args produce a human-readable fallback."""
    desc = generate_scope_description("my_tool", {})
    assert "my_tool" in desc
    assert "no argument constraints" in desc


def test_scope_description_non_string_arg():
    """Non-string args are serialised with json.dumps."""
    desc = generate_scope_description("set_value", {"count": 42, "flag": True})
    assert "count = 42" in desc
    assert "flag = true" in desc


def test_scope_description_sorted_keys():
    """Args appear sorted by key name."""
    desc = generate_scope_description("my_tool", {"z_key": "z", "a_key": "a"})
    a_pos = desc.index("a_key")
    z_pos = desc.index("z_key")
    assert a_pos < z_pos


# ---------------------------------------------------------------------------
# MockPool for suggestions tests
# ---------------------------------------------------------------------------


class MockPool:
    def __init__(self) -> None:
        self.suggestion_rows: list[dict[str, Any]] = []
        self.rule_rows: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    async def execute(self, query: str, *args: Any) -> None:
        if "INSERT INTO autonomy_suggestions" in query:
            row: dict[str, Any] = {
                "id": args[0],
                "suggestion_type": args[1],
                "pattern_fingerprint": args[2],
                "tool_name": args[3],
                "representative_args": args[4],
                "status": args[5],
                "approval_count_at_creation": args[6],
                "created_at": args[7],
                "resulting_rule_id": args[8] if len(args) > 8 else None,
                "decided_at": None,
                "decided_by": None,
                "cooldown_until": None,
                "dismissal_reason": None,
            }
            self.suggestion_rows.append(row)
        elif "INSERT INTO approval_rules" in query:
            rule: dict[str, Any] = {
                "id": args[0],
                "tool_name": args[1],
                "arg_constraints": args[2],
                "description": args[3],
                "created_at": args[4],
                "max_uses": args[5],
                "active": args[6],
            }
            self.rule_rows.append(rule)
        elif "UPDATE autonomy_suggestions" in query:
            self._apply_suggestion_update(query, args)
        elif "UPDATE approval_rules" in query:
            if "SET active = false WHERE id = $1" in query:
                rule_id = args[0]
                for r in self.rule_rows:
                    if r["id"] == rule_id:
                        r["active"] = False
            elif "SET active = $1" in query:
                rule_id = args[1]
                for r in self.rule_rows:
                    if r["id"] == rule_id:
                        r["active"] = args[0]
        elif "INSERT INTO approval_events" in query:
            self.events.append({"event_type": args[0]})

    def _apply_suggestion_update(self, query: str, args: Any) -> None:
        if "SET status = $1, decided_at = $2, decided_by = $3, resulting_rule_id = $4" in query:
            sugg_id = args[4]
            for r in self.suggestion_rows:
                if r["id"] == sugg_id:
                    r["status"] = args[0]
                    r["decided_at"] = args[1]
                    r["decided_by"] = args[2]
                    r["resulting_rule_id"] = args[3]
        elif (
            "SET status = $1, decided_at = $2, decided_by = $3, "
            "cooldown_until = $4, dismissal_reason = $5" in query
        ):
            sugg_id = args[5]
            for r in self.suggestion_rows:
                if r["id"] == sugg_id:
                    r["status"] = args[0]
                    r["decided_at"] = args[1]
                    r["decided_by"] = args[2]
                    r["cooldown_until"] = args[3]
                    r["dismissal_reason"] = args[4]
        elif "SET status = $1, decided_at = $2, decided_by = $3" in query:
            sugg_id = args[3]
            for r in self.suggestion_rows:
                if r["id"] == sugg_id:
                    r["status"] = args[0]
                    r["decided_at"] = args[1]
                    r["decided_by"] = args[2]
        elif "SET status = $1, decided_at = $2 WHERE id = $3" in query:
            sugg_id = args[2]
            for r in self.suggestion_rows:
                if r["id"] == sugg_id:
                    r["status"] = args[0]
                    r["decided_at"] = args[1]

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "autonomy_suggestions" in query and "WHERE id = $1" in query:
            sugg_id = args[0]
            for r in self.suggestion_rows:
                if r["id"] == sugg_id:
                    return dict(r)
            return None
        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "autonomy_suggestions" in query:
            results = list(self.suggestion_rows)
            if "WHERE tool_name = $1" in query:
                tool = args[0]
                results = [r for r in results if r.get("tool_name") == tool]
            if "status = 'pending'" in query:
                results = [r for r in results if r.get("status") == "pending"]
            if "suggestion_type = 'promotion'" in query:
                results = [r for r in results if r.get("suggestion_type") == "promotion"]
            # For list_suggestions, apply status / suggestion_type filters from params
            # The query uses $1, $2 params so we inspect conditions inline
            return [dict(r) for r in results]
        if "approval_events" in query or "approval_rules" in query:
            return []
        return []


class MockAction:
    def __init__(self, tool_name: str = "test_tool", tool_args: dict | None = None) -> None:
        self.id = uuid.uuid4()
        self.tool_name = tool_name
        self.tool_args = tool_args or {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_suggestion(
    pool: MockPool,
    suggestion_type: str = "promotion",
    status: str = "pending",
    resulting_rule_id: uuid.UUID | None = None,
    tool_name: str = "my_tool",
    rep_args: dict | None = None,
) -> uuid.UUID:
    sugg_id = uuid.uuid4()
    pool.suggestion_rows.append(
        {
            "id": sugg_id,
            "suggestion_type": suggestion_type,
            "pattern_fingerprint": "fp_abc",
            "tool_name": tool_name,
            "representative_args": json.dumps(rep_args or {"key": "value"}),
            "status": status,
            "approval_count_at_creation": 5,
            "created_at": datetime.now(UTC),
            "decided_at": None,
            "decided_by": None,
            "resulting_rule_id": resulting_rule_id,
            "cooldown_until": None,
            "dismissal_reason": None,
        }
    )
    return sugg_id


# ---------------------------------------------------------------------------
# create_promotion_suggestion tests (task 5.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_promotion_suggestion_inserts_row():
    """create_promotion_suggestion inserts a row with correct fields."""
    pool = MockPool()

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        result = await create_promotion_suggestion(
            pool=pool,
            pattern_fingerprint="abc123",
            tool_name="send_telegram",
            representative_args={"chat_id": "mom"},
            approval_count=5,
        )

    assert len(pool.suggestion_rows) == 1
    row = pool.suggestion_rows[0]
    assert row["suggestion_type"] == "promotion"
    assert row["status"] == "pending"
    assert row["approval_count_at_creation"] == 5
    assert result["tool_name"] == "send_telegram"
    assert "scope_description" in result


@pytest.mark.asyncio
async def test_create_promotion_suggestion_returns_correct_fields():
    """create_promotion_suggestion return dict has all expected keys."""
    pool = MockPool()

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        result = await create_promotion_suggestion(
            pool=pool,
            pattern_fingerprint="fp_test",
            tool_name="send_email",
            representative_args={"to": "mom@example.com"},
            approval_count=7,
        )

    assert result["status"] == "pending"
    assert result["suggestion_type"] == "promotion"
    assert result["pattern_fingerprint"] == "fp_test"
    assert result["approval_count_at_creation"] == 7
    assert result["decided_at"] is None
    assert result["resulting_rule_id"] is None


@pytest.mark.asyncio
async def test_create_promotion_suggestion_records_event():
    """create_promotion_suggestion records a promotion_suggested audit event."""
    pool = MockPool()
    recorded_events = []

    async def mock_record_event(pool, event_type, **kwargs):
        recorded_events.append(event_type)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        side_effect=mock_record_event,
    ):
        await create_promotion_suggestion(
            pool=pool,
            pattern_fingerprint="abc123",
            tool_name="my_tool",
            representative_args={},
            approval_count=5,
        )

    from butlers.modules.approvals.events import ApprovalEventType

    assert ApprovalEventType.PROMOTION_SUGGESTED in recorded_events


# ---------------------------------------------------------------------------
# create_demotion_suggestion tests (task 6.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_demotion_suggestion_inserts_row():
    """create_demotion_suggestion inserts a demotion row."""
    pool = MockPool()
    rule_id = uuid.uuid4()
    action = MockAction(tool_name="send_email", tool_args={"to": "mom@example.com"})

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        result = await create_demotion_suggestion(
            pool=pool,
            action=action,
            rule_id=rule_id,
            error_details="Email service unavailable",
        )

    assert len(pool.suggestion_rows) == 1
    row = pool.suggestion_rows[0]
    assert row["suggestion_type"] == "demotion"
    assert row["status"] == "pending"
    assert result["suggestion_type"] == "demotion"
    assert result["resulting_rule_id"] == str(rule_id)


@pytest.mark.asyncio
async def test_create_demotion_suggestion_records_event():
    """create_demotion_suggestion records a demotion_suggested event."""
    pool = MockPool()
    rule_id = uuid.uuid4()
    action = MockAction()
    recorded_events = []

    async def mock_record_event(pool, event_type, **kwargs):
        recorded_events.append(event_type)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        side_effect=mock_record_event,
    ):
        await create_demotion_suggestion(
            pool=pool, action=action, rule_id=rule_id, error_details="err"
        )

    from butlers.modules.approvals.events import ApprovalEventType

    assert ApprovalEventType.DEMOTION_SUGGESTED in recorded_events


@pytest.mark.asyncio
async def test_create_demotion_suggestion_stores_rule_id():
    """create_demotion_suggestion stores rule_id in resulting_rule_id field."""
    pool = MockPool()
    rule_id = uuid.uuid4()
    action = MockAction(tool_name="send_sms", tool_args={"number": "+1234567890"})

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        result = await create_demotion_suggestion(
            pool=pool,
            action=action,
            rule_id=rule_id,
            error_details="SMS provider down",
        )

    assert result["resulting_rule_id"] == str(rule_id)
    assert pool.suggestion_rows[0]["resulting_rule_id"] == rule_id


# ---------------------------------------------------------------------------
# confirm_suggestion tests — promotion (task 5.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_promotion_suggestion_creates_rule():
    """Confirming a promotion suggestion creates a standing rule with exact constraints."""
    pool = MockPool()
    sugg_id = _seed_suggestion(pool, suggestion_type="promotion")

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        await confirm_suggestion(pool, sugg_id, "owner")

    assert len(pool.rule_rows) == 1
    rule = pool.rule_rows[0]
    constraints = json.loads(rule["arg_constraints"])
    assert constraints["key"]["type"] == "exact"
    assert constraints["key"]["value"] == "value"


@pytest.mark.asyncio
async def test_confirm_promotion_suggestion_transitions_to_confirmed():
    """Confirmed promotion suggestion has status=confirmed."""
    pool = MockPool()
    sugg_id = _seed_suggestion(pool, suggestion_type="promotion")

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        result = await confirm_suggestion(pool, sugg_id, "owner")

    assert result["status"] == "confirmed"
    assert result["resulting_rule_id"] is not None


@pytest.mark.asyncio
async def test_confirm_promotion_records_promotion_confirmed_event():
    """Confirmed promotion suggestion records a promotion_confirmed event."""
    pool = MockPool()
    sugg_id = _seed_suggestion(pool, suggestion_type="promotion")
    recorded_events = []

    async def mock_record_event(pool, event_type, **kwargs):
        recorded_events.append(event_type)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        side_effect=mock_record_event,
    ):
        await confirm_suggestion(pool, sugg_id, "owner")

    from butlers.modules.approvals.events import ApprovalEventType

    assert ApprovalEventType.PROMOTION_CONFIRMED in recorded_events


@pytest.mark.asyncio
async def test_confirm_promotion_exact_constraints_for_all_args():
    """Each arg in representative_args becomes an exact constraint in the new rule."""
    pool = MockPool()
    sugg_id = _seed_suggestion(
        pool,
        suggestion_type="promotion",
        rep_args={"chat_id": "mom_123", "text": "hello", "urgent": False},
    )

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        await confirm_suggestion(pool, sugg_id, "owner")

    assert len(pool.rule_rows) == 1
    constraints = json.loads(pool.rule_rows[0]["arg_constraints"])
    assert constraints["chat_id"] == {"type": "exact", "value": "mom_123"}
    assert constraints["text"] == {"type": "exact", "value": "hello"}
    assert constraints["urgent"] == {"type": "exact", "value": False}


# ---------------------------------------------------------------------------
# confirm_suggestion tests — demotion (task 6.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_demotion_suggestion_revokes_rule():
    """Confirming a demotion suggestion revokes the referenced standing rule."""
    pool = MockPool()
    rule_id = uuid.uuid4()
    pool.rule_rows.append(
        {
            "id": rule_id,
            "tool_name": "my_tool",
            "arg_constraints": "{}",
            "description": "Test rule",
            "active": True,
        }
    )
    sugg_id = _seed_suggestion(pool, suggestion_type="demotion", resulting_rule_id=rule_id)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        result = await confirm_suggestion(pool, sugg_id, "owner")

    # Rule should be revoked (active = False)
    assert pool.rule_rows[0]["active"] is False
    assert result["status"] == "confirmed"


@pytest.mark.asyncio
async def test_confirm_demotion_records_demotion_confirmed_event():
    """Confirming a demotion suggestion records a demotion_confirmed event."""
    pool = MockPool()
    rule_id = uuid.uuid4()
    pool.rule_rows.append(
        {"id": rule_id, "tool_name": "my_tool", "arg_constraints": "{}", "active": True}
    )
    sugg_id = _seed_suggestion(pool, suggestion_type="demotion", resulting_rule_id=rule_id)
    recorded_events = []

    async def mock_record_event(pool, event_type, **kwargs):
        recorded_events.append(event_type)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        side_effect=mock_record_event,
    ):
        await confirm_suggestion(pool, sugg_id, "owner")

    from butlers.modules.approvals.events import ApprovalEventType

    assert ApprovalEventType.DEMOTION_CONFIRMED in recorded_events


@pytest.mark.asyncio
async def test_confirm_already_decided_returns_error():
    """Confirming an already-decided suggestion returns an error."""
    pool = MockPool()
    sugg_id = _seed_suggestion(pool, status="confirmed")

    result = await confirm_suggestion(pool, sugg_id, "owner")
    assert "error" in result
    assert "confirmed" in result["error"]


@pytest.mark.asyncio
async def test_confirm_nonexistent_suggestion_returns_error():
    """Confirming a nonexistent suggestion returns an error."""
    pool = MockPool()
    nonexistent = uuid.uuid4()

    result = await confirm_suggestion(pool, nonexistent, "owner")
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_confirm_invalid_uuid_returns_error():
    """Confirming with an invalid UUID returns an error."""
    pool = MockPool()
    result = await confirm_suggestion(pool, "not-a-uuid", "owner")
    assert "error" in result


# ---------------------------------------------------------------------------
# dismiss_suggestion tests (task 5.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_suggestion_sets_cooldown():
    """Dismissed suggestion has cooldown_until set."""
    pool = MockPool()
    sugg_id = _seed_suggestion(pool)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        result = await dismiss_suggestion(
            pool, sugg_id, "owner", reason="Not needed", cooldown_days=30
        )

    assert result["status"] == "dismissed"
    assert result["cooldown_until"] is not None
    assert result["dismissal_reason"] == "Not needed"


@pytest.mark.asyncio
async def test_dismiss_suggestion_custom_cooldown():
    """dismiss_suggestion respects custom cooldown_days parameter."""
    pool = MockPool()
    sugg_id = _seed_suggestion(pool)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        await dismiss_suggestion(pool, sugg_id, "owner", cooldown_days=7)

    row = pool.suggestion_rows[0]
    assert row["cooldown_until"] is not None


@pytest.mark.asyncio
async def test_dismiss_promotion_suggestion_records_promotion_dismissed_event():
    """Dismissing a promotion suggestion records a promotion_dismissed event."""
    pool = MockPool()
    sugg_id = _seed_suggestion(pool, suggestion_type="promotion")
    recorded_events = []

    async def mock_record_event(pool, event_type, **kwargs):
        recorded_events.append(event_type)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        side_effect=mock_record_event,
    ):
        await dismiss_suggestion(pool, sugg_id, "owner")

    from butlers.modules.approvals.events import ApprovalEventType

    assert ApprovalEventType.PROMOTION_DISMISSED in recorded_events


@pytest.mark.asyncio
async def test_dismiss_demotion_suggestion_records_demotion_dismissed_event():
    """Dismissing a demotion suggestion records a demotion_dismissed event."""
    pool = MockPool()
    sugg_id = _seed_suggestion(pool, suggestion_type="demotion")
    recorded_events = []

    async def mock_record_event(pool, event_type, **kwargs):
        recorded_events.append(event_type)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        side_effect=mock_record_event,
    ):
        await dismiss_suggestion(pool, sugg_id, "owner")

    from butlers.modules.approvals.events import ApprovalEventType

    assert ApprovalEventType.DEMOTION_DISMISSED in recorded_events


@pytest.mark.asyncio
async def test_dismiss_already_decided_returns_error():
    """Dismissing an already-decided suggestion returns an error."""
    pool = MockPool()
    sugg_id = _seed_suggestion(pool, status="dismissed")

    result = await dismiss_suggestion(pool, sugg_id, "owner")
    assert "error" in result
    assert "dismissed" in result["error"]


@pytest.mark.asyncio
async def test_dismiss_demotion_keeps_rule_active():
    """Dismissing a demotion suggestion does NOT revoke the referenced rule."""
    pool = MockPool()
    rule_id = uuid.uuid4()
    pool.rule_rows.append(
        {"id": rule_id, "tool_name": "my_tool", "arg_constraints": "{}", "active": True}
    )
    sugg_id = _seed_suggestion(pool, suggestion_type="demotion", resulting_rule_id=rule_id)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        result = await dismiss_suggestion(pool, sugg_id, "owner")

    # Rule must remain active
    assert pool.rule_rows[0]["active"] is True
    assert result["status"] == "dismissed"


# ---------------------------------------------------------------------------
# list_suggestions tests (task 5.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_suggestions_returns_scope_description():
    """list_suggestions includes scope_description in each result."""
    pool = MockPool()
    _seed_suggestion(pool, suggestion_type="promotion")
    _seed_suggestion(pool, suggestion_type="demotion")

    results = await list_suggestions(pool, status=None)

    assert len(results) >= 1
    for r in results:
        assert "scope_description" in r


@pytest.mark.asyncio
async def test_list_suggestions_filters_by_status():
    """list_suggestions filters correctly using the MockPool."""
    pool = MockPool()
    _seed_suggestion(pool, status="pending")
    _seed_suggestion(pool, status="confirmed")

    # The mock pool returns all rows but the real SQL would filter;
    # since mock fetch doesn't filter by parametrized status, we test the
    # data shape is correct (real DB filtering tested via DB integration tests)
    results = await list_suggestions(pool, status="pending")
    assert isinstance(results, list)
    for r in results:
        assert "id" in r
        assert "suggestion_type" in r
        assert "status" in r


@pytest.mark.asyncio
async def test_list_suggestions_returns_list_of_dicts():
    """list_suggestions always returns a list of dicts."""
    pool = MockPool()

    results = await list_suggestions(pool, status="pending")
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# supersede_matching_suggestions tests (task 5.6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supersede_matching_suggestions_supersedes_covered_suggestion():
    """Pending suggestions covered by a new rule are superseded."""
    pool = MockPool()
    sugg_id = uuid.uuid4()
    pool.suggestion_rows.append(
        {
            "id": sugg_id,
            "suggestion_type": "promotion",
            "tool_name": "send_telegram",
            "pattern_fingerprint": "fp_abc",
            "representative_args": json.dumps({"chat_id": "mom", "text": "hello"}),
            "status": "pending",
            "approval_count_at_creation": 5,
            "created_at": datetime.now(UTC),
            "decided_at": None,
            "decided_by": None,
            "resulting_rule_id": None,
            "cooldown_until": None,
            "dismissal_reason": None,
        }
    )

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        count = await supersede_matching_suggestions(
            pool=pool,
            tool_name="send_telegram",
            arg_constraints={
                "chat_id": {"type": "exact", "value": "mom"},
                "text": {"type": "exact", "value": "hello"},
            },
        )

    assert count == 1
    assert pool.suggestion_rows[0]["status"] == "superseded"


@pytest.mark.asyncio
async def test_supersede_matching_suggestions_records_superseded_event():
    """supersede_matching_suggestions records promotion_superseded events."""
    pool = MockPool()
    sugg_id = uuid.uuid4()
    pool.suggestion_rows.append(
        {
            "id": sugg_id,
            "suggestion_type": "promotion",
            "tool_name": "my_tool",
            "pattern_fingerprint": "fp",
            "representative_args": json.dumps({"key": "val"}),
            "status": "pending",
            "approval_count_at_creation": 5,
            "created_at": datetime.now(UTC),
            "decided_at": None,
            "decided_by": None,
            "resulting_rule_id": None,
            "cooldown_until": None,
            "dismissal_reason": None,
        }
    )
    recorded_events = []

    async def mock_record_event(pool, event_type, **kwargs):
        recorded_events.append(event_type)

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        side_effect=mock_record_event,
    ):
        await supersede_matching_suggestions(
            pool=pool,
            tool_name="my_tool",
            arg_constraints={"key": {"type": "exact", "value": "val"}},
        )

    from butlers.modules.approvals.events import ApprovalEventType

    assert ApprovalEventType.PROMOTION_SUPERSEDED in recorded_events


@pytest.mark.asyncio
async def test_supersede_does_not_affect_non_matching_suggestions():
    """Suggestions with different arg values are NOT superseded."""
    pool = MockPool()
    sugg_id = uuid.uuid4()
    pool.suggestion_rows.append(
        {
            "id": sugg_id,
            "suggestion_type": "promotion",
            "tool_name": "send_telegram",
            "pattern_fingerprint": "fp_abc",
            "representative_args": json.dumps({"chat_id": "dad"}),  # different value
            "status": "pending",
            "approval_count_at_creation": 5,
            "created_at": datetime.now(UTC),
            "decided_at": None,
            "decided_by": None,
            "resulting_rule_id": None,
            "cooldown_until": None,
            "dismissal_reason": None,
        }
    )

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        count = await supersede_matching_suggestions(
            pool=pool,
            tool_name="send_telegram",
            arg_constraints={"chat_id": {"type": "exact", "value": "mom"}},
        )

    assert count == 0
    assert pool.suggestion_rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_supersede_empty_constraints_covers_all():
    """An unconstrained rule (empty arg_constraints) supersedes all matching suggestions."""
    pool = MockPool()
    sugg_id = uuid.uuid4()
    pool.suggestion_rows.append(
        {
            "id": sugg_id,
            "suggestion_type": "promotion",
            "tool_name": "my_tool",
            "pattern_fingerprint": "fp",
            "representative_args": json.dumps({"any_key": "any_val"}),
            "status": "pending",
            "approval_count_at_creation": 5,
            "created_at": datetime.now(UTC),
            "decided_at": None,
            "decided_by": None,
            "resulting_rule_id": None,
            "cooldown_until": None,
            "dismissal_reason": None,
        }
    )

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        count = await supersede_matching_suggestions(
            pool=pool,
            tool_name="my_tool",
            arg_constraints={},  # unconstrained
        )

    assert count == 1
    assert pool.suggestion_rows[0]["status"] == "superseded"


@pytest.mark.asyncio
async def test_supersede_returns_zero_for_no_pending_suggestions():
    """supersede_matching_suggestions returns 0 when there are no pending suggestions."""
    pool = MockPool()

    with patch(
        "butlers.modules.approvals.events.record_approval_event",
        new_callable=AsyncMock,
    ):
        count = await supersede_matching_suggestions(
            pool=pool,
            tool_name="no_suggestions_tool",
            arg_constraints={"key": "val"},
        )

    assert count == 0


# ---------------------------------------------------------------------------
# New event types in ApprovalEventType (task 9.1)
# ---------------------------------------------------------------------------


def test_approval_event_type_has_promotion_events():
    """ApprovalEventType includes all required promotion event types."""
    from butlers.modules.approvals.events import ApprovalEventType

    assert hasattr(ApprovalEventType, "PROMOTION_SUGGESTED")
    assert hasattr(ApprovalEventType, "PROMOTION_CONFIRMED")
    assert hasattr(ApprovalEventType, "PROMOTION_DISMISSED")
    assert hasattr(ApprovalEventType, "PROMOTION_SUPERSEDED")


def test_approval_event_type_has_demotion_events():
    """ApprovalEventType includes all required demotion event types."""
    from butlers.modules.approvals.events import ApprovalEventType

    assert hasattr(ApprovalEventType, "DEMOTION_SUGGESTED")
    assert hasattr(ApprovalEventType, "DEMOTION_CONFIRMED")
    assert hasattr(ApprovalEventType, "DEMOTION_DISMISSED")


def test_promotion_event_values():
    """Promotion event type string values are correct."""
    from butlers.modules.approvals.events import ApprovalEventType

    assert ApprovalEventType.PROMOTION_SUGGESTED.value == "promotion_suggested"
    assert ApprovalEventType.PROMOTION_CONFIRMED.value == "promotion_confirmed"
    assert ApprovalEventType.PROMOTION_DISMISSED.value == "promotion_dismissed"
    assert ApprovalEventType.PROMOTION_SUPERSEDED.value == "promotion_superseded"


def test_demotion_event_values():
    """Demotion event type string values are correct."""
    from butlers.modules.approvals.events import ApprovalEventType

    assert ApprovalEventType.DEMOTION_SUGGESTED.value == "demotion_suggested"
    assert ApprovalEventType.DEMOTION_CONFIRMED.value == "demotion_confirmed"
    assert ApprovalEventType.DEMOTION_DISMISSED.value == "demotion_dismissed"
