"""Tests for the consolidation executor in the Memory Butler."""

from __future__ import annotations

import importlib.util
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load consolidation_executor module from disk (roster/ is not a Python package).
# Mock sentence_transformers before loading to avoid heavy dependency.
# ---------------------------------------------------------------------------

_EXECUTOR_PATH = MEMORY_MODULE_PATH / "consolidation_executor.py"

_PARSER_PATH = MEMORY_MODULE_PATH / "consolidation_parser.py"


def _load_parser_module():
    spec = importlib.util.spec_from_file_location("consolidation_parser", _PARSER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_executor_module():
    # sys.modules.setdefault("sentence_transformers", MagicMock())
    spec = importlib.util.spec_from_file_location("consolidation_executor", _EXECUTOR_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_parser_mod = _load_parser_module()
_exec_mod = _load_executor_module()

execute_consolidation = _exec_mod.execute_consolidation
ConsolidationResult = _parser_mod.ConsolidationResult
NewFact = _parser_mod.NewFact
UpdatedFact = _parser_mod.UpdatedFact
NewRule = _parser_mod.NewRule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_pool() -> AsyncMock:
    """Create a mock asyncpg pool."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value="UPDATE 2")
    return pool


def _mock_embedding_engine() -> MagicMock:
    """Create a mock embedding engine."""
    return MagicMock()


def _make_episode_ids(n: int = 2) -> list[uuid.UUID]:
    """Generate a list of episode UUIDs."""
    return [uuid.uuid4() for _ in range(n)]


# ---------------------------------------------------------------------------
# Tests — New facts stored and linked to source episodes
# ---------------------------------------------------------------------------


class TestNewFacts:
    """Tests for new fact storage and linking."""

    async def test_new_facts_stored_and_linked(self) -> None:
        """New facts are stored via store_fact and linked to source episodes."""
        fact_id = uuid.uuid4()
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(2)

        parsed = ConsolidationResult(
            new_facts=[
                NewFact(
                    subject="user",
                    predicate="prefers",
                    content="dark mode",
                    permanence="stable",
                    importance=7.0,
                    tags=["preference"],
                ),
            ],
        )

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock) as mock_sf,
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock) as mock_cl,
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            mock_sf.return_value = fact_id

            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["facts_created"] == 1
        assert result["errors"] == []

        # Verify store_fact was called with correct args (including tenant_id/request_id)
        mock_sf.assert_awaited_once_with(
            pool,
            "user",
            "prefers",
            "dark mode",
            engine,
            importance=7.0,
            permanence="stable",
            scope="test-butler",
            tags=["preference"],
            source_butler="test-butler",
            entity_id=None,
            tenant_id="shared",
            request_id=None,
        )

        # Verify derived_from links created for each episode
        assert mock_cl.await_count == 2
        for i, episode_id in enumerate(episode_ids):
            mock_cl.assert_any_await(pool, "fact", fact_id, "episode", episode_id, "derived_from")

    async def test_multiple_new_facts(self) -> None:
        """Multiple new facts are all stored and linked."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            new_facts=[
                NewFact(subject="user", predicate="likes", content="coffee"),
                NewFact(subject="user", predicate="dislikes", content="tea"),
                NewFact(subject="project", predicate="language", content="python"),
            ],
        )

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock) as mock_sf,
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock) as mock_cl,
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            mock_sf.return_value = uuid.uuid4()

            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["facts_created"] == 3
        assert mock_sf.await_count == 3
        # 3 facts * 1 episode = 3 links
        assert mock_cl.await_count == 3


# ---------------------------------------------------------------------------
# Tests — Updated facts stored with correct target_id
# ---------------------------------------------------------------------------


class TestUpdatedFacts:
    """Tests for updated fact storage."""

    async def test_updated_facts_stored(self) -> None:
        """Updated facts are stored via store_fact (auto-supersession)."""
        target_id = str(uuid.uuid4())
        new_id = uuid.uuid4()
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            updated_facts=[
                UpdatedFact(
                    target_id=target_id,
                    subject="user",
                    predicate="location",
                    content="Berlin",
                    permanence="standard",
                ),
            ],
        )

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock) as mock_sf,
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock) as mock_cl,
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            mock_sf.return_value = new_id

            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["facts_updated"] == 1
        assert result["errors"] == []

        # store_fact is called with the updated subject/predicate/content
        # (storage.py handles auto-supersession internally)
        mock_sf.assert_awaited_once_with(
            pool,
            "user",
            "location",
            "Berlin",
            engine,
            permanence="standard",
            scope="test-butler",
            source_butler="test-butler",
            entity_id=None,
            tenant_id="shared",
            request_id=None,
        )

        # derived_from links created
        mock_cl.assert_awaited_once_with(
            pool, "fact", new_id, "episode", episode_ids[0], "derived_from"
        )


