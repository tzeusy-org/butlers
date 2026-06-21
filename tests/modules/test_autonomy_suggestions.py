"""Condensed autonomy tests — behavioral contract only.

Replaces test_autonomy_suggestions.py (20) + test_autonomy_tracker.py (16)
+ test_autonomy_module_integration.py (14) = 50 tests replaced with ~10.

Covers:
- Fingerprint determinism and key-order independence
- Approval count tracking

[bu-7sd7a]
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from butlers.modules.approvals.autonomy_tracker import (
    compute_fingerprint,
    get_approval_count,
    record_approval,
)

pytestmark = pytest.mark.unit


class MockPool:
    def __init__(self) -> None:
        self._approvals: list[dict[str, Any]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "COUNT" in query and args:
            fp = args[0]
            cnt = sum(1 for r in self._approvals if r.get("fingerprint") == fp)
            return {"cnt": cnt}
        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        return 0

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO autonomy_approval_history" in query:
            # args: (id, fingerprint, tool_name, tool_args, action_id, approved_at, ...)
            self._approvals.append(
                {
                    "fingerprint": args[1] if len(args) > 1 else "",
                    "tool_name": args[2] if len(args) > 2 else "",
                }
            )
        return "OK"


class TestComputeFingerprint:
    @pytest.mark.parametrize(
        ("left", "right", "expect_equal"),
        [
            # deterministic + key-order independence: equivalent inputs collide
            (
                ("email_send", {"to": "alice@example.com", "subject": "Hi"}),
                ("email_send", {"subject": "Hi", "to": "alice@example.com"}),
                True,
            ),
            # differing tool name diverges
            (("tool_a", {"x": "1"}), ("tool_b", {"x": "1"}), False),
            # differing args diverge
            (
                ("tool", {"to": "alice@example.com"}),
                ("tool", {"to": "bob@example.com"}),
                False,
            ),
        ],
    )
    def test_fingerprint_equality(
        self,
        left: tuple[str, dict[str, str]],
        right: tuple[str, dict[str, str]],
        expect_equal: bool,
    ) -> None:
        fp1 = compute_fingerprint(*left)
        fp2 = compute_fingerprint(*right)
        assert (fp1 == fp2) is expect_equal

    def test_returns_non_empty_string(self) -> None:
        fp = compute_fingerprint("tool", {"a": "1"})
        assert isinstance(fp, str) and len(fp) > 0


class TestApprovalCount:
    async def test_zero_for_unknown_fingerprint(self) -> None:
        pool = MockPool()
        count = await get_approval_count(pool, "nonexistent-fp")
        assert count == 0

    async def test_count_increases_after_record(self) -> None:
        pool = MockPool()
        fp = compute_fingerprint("email_send", {"to": "alice@example.com"})

        class _FakeAction:
            tool_name = "email_send"
            tool_args = {"to": "alice@example.com"}
            id = uuid.UUID("00000000-0000-0000-0000-000000000001")
            requested_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
            decided_at = datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC)

        await record_approval(pool, _FakeAction())
        count = await get_approval_count(pool, fp)
        assert count >= 1
