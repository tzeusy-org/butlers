"""Integration-style tests for memory storage operations.

These tests verify cross-cutting concerns that span multiple storage functions
working together: store->get, store->forget, full supersession flow,
permanence->decay mapping, and reference bumping.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load storage module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_STORAGE_PATH = Path(__file__).resolve().parent.parent / "storage.py"


def _load_storage_module():
    """Load storage.py with sentence_transformers mocked out."""
    mock_st = MagicMock()
    mock_st.SentenceTransformer.return_value = MagicMock()
    # sys.modules.setdefault("sentence_transformers", mock_st)

    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
store_episode = _mod.store_episode
store_fact = _mod.store_fact
store_rule = _mod.store_rule
get_memory = _mod.get_memory
forget_memory = _mod.forget_memory
_PERMANENCE_DECAY = _mod._PERMANENCE_DECAY

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Async context manager helper for mocking asyncpg pool/conn
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def embedding_engine() -> MagicMock:
    """Return a mock EmbeddingEngine that produces a deterministic 384-d vector."""
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


@pytest.fixture()
def simple_pool() -> AsyncMock:
    """Return a simple AsyncMock pool for store_episode/store_rule/get_memory/forget_memory."""
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


@pytest.fixture()
def fact_pool():
    """Return (pool, conn) mocks wired up for store_fact (pool.acquire/conn.transaction).

    By default conn.fetchrow returns None (no existing fact).
    """
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM(None))

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))

    return pool, conn


# ---------------------------------------------------------------------------
# TestStoreAndGet -- store each type, then get_memory with correct table
# ---------------------------------------------------------------------------


class TestStoreAndGet:
    """Verify that store_*() returns a UUID that get_memory() can use
    with the correct table for each memory type."""

    async def test_store_episode_then_get_memory(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """store_episode returns a UUID; get_memory('episode', id) queries the episodes table."""
        episode_id = await store_episode(
            simple_pool, "User had a meeting", "test-butler", embedding_engine
        )
        assert isinstance(episode_id, uuid.UUID)

        # Now set up pool for get_memory
        mock_record = {"id": episode_id, "content": "User had a meeting", "reference_count": 1}
        simple_pool.fetchrow = AsyncMock(return_value=mock_record)

        result = await get_memory(simple_pool, "episode", episode_id)

        assert result is not None
        assert result["id"] == episode_id
        sql = simple_pool.fetchrow.call_args[0][0]
        assert "UPDATE episodes" in sql
        assert simple_pool.fetchrow.call_args[0][1] == episode_id

    async def test_store_fact_then_get_memory(self, fact_pool, embedding_engine: MagicMock) -> None:
        """store_fact returns a UUID; get_memory('fact', id) queries the facts table."""
        pool, _conn = fact_pool
        fact_id = await store_fact(pool, "user", "city", "Berlin", embedding_engine)
        assert isinstance(fact_id, uuid.UUID)

        # Wire up a simple pool for get_memory (it uses pool.fetchrow directly)
        get_pool = AsyncMock()
        mock_record = {
            "id": fact_id,
            "subject": "user",
            "predicate": "city",
            "reference_count": 1,
        }
        get_pool.fetchrow = AsyncMock(return_value=mock_record)

        result = await get_memory(get_pool, "fact", fact_id)

        assert result is not None
        assert result["id"] == fact_id
        sql = get_pool.fetchrow.call_args[0][0]
        assert "UPDATE facts" in sql
        assert get_pool.fetchrow.call_args[0][1] == fact_id

    async def test_store_rule_then_get_memory(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """store_rule returns a UUID; get_memory('rule', id) queries the rules table."""
        rule_id = await store_rule(simple_pool, "Always greet the user politely", embedding_engine)
        assert isinstance(rule_id, uuid.UUID)

        # Now set up pool for get_memory
        mock_record = {"id": rule_id, "content": "Always greet the user politely"}
        simple_pool.fetchrow = AsyncMock(return_value=mock_record)

        result = await get_memory(simple_pool, "rule", rule_id)

        assert result is not None
        assert result["id"] == rule_id
        sql = simple_pool.fetchrow.call_args[0][0]
        assert "UPDATE rules" in sql
        assert simple_pool.fetchrow.call_args[0][1] == rule_id


# ---------------------------------------------------------------------------
# TestStoreAndForget -- store each type, then forget with correct strategy
# ---------------------------------------------------------------------------


class TestStoreAndForget:
    """Verify that store_*() followed by forget_memory() applies the
    correct soft-delete strategy for each memory type."""

    async def test_store_fact_then_forget_sets_retracted(
        self, fact_pool, embedding_engine: MagicMock
    ) -> None:
        """store_fact -> forget_memory('fact', id) sets validity='retracted'."""
        pool, _conn = fact_pool
        fact_id = await store_fact(pool, "user", "city", "Berlin", embedding_engine)

        # Set up a simple pool for forget_memory
        forget_pool = AsyncMock()
        forget_pool.execute = AsyncMock(return_value="UPDATE 1")

        result = await forget_memory(forget_pool, "fact", fact_id)

        assert result is True
        sql = forget_pool.execute.call_args[0][0]
        assert "UPDATE facts" in sql
        assert "validity = 'retracted'" in sql
        assert forget_pool.execute.call_args[0][1] == fact_id

    async def test_store_episode_then_forget_sets_expires_at(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """store_episode -> forget_memory('episode', id) sets expires_at=now()."""
        episode_id = await store_episode(
            simple_pool, "User mentioned a trip", "test-butler", embedding_engine
        )

        # Set up a separate pool for forget_memory
        forget_pool = AsyncMock()
        forget_pool.execute = AsyncMock(return_value="UPDATE 1")

        result = await forget_memory(forget_pool, "episode", episode_id)

        assert result is True
        sql = forget_pool.execute.call_args[0][0]
        assert "UPDATE episodes" in sql
        assert "expires_at = now()" in sql
        assert forget_pool.execute.call_args[0][1] == episode_id

    async def test_store_rule_then_forget_merges_forgotten_flag(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """store_rule -> forget_memory('rule', id) merges {"forgotten": true} into metadata."""
        rule_id = await store_rule(
            simple_pool, "Never delete without confirmation", embedding_engine
        )

        # Set up a separate pool for forget_memory
        forget_pool = AsyncMock()
        forget_pool.execute = AsyncMock(return_value="UPDATE 1")

        result = await forget_memory(forget_pool, "rule", rule_id)

        assert result is True
        sql = forget_pool.execute.call_args[0][0]
        assert "UPDATE rules" in sql
        assert '"forgotten": true' in sql
        assert forget_pool.execute.call_args[0][1] == rule_id

    async def test_forget_returns_false_when_not_found(self) -> None:
        """forget_memory returns False when no matching row exists."""
        pool = AsyncMock()
        pool.execute = AsyncMock(return_value="UPDATE 0")
        fake_id = uuid.uuid4()

        result = await forget_memory(pool, "fact", fake_id)

        assert result is False


# ---------------------------------------------------------------------------
# TestFactSupersessionFlow -- full end-to-end supersession scenario
# ---------------------------------------------------------------------------


class TestFactSupersessionFlow:
    """Test the complete supersession flow when storing a fact that replaces
    an existing active fact with the same (subject, predicate)."""

    async def test_full_supersession_flow(self, embedding_engine: MagicMock) -> None:
        """Store a fact, then store another with the same subject+predicate.

        Verifies:
        1. Old fact is marked as 'superseded'
        2. New fact has supersedes_id pointing to old fact
        3. A memory_links row is created with relation='supersedes'
        4. Exactly 3 execute calls (UPDATE old, INSERT new, INSERT link)
        """
        # First store: no existing fact
        conn1 = AsyncMock()
        conn1.fetchrow = AsyncMock(return_value=None)
        conn1.execute = AsyncMock()
        conn1.transaction = MagicMock(return_value=_AsyncCM(None))
        pool1 = MagicMock()
        pool1.acquire = MagicMock(return_value=_AsyncCM(conn1))

        first_id = await store_fact(pool1, "user", "city", "Berlin", embedding_engine)
        # First store: only 1 execute (INSERT), no supersession
        assert conn1.execute.call_count == 1
        assert "INSERT INTO facts" in conn1.execute.call_args_list[0].args[0]

        # Second store: existing fact found
        conn2 = AsyncMock()
        conn2.fetchrow = AsyncMock(return_value={"id": first_id})
        conn2.execute = AsyncMock()
        conn2.transaction = MagicMock(return_value=_AsyncCM(None))
        pool2 = MagicMock()
        pool2.acquire = MagicMock(return_value=_AsyncCM(conn2))

        new_id = await store_fact(pool2, "user", "city", "Munich", embedding_engine)

        # Verify exactly 3 execute calls
        assert conn2.execute.call_count == 3

        # Call 1: UPDATE old fact to 'superseded'
        update_call = conn2.execute.call_args_list[0]
        assert "UPDATE facts SET validity = 'superseded'" in update_call.args[0]
        assert update_call.args[1] == first_id

        # Call 2: INSERT new fact with supersedes_id
        insert_call = conn2.execute.call_args_list[1]
        assert "INSERT INTO facts" in insert_call.args[0]
        # supersedes_id is parameter $13 -> index 13 in args
        assert insert_call.args[13] == first_id

        # Call 3: INSERT memory_links
        link_call = conn2.execute.call_args_list[2]
        assert "INSERT INTO memory_links" in link_call.args[0]
        assert "'supersedes'" in link_call.args[0]
        assert link_call.args[1] == new_id  # source is new fact
        assert link_call.args[2] == first_id  # target is old fact

    async def test_no_supersession_without_existing(
        self, fact_pool, embedding_engine: MagicMock
    ) -> None:
        """When no existing fact matches, there is no UPDATE and no link INSERT."""
        pool, conn = fact_pool
        # conn.fetchrow returns None by default (no existing fact)

        await store_fact(pool, "user", "city", "Berlin", embedding_engine)

        # Only 1 execute: the INSERT
        assert conn.execute.call_count == 1
        insert_call = conn.execute.call_args_list[0]
        assert "INSERT INTO facts" in insert_call.args[0]
        # supersedes_id ($13) should be None
        assert insert_call.args[13] is None

    async def test_supersession_preserves_content(self, embedding_engine: MagicMock) -> None:
        """The new fact's content is correctly stored during supersession."""
        old_id = uuid.uuid4()

        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"id": old_id})
        conn.execute = AsyncMock()
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        await store_fact(pool, "user", "city", "Munich", embedding_engine)

        insert_call = conn.execute.call_args_list[1]
        # content is parameter $4 -> index 4
        assert insert_call.args[4] == "Munich"