# ---------------------------------------------------------------------------
# Tests — New rules stored and linked to source episodes
# ---------------------------------------------------------------------------


class TestNewRules:
    """Tests for new rule storage and linking."""

    async def test_new_rules_stored_and_linked(self) -> None:
        """New rules are stored via store_rule and linked to source episodes."""
        rule_id = uuid.uuid4()
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(2)

        parsed = ConsolidationResult(
            new_rules=[
                NewRule(content="Always greet politely", tags=["etiquette"]),
            ],
        )

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock) as mock_cl,
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock) as mock_sr,
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            mock_sr.return_value = rule_id

            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["rules_created"] == 1
        assert result["errors"] == []

        mock_sr.assert_awaited_once_with(
            pool,
            "Always greet politely",
            engine,
            scope="test-butler",
            tags=["etiquette"],
            source_butler="test-butler",
            tenant_id="shared",
            request_id=None,
        )

        # derived_from links for each episode
        assert mock_cl.await_count == 2
        for episode_id in episode_ids:
            mock_cl.assert_any_await(pool, "rule", rule_id, "episode", episode_id, "derived_from")


# ---------------------------------------------------------------------------
# Tests — Confirmations call confirm_memory correctly
# ---------------------------------------------------------------------------


class TestConfirmations:
    """Tests for fact confirmations."""

    async def test_confirmations_call_confirm_memory(self) -> None:
        """Each confirmation UUID triggers confirm_memory('fact', uuid)."""
        fact_id_1 = str(uuid.uuid4())
        fact_id_2 = str(uuid.uuid4())
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            confirmations=[fact_id_1, fact_id_2],
        )

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock) as mock_cm,
        ):
            mock_cm.return_value = True

            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["confirmations_made"] == 2
        assert result["errors"] == []

        mock_cm.assert_any_await(pool, "fact", uuid.UUID(fact_id_1))
        mock_cm.assert_any_await(pool, "fact", uuid.UUID(fact_id_2))


# ---------------------------------------------------------------------------
# Tests — Episodes marked consolidated after execution
# ---------------------------------------------------------------------------


