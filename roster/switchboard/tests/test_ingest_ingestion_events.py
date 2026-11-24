"""Unit tests for public.ingestion_events write inside ingest_v1 pipeline.

Verifies the invariants stated in bu-0b7.3:
- Accepting a new envelope inserts into BOTH message_inbox AND
  public.ingestion_events in the same transaction.
- Duplicate detection (either pre-lock or inside the advisory-lock
  re-check) skips the public.ingestion_events insert.
- The request_id returned in IngestAcceptedResponse equals the id
  that would be written to public.ingestion_events.
- Fields written to public.ingestion_events match the envelope.

Also verifies the ensure_partition outside-transaction invariant (bu-v8ip):
- ensure_partition is called via pool.execute() (auto-commit / outside the
  advisory-lock transaction) so that a transaction rollback cannot drop a
  newly-created partition.
- Pre-lock duplicate detection skips ensure_partition entirely (no inserts
  needed, no partition required).

The accept/duplicate column contract runs ``ingest_v1`` against the real
core + switchboard chains and reads the rows back. The transaction-boundary,
failure-classification and publish tests use strict ``fake_pool`` seams whose
pool and acquired connection are separate mocks, so a statement's placement
(auto-commit pool vs. the advisory-lock transaction) stays observable.
"""

from __future__ import annotations

import shutil
import time
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.testing.fake_pool import FakeConnection, FakePool, fake_pool
from butlers.testing.records import row
from butlers.tools.switchboard.ingestion.ingest import (
    IngestPersistenceError,
    IngestStoreRefusedError,
    ingest_v1,
)

pytestmark = pytest.mark.unit


docker_available = shutil.which("docker") is not None

_PARTITION = "switchboard_message_inbox_ensure_partition"
_EVENTS_INSERT = "INSERT INTO public.ingestion_events"
_INBOX_INSERT = "INSERT INTO switchboard.message_inbox"


# ---------------------------------------------------------------------------
# Pool seams
# ---------------------------------------------------------------------------


def _ingest_pool(
    *,
    pre_lock_existing: object = None,
    in_lock_existing: object = None,
    partition_error: BaseException | None = None,
    transaction_error: BaseException | None = None,
) -> FakePool:
    """A pool whose auto-commit calls and locked-transaction calls are separate mocks.

    ``pool.execute``/``pool.fetchrow`` are the auto-commit path (partition DDL,
    pre-lock dedupe lookups, fleet NOTIFY); ``pool.connection`` is the acquired
    connection running the advisory-lock transaction.
    """
    pool = fake_pool(
        fetchrow=pre_lock_existing,
        execute=AsyncMock(side_effect=partition_error) if partition_error else "SELECT 1",
    )
    pool.connection = FakeConnection(
        fetchrow=in_lock_existing,
        execute=AsyncMock(side_effect=transaction_error) if transaction_error else "OK",
    )
    return pool


def _sqls(mock: AsyncMock) -> list[str]:
    return [call.args[0] for call in mock.await_args_list]


def _partition_calls(mock: AsyncMock) -> list[str]:
    return [sql for sql in _sqls(mock) if _PARTITION in sql]


