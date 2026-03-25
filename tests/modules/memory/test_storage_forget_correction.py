"""Tests for correction-driven retraction in forget_memory() storage layer.

Covers tasks 5.1-5.4 from openspec/changes/error-recovery-corrections/tasks.md:
  5.1 — correction_id and correction_reason params on forget_memory
  5.2 — memory metadata updated with correction provenance
  5.3 — memory_events row inserted with correction_driven_retraction event type
  5.4 — guard against already-retracted or superseded memories
"""

from __future__ import annotations

import importlib.util
import json
import uuid
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
forget_memory = _mod.forget_memory
CorrectionGuardError = _mod.CorrectionGuardError
_forget_plain = _mod._forget_plain
_forget_with_correction_provenance = _mod._forget_with_correction_provenance
_check_correction_preconditions = _mod._check_correction_preconditions

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def memory_id() -> uuid.UUID:
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture()
def correction_id() -> str:
    return "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture()
def mock_pool_update1() -> AsyncMock:
    """Pool whose execute always returns 'UPDATE 1'."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


def _make_conn(*, execute_result: str = "UPDATE 1") -> AsyncMock:
    """Build a mock connection whose execute returns *execute_result*."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=execute_result)

    # Mimic asyncpg's async context manager for conn.transaction()
    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock(return_value=None)
    tx_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_cm)
    return conn


def _make_pool_with_conn(conn: AsyncMock) -> AsyncMock:
    """Pool that yields *conn* via pool.acquire().__aenter__."""
    pool = AsyncMock()
    acq_cm = MagicMock()
    acq_cm.__aenter__ = AsyncMock(return_value=conn)
    acq_cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acq_cm)
    return pool


# ---------------------------------------------------------------------------
# CorrectionGuardError
# ---------------------------------------------------------------------------


class TestCorrectionGuardError:
    """Basic attributes of the guard exception."""

    def test_has_reason_and_message_attributes(self) -> None:
        """CorrectionGuardError exposes reason and message."""
        exc = CorrectionGuardError(reason="already_retracted", message="It's gone.")
        assert exc.reason == "already_retracted"
        assert exc.message == "It's gone."
        assert str(exc) == "It's gone."


# ---------------------------------------------------------------------------
# _check_correction_preconditions — fact guards
# ---------------------------------------------------------------------------


