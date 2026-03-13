"""Unit tests for shared.ingestion_events write inside ingest_v1 pipeline.

Verifies the invariants stated in bu-0b7.3:
- Accepting a new envelope inserts into BOTH message_inbox AND
  shared.ingestion_events in the same transaction.
- Duplicate detection (either pre-lock or inside the advisory-lock
  re-check) skips the shared.ingestion_events insert.
- The request_id returned in IngestAcceptedResponse equals the id
  that would be written to shared.ingestion_events.
- Fields written to shared.ingestion_events match the envelope.

Also verifies the ensure_partition outside-transaction invariant (bu-v8ip):
- ensure_partition is called via pool.execute() (auto-commit / outside the
  advisory-lock transaction) so that a transaction rollback cannot drop a
  newly-created partition.
- Pre-lock duplicate detection skips ensure_partition entirely (no inserts
  needed, no partition required).

These tests use a fake asyncpg pool/connection so no Docker is needed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from butlers.tools.switchboard.ingestion.ingest import IngestAcceptedResponse, ingest_v1

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake asyncpg infrastructure
# ---------------------------------------------------------------------------


class _FakeConn:
    """Minimal fake asyncpg connection that captures execute() calls."""

    def __init__(self, *, existing_row: dict | None = None) -> None:
        # If set, inner duplicate re-check inside lock returns this row.
        self._inner_existing: dict | None = existing_row
        # Ordered list of (sql, args) tuples captured from execute().
        self.execute_calls: list[tuple[str, tuple]] = []
        # The UUID7 that will be "inserted" (set on first INSERT call).
        self._inserted_id: UUID | None = None

    def transaction(self) -> Any:
        return _FakeTransaction()

    async def execute(self, sql: str, *args: Any) -> str:
        self.execute_calls.append((sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        # First fetchrow inside lock = inner duplicate check.
        # Return None (no duplicate) unless test configured one.
        return self._inner_existing

    def _executed_sqls(self) -> list[str]:
        return [sql for sql, _ in self.execute_calls]

    def ingestion_events_args(self) -> tuple | None:
        """Return the parameter tuple from the shared.ingestion_events INSERT, or None."""
        for sql, args in self.execute_calls:
            if "shared.ingestion_events" in sql:
                return args
        return None

    def has_message_inbox_insert(self) -> bool:
        return any("INSERT INTO message_inbox" in sql for sql, _ in self.execute_calls)

    def has_ingestion_events_insert(self) -> bool:
        return any("shared.ingestion_events" in sql for sql, _ in self.execute_calls)


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _FakePool:
    """Fake asyncpg pool with one shared connection."""

    def __init__(self, *, inner_existing: dict | None = None) -> None:
        self.conn = _FakeConn(existing_row=inner_existing)
        # Outer duplicate check (pre-lock) — controlled via fetchrow_result.
        self._outer_existing: dict | None = None
        # Track pool-level execute() calls (e.g. ensure_partition outside tx).
        self.pool_execute_calls: list[tuple[str, tuple]] = []
        # If set, pool.execute() raises this exception (simulates DB failure).
        self._pool_execute_raises: Exception | None = None

    def set_outer_existing(self, row: dict) -> None:
        """Make the pre-lock duplicate check return an existing row."""
        self._outer_existing = row

    def set_pool_execute_to_raise(self, exc: Exception) -> None:
        """Make pool.execute() raise exc (simulates ensure_partition failure)."""
        self._pool_execute_raises = exc

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)

    async def execute(self, sql: str, *args: Any) -> str:
        """Pool-level execute — used for ensure_partition (outside transaction)."""
        if self._pool_execute_raises is not None:
            raise self._pool_execute_raises
        self.pool_execute_calls.append((sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        """Top-level pool.fetchrow — used for the pre-lock duplicate check."""
        return self._outer_existing


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def _telegram_envelope(
    *,
    update_id: str = "12345",
    bot_id: str = "bot_test",
    sender_id: str = "user_1",
    thread_id: str | None = None,
    ingestion_tier: str = "full",
    policy_tier: str = "default",
) -> dict:
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram_bot",
            "provider": "telegram",
            "endpoint_identity": bot_id,
        },
        "event": {
            "external_event_id": update_id,
            "external_thread_id": thread_id,
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": sender_id},
        "payload": {
            "raw": {"update_id": int(update_id)},
            "normalized_text": "hello",
        },
        "control": {
            "ingestion_tier": ingestion_tier,
            "policy_tier": policy_tier,
        },
    }


def _email_envelope(
    *,
    message_id: str = "<abc@example.com>",
    mailbox: str = "inbox@example.com",
    sender: str = "alice@example.com",
    thread_id: str | None = "thread-42",
    ingestion_tier: str = "full",
    policy_tier: str = "default",
) -> dict:
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "email",
            "provider": "gmail",
            "endpoint_identity": mailbox,
        },
        "event": {
            "external_event_id": message_id,
            "external_thread_id": thread_id,
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": sender},
        "payload": {
            "raw": {"subject": "Test", "body": "Hi"},
            "normalized_text": "Test\nHi",
        },
        "control": {
            "ingestion_tier": ingestion_tier,
            "policy_tier": policy_tier,
        },
    }


# ---------------------------------------------------------------------------
# Tests: new (non-duplicate) ingest
# ---------------------------------------------------------------------------


class TestIngestionEventsWriteOnAccept:
    """shared.ingestion_events is written atomically with message_inbox on accept."""

    async def test_new_ingest_writes_ingestion_events(self) -> None:
        pool = _FakePool()
        envelope = _telegram_envelope(update_id="111")

        result = await ingest_v1(
            pool, envelope, policy_evaluator=None, enable_thread_affinity=False
        )

        assert isinstance(result, IngestAcceptedResponse)
        assert result.duplicate is False
        assert pool.conn.has_message_inbox_insert()
        assert pool.conn.has_ingestion_events_insert(), (
            "shared.ingestion_events INSERT must be executed for a new ingest"
        )

    async def test_ingestion_events_id_equals_request_id(self) -> None:
        pool = _FakePool()
        envelope = _telegram_envelope(update_id="222")

        result = await ingest_v1(
            pool, envelope, policy_evaluator=None, enable_thread_affinity=False
        )

        args = pool.conn.ingestion_events_args()
        assert args is not None, "ingestion_events INSERT was not executed"
        # $1 is the id parameter
        inserted_id: UUID = args[0]
        assert inserted_id == result.request_id, (
            "shared.ingestion_events.id must equal the returned request_id"
        )

    async def test_ingestion_events_source_channel(self) -> None:
        pool = _FakePool()
        envelope = _email_envelope(message_id="<ch@example.com>")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $3 = source_channel
        assert args[2] == "email"

    async def test_ingestion_events_source_provider(self) -> None:
        pool = _FakePool()
        envelope = _email_envelope(message_id="<prov@example.com>")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $4 = source_provider
        assert args[3] == "gmail"

    async def test_ingestion_events_source_endpoint_identity(self) -> None:
        pool = _FakePool()
        envelope = _email_envelope(mailbox="inbox@mybutler.com", message_id="<ep@example.com>")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $5 = source_endpoint_identity
        assert args[4] == "inbox@mybutler.com"

    async def test_ingestion_events_source_sender_identity(self) -> None:
        pool = _FakePool()
        envelope = _email_envelope(sender="bob@example.com", message_id="<snd@example.com>")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $6 = source_sender_identity
        assert args[5] == "bob@example.com"

    async def test_ingestion_events_source_thread_identity_populated(self) -> None:
        pool = _FakePool()
        envelope = _email_envelope(thread_id="thread-xyz", message_id="<thr@example.com>")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $7 = source_thread_identity
        assert args[6] == "thread-xyz"

    async def test_ingestion_events_source_thread_identity_null_when_absent(self) -> None:
        pool = _FakePool()
        envelope = _telegram_envelope(update_id="333", thread_id=None)

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $7 = source_thread_identity
        assert args[6] is None

    async def test_ingestion_events_external_event_id(self) -> None:
        pool = _FakePool()
        envelope = _email_envelope(message_id="<evid@example.com>")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $8 = external_event_id
        assert args[7] == "<evid@example.com>"

    async def test_ingestion_events_dedupe_strategy_is_connector_api(self) -> None:
        pool = _FakePool()
        envelope = _telegram_envelope(update_id="444")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $10 = dedupe_strategy
        assert args[9] == "connector_api"

    async def test_ingestion_events_ingestion_tier(self) -> None:
        """Tier 2 (metadata) envelopes write ingestion_tier='metadata'."""
        pool = _FakePool()
        # Tier 2 envelopes require payload.raw=null per the contract validator.
        envelope = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "email",
                "provider": "gmail",
                "endpoint_identity": "inbox@example.com",
            },
            "event": {
                "external_event_id": "<meta@example.com>",
                "external_thread_id": None,
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "sender@example.com"},
            "payload": {
                "raw": None,
                "normalized_text": "Subject: Newsletter",
            },
            "control": {
                "ingestion_tier": "metadata",
                "policy_tier": "default",
            },
        }

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $11 = ingestion_tier
        assert args[10] == "metadata"

    async def test_ingestion_events_policy_tier(self) -> None:
        pool = _FakePool()
        envelope = _email_envelope(policy_tier="high_priority", message_id="<hp@example.com>")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $12 = policy_tier
        assert args[11] == "high_priority"

    async def test_ingestion_events_triage_fields_null_when_no_evaluator(self) -> None:
        """triage_decision and triage_target are None when policy_evaluator=None."""
        pool = _FakePool()
        envelope = _telegram_envelope(update_id="555")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # $13 = triage_decision, $14 = triage_target
        assert args[12] is None, "triage_decision must be None when no evaluator"
        assert args[13] is None, "triage_target must be None when no evaluator"

    async def test_ingestion_events_triage_decision_populated(self) -> None:
        """triage_decision and triage_target are written from the PolicyDecision result."""
        import time

        from butlers.ingestion_policy import IngestionPolicyEvaluator

        pool = _FakePool()
        envelope = _email_envelope(message_id="<triage@example.com>")

        # Create evaluator with no rules -> produces pass_through
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._rules = []
        evaluator._last_loaded_at = time.monotonic()

        await ingest_v1(
            pool,
            envelope,
            policy_evaluator=evaluator,
            enable_thread_affinity=False,
        )

        args = pool.conn.ingestion_events_args()
        assert args is not None
        # With an empty rule set, evaluator produces pass_through; target_butler is None
        assert args[12] == "pass_through", (
            "triage_decision should be 'pass_through' for empty rule set"
        )
        assert args[13] is None, "triage_target must be None for pass_through decision"

    async def test_both_inserts_share_same_received_at(self) -> None:
        """message_inbox and shared.ingestion_events receive the same received_at."""
        pool = _FakePool()
        envelope = _telegram_envelope(update_id="666")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        # $2 in message_inbox INSERT and $2 in ingestion_events INSERT must match.
        inbox_received_at = None
        events_received_at = None
        for sql, args in pool.conn.execute_calls:
            if "INSERT INTO message_inbox" in sql:
                inbox_received_at = args[1]  # $2
            elif "shared.ingestion_events" in sql:
                events_received_at = args[1]  # $2

        assert inbox_received_at is not None
        assert events_received_at is not None
        assert inbox_received_at == events_received_at, (
            "Both inserts must use the same received_at timestamp"
        )


# ---------------------------------------------------------------------------
# Tests: duplicate detection — skip ingestion_events insert
# ---------------------------------------------------------------------------


class TestIngestionEventsSkippedOnDuplicate:
    """shared.ingestion_events is NOT written for duplicate submissions."""

    async def test_pre_lock_duplicate_skips_ingestion_events(self) -> None:
        """Pre-lock duplicate check (pool.fetchrow) exits early — no DB inserts at all."""
        pool = _FakePool()
        existing_id = uuid.uuid4()
        pool.set_outer_existing({"request_id": existing_id})

        result = await ingest_v1(
            pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
        )

        assert result.duplicate is True
        assert result.request_id == existing_id
        assert not pool.conn.has_message_inbox_insert(), (
            "message_inbox INSERT must NOT run for pre-lock duplicate"
        )
        assert not pool.conn.has_ingestion_events_insert(), (
            "shared.ingestion_events INSERT must NOT run for pre-lock duplicate"
        )

    async def test_inside_lock_duplicate_skips_ingestion_events(self) -> None:
        """Duplicate detected inside advisory lock returns early — no DB inserts."""
        existing_id = uuid.uuid4()
        # inner_existing triggers the inside-lock re-check path
        pool = _FakePool(inner_existing={"request_id": existing_id})

        result = await ingest_v1(
            pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
        )

        assert result.duplicate is True
        assert result.request_id == existing_id
        assert not pool.conn.has_message_inbox_insert(), (
            "message_inbox INSERT must NOT run when inside-lock duplicate detected"
        )
        assert not pool.conn.has_ingestion_events_insert(), (
            "shared.ingestion_events INSERT must NOT run when inside-lock duplicate detected"
        )

    async def test_duplicate_returns_existing_request_id(self) -> None:
        """The request_id for a duplicate equals the pre-existing row's id."""
        pool = _FakePool()
        canonical_id = uuid.uuid4()
        pool.set_outer_existing({"request_id": canonical_id})

        result = await ingest_v1(
            pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
        )

        assert result.request_id == canonical_id


