"""Prepared-action status resolver (bu-jv1moa.114, REQ-home-deterministic-jobs-011): pure rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from butlers.jobs import home_prepared_status as status

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 14, 4, 0, tzinfo=UTC)


def _action(state: str, *, decided_by: str | None = None, expires: timedelta | None = None):
    return {
        "status": state,
        "decided_by": decided_by,
        "expires_at": NOW + expires if expires is not None else None,
    }


#: One table for the Python resolver and, in the migration test, the SQL definer.
CASES: list[tuple[str, dict[str, Any] | None, str | None, str]] = [
    ("pending", _action("pending", expires=timedelta(hours=1)), None, "awaiting_approval"),
    ("pending-no-expiry", _action("pending"), None, "awaiting_approval"),
    ("pending-past-expiry", _action("pending", expires=-timedelta(minutes=1)), None, "expired"),
    (
        "approved-or-refused-replay",
        _action("approved", decided_by="human:owner"),
        None,
        "approved_not_run",
    ),
    ("rejected-by-owner", _action("rejected", decided_by="human:owner"), None, "rejected"),
    (
        "rejected-by-system",
        _action("rejected", decided_by=status.CANCEL_ACTOR),
        None,
        "cancelled",
    ),
    ("goal-cancelled", _action("cancelled"), None, "cancelled"),
    ("expired", _action("expired"), None, "expired"),
    ("abandoned", _action("abandoned"), None, "abandoned"),
    ("executed-without-receipt", _action("executed"), None, "unknown"),
    ("novel-status", _action("mystery"), None, "unknown"),
    ("missing-row", None, None, "unknown"),
    ("receipt-succeeded", _action("executed"), "succeeded", "succeeded"),
    ("receipt-failed", _action("approved"), "failed", "failed"),
    ("receipt-unverified", _action("executed"), "unverified", "unverified"),
    ("receipt-attempting", _action("approved"), "attempting", "unverified"),
    (
        "receipt-wins-over-cancel",
        _action("rejected", decided_by=status.CANCEL_ACTOR),
        "succeeded",
        "succeeded",
    ),
    ("receipt-without-row", None, "succeeded", "succeeded"),
    ("receipt-unrecognised", _action("approved"), "weird", "unknown"),
]


@pytest.mark.parametrize(
    ("action", "receipt", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_classify_maps_every_case(action, receipt, expected) -> None:
    got = status.classify(action, {"status": receipt} if receipt else None, now=NOW)
    assert got == expected
    assert got in status.STATUSES


def test_the_answer_space_is_closed_and_complete() -> None:
    assert {c[3] for c in CASES} == set(status.STATUSES)
    assert len(status.STATUSES) == len(set(status.STATUSES)) == 10


class _Pool:
    def __init__(self, action=None, receipt=None, *, action_error=None, receipt_error=None):
        self.action, self.receipt = action, receipt
        self.action_error, self.receipt_error = action_error, receipt_error
        self.sql: list[str] = []

    async def fetchrow(self, sql: str, *args: Any):
        self.sql.append(sql)
        if "pending_actions" in sql:
            if self.action_error:
                raise self.action_error
            return self.action
        if self.receipt_error:
            raise self.receipt_error
        return self.receipt


async def test_the_resolver_reads_only_status_columns_never_arguments() -> None:
    pool = _Pool(_action("approved"), None)
    assert await status.prepared_action_status(pool, uuid.uuid4()) == "approved_not_run"  # type: ignore[arg-type]
    joined = " ".join(pool.sql)
    for forbidden in ("tool_args", "target", "data", "result", "why", "evidence", "failure_reason"):
        assert forbidden not in joined


async def test_a_receipt_decides_the_executed_case() -> None:
    pool = _Pool(_action("approved"), {"status": "unverified"})
    assert await status.prepared_action_status(pool, uuid.uuid4()) == "unverified"  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "not-a-uuid", None])
async def test_a_malformed_id_is_unknown(bad) -> None:
    assert await status.prepared_action_status(_Pool(), bad) == "unknown"  # type: ignore[arg-type]


async def test_an_unreadable_approval_row_is_unknown() -> None:
    pool = _Pool(action_error=asyncpg.PostgresError())
    assert await status.prepared_action_status(pool, uuid.uuid4()) == "unknown"  # type: ignore[arg-type]


async def test_an_unreadable_receipt_is_unknown_not_not_run() -> None:
    pool = _Pool(_action("approved"), receipt_error=asyncpg.PostgresError())
    assert await status.prepared_action_status(pool, uuid.uuid4()) == "unknown"  # type: ignore[arg-type]


async def test_a_schema_without_the_actuation_ledger_has_no_receipt() -> None:
    pool = _Pool(_action("pending"), receipt_error=asyncpg.UndefinedTableError())
    assert await status.prepared_action_status(pool, uuid.uuid4()) == "awaiting_approval"  # type: ignore[arg-type]