class TestCheckCorrectionPreconditionsFact:
    """Pre-flight guard for facts."""

    async def test_retracted_fact_raises_already_retracted(self, memory_id: uuid.UUID) -> None:
        """A fact with validity='retracted' raises CorrectionGuardError(already_retracted)."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"validity": "retracted"})
        with pytest.raises(CorrectionGuardError) as exc_info:
            await _check_correction_preconditions(pool, "fact", memory_id)
        assert exc_info.value.reason == "already_retracted"
        assert "already retracted" in exc_info.value.message.lower()

    async def test_superseded_fact_raises_already_superseded(self, memory_id: uuid.UUID) -> None:
        """A fact with validity='superseded' raises CorrectionGuardError(already_superseded)."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"validity": "superseded"})
        with pytest.raises(CorrectionGuardError) as exc_info:
            await _check_correction_preconditions(pool, "fact", memory_id)
        assert exc_info.value.reason == "already_superseded"
        assert "superseded" in exc_info.value.message.lower()

    async def test_active_fact_does_not_raise(self, memory_id: uuid.UUID) -> None:
        """An active fact passes the pre-flight check without error."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"validity": "active"})
        await _check_correction_preconditions(pool, "fact", memory_id)  # no raise

    async def test_not_found_fact_does_not_raise(self, memory_id: uuid.UUID) -> None:
        """When the fact row is not found, the guard passes (forget will return False)."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await _check_correction_preconditions(pool, "fact", memory_id)  # no raise

    async def test_queries_facts_table_by_id(self, memory_id: uuid.UUID) -> None:
        """The guard queries the facts table using the memory_id."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await _check_correction_preconditions(pool, "fact", memory_id)
        sql = pool.fetchrow.call_args[0][0]
        assert "FROM facts" in sql
        assert pool.fetchrow.call_args[0][1] == memory_id


# ---------------------------------------------------------------------------
# _check_correction_preconditions — rule guards
# ---------------------------------------------------------------------------


class TestCheckCorrectionPreconditionsRule:
    """Pre-flight guard for rules."""

    async def test_forgotten_rule_raises_already_forgotten(self, memory_id: uuid.UUID) -> None:
        """A rule with metadata.forgotten=true raises CorrectionGuardError(already_forgotten)."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"metadata": {"forgotten": True}})
        with pytest.raises(CorrectionGuardError) as exc_info:
            await _check_correction_preconditions(pool, "rule", memory_id)
        assert exc_info.value.reason == "already_forgotten"

    async def test_not_forgotten_rule_does_not_raise(self, memory_id: uuid.UUID) -> None:
        """A rule without forgotten=true passes the check."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"metadata": {"some_key": "value"}})
        await _check_correction_preconditions(pool, "rule", memory_id)  # no raise

    async def test_rule_with_null_metadata_does_not_raise(self, memory_id: uuid.UUID) -> None:
        """A rule with None metadata passes the check."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"metadata": None})
        await _check_correction_preconditions(pool, "rule", memory_id)  # no raise

    async def test_rule_with_string_metadata_parses_json(self, memory_id: uuid.UUID) -> None:
        """If metadata is a JSON string (asyncpg edge case), it is parsed correctly."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"metadata": json.dumps({"forgotten": True})})
        with pytest.raises(CorrectionGuardError) as exc_info:
            await _check_correction_preconditions(pool, "rule", memory_id)
        assert exc_info.value.reason == "already_forgotten"

    async def test_queries_rules_table_by_id(self, memory_id: uuid.UUID) -> None:
        """The guard queries the rules table using the memory_id."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await _check_correction_preconditions(pool, "rule", memory_id)
        sql = pool.fetchrow.call_args[0][0]
        assert "FROM rules" in sql
        assert pool.fetchrow.call_args[0][1] == memory_id


# ---------------------------------------------------------------------------
# _check_correction_preconditions — episodes (no blocking guard)
# ---------------------------------------------------------------------------


class TestCheckCorrectionPreconditionsEpisode:
    """Episodes have no blocking guard — pre-flight always passes."""

    async def test_episode_does_not_query_db(self, memory_id: uuid.UUID) -> None:
        """Episode pre-flight check does not hit the DB."""
        pool = AsyncMock()
        await _check_correction_preconditions(pool, "episode", memory_id)
        pool.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# _forget_with_correction_provenance
# ---------------------------------------------------------------------------