# ---------------------------------------------------------------------------
# TestPermanenceDecay -- parametrized permanence-to-decay mapping
# ---------------------------------------------------------------------------


class TestPermanenceDecay:
    """Verify all permanence levels map to correct decay rates in store_fact."""

    @pytest.mark.parametrize(
        ("permanence", "expected_decay"),
        [
            ("permanent", 0.0),
            ("stable", 0.002),
            ("standard", 0.008),
            ("volatile", 0.03),
            ("ephemeral", 0.1),
        ],
    )
    async def test_permanence_maps_to_decay_rate(
        self, fact_pool, embedding_engine: MagicMock, permanence: str, expected_decay: float
    ) -> None:
        """Each permanence level produces the correct decay_rate in the INSERT."""
        pool, conn = fact_pool
        await store_fact(pool, "user", "data", "value", embedding_engine, permanence=permanence)
        insert_call = conn.execute.call_args_list[0]
        # decay_rate is parameter $9 -> index 9 in args
        assert insert_call.args[9] == expected_decay

    async def test_unknown_permanence_raises_value_error(
        self, fact_pool, embedding_engine: MagicMock
    ) -> None:
        """An unrecognized permanence string raises ValueError."""
        pool, _conn = fact_pool
        with pytest.raises(ValueError, match="Invalid permanence"):
            await store_fact(
                pool, "user", "data", "value", embedding_engine, permanence="nonexistent"
            )

    async def test_permanence_decay_constant_has_five_entries(self) -> None:
        """The _PERMANENCE_DECAY mapping contains exactly 5 entries."""
        assert len(_PERMANENCE_DECAY) == 5
        assert set(_PERMANENCE_DECAY.keys()) == {
            "permanent",
            "stable",
            "standard",
            "volatile",
            "ephemeral",
        }

    async def test_decay_rates_increase_with_volatility(self) -> None:
        """Decay rates increase from permanent (0.0) to ephemeral (0.1)."""
        ordered = ["permanent", "stable", "standard", "volatile", "ephemeral"]
        rates = [_PERMANENCE_DECAY[p] for p in ordered]
        for i in range(len(rates) - 1):
            assert rates[i] < rates[i + 1], (
                f"Expected {ordered[i]} ({rates[i]}) < {ordered[i + 1]} ({rates[i + 1]})"
            )


