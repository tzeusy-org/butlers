"""Behavioral tests for memory writing MCP tools.

Tests exercise behavior through the public MCP tool interface.
Anti-patterns eliminated: mock delegation tests, exact SQL string assertions,
per-call-count assertions.

Covers:
  - normalize_predicate: canonical snake_case normalization at MCP layer
  - memory_store_episode: result shape and parameter forwarding
  - memory_store_fact: predicate normalization integration + result shape
  - memory_store_rule: result shape and parameter forwarding
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.memory.tools import (
    _helpers,
    memory_store_episode,
    memory_store_fact,
    memory_store_rule,
)
from butlers.modules.memory.tools.writing import (
    _reset_identity_registry_cache,
    is_identity_registry_predicate,
    normalize_predicate,
    refresh_identity_registry_predicates,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_registry_cache():
    """Reset the module-global registry-predicate cache around every test."""
    _reset_identity_registry_cache()
    yield
    _reset_identity_registry_cache()


def _registry_pool(predicates: list[str]) -> AsyncMock:
    """An asyncpg-pool mock whose registry fetch returns *predicates* as rows."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"predicate": p} for p in predicates])
    return pool


@pytest.fixture()
def pool() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def engine() -> MagicMock:
    m = MagicMock()
    m.embed.return_value = [0.1] * 384
    return m


# ---------------------------------------------------------------------------
# normalize_predicate
# ---------------------------------------------------------------------------


