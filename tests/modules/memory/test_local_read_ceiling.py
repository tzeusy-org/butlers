"""Tests for the local read ceiling on recall/search/memory_context (bu-2jtfw.3).

Background: the cross-butler catalog (``search_catalog``) has always enforced
a server-held sensitivity ceiling (``CatalogReadPolicy``/
``resolve_catalog_read_policy``, loaded from ``runtime_config.
catalog_read_sensitivity``). Local reads — ``recall``, ``search``, and
``memory_context``'s Profile Facts / Task-Relevant Facts / Recent Episodes
fetches — had none: a confidential owner fact was returned by local recall
and injected into every session's memory_context regardless of who or what
triggered that session. This generalizes the SAME ceiling to every local
read path, matching the catalog's existing SQL-level enforcement.

Unit tests (no DB) pin the policy-resolution fail-closed marker and the
explicit-filter authorization guard. Integration tests (Docker + Postgres,
full core+memory migration chain) prove the ceiling is enforced in SQL, not
a post-fetch Python filter, and that memory_context reports withheld facts
rather than silently dropping them.
"""

from __future__ import annotations

import shutil
import uuid
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.memory import search as _search
from butlers.modules.memory.tools import context as _context
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Unit tests -- no DB required
# ---------------------------------------------------------------------------


class TestPolicyDefaultedMarker:
    """An unrecognized ceiling fails closed AND is distinguishable from a
    caller genuinely holding 'normal' authority."""

    def test_known_ceiling_is_not_defaulted(self) -> None:
        policy = _search.resolve_catalog_read_policy("normal")
        assert policy.allowed_sensitivities == ("normal",)
        assert policy.policy_defaulted is False

    def test_unknown_ceiling_defaults_to_normal_and_flags_it(self) -> None:
        policy = _search.resolve_catalog_read_policy("caller-invented-level")
        assert policy.allowed_sensitivities == ("normal",)
        assert policy.policy_defaulted is True

    def test_confidential_ceiling_is_not_defaulted(self) -> None:
        policy = _search.resolve_catalog_read_policy("confidential")
        assert policy.policy_defaulted is False