# ---------------------------------------------------------------------------
# TestReferenceBumping -- get_memory atomically bumps reference_count
# ---------------------------------------------------------------------------


class TestReferenceBumping:
    """Verify that get_memory atomically bumps reference_count and
    updates last_referenced_at."""

    async def test_get_memory_bumps_reference_count(self) -> None:
        """get_memory SQL includes reference_count = reference_count + 1."""
        pool = AsyncMock()
        mock_record = {"id": uuid.uuid4(), "reference_count": 5}
        pool.fetchrow = AsyncMock(return_value=mock_record)
        memory_id = uuid.uuid4()

        await get_memory(pool, "fact", memory_id)

        sql = pool.fetchrow.call_args[0][0]
        assert "reference_count = reference_count + 1" in sql

    async def test_get_memory_updates_last_referenced_at(self) -> None:
        """get_memory SQL includes last_referenced_at = now()."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
        memory_id = uuid.uuid4()

        await get_memory(pool, "episode", memory_id)

        sql = pool.fetchrow.call_args[0][0]
        assert "last_referenced_at = now()" in sql

    async def test_get_memory_returns_none_for_nonexistent(self) -> None:
        """get_memory returns None when pool.fetchrow returns None."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        memory_id = uuid.uuid4()

        result = await get_memory(pool, "rule", memory_id)

        assert result is None

    async def test_get_memory_returns_updated_record(self) -> None:
        """get_memory returns the full record dict from RETURNING *."""
        pool = AsyncMock()
        expected = {
            "id": uuid.uuid4(),
            "content": "test fact",
            "reference_count": 3,
            "subject": "user",
        }
        pool.fetchrow = AsyncMock(return_value=expected)

        result = await get_memory(pool, "fact", expected["id"])

        assert result == expected

    async def test_get_memory_sql_uses_returning_star(self) -> None:
        """get_memory SQL includes RETURNING * to return the updated row."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        memory_id = uuid.uuid4()

        await get_memory(pool, "episode", memory_id)

        sql = pool.fetchrow.call_args[0][0]
        assert "RETURNING *" in sql

    async def test_get_memory_passes_uuid_as_parameter(self) -> None:
        """get_memory passes the memory_id as $1 to the query."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        memory_id = uuid.uuid4()

        await get_memory(pool, "fact", memory_id)

        assert pool.fetchrow.call_args[0][1] == memory_id

    async def test_get_memory_invalid_type_raises(self) -> None:
        """get_memory raises ValueError for invalid memory_type."""
        pool = AsyncMock()
        with pytest.raises(ValueError, match="Invalid memory_type"):
            await get_memory(pool, "invalid", uuid.uuid4())

        # Pool should not be called at all
        pool.fetchrow.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestStoreGetForgetRoundTrip -- full lifecycle for each type