class TestEpisodeConsolidation:
    """Tests for marking episodes as consolidated."""

    async def test_episodes_marked_consolidated(self) -> None:
        """All source episodes are marked as consolidated (terminal state) after execution."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(3)

        parsed = ConsolidationResult()  # Empty — no actions

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["episodes_consolidated"] == 3

        # Two pool.execute calls: 1) terminal state UPDATE, 2) memory_events INSERT (best-effort)
        assert pool.execute.await_count >= 1
        first_sql = pool.execute.call_args_list[0][0][0]
        # SQL is multi-line with extra spaces; normalise for matching
        norm_sql = " ".join(first_sql.split())
        assert "consolidated = true" in norm_sql
        assert "consolidation_status = 'consolidated'" in norm_sql
        assert "leased_until" in norm_sql  # lease is cleared on success
        assert "leased_by" in norm_sql
        assert "ANY($1)" in norm_sql
        assert pool.execute.call_args_list[0][0][1] == episode_ids


# ---------------------------------------------------------------------------
# Tests — Partial failure resilience
# ---------------------------------------------------------------------------


class TestPartialFailureResilience:
    """Tests that one action failure does not prevent others from executing."""

    async def test_fact_failure_does_not_block_rules(self) -> None:
        """A failing store_fact does not prevent store_rule from running."""
        rule_id = uuid.uuid4()
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            new_facts=[
                NewFact(subject="user", predicate="likes", content="broken"),
            ],
            new_rules=[
                NewRule(content="A working rule"),
            ],
        )

        with (
            patch.object(
                _exec_mod,
                "store_fact",
                new_callable=AsyncMock,
                side_effect=RuntimeError("db error"),
            ),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock) as mock_cl,
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock) as mock_sr,
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            mock_sr.return_value = rule_id

            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["facts_created"] == 0
        assert result["rules_created"] == 1
        assert len(result["errors"]) == 1
        # Error message should be sanitized (no internal details in return value)
        assert "Failed to store new fact" in result["errors"][0]
        assert "user/likes" in result["errors"][0]

        # Rule was still stored and linked
        mock_sr.assert_awaited_once()
        assert mock_cl.await_count == 1

    async def test_confirmation_failure_does_not_block_others(self) -> None:
        """A failing confirmation does not block other confirmations."""
        good_id = str(uuid.uuid4())
        bad_id = str(uuid.uuid4())
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            confirmations=[bad_id, good_id],
        )

        call_count = 0

        async def confirm_side_effect(pool, mtype, mid):
            nonlocal call_count
            call_count += 1
            if mid == uuid.UUID(bad_id):
                raise RuntimeError("not found")
            return True

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(
                _exec_mod,
                "confirm_memory",
                side_effect=confirm_side_effect,
            ),
        ):
            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["confirmations_made"] == 1
        assert len(result["errors"]) == 1
        assert bad_id in result["errors"][0]

    async def test_episode_update_failure_reported(self) -> None:
        """Failure to mark episodes consolidated is captured in errors."""
        pool = _mock_pool()
        pool.execute = AsyncMock(side_effect=RuntimeError("connection lost"))
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult()  # No actions, just episode marking

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["episodes_consolidated"] == 0
        assert len(result["errors"]) == 1
        # Error message should be sanitized (no internal details in return value)
        assert "Failed to mark episodes as consolidated" in result["errors"][0]


# ---------------------------------------------------------------------------
# Tests — Empty ConsolidationResult
# ---------------------------------------------------------------------------


class TestEmptyResult:
    """Tests for empty consolidation results."""

    async def test_empty_result_does_nothing_except_mark_episodes(self) -> None:
        """An empty ConsolidationResult only marks episodes consolidated."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(2)

        parsed = ConsolidationResult()

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock) as mock_sf,
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock) as mock_cl,
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock) as mock_sr,
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock) as mock_cm,
        ):
            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["facts_created"] == 0
        assert result["facts_updated"] == 0
        assert result["rules_created"] == 0
        assert result["confirmations_made"] == 0
        assert result["episodes_consolidated"] == 2
        assert result["errors"] == []

        mock_sf.assert_not_awaited()
        mock_sr.assert_not_awaited()
        mock_cl.assert_not_awaited()
        mock_cm.assert_not_awaited()

    async def test_empty_episode_ids_no_update(self) -> None:
        """With empty source_episode_ids, no UPDATE is issued."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()

        parsed = ConsolidationResult()

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            result = await execute_consolidation(pool, engine, parsed, [], "test-butler")

        assert result["episodes_consolidated"] == 0
        pool.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests — Scope defaults to butler_name
# ---------------------------------------------------------------------------


class TestRetryAndFailureHandling:
    """Tests for executor behavior when individual actions fail.

    Note: Full retry/dead-letter state transitions are handled by
    consolidation.py (_mark_group_failed) for group-level failures.
    The executor always marks episodes 'consolidated' when it runs —
    partial action failures (individual facts) still result in
    'consolidated' because the LLM produced output.
    """

    async def test_episodes_still_marked_consolidated_after_partial_action_failure(
        self,
    ) -> None:
        """Episodes are marked consolidated even when individual fact storage fails.

        The executor marks the episode as consolidated because the LLM ran
        successfully. Partial action errors are surfaced in the errors list
        for observability.
        """
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(2)

        parsed = ConsolidationResult(
            new_facts=[
                NewFact(subject="user", predicate="likes", content="broken"),
            ],
        )

        with (
            patch.object(
                _exec_mod,
                "store_fact",
                new_callable=AsyncMock,
                side_effect=RuntimeError("storage error"),
            ),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        # Episodes still get consolidated — the LLM ran, partial result is expected
        assert result["episodes_consolidated"] == 2
        assert len(result["errors"]) > 0

        # Verify the terminal UPDATE was called (consolidated state + lease clear)
        assert pool.execute.await_count >= 1
        first_sql = " ".join(pool.execute.call_args_list[0][0][0].split())
        assert "consolidation_status = 'consolidated'" in first_sql
        assert "leased_until" in first_sql

    async def test_episodes_consolidated_terminal_state_is_correct(self) -> None:
        """The terminal state UPDATE sets the correct columns."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            new_facts=[
                NewFact(subject="user", predicate="likes", content="broken"),
            ],
        )

        with (
            patch.object(
                _exec_mod,
                "store_fact",
                new_callable=AsyncMock,
                side_effect=RuntimeError("persistent error"),
            ),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            result = await execute_consolidation(pool, engine, parsed, episode_ids, "test-butler")

        assert result["episodes_consolidated"] == 1
        # Verify terminal state columns (SQL is multiline — normalise before matching)
        raw_sql = pool.execute.call_args_list[0][0][0]
        first_sql = " ".join(raw_sql.split())
        assert "consolidated = true" in first_sql
        assert "consolidation_status = 'consolidated'" in first_sql
        assert "leased_until" in first_sql
        assert "leased_by" in first_sql
        assert "NULL" in first_sql


class TestScopeDefault:
    """Tests for scope parameter handling."""

    async def test_scope_defaults_to_butler_name(self) -> None:
        """When scope is None, it defaults to the butler_name."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            new_facts=[NewFact(subject="s", predicate="p", content="c")],
            new_rules=[NewRule(content="a rule")],
        )

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock) as mock_sf,
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock) as mock_sr,
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            mock_sf.return_value = uuid.uuid4()
            mock_sr.return_value = uuid.uuid4()

            await execute_consolidation(pool, engine, parsed, episode_ids, "my-butler")

        # scope should be "my-butler" (the butler_name)
        sf_kwargs = mock_sf.call_args
        assert sf_kwargs.kwargs.get("scope") == "my-butler"

        sr_kwargs = mock_sr.call_args
        assert sr_kwargs.kwargs.get("scope") == "my-butler"

    async def test_explicit_scope_overrides_butler_name(self) -> None:
        """When scope is explicitly provided, it overrides butler_name."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            new_facts=[NewFact(subject="s", predicate="p", content="c")],
        )

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock) as mock_sf,
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            mock_sf.return_value = uuid.uuid4()

            await execute_consolidation(
                pool, engine, parsed, episode_ids, "my-butler", scope="global"
            )

        sf_kwargs = mock_sf.call_args
        assert sf_kwargs.kwargs.get("scope") == "global"


