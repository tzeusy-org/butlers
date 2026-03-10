"""Tests for rule_applications audit row insertion in mark_helpful/mark_harmful.

Verifies that each call to mark_helpful() / mark_harmful() inserts exactly one
row into rule_applications with the correct outcome, tenant_id, rule_id, and
optional session_id / request_id context.

These tests operate against mock asyncpg connections to stay fully unit-level.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
mark_helpful = _mod.mark_helpful
mark_harmful = _mod.mark_harmful

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Async context manager helper
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Simple async context manager wrapper returning a fixed value."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RULE_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_SESSION_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
_REQUEST_ID = "req-abc123"
_TENANT_ID = "test-tenant"


def _make_rule_row(
    *,
    applied_count: int = 1,
    success_count: int = 1,
    harmful_count: int = 0,
    maturity: str = "candidate",
    tenant_id: str = _TENANT_ID,
    metadata: dict | None = None,
) -> dict:
    """Build a dict resembling an asyncpg Record returned by RETURNING *."""
    return {
        "id": _RULE_ID,
        "content": "test rule",
        "embedding": "[0.1, 0.2]",
        "search_vector": "test",
        "scope": "global",
        "maturity": maturity,
        "confidence": 0.5,
        "decay_rate": 0.01,
        "effectiveness_score": 0.0,
        "applied_count": applied_count,
        "success_count": success_count,
        "harmful_count": harmful_count,
        "source_episode_id": None,
        "source_butler": "test-butler",
        "created_at": datetime.now(UTC),
        "tags": "[]",
        "metadata": json.dumps(metadata or {}),
        "last_applied_at": datetime.now(UTC),
        "reference_count": 0,
        "last_referenced_at": None,
        "tenant_id": tenant_id,
        "request_id": None,
        "retention_class": "rule",
        "sensitivity": "normal",
    }


def _make_pool_and_conn(fetchrow_return=None):
    """Create mock pool and conn wired with _AsyncCM pattern."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))

    return pool, conn


def _get_audit_insert_call(conn):
    """Return the positional args of the rule_applications INSERT execute() call.

    mark_helpful / mark_harmful each call conn.execute() twice:
      call_args_list[0] — UPDATE rules SET effectiveness_score / maturity …
      call_args_list[1] — INSERT INTO rule_applications …
    """
    assert len(conn.execute.call_args_list) >= 2, (
        "Expected at least 2 execute() calls; got "
        f"{len(conn.execute.call_args_list)}: "
        f"{[c[0][0] for c in conn.execute.call_args_list]}"
    )
    return conn.execute.call_args_list[1][0]


# ---------------------------------------------------------------------------
# mark_helpful audit tests
# ---------------------------------------------------------------------------


class TestMarkHelpfulAuditInsert:
    """mark_helpful inserts a rule_applications row with outcome='helpful'."""

    async def test_inserts_rule_applications_row(self) -> None:
        row = _make_rule_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        # Two execute() calls must happen: UPDATE rules + INSERT rule_applications
        assert conn.execute.await_count == 2

    async def test_audit_sql_targets_rule_applications(self) -> None:
        row = _make_rule_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        args = _get_audit_insert_call(conn)
        assert "rule_applications" in args[0]

    async def test_audit_outcome_is_helpful(self) -> None:
        row = _make_rule_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        args = _get_audit_insert_call(conn)
        assert "'helpful'" in args[0]

    async def test_audit_tenant_id_from_rule_row(self) -> None:
        row = _make_rule_row(tenant_id=_TENANT_ID)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        args = _get_audit_insert_call(conn)
        # $1 is tenant_id
        assert args[1] == _TENANT_ID

    async def test_audit_rule_id_passed(self) -> None:
        row = _make_rule_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        args = _get_audit_insert_call(conn)
        # $2 is rule_id
        assert args[2] == _RULE_ID

    async def test_audit_session_id_default_none(self) -> None:
        row = _make_rule_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        args = _get_audit_insert_call(conn)
        # $3 is session_id
        assert args[3] is None

    async def test_audit_request_id_default_none(self) -> None:
        row = _make_rule_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        args = _get_audit_insert_call(conn)
        # $4 is request_id
        assert args[4] is None

    async def test_audit_session_id_forwarded(self) -> None:
        row = _make_rule_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID, session_id=_SESSION_ID)

        args = _get_audit_insert_call(conn)
        assert args[3] == _SESSION_ID

    async def test_audit_request_id_forwarded(self) -> None:
        row = _make_rule_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID, request_id=_REQUEST_ID)

        args = _get_audit_insert_call(conn)
        assert args[4] == _REQUEST_ID

    async def test_no_audit_when_rule_not_found(self) -> None:
        """If the rule does not exist, no INSERT should occur."""
        pool, conn = _make_pool_and_conn(fetchrow_return=None)

        await mark_helpful(pool, _RULE_ID)

        conn.execute.assert_not_awaited()

    async def test_counter_update_still_fires(self) -> None:
        """The existing UPDATE rules … execute() must still happen alongside audit."""
        row = _make_rule_row()
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_helpful(pool, _RULE_ID)

        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "UPDATE rules" in first_call_sql
        assert "effectiveness_score" in first_call_sql