def _has_insert(pool: FakePool, statement: str) -> bool:
    return any(statement in sql for sql in _sqls(pool.connection.execute))


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
    reply_target_ref = thread_id or "12345:1"
    chat_id = reply_target_ref.partition(":")[0]
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram_bot",
            "provider": "telegram",
            "endpoint_identity": bot_id,
        },
        "event": {
            "external_event_id": update_id,
            "external_conversation_id": f"telegram:{chat_id}",
            "reply_target_ref": reply_target_ref,
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
# Tests: accepted ingest writes public.ingestion_events (real database)
# ---------------------------------------------------------------------------


@pytest.fixture
async def switchboard_pool(db):
    """A private clone of the real core + switchboard chains, switchboard-scoped."""
    return await db(
        "core", "switchboard", schemas={"switchboard": "switchboard"}, schema="switchboard"
    )


def _metadata_email_envelope() -> dict:
    # Tier 2 envelopes require payload.raw=null per the contract validator.
    return {
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
        "payload": {"raw": None, "normalized_text": "Subject: Newsletter"},
        "control": {"ingestion_tier": "metadata", "policy_tier": "default"},
    }


def _pass_through_evaluator():
    from butlers.ingestion_policy import IngestionPolicyEvaluator

    # No rules: every envelope is a pass_through with no target butler.
    evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
    evaluator._rules = []
    evaluator._last_loaded_at = time.monotonic()
    return evaluator


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_accepted_ingests_write_one_matching_ingestion_event_each(switchboard_pool):
    """Each accepted envelope writes one ingestion_events row mirroring it; a resubmit
    is a duplicate that writes nothing new."""
    full = _email_envelope(
        message_id="<evid@example.com>",
        mailbox="inbox@mybutler.com",
        sender="bob@example.com",
        thread_id="thread-xyz",
        policy_tier="high_priority",
    )
    full["sender"]["display_name"] = "Bob Roe"

    async def counts() -> tuple[int, int]:
        events = await switchboard_pool.fetchval("SELECT COUNT(*) FROM public.ingestion_events")
        inbox = await switchboard_pool.fetchval("SELECT COUNT(*) FROM switchboard.message_inbox")
        return events, inbox

    before_events, before_inbox = await counts()
    accepted = {
        "full": await ingest_v1(
            switchboard_pool, full, policy_evaluator=None, enable_thread_affinity=False
        ),
        "metadata": await ingest_v1(
            switchboard_pool,
            _metadata_email_envelope(),
            policy_evaluator=None,
            enable_thread_affinity=False,
        ),
        "telegram": await ingest_v1(
            switchboard_pool,
            _telegram_envelope(update_id="113", thread_id="12345:7"),
            policy_evaluator=None,
            enable_thread_affinity=False,
        ),
        "triaged": await ingest_v1(
            switchboard_pool,
            _email_envelope(message_id="<triage@example.com>"),
            policy_evaluator=_pass_through_evaluator(),
            enable_thread_affinity=False,
        ),
    }
    assert all(not result.duplicate for result in accepted.values())

    rows = {
        name: await switchboard_pool.fetchrow(
            "SELECT * FROM public.ingestion_events WHERE id = $1", result.request_id
        )
        for name, result in accepted.items()
    }
    full_row = rows["full"]
    assert full_row is not None, "ingestion_events.id must equal the returned request_id"
    assert full_row["source_channel"] == "email"
    assert full_row["source_provider"] == "gmail"
    assert full_row["source_endpoint_identity"] == "inbox@mybutler.com"
    assert full_row["source_sender_identity"] == "bob@example.com"
    assert full_row["source_sender_display_name"] == "Bob Roe"
    assert full_row["source_thread_identity"] == "thread-xyz"
    assert full_row["external_event_id"] == "<evid@example.com>"
    assert full_row["dedupe_strategy"] == "connector_api"
    assert full_row["ingestion_tier"] == "full"
    assert full_row["policy_tier"] == "high_priority"
    # No evaluator: no triage decision is recorded.
    assert full_row["triage_decision"] is None and full_row["triage_target"] is None

    metadata_row = rows["metadata"]
    assert metadata_row["ingestion_tier"] == "metadata"
    assert metadata_row["source_thread_identity"] is None
    assert metadata_row["source_sender_display_name"] is None

    # correct_route replays this column as notify's source_thread_identity,
    # so a split producer stores its reply target, not its conversation key.
    assert rows["telegram"]["source_channel"] == "telegram_bot"
    assert rows["telegram"]["source_thread_identity"] == "12345:7"

    assert rows["triaged"]["triage_decision"] == "pass_through"
    assert rows["triaged"]["triage_target"] is None

    # The inbox row and the event row share one id and one received_at.
    for name, result in accepted.items():
        inbox_received_at = await switchboard_pool.fetchval(
            "SELECT received_at FROM switchboard.message_inbox WHERE id = $1", result.request_id
        )
        assert inbox_received_at is not None
        assert inbox_received_at == rows[name]["received_at"]

    # Resubmitting is a duplicate: the canonical id comes back and no row is added.
    again = await ingest_v1(
        switchboard_pool, full, policy_evaluator=None, enable_thread_affinity=False
    )
    assert again.duplicate is True
    assert again.request_id == accepted["full"].request_id
    assert await counts() == (before_events + 4, before_inbox + 4)


# ---------------------------------------------------------------------------
# Tests: duplicate detection inside the advisory lock
# ---------------------------------------------------------------------------


class TestIngestionEventsSkippedOnDuplicate:
    """public.ingestion_events is NOT written for duplicate submissions."""

    async def test_inside_lock_duplicate_skips_ingestion_events(self) -> None:
        """Duplicate detected inside advisory lock returns early — no DB inserts."""
        existing_id = uuid.uuid4()
        # The pre-lock lookups miss; the re-check inside the lock finds the row.
        pool = _ingest_pool(in_lock_existing=row(request_id=existing_id))

        result = await ingest_v1(
            pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
        )

        assert result.duplicate is True
        assert result.request_id == existing_id
        assert not _has_insert(pool, _INBOX_INSERT), (
            "message_inbox INSERT must NOT run when inside-lock duplicate detected"
        )
        assert not _has_insert(pool, _EVENTS_INSERT), (
            "public.ingestion_events INSERT must NOT run when inside-lock duplicate detected"
        )


# ---------------------------------------------------------------------------
# Tests: ensure_partition called outside transaction (bu-v8ip fix)
# ---------------------------------------------------------------------------


class TestEnsurePartitionOutsideTransaction:
    """ensure_partition must be called via pool.execute() (auto-commit), not
    inside the advisory-lock transaction.

    Background (bu-v8ip): If ensure_partition runs inside a transaction and
    that transaction rolls back (e.g. public.ingestion_events missing, network
    error), the newly-created partition is also dropped, causing every
    subsequent insert to fail in a tight loop.

    The fix (bu-v8ip) moves ensure_partition to a pool.execute() call BEFORE
    the transaction block so that DDL commits immediately and independently.
    """

    async def test_ensure_partition_called_via_pool_execute(self) -> None:
        """ensure_partition must be called on pool (auto-commit), not conn (inside tx)."""
        pool = _ingest_pool()
        envelope = _telegram_envelope(update_id="90001")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        # pool.execute is the auto-commit path, outside the transaction.
        assert _has_insert(pool, _EVENTS_INSERT)
        assert len(_partition_calls(pool.execute)) == 1, (
            "ensure_partition must be called exactly once via pool.execute() "
            "(outside the advisory-lock transaction)"
        )

    async def test_ensure_partition_not_in_conn_execute_calls(self) -> None:
        """ensure_partition must NOT appear in conn.execute_calls (inside-tx path)."""
        pool = _ingest_pool()
        envelope = _telegram_envelope(update_id="90002")

        await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)

        # pool.connection.execute runs inside the advisory-lock transaction.
        assert _has_insert(pool, _INBOX_INSERT)
        assert len(_partition_calls(pool.connection.execute)) == 0, (
            "ensure_partition must NOT be called inside the advisory-lock "
            "transaction (conn.execute); a transaction rollback would drop "
            "the newly-created partition"
        )

    async def test_pre_lock_duplicate_skips_ensure_partition(self) -> None:
        """Pre-lock duplicate detection exits early — ensure_partition is not called."""
        existing_id = uuid.uuid4()
        pool = _ingest_pool(pre_lock_existing=row(request_id=existing_id))

        result = await ingest_v1(
            pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
        )

        assert result.duplicate is True
        assert result.request_id == existing_id
        pool.acquire.assert_not_called()  # no transaction, so no inserts at all
        assert len(_partition_calls(pool.execute)) == 0, (
            "ensure_partition must NOT be called for pre-lock duplicate "
            "(no insert needed, no partition required)"
        )

    async def test_ensure_partition_failure_raises_persistence_error(self) -> None:
        """A transport failure during ensure_partition is re-raised as the transient error."""
        pool = _ingest_pool(partition_error=ConnectionResetError("DB connection failed"))
        envelope = _telegram_envelope(update_id="90003")

        with pytest.raises(
            IngestPersistenceError,
            match="Failed to ensure message_inbox partition: DB connection failed",
        ):
            await ingest_v1(pool, envelope, policy_evaluator=None, enable_thread_affinity=False)