# ---------------------------------------------------------------------------


class TestStoreGetForgetRoundTrip:
    """Verify the full store -> get -> forget lifecycle for each memory type."""

    async def test_episode_lifecycle(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """Episode: store -> get (bumps reference) -> forget (sets expires_at=now())."""
        # Store
        episode_id = await store_episode(
            simple_pool, "User discussed travel plans", "test-butler", embedding_engine
        )
        assert isinstance(episode_id, uuid.UUID)

        # Get
        simple_pool.fetchrow = AsyncMock(
            return_value={"id": episode_id, "content": "User discussed travel plans"}
        )
        result = await get_memory(simple_pool, "episode", episode_id)
        assert result["id"] == episode_id
        sql = simple_pool.fetchrow.call_args[0][0]
        assert "reference_count = reference_count + 1" in sql

        # Forget
        simple_pool.execute = AsyncMock(return_value="UPDATE 1")
        forgotten = await forget_memory(simple_pool, "episode", episode_id)
        assert forgotten is True
        sql = simple_pool.execute.call_args[0][0]
        assert "expires_at = now()" in sql

    async def test_fact_lifecycle(self, fact_pool, embedding_engine: MagicMock) -> None:
        """Fact: store -> get (bumps reference) -> forget (sets validity='retracted')."""
        pool, _conn = fact_pool

        # Store
        fact_id = await store_fact(pool, "user", "language", "Python", embedding_engine)
        assert isinstance(fact_id, uuid.UUID)

        # Get (uses pool.fetchrow directly, so we need a pool with fetchrow)
        get_pool = AsyncMock()
        get_pool.fetchrow = AsyncMock(
            return_value={"id": fact_id, "subject": "user", "predicate": "language"}
        )
        result = await get_memory(get_pool, "fact", fact_id)
        assert result["id"] == fact_id

        # Forget
        forget_pool = AsyncMock()
        forget_pool.execute = AsyncMock(return_value="UPDATE 1")
        forgotten = await forget_memory(forget_pool, "fact", fact_id)
        assert forgotten is True
        sql = forget_pool.execute.call_args[0][0]
        assert "validity = 'retracted'" in sql

    async def test_rule_lifecycle(
        self, simple_pool: AsyncMock, embedding_engine: MagicMock
    ) -> None:
        """Rule: store -> get (bumps reference) -> forget (merges forgotten flag)."""
        # Store
        rule_id = await store_rule(simple_pool, "Always confirm before deleting", embedding_engine)
        assert isinstance(rule_id, uuid.UUID)

        # Get
        simple_pool.fetchrow = AsyncMock(
            return_value={"id": rule_id, "content": "Always confirm before deleting"}
        )
        result = await get_memory(simple_pool, "rule", rule_id)
        assert result["id"] == rule_id

        # Forget
        simple_pool.execute = AsyncMock(return_value="UPDATE 1")
        forgotten = await forget_memory(simple_pool, "rule", rule_id)
        assert forgotten is True
        sql = simple_pool.execute.call_args[0][0]
        assert '"forgotten": true' in sql
