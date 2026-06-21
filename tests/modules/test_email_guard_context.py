"""Unit tests for context-mismatch detection in the email guard.

Covers the new ``msg_context`` / ``_get_email_context`` / ``_context_conflicts``
additions from bu-uv4b4.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from butlers.modules.approvals._shared import is_primary_contact
from butlers.modules.approvals.email_guard import (
    _context_conflicts,
    _get_email_context,
    check_email_recipient,
)

# ---------------------------------------------------------------------------
# _context_conflicts unit tests
# ---------------------------------------------------------------------------


class TestContextConflicts:
    def test_no_conflict_when_address_context_is_none(self) -> None:
        """Unclassified address never conflicts."""
        assert _context_conflicts("personal", None) is False
        assert _context_conflicts("work", None) is False
        assert _context_conflicts("other", None) is False

    def test_no_conflict_when_contexts_match(self) -> None:
        assert _context_conflicts("personal", "personal") is False
        assert _context_conflicts("work", "work") is False
        assert _context_conflicts("other", "other") is False

    def test_conflict_when_contexts_differ(self) -> None:
        assert _context_conflicts("personal", "work") is True
        assert _context_conflicts("work", "personal") is True
        assert _context_conflicts("personal", "other") is True
        assert _context_conflicts("other", "personal") is True


# ---------------------------------------------------------------------------
# _get_email_context unit tests
# ---------------------------------------------------------------------------


class TestGetEmailContext:
    @pytest.mark.parametrize(
        ("fetchrow_return", "fetchrow_side_effect", "expected"),
        [
            ({"context": "personal"}, None, "personal"),  # row exists
            (None, None, None),  # row missing
            ({"context": None}, None, None),  # context column null
            (None, Exception("DB unavailable"), None),  # db error degrades to None
        ],
    )
    async def test_get_email_context(self, fetchrow_return, fetchrow_side_effect, expected) -> None:
        pool = AsyncMock()
        if fetchrow_side_effect is not None:
            pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
        else:
            pool.fetchrow = AsyncMock(return_value=fetchrow_return)
        assert await _get_email_context(pool, "user@example.com") == expected


# ---------------------------------------------------------------------------
# is_primary_contact unit tests (email channel)
# ---------------------------------------------------------------------------


class TestIsPrimaryEmail:
    """Tests for email primacy via the shared is_primary_contact helper (email channel)."""

    @pytest.mark.parametrize(
        ("fetchrow_return", "fetchrow_side_effect", "expected"),
        [
            ({"primary": True}, None, True),  # is_primary set
            ({"primary": False}, None, False),  # not primary
            (None, None, False),  # row missing
            (None, Exception("column missing"), False),  # db error → not primary
        ],
    )
    async def test_is_primary_email(self, fetchrow_return, fetchrow_side_effect, expected) -> None:
        pool = AsyncMock()
        if fetchrow_side_effect is not None:
            pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
        else:
            pool.fetchrow = AsyncMock(return_value=fetchrow_return)
        result = await is_primary_contact(pool, uuid.uuid4(), "email", "owner@example.com")
        assert result is expected


# ---------------------------------------------------------------------------
# check_email_recipient context mismatch integration tests
# ---------------------------------------------------------------------------


def _make_contact(roles=None):
    from butlers.identity import ResolvedContact

    return ResolvedContact(
        contact_id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        name="Test",
        roles=roles or ["contact"],
    )


_COMMON_KWARGS = {
    "email_target": "friend@work.com",
    "rule_tool_name": "notify",
    "rule_match_args": {"recipient": "friend@work.com"},
    "park_tool_name": "notify",
    "park_tool_args": {"recipient": "friend@work.com", "channel": "email"},
    "park_summary": "test park summary",
}


class TestCheckEmailRecipientContextMismatch:
    async def test_context_mismatch_parks_non_owner(self) -> None:
        """Personal message to a work-tagged address → parked."""
        pool = AsyncMock()
        contact = _make_contact(roles=["contact"])
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=contact),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value="work"),
            ),
        ):
            decision = await check_email_recipient(pool, msg_context="personal", **_COMMON_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parked"
        assert decision.action_id is not None
        # pending_action INSERT was called for context mismatch
        pool.execute.assert_awaited_once()

    async def test_context_mismatch_parks_unknown_contact(self) -> None:
        """Personal message to a work-tagged address from unknown contact → parked."""
        pool = AsyncMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value="work"),
            ),
        ):
            decision = await check_email_recipient(pool, msg_context="personal", **_COMMON_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parked"
        assert decision.contact_desc == "unknown contact"

    async def test_no_context_mismatch_when_contexts_match(self) -> None:
        """Same context → no mismatch, falls through to rules/park."""
        pool = AsyncMock()
        contact = _make_contact(roles=["contact"])
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=contact),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value="work"),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            decision = await check_email_recipient(pool, msg_context="work", **_COMMON_KWARGS)

        # parked for no-rule reason (not context mismatch)
        assert decision.allowed is False
        assert decision.reason == "parked"

    async def test_no_mismatch_when_address_context_is_none(self) -> None:
        """Unclassified address context is always compatible."""
        pool = AsyncMock()
        contact = _make_contact(roles=["contact"])
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=contact),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            decision = await check_email_recipient(pool, msg_context="personal", **_COMMON_KWARGS)

        # falls through to no-rule park, not context mismatch
        assert decision.allowed is False
        assert decision.reason == "parked"

    async def test_no_msg_context_skips_mismatch_check(self) -> None:
        """When msg_context is None, no context check is performed."""
        pool = AsyncMock()
        contact = _make_contact(roles=["contact"])
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=contact),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value="work"),
            ) as mock_get_ctx,
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            decision = await check_email_recipient(pool, **_COMMON_KWARGS)

        # _get_email_context should NOT have been called
        mock_get_ctx.assert_not_awaited()
        assert decision.allowed is False
        assert decision.reason == "parked"

    async def test_owner_primary_skips_context_check(self) -> None:
        """Owner primary address auto-approves before context mismatch check."""
        pool = AsyncMock()
        owner = _make_contact(roles=["owner"])
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=owner),
            ),
            patch(
                "butlers.modules.approvals.email_guard.is_primary_contact",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value="work"),
            ) as mock_get_ctx,
        ):
            decision = await check_email_recipient(pool, msg_context="personal", **_COMMON_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "owner"
        # context check must not have run for primary owner
        mock_get_ctx.assert_not_awaited()

    async def test_owner_non_primary_falls_through_to_context_check(self) -> None:
        """Non-primary owner address is NOT auto-approved; context check runs."""
        pool = AsyncMock()
        owner = _make_contact(roles=["owner"])
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=owner),
            ),
            patch(
                "butlers.modules.approvals.email_guard.is_primary_contact",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value="work"),
            ),
        ):
            decision = await check_email_recipient(pool, msg_context="personal", **_COMMON_KWARGS)

        # context mismatch (personal vs work) → parked
        assert decision.allowed is False
        assert decision.reason == "parked"