# ---------------------------------------------------------------------------
# Tests: persistence failures split into transient vs deterministic (bu-u5euwb.33)
# ---------------------------------------------------------------------------

# Transport-class failures: the same envelope may persist on a later attempt.
_TRANSIENT_FAILURES = [
    ConnectionResetError("reset"),
    TimeoutError("timed out"),
    asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed"),
    asyncpg.exceptions.ConnectionFailureError("08006"),
    asyncpg.exceptions.SerializationError("40001"),
    asyncpg.exceptions.DeadlockDetectedError("40P01"),
    asyncpg.exceptions.TooManyConnectionsError("53300"),
    asyncpg.exceptions.AdminShutdownError("57P01"),
    asyncpg.exceptions.CannotConnectNowError("57P03"),
    asyncpg.exceptions.QueryCanceledError("57014"),
]

# Deterministic failures: this envelope fails the same way on every attempt, so a
# connector that held its checkpoint on it would wedge forever.
_DETERMINISTIC_FAILURES = [
    asyncpg.exceptions.DataError("22000"),
    asyncpg.exceptions.StringDataRightTruncationError("22001"),
    asyncpg.exceptions.UntranslatableCharacterError("22P05"),
    asyncpg.exceptions.UniqueViolationError("23505"),
    asyncpg.exceptions.NotNullViolationError("23502"),
    asyncpg.exceptions.CheckViolationError("23514"),
    asyncpg.exceptions.UndefinedColumnError("42703"),
    ValueError("unexpected"),
]