class TestNormalizePredicate:
    """normalize_predicate converts raw predicates to canonical snake_case."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("job-title", "job_title"),  # hyphen->underscore + lowercase
            ("is_parent_of", "parent_of"),  # is_ prefix stripped
            ("Is-Parent Of", "parent_of"),  # mixed case + prefix + hyphen + space
        ],
    )
    def test_normalization_cases(self, raw: str, expected: str) -> None:
        assert normalize_predicate(raw) == expected

    def test_mid_word_is_not_stripped(self) -> None:
        assert normalize_predicate("sibling_is_alive") == "sibling_is_alive"


# ---------------------------------------------------------------------------
# memory_store_episode
# ---------------------------------------------------------------------------


class TestMemoryStoreEpisode:
    """memory_store_episode returns MCP-friendly dict with id and expires_at."""

    async def test_result_shape(self, pool: AsyncMock) -> None:
        episode_id = uuid.uuid4()
        expires_at = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        with patch.object(
            _helpers._storage,
            "store_episode",
            new_callable=AsyncMock,
            return_value={"id": episode_id, "expires_at": expires_at},
        ):
            result = await memory_store_episode(pool, "some content", "butler")
        assert result == {"id": str(episode_id), "expires_at": expires_at.isoformat()}

    async def test_session_id_forwarded(self, pool: AsyncMock) -> None:
        sid = uuid.uuid4()
        with patch.object(
            _helpers._storage,
            "store_episode",
            new_callable=AsyncMock,
            return_value={"id": uuid.uuid4(), "expires_at": datetime.now(UTC)},
        ) as m:
            await memory_store_episode(pool, "test", "butler", session_id=str(sid))
        assert m.call_args.kwargs["session_id"] == sid

    async def test_default_importance_is_five(self, pool: AsyncMock) -> None:
        with patch.object(
            _helpers._storage,
            "store_episode",
            new_callable=AsyncMock,
            return_value={"id": uuid.uuid4(), "expires_at": datetime.now(UTC)},
        ) as m:
            await memory_store_episode(pool, "test", "butler")
        assert m.call_args.kwargs["importance"] == 5.0


# ---------------------------------------------------------------------------
# memory_store_fact
# ---------------------------------------------------------------------------


class TestMemoryStoreFact:
    """memory_store_fact normalizes predicate at tool layer and returns id + superseded_id."""

    async def test_result_shape_no_supersession(self, pool: AsyncMock, engine: MagicMock) -> None:
        fact_id = uuid.uuid4()
        with patch.object(
            _helpers._storage, "store_fact", new_callable=AsyncMock, return_value={"id": fact_id}
        ):
            result = await memory_store_fact(pool, engine, "user", "name", "Alice")
        assert result == {"id": str(fact_id), "superseded_id": None}

    async def test_result_shape_with_supersession(self, pool: AsyncMock, engine: MagicMock) -> None:
        fact_id, old_id = uuid.uuid4(), uuid.uuid4()
        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": fact_id, "supersedes_id": old_id},
        ):
            result = await memory_store_fact(pool, engine, "user", "city", "Berlin")
        assert result["superseded_id"] == str(old_id)

    async def test_predicate_normalized(self, pool: AsyncMock, engine: MagicMock) -> None:
        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": uuid.uuid4()},
        ) as m:
            await memory_store_fact(pool, engine, "user", "Is-Parent Of", "Alice")
        assert m.call_args.args[2] == "parent_of"

    async def test_kwargs_forwarded(self, pool: AsyncMock, engine: MagicMock) -> None:
        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": uuid.uuid4()},
        ) as m:
            await memory_store_fact(
                pool,
                engine,
                "user",
                "pref",
                "dark",
                importance=9.0,
                permanence="stable",
                tags=["ui"],
            )
        kw = m.call_args.kwargs
        assert kw["importance"] == 9.0 and kw["permanence"] == "stable" and kw["tags"] == ["ui"]


# ---------------------------------------------------------------------------
# Writer-side identity boundary (canonical fact-store layering)
# ---------------------------------------------------------------------------


class TestIdentityPredicateBoundary:
    """Identity-contact predicates MUST NOT land in the memory facts table.

    module-memory (entity-v3 delta) + relationship-entity-lifecycle
    "Canonical fact-store layering": their single write path is
    relationship_assert_fact() into relationship.entity_facts. memory_store_fact
    rejects them before any storage write.
    """

    @pytest.mark.parametrize(
        "predicate",
        ["has_email", "has_phone", "has_handle", "has_address", "has_birthday", "has_website"],
    )
    def test_is_identity_registry_predicate_true_for_registry_predicates(
        self, predicate: str
    ) -> None:
        assert is_identity_registry_predicate(predicate)

    @pytest.mark.parametrize(
        "predicate",
        ["name", "city", "parent_of", "works_at", "likes", "phone_brand", "email_signature"],
    )
    def test_is_identity_registry_predicate_false_for_narrative_predicates(
        self, predicate: str
    ) -> None:
        assert not is_identity_registry_predicate(predicate)

    @pytest.mark.parametrize(
        ("raw_predicate", "normalized"),
        [
            ("has-email", "has_email"),  # hyphenated registry form normalizes to snake_case
            ("has_phone", "has_phone"),
            ("Has-Handle", "has_handle"),  # mixed case still routed
            ("has-address", "has_address"),
            ("has-birthday", "has_birthday"),
            ("has-website", "has_website"),
        ],
    )
    async def test_rejects_identity_predicate_before_storage(
        self,
        pool: AsyncMock,
        engine: MagicMock,
        raw_predicate: str,
        normalized: str,
    ) -> None:
        """An identity predicate raises ValueError and never reaches storage."""
        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as store_fact:
            with pytest.raises(ValueError) as excinfo:
                await memory_store_fact(pool, engine, "user", raw_predicate, "alice@example.com")
        # No storage write happened — the boundary fires before delegation.
        store_fact.assert_not_called()
        msg = str(excinfo.value)
        assert "out of scope for the memory facts store" in msg
        assert normalized in msg

    async def test_narrative_predicate_still_stored(
        self, pool: AsyncMock, engine: MagicMock
    ) -> None:
        """A narrative fact (non-registry predicate) is unaffected by the boundary."""
        fact_id = uuid.uuid4()
        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": fact_id},
        ) as store_fact:
            result = await memory_store_fact(
                pool, engine, "user", "works_at", "mentioned switching jobs"
            )
        store_fact.assert_awaited_once()
        assert result == {"id": str(fact_id), "superseded_id": None}


# ---------------------------------------------------------------------------
# Registry-driven identity boundary (future contact predicates)
# ---------------------------------------------------------------------------


class TestRegistryDrivenIdentityBoundary:
    """The rejection set is registry-driven, not a fixed list.

    module-memory delta: the boundary covers "future contact predicates in
    relationship.entity_predicate_registry". A newly seeded contact predicate is
    rejected without a code change; a registry read failure degrades to the
    static floor (never fails open, never raises into the write path).
    """

    async def test_future_registry_predicate_is_rejected(self, engine: MagicMock) -> None:
        """A contact predicate present only in the live registry is rejected."""
        # "has-fax" is NOT in the static floor — only the registry knows it.
        pool = _registry_pool(["has-email", "has-fax"])
        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as store_fact:
            with pytest.raises(ValueError) as excinfo:
                await memory_store_fact(pool, engine, "user", "has-fax", "+1-555-0100")
        store_fact.assert_not_called()
        assert "out of scope for the memory facts store" in str(excinfo.value)

    async def test_floor_predicate_rejected_even_when_registry_empty(
        self, engine: MagicMock
    ) -> None:
        """A floor predicate is rejected even if the registry returns nothing."""
        pool = _registry_pool([])  # registry empty / unseeded
        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock) as store_fact:
            with pytest.raises(ValueError):
                await memory_store_fact(pool, engine, "user", "has-email", "a@b.com")
        store_fact.assert_not_called()

    async def test_registry_read_failure_degrades_to_floor(self, engine: MagicMock) -> None:
        """A registry read error must not fail the write path open or raise.

        Floor predicates stay rejected; a narrative predicate still stores.
        """
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("no SELECT grant"))
        # Floor predicate still rejected despite the registry read blowing up.
        with patch.object(_helpers._storage, "store_fact", new_callable=AsyncMock):
            with pytest.raises(ValueError):
                await memory_store_fact(pool, engine, "user", "has-phone", "+1")
        # Narrative predicate unaffected — stored normally.
        fact_id = uuid.uuid4()
        with patch.object(
            _helpers._storage,
            "store_fact",
            new_callable=AsyncMock,
            return_value={"id": fact_id},
        ) as store_fact:
            result = await memory_store_fact(pool, engine, "user", "works_at", "context")
        store_fact.assert_awaited_once()
        assert result == {"id": str(fact_id), "superseded_id": None}

    async def test_snapshot_unions_floor_and_registry(self) -> None:
        """refresh_identity_registry_predicates returns floor ∪ registry, normalized."""
        pool = _registry_pool(["has-fax"])  # registry omits the seeded floor
        snapshot = await refresh_identity_registry_predicates(pool)
        # Floor preserved even though the registry mock omitted it.
        assert "has_email" in snapshot
        # Registry-only predicate present, normalized to snake_case.
        assert "has_fax" in snapshot

    async def test_snapshot_is_cached_within_ttl(self) -> None:
        """A second call within the TTL reuses the snapshot (no extra query)."""
        pool = _registry_pool(["has-fax"])
        await refresh_identity_registry_predicates(pool)
        await refresh_identity_registry_predicates(pool)
        # Only one registry read despite two refresh calls.
        assert pool.fetch.await_count == 1

    def test_explicit_snapshot_arg_rejects_registry_only_predicate(self) -> None:
        """is_identity_registry_predicate honors an explicit registry snapshot."""
        snapshot = frozenset({"has_email", "has_fax"})
        assert is_identity_registry_predicate("has_fax", snapshot)
        # Without the snapshot, a registry-only predicate is not in the floor.
        assert not is_identity_registry_predicate("has_fax")


# ---------------------------------------------------------------------------
# memory_store_rule
# ---------------------------------------------------------------------------


class TestMemoryStoreRule:
    """memory_store_rule returns id dict."""

    async def test_result_shape(self, pool: AsyncMock, engine: MagicMock) -> None:
        rule_id = uuid.uuid4()
        with patch.object(
            _helpers._storage, "store_rule", new_callable=AsyncMock, return_value={"id": rule_id}
        ):
            result = await memory_store_rule(pool, engine, "Always greet")
        assert result == {"id": str(rule_id)}

    async def test_scope_and_tags_forwarded(self, pool: AsyncMock, engine: MagicMock) -> None:
        with patch.object(
            _helpers._storage,
            "store_rule",
            new_callable=AsyncMock,
            return_value={"id": uuid.uuid4()},
        ) as m:
            await memory_store_rule(pool, engine, "rule", scope="butler:x", tags=["safety"])
        kw = m.call_args.kwargs
        assert kw["scope"] == "butler:x" and kw["tags"] == ["safety"]