class TestExplicitSensitivityFilterAuthorization:
    """A caller cannot use the structured `sensitivity` filter to reach above
    their own read ceiling -- this must raise, not silently return []."""

    @pytest.fixture()
    def engine(self) -> MagicMock:
        m = MagicMock()
        m.embed.return_value = [0.0] * 384
        return m

    async def test_recall_raises_when_filter_exceeds_ceiling(self, engine: MagicMock) -> None:
        pool = AsyncMock()
        with pytest.raises(_search.SensitivityAuthorizationError):
            await _search.recall(
                pool,
                "topic",
                engine,
                filters={"sensitivity": "confidential"},
                read_policy=_search.resolve_catalog_read_policy("normal"),
            )

    async def test_search_raises_when_filter_exceeds_ceiling(self, engine: MagicMock) -> None:
        pool = AsyncMock()
        with pytest.raises(_search.SensitivityAuthorizationError):
            await _search.search(
                pool,
                "query",
                engine,
                filters={"sensitivity": "pii"},
                read_policy=_search.resolve_catalog_read_policy("normal"),
            )

    async def test_recall_allows_filter_within_ceiling(self, engine: MagicMock) -> None:
        """A filter value the ceiling already permits must not raise -- this
        exercises the same path with an unmocked pool, so it also proves the
        guard runs before any query, not as a side effect of one failing."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock(return_value=None)
        result = await _search.recall(
            pool,
            "topic",
            engine,
            filters={"sensitivity": "normal"},
            read_policy=_search.resolve_catalog_read_policy("normal"),
        )
        assert result == []


class TestMemoryContextAssemblyUsesOnePolicy:
    """Profile Facts and recall must agree on exactly one read_policy per
    memory_context assembly -- pinned by capturing what each fetch receives."""

    async def test_profile_facts_and_recall_receive_the_same_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        async def fake_fetch_profile_facts(
            pool, tenant_id, *, limit=50, allowed_sensitivities=None
        ):
            captured["profile_allowed"] = allowed_sensitivities
            return [], 0

        async def fake_recall(*args, **kwargs):
            captured["recall_policy"] = kwargs.get("read_policy")
            return []

        monkeypatch.setattr(_context, "_fetch_profile_facts", fake_fetch_profile_facts)
        monkeypatch.setattr(_context._search, "recall", fake_recall)

        policy = _search.resolve_catalog_read_policy("pii")
        engine = MagicMock()
        engine.embed.return_value = [0.0] * 384

        await _context.memory_context(
            AsyncMock(),
            engine,
            "prompt",
            "health",
            catalog_read_policy=policy,
        )

        assert captured["profile_allowed"] == policy.allowed_sensitivities
        assert captured["recall_policy"] is policy


# ---------------------------------------------------------------------------
# Integration tests -- require Docker + Postgres, full core+memory migrations
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _ceiling_migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "memory"]
    )


@pytest.fixture
async def ceiling_pool(_ceiling_migrated_db_url: str):
    p = await asyncpg.create_pool(
        _ceiling_migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
    )
    await p.execute(
        "TRUNCATE TABLE facts, rules, episodes, public.entities RESTART IDENTITY CASCADE"
    )
    yield p
    await p.close()


async def _insert_fact(
    pool,
    *,
    content: str,
    sensitivity: str,
    scope: str = "zz_ceiling_test",
    entity_id: uuid.UUID | None = None,
    importance: float = 5.0,
) -> uuid.UUID:
    # subject/predicate are unique per call: (scope, subject, predicate) has
    # a partial unique index for no-entity property facts. last_confirmed_at
    # is set to now(), matching what store_fact always does at creation --
    # this test is about the sensitivity ceiling, not the separate
    # confidence-render exclusion for never-confirmed decaying facts.
    subject = f"owner-{uuid.uuid4().hex[:8]}"
    predicate = f"note-{uuid.uuid4().hex[:8]}"
    row = await pool.fetchrow(
        """
        INSERT INTO facts (subject, predicate, content, scope, sensitivity,
                            entity_id, importance, search_vector, last_confirmed_at)
        VALUES ($6, $7, $1, $2, $3, $4, $5, to_tsvector('english', $1), now())
        RETURNING id
        """,
        content,
        scope,
        sensitivity,
        entity_id,
        importance,
        subject,
        predicate,
    )
    return row["id"]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
class TestLocalCeilingEnforcedInSQL:
    async def test_keyword_search_ceiling_zero_rows_when_only_above_ceiling_exists(
        self, ceiling_pool
    ) -> None:
        """A pool containing ONLY an above-ceiling row returns zero rows --
        proof the filter runs in SQL (there is no post-fetch path here to run
        instead)."""
        await _insert_fact(
            ceiling_pool, content="unique needle alpha bravo", sensitivity="confidential"
        )

        results = await _search.keyword_search(
            ceiling_pool, "unique needle alpha bravo", "facts", allowed_sensitivities=["normal"]
        )
        assert results == []

    async def test_keyword_search_ceiling_allows_normal_row(self, ceiling_pool) -> None:
        fact_id = await _insert_fact(
            ceiling_pool, content="unique needle charlie delta", sensitivity="normal"
        )
        results = await _search.keyword_search(
            ceiling_pool, "unique needle charlie delta", "facts", allowed_sensitivities=["normal"]
        )
        assert {r["id"] for r in results} == {fact_id}

    async def test_keyword_search_confidential_ceiling_sees_both(self, ceiling_pool) -> None:
        normal_id = await _insert_fact(
            ceiling_pool, content="unique needle echo foxtrot", sensitivity="normal"
        )
        confidential_id = await _insert_fact(
            ceiling_pool, content="unique needle echo foxtrot", sensitivity="confidential"
        )
        results = await _search.keyword_search(
            ceiling_pool,
            "unique needle echo foxtrot",
            "facts",
            allowed_sensitivities=["normal", "pii", "confidential"],
        )
        assert {r["id"] for r in results} == {normal_id, confidential_id}

    async def test_no_ceiling_argument_preserves_back_compat(self, ceiling_pool) -> None:
        """allowed_sensitivities=None (the default) applies no filter at all --
        existing low-level callers of keyword_search/semantic_search are
        unaffected; the ceiling is opt-in at this layer and mandatory at
        recall()/search()."""
        await _insert_fact(
            ceiling_pool, content="unique needle golf hotel", sensitivity="confidential"
        )
        results = await _search.keyword_search(ceiling_pool, "unique needle golf hotel", "facts")
        assert len(results) == 1

    async def test_memory_context_withholds_confidential_owner_fact(self, ceiling_pool) -> None:
        """Reproduction + fix: a confidential owner fact is absent from
        memory_context output under a 'normal' ceiling, and the block reports
        withheld: 1 rather than silently dropping it."""
        entity_row = await ceiling_pool.fetchrow(
            "INSERT INTO public.entities (canonical_name, roles) VALUES ('Owner', ARRAY['owner'])"
            " RETURNING id"
        )
        owner_id = entity_row["id"]

        await _insert_fact(
            ceiling_pool,
            content="aortic valve regurgitation diagnosis",
            sensitivity="confidential",
            scope="zz_never_recalled",
            entity_id=owner_id,
            importance=9.0,
        )
        await _insert_fact(
            ceiling_pool,
            content="prefers oat milk in coffee",
            sensitivity="normal",
            scope="zz_never_recalled",
            entity_id=owner_id,
            importance=8.0,
        )

        engine = MagicMock()
        engine.embed.return_value = [0.0] * 384

        # No runtime_config row exists in this fresh schema -> load_catalog_read_policy
        # fails closed to 'normal', proving the default path (not just an
        # explicitly-passed policy) withholds correctly.
        result = await _context.memory_context(
            ceiling_pool,
            engine,
            "unrelated trigger prompt",
            "health",
        )

        assert "aortic valve regurgitation" not in result
        assert "oat milk" in result
        assert "withheld: 1" in result

    async def test_memory_context_confidential_ceiling_shows_everything(self, ceiling_pool) -> None:
        entity_row = await ceiling_pool.fetchrow(
            "INSERT INTO public.entities (canonical_name, roles) VALUES ('Owner', ARRAY['owner'])"
            " RETURNING id"
        )
        owner_id = entity_row["id"]
        await _insert_fact(
            ceiling_pool,
            content="aortic valve regurgitation diagnosis",
            sensitivity="confidential",
            scope="zz_never_recalled",
            entity_id=owner_id,
            importance=9.0,
        )

        engine = MagicMock()
        engine.embed.return_value = [0.0] * 384

        result = await _context.memory_context(
            ceiling_pool,
            engine,
            "unrelated trigger prompt",
            "health",
            catalog_read_policy=_search.resolve_catalog_read_policy("confidential"),
        )

        assert "aortic valve regurgitation" in result
        assert "withheld:" not in result