# ---------------------------------------------------------------------------
# Tests — tenant_id and request_id threaded through store calls
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    """Tests that tenant_id and request_id are correctly threaded to store calls."""

    async def test_tenant_id_threaded_to_store_fact(self) -> None:
        """tenant_id is passed to store_fact for new and updated facts."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            new_facts=[NewFact(subject="user", predicate="locale", content="en-GB")],
            updated_facts=[
                UpdatedFact(
                    target_id=str(uuid.uuid4()),
                    subject="user",
                    predicate="timezone",
                    content="Europe/London",
                    permanence="standard",
                )
            ],
        )

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock) as mock_sf,
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            mock_sf.return_value = uuid.uuid4()

            await execute_consolidation(
                pool,
                engine,
                parsed,
                episode_ids,
                "test-butler",
                tenant_id="acme",
                request_id="req-123",
            )

        assert mock_sf.await_count == 2
        for call in mock_sf.await_args_list:
            assert call.kwargs.get("tenant_id") == "acme"
            assert call.kwargs.get("request_id") == "req-123"

    async def test_tenant_id_threaded_to_store_rule(self) -> None:
        """tenant_id is passed to store_rule."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            new_rules=[NewRule(content="Speak formally")],
        )

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock) as mock_sr,
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            mock_sr.return_value = uuid.uuid4()

            await execute_consolidation(
                pool,
                engine,
                parsed,
                episode_ids,
                "test-butler",
                tenant_id="acme",
                request_id="req-456",
            )

        mock_sr.assert_awaited_once()
        sr_kwargs = mock_sr.call_args.kwargs
        assert sr_kwargs.get("tenant_id") == "acme"
        assert sr_kwargs.get("request_id") == "req-456"

    async def test_tenant_id_included_in_memory_events(self) -> None:
        """memory_events INSERT includes the correct tenant_id."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(2)

        parsed = ConsolidationResult()  # No actions — just episode marking

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock),
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            await execute_consolidation(
                pool,
                engine,
                parsed,
                episode_ids,
                "test-butler",
                tenant_id="acme",
            )

        # Two pool.execute calls: 1) terminal state UPDATE, 2) memory_events INSERT
        assert pool.execute.await_count >= 2
        # The memory_events INSERT is the second call
        events_sql_call = pool.execute.call_args_list[1]
        events_sql = events_sql_call[0][0]
        assert "memory_events" in events_sql
        assert "tenant_id" in events_sql
        # The tenant_id parameter is passed as second positional arg ($2)
        assert events_sql_call[0][2] == "acme"

    async def test_default_tenant_id_is_shared(self) -> None:
        """When tenant_id is not specified, it defaults to 'owner'."""
        pool = _mock_pool()
        engine = _mock_embedding_engine()
        episode_ids = _make_episode_ids(1)

        parsed = ConsolidationResult(
            new_facts=[NewFact(subject="u", predicate="p", content="c")],
        )

        with (
            patch.object(_exec_mod, "store_fact", new_callable=AsyncMock) as mock_sf,
            patch.object(_exec_mod, "create_link", new_callable=AsyncMock),
            patch.object(_exec_mod, "store_rule", new_callable=AsyncMock),
            patch.object(_exec_mod, "confirm_memory", new_callable=AsyncMock),
        ):
            mock_sf.return_value = uuid.uuid4()

            await execute_consolidation(pool, engine, parsed, episode_ids, "my-butler")

        sf_kwargs = mock_sf.call_args.kwargs
        assert sf_kwargs.get("tenant_id") == "shared"
        assert sf_kwargs.get("request_id") is None