# ---------------------------------------------------------------------------
# mark_harmful audit tests
# ---------------------------------------------------------------------------


class TestMarkHarmfulAuditInsert:
    """mark_harmful inserts a rule_applications row with outcome='harmful'."""

    async def test_inserts_rule_applications_row(self) -> None:
        row = _make_rule_row(success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        # Two execute() calls must happen: UPDATE rules + INSERT rule_applications
        assert conn.execute.await_count == 2

    async def test_audit_sql_targets_rule_applications(self) -> None:
        row = _make_rule_row(success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        args = _get_audit_insert_call(conn)
        assert "rule_applications" in args[0]

    async def test_audit_outcome_is_harmful(self) -> None:
        row = _make_rule_row(success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        args = _get_audit_insert_call(conn)
        assert "'harmful'" in args[0]

    async def test_audit_tenant_id_from_rule_row(self) -> None:
        row = _make_rule_row(tenant_id=_TENANT_ID, success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        args = _get_audit_insert_call(conn)
        assert args[1] == _TENANT_ID

    async def test_audit_rule_id_passed(self) -> None:
        row = _make_rule_row(success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        args = _get_audit_insert_call(conn)
        assert args[2] == _RULE_ID

    async def test_audit_session_id_forwarded(self) -> None:
        row = _make_rule_row(success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID, session_id=_SESSION_ID)

        args = _get_audit_insert_call(conn)
        assert args[3] == _SESSION_ID

    async def test_audit_request_id_forwarded(self) -> None:
        row = _make_rule_row(success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID, request_id=_REQUEST_ID)

        args = _get_audit_insert_call(conn)
        assert args[4] == _REQUEST_ID

    async def test_audit_notes_includes_reason(self) -> None:
        """When reason is provided, notes JSON must contain it."""
        row = _make_rule_row(success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID, reason="caused confusion")

        args = _get_audit_insert_call(conn)
        # $5 is the notes JSON string (::jsonb cast is in SQL, arg is a string)
        notes_json = args[5]
        parsed = json.loads(notes_json)
        assert parsed["reason"] == "caused confusion"

    async def test_audit_notes_empty_when_no_reason(self) -> None:
        row = _make_rule_row(success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID, reason=None)

        args = _get_audit_insert_call(conn)
        notes_json = args[5]
        parsed = json.loads(notes_json)
        assert parsed == {}

    async def test_no_audit_when_rule_not_found(self) -> None:
        """If the rule does not exist, no INSERT should occur."""
        pool, conn = _make_pool_and_conn(fetchrow_return=None)

        await mark_harmful(pool, _RULE_ID)

        conn.execute.assert_not_awaited()

    async def test_counter_update_still_fires(self) -> None:
        """The existing UPDATE rules … execute() must still happen alongside audit."""
        row = _make_rule_row(success_count=0, harmful_count=1)
        pool, conn = _make_pool_and_conn(fetchrow_return=row)

        await mark_harmful(pool, _RULE_ID)

        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "UPDATE rules" in first_call_sql
        assert "effectiveness_score" in first_call_sql