class TestForgetWithCorrectionProvenance:
    """Atomic retraction + provenance writes."""

    async def test_fact_sets_validity_retracted_and_merges_provenance(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """For a fact, UPDATE sets validity=retracted and merges correction_id into metadata."""
        conn = _make_conn()
        pool = _make_pool_with_conn(conn)

        await _forget_with_correction_provenance(
            pool, "fact", memory_id, correction_id=correction_id, correction_reason="wrong data"
        )

        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "UPDATE facts" in first_call_sql
        assert "validity = 'retracted'" in first_call_sql
        assert "metadata" in first_call_sql

        # Second arg should be the memory_id
        assert conn.execute.call_args_list[0][0][1] == memory_id

        # Third arg should be a JSON string with correction provenance
        provenance_arg = conn.execute.call_args_list[0][0][2]
        provenance = json.loads(provenance_arg)
        assert provenance["correction_id"] == correction_id
        assert provenance["correction_reason"] == "wrong data"

    async def test_episode_sets_expires_at_and_merges_provenance(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """For an episode, UPDATE sets expires_at=now() and merges correction provenance."""
        conn = _make_conn()
        pool = _make_pool_with_conn(conn)

        await _forget_with_correction_provenance(
            pool, "episode", memory_id, correction_id=correction_id, correction_reason=None
        )

        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "UPDATE episodes" in first_call_sql
        assert "expires_at = now()" in first_call_sql
        assert "metadata" in first_call_sql

    async def test_rule_merges_forgotten_and_provenance(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """For a rule, UPDATE merges forgotten=true plus correction_id into metadata."""
        conn = _make_conn()
        pool = _make_pool_with_conn(conn)

        await _forget_with_correction_provenance(
            pool, "rule", memory_id, correction_id=correction_id, correction_reason="bad rule"
        )

        first_call_sql = conn.execute.call_args_list[0][0][0]
        assert "UPDATE rules" in first_call_sql
        assert "metadata" in first_call_sql

        rule_patch_arg = conn.execute.call_args_list[0][0][2]
        rule_patch = json.loads(rule_patch_arg)
        assert rule_patch["forgotten"] is True
        assert rule_patch["correction_id"] == correction_id
        assert rule_patch["correction_reason"] == "bad rule"

    async def test_inserts_memory_events_row_when_found(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """When memory is found (UPDATE 1), inserts a memory_events row."""
        conn = _make_conn(execute_result="UPDATE 1")
        pool = _make_pool_with_conn(conn)

        await _forget_with_correction_provenance(
            pool, "fact", memory_id, correction_id=correction_id, correction_reason="bad"
        )

        # Should have 2 execute calls: the UPDATE and the INSERT
        assert conn.execute.call_count == 2
        insert_sql = conn.execute.call_args_list[1][0][0]
        assert "INSERT INTO memory_events" in insert_sql
        assert "correction_driven_retraction" in insert_sql

    async def test_memory_events_payload_includes_correction_id(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """The memory_events payload includes correction_id and memory_id."""
        conn = _make_conn(execute_result="UPDATE 1")
        pool = _make_pool_with_conn(conn)

        await _forget_with_correction_provenance(
            pool, "fact", memory_id, correction_id=correction_id, correction_reason="reason"
        )

        insert_args = conn.execute.call_args_list[1][0]
        # Args: sql, memory_type, memory_id, payload_json
        payload = json.loads(insert_args[3])
        assert payload["correction_id"] == correction_id
        assert payload["memory_id"] == str(memory_id)
        assert payload["memory_type"] == "fact"
        assert payload["correction_reason"] == "reason"

    async def test_no_memory_events_row_when_not_found(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """When memory is not found (UPDATE 0), no memory_events row is inserted."""
        conn = _make_conn(execute_result="UPDATE 0")
        pool = _make_pool_with_conn(conn)

        result = await _forget_with_correction_provenance(
            pool, "fact", memory_id, correction_id=correction_id, correction_reason=None
        )

        assert result is False
        # Only the UPDATE was issued, no INSERT
        assert conn.execute.call_count == 1

    async def test_correction_reason_omitted_from_payload_when_none(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """When correction_reason is None, it is not included in the event payload."""
        conn = _make_conn(execute_result="UPDATE 1")
        pool = _make_pool_with_conn(conn)

        await _forget_with_correction_provenance(
            pool, "fact", memory_id, correction_id=correction_id, correction_reason=None
        )

        insert_args = conn.execute.call_args_list[1][0]
        payload = json.loads(insert_args[3])
        assert "correction_reason" not in payload

    async def test_returns_true_when_found(self, memory_id: uuid.UUID, correction_id: str) -> None:
        """Returns True when the memory row was updated."""
        conn = _make_conn(execute_result="UPDATE 1")
        pool = _make_pool_with_conn(conn)
        result = await _forget_with_correction_provenance(
            pool, "fact", memory_id, correction_id=correction_id, correction_reason=None
        )
        assert result is True

    async def test_returns_false_when_not_found(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """Returns False when no row matched the given ID."""
        conn = _make_conn(execute_result="UPDATE 0")
        pool = _make_pool_with_conn(conn)
        result = await _forget_with_correction_provenance(
            pool, "fact", memory_id, correction_id=correction_id, correction_reason=None
        )
        assert result is False

    async def test_memory_events_actor_is_correction_system(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """The memory_events row has actor='correction_system'."""
        conn = _make_conn(execute_result="UPDATE 1")
        pool = _make_pool_with_conn(conn)

        await _forget_with_correction_provenance(
            pool, "fact", memory_id, correction_id=correction_id, correction_reason=None
        )

        insert_sql = conn.execute.call_args_list[1][0][0]
        assert "correction_system" in insert_sql

    async def test_memory_events_memory_type_column_set(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """The memory_events INSERT passes memory_type as a positional arg."""
        conn = _make_conn(execute_result="UPDATE 1")
        pool = _make_pool_with_conn(conn)

        await _forget_with_correction_provenance(
            pool, "episode", memory_id, correction_id=correction_id, correction_reason=None
        )

        insert_args = conn.execute.call_args_list[1][0]
        # Args: sql, memory_type, memory_id, payload_json
        assert insert_args[1] == "episode"
        assert insert_args[2] == memory_id


# ---------------------------------------------------------------------------
# forget_memory (top-level) — correction path integration
# ---------------------------------------------------------------------------


class TestForgetMemoryWithCorrection:
    """Top-level forget_memory with correction_id wiring."""

    async def test_no_correction_id_calls_plain_path(self, memory_id: uuid.UUID) -> None:
        """Without correction_id, the plain (non-correction) path is used."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")
        result = await forget_memory(pool, "fact", memory_id)
        assert result is True
        pool.execute.assert_awaited_once()
        sql = pool.execute.call_args[0][0]
        assert "UPDATE facts" in sql
        assert "validity = 'retracted'" in sql
        # Plain path does NOT modify metadata
        assert "correction" not in sql

    async def test_with_correction_id_runs_guard(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """With correction_id, the pre-flight guard is executed."""
        pool = AsyncMock()
        # Guard fetchrow returns retracted → should raise
        pool.fetchrow = AsyncMock(return_value={"validity": "retracted"})

        with pytest.raises(CorrectionGuardError) as exc_info:
            await forget_memory(pool, "fact", memory_id, correction_id=correction_id)
        assert exc_info.value.reason == "already_retracted"

    async def test_with_correction_id_superseded_fact_raises(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """Superseded fact raises CorrectionGuardError."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"validity": "superseded"})
        with pytest.raises(CorrectionGuardError) as exc_info:
            await forget_memory(pool, "fact", memory_id, correction_id=correction_id)
        assert exc_info.value.reason == "already_superseded"

    async def test_with_correction_id_forgotten_rule_raises(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """Forgotten rule raises CorrectionGuardError."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"metadata": {"forgotten": True}})
        with pytest.raises(CorrectionGuardError) as exc_info:
            await forget_memory(pool, "rule", memory_id, correction_id=correction_id)
        assert exc_info.value.reason == "already_forgotten"

    async def test_with_correction_id_active_fact_proceeds(
        self, memory_id: uuid.UUID, correction_id: str
    ) -> None:
        """Active fact with correction_id goes through the provenance path."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"validity": "active"})

        conn = _make_conn(execute_result="UPDATE 1")
        acq_cm = MagicMock()
        acq_cm.__aenter__ = AsyncMock(return_value=conn)
        acq_cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=acq_cm)

        result = await forget_memory(
            pool,
            "fact",
            memory_id,
            correction_id=correction_id,
            correction_reason="wrong info",
        )
        assert result is True
        # UPDATE + INSERT
        assert conn.execute.call_count == 2

    async def test_invalid_type_raises_value_error(self, memory_id: uuid.UUID) -> None:
        """Invalid memory_type raises ValueError regardless of correction params."""
        pool = AsyncMock()
        with pytest.raises(ValueError, match="Invalid memory_type"):
            await forget_memory(pool, "bogus", memory_id, correction_id="some-id")