# ---------------------------------------------------------------------------
# Tests: ensure_partition called outside transaction (bu-v8ip fix)
# ---------------------------------------------------------------------------


class TestEnsurePartitionOutsideTransaction:
    """ensure_partition must be called via pool.execute() (auto-commit), not
    inside the advisory-lock transaction.

    Background (bu-v8ip): If ensure_partition runs inside a transaction and
    that transaction rolls back (e.g. shared.ingestion_events missing, network
    error), the newly-created partition is also dropped, causing every
    subsequent insert to fail in a tight loop.

    The fix (bu-v8ip) moves ensure_partition to a pool.execute() call BEFORE
    the transaction block so that DDL commits immediately and independently.
    """

    async def test_ensure_partition_called_via_pool_execute(self) -> None:
        """ensure_partition must be called on pool (auto-commit), not conn (inside tx)."""
        pool = _FakePool()
        envelope = _telegram_envelope(update_id="90001")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        # pool.pool_execute_calls captures calls routed through pool.execute()
        # (outside the transaction).  At least one should be ensure_partition.
        ensure_partition_calls = [
            sql
            for sql, _ in pool.pool_execute_calls
            if "switchboard_message_inbox_ensure_partition" in sql
        ]
        assert len(ensure_partition_calls) == 1, (
            "ensure_partition must be called exactly once via pool.execute() "
            "(outside the advisory-lock transaction)"
        )

    async def test_ensure_partition_not_in_conn_execute_calls(self) -> None:
        """ensure_partition must NOT appear in conn.execute_calls (inside-tx path)."""
        pool = _FakePool()
        envelope = _telegram_envelope(update_id="90002")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        # conn.execute_calls are calls inside the advisory-lock transaction.
        conn_ensure_partition_calls = [
            sql
            for sql, _ in pool.conn.execute_calls
            if "switchboard_message_inbox_ensure_partition" in sql
        ]
        assert len(conn_ensure_partition_calls) == 0, (
            "ensure_partition must NOT be called inside the advisory-lock "
            "transaction (conn.execute); a transaction rollback would drop "
            "the newly-created partition"
        )

    async def test_pre_lock_duplicate_skips_ensure_partition(self) -> None:
        """Pre-lock duplicate detection exits early — ensure_partition is not called."""
        pool = _FakePool()
        existing_id = uuid.uuid4()
        pool.set_outer_existing({"request_id": existing_id})

        result = await ingest_v1(
            pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
        )

        assert result.duplicate is True
        ensure_partition_calls = [
            sql
            for sql, _ in pool.pool_execute_calls
            if "switchboard_message_inbox_ensure_partition" in sql
        ]
        assert len(ensure_partition_calls) == 0, (
            "ensure_partition must NOT be called for pre-lock duplicate "
            "(no insert needed, no partition required)"
        )

    async def test_ensure_partition_failure_raises_runtime_error(self) -> None:
        """A failure during ensure_partition is caught and re-raised as RuntimeError."""
        pool = _FakePool()
        pool.set_pool_execute_to_raise(ValueError("DB connection failed"))
        envelope = _telegram_envelope(update_id="90003")

        with pytest.raises(
            RuntimeError, match="Failed to ensure message_inbox partition: DB connection failed"
        ):
            await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)