def _ids(exc: Exception) -> str:
    return type(exc).__name__


class TestPersistenceFailureClassification:
    @pytest.mark.parametrize("failure", _TRANSIENT_FAILURES, ids=_ids)
    async def test_transient_insert_failure_is_retryable(self, failure: Exception) -> None:
        # Any failure inside the dedup transaction (lock or INSERT) is classified.
        pool = _ingest_pool(transaction_error=failure)

        with pytest.raises(IngestPersistenceError) as raised:
            await ingest_v1(
                pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
            )

        assert raised.value.__cause__ is failure

    @pytest.mark.parametrize("failure", _DETERMINISTIC_FAILURES, ids=_ids)
    async def test_deterministic_insert_failure_is_not_retryable(self, failure: Exception) -> None:
        # Any failure inside the dedup transaction (lock or INSERT) is classified.
        pool = _ingest_pool(transaction_error=failure)

        with pytest.raises(IngestStoreRefusedError) as raised:
            await ingest_v1(
                pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
            )

        assert not isinstance(raised.value, IngestPersistenceError)
        assert raised.value.__cause__ is failure

    @pytest.mark.parametrize("failure", _TRANSIENT_FAILURES, ids=_ids)
    async def test_transient_partition_failure_is_retryable(self, failure: Exception) -> None:
        pool = _ingest_pool(partition_error=failure)

        with pytest.raises(IngestPersistenceError):
            await ingest_v1(
                pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
            )

    @pytest.mark.parametrize("failure", _DETERMINISTIC_FAILURES, ids=_ids)
    async def test_deterministic_partition_failure_is_not_retryable(
        self, failure: Exception
    ) -> None:
        pool = _ingest_pool(partition_error=failure)

        with pytest.raises(IngestStoreRefusedError):
            await ingest_v1(
                pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
            )


# ---------------------------------------------------------------------------
# Tests: "ingestion" event fans onto the multiplexed fleet event bus (bu-h8ioq)
# ---------------------------------------------------------------------------


class TestIngestionEventPublishesOnBus:
    """A new (non-duplicate) ingest publishes an "ingestion" fleet event."""

    # Spec: REQ-core-fleet-events-009
    @pytest.mark.parametrize("durable_write_failure", [False, True])
    async def test_new_ingest_publishes_ingestion_event_cross_process(
        self, durable_write_failure: bool
    ) -> None:
        """The switchboard daemon publishes through the RFC 0022 bridge."""
        from unittest.mock import patch

        pool = _ingest_pool(
            transaction_error=(
                RuntimeError("synthetic durable write failure") if durable_write_failure else None
            )
        )
        envelope = _telegram_envelope(update_id="70000")
        mock_publish = AsyncMock()

        with patch("butlers.fleet_events.publish_fleet_event", new=mock_publish):
            if durable_write_failure:
                with pytest.raises(RuntimeError):
                    await ingest_v1(
                        pool, envelope, policy_evaluator=None, enable_thread_affinity=False
                    )
                mock_publish.assert_not_awaited()
                return
            result = await ingest_v1(
                pool, envelope, policy_evaluator=None, enable_thread_affinity=False
            )

        mock_publish.assert_awaited_once()
        call_args = mock_publish.call_args
        assert call_args.args[0] is pool
        assert call_args.args[1] == "ingestion"
        assert call_args.args[2] == {
            "request_id": str(result.request_id),
            "source_channel": "telegram_bot",
            "triage_decision": None,
            "triage_target": None,
        }

    # Spec: REQ-core-fleet-events-009
    async def test_duplicate_ingest_does_not_publish_ingestion_event(self) -> None:
        """A duplicate submission writes no new row, so no event should publish."""
        from unittest.mock import patch

        pool = _ingest_pool(pre_lock_existing=row(request_id=uuid.uuid4()))
        mock_publish = AsyncMock()

        with patch("butlers.fleet_events.publish_fleet_event", new=mock_publish):
            result = await ingest_v1(
                pool, _telegram_envelope(), policy_evaluator=None, enable_thread_affinity=False
            )

        assert result.duplicate is True
        mock_publish.assert_not_awaited()
