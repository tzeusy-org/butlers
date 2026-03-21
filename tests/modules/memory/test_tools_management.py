"""Tests for management and context building MCP tools in tools.py."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load tools module (mocking sentence_transformers first)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Load tools module
# ---------------------------------------------------------------------------
from butlers.modules.memory.tools import (
    _helpers,
    memory_context,
    memory_forget,
    memory_stats,
    predicate_list,
    predicate_search,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Return an AsyncMock asyncpg pool."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


@pytest.fixture()
def memory_id() -> uuid.UUID:
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture()
def mock_embedding_engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


# ---------------------------------------------------------------------------
# memory_forget tests
# ---------------------------------------------------------------------------


class TestMemoryForget:
    """Tests for memory_forget tool wrapper."""

    async def test_delegates_to_storage_forget_memory(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """memory_forget delegates to _storage.forget_memory."""
        _helpers._storage.forget_memory = AsyncMock(return_value=True)
        result = await memory_forget(mock_pool, "fact", str(memory_id))
        _helpers._storage.forget_memory.assert_awaited_once_with(mock_pool, "fact", memory_id)
        assert result == {"forgotten": True}

    async def test_returns_forgotten_false_when_not_found(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """memory_forget returns {'forgotten': False} when storage returns False."""
        _helpers._storage.forget_memory = AsyncMock(return_value=False)
        result = await memory_forget(mock_pool, "fact", str(memory_id))
        assert result == {"forgotten": False}

    async def test_converts_string_id_to_uuid(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """memory_forget converts the string memory_id to a uuid.UUID."""
        _helpers._storage.forget_memory = AsyncMock(return_value=True)
        await memory_forget(mock_pool, "rule", str(memory_id))
        call_args = _helpers._storage.forget_memory.call_args[0]
        assert isinstance(call_args[2], uuid.UUID)
        assert call_args[2] == memory_id

    async def test_passes_memory_type_through(
        self, mock_pool: AsyncMock, memory_id: uuid.UUID
    ) -> None:
        """memory_forget passes memory_type verbatim to storage."""
        _helpers._storage.forget_memory = AsyncMock(return_value=True)
        for mtype in ("episode", "fact", "rule"):
            await memory_forget(mock_pool, mtype, str(memory_id))
            assert _helpers._storage.forget_memory.call_args[0][1] == mtype


# ---------------------------------------------------------------------------
# memory_stats tests
# ---------------------------------------------------------------------------


class TestMemoryStats:
    """Tests for memory_stats tool wrapper."""

    async def test_returns_all_expected_top_level_keys(self, mock_pool: AsyncMock) -> None:
        """memory_stats returns episodes, facts, and rules top-level keys."""
        result = await memory_stats(mock_pool)
        assert "episodes" in result
        assert "facts" in result
        assert "rules" in result

    async def test_episodes_structure(self, mock_pool: AsyncMock) -> None:
        """Episodes section has total, unconsolidated, backlog_age_hours."""
        mock_pool.fetchval = AsyncMock(return_value=42)
        result = await memory_stats(mock_pool)
        ep = result["episodes"]
        assert "total" in ep
        assert "unconsolidated" in ep
        assert "backlog_age_hours" in ep

    async def test_facts_structure(self, mock_pool: AsyncMock) -> None:
        """Facts section has active, fading, superseded, expired."""
        result = await memory_stats(mock_pool)
        facts = result["facts"]
        assert "active" in facts
        assert "fading" in facts
        assert "superseded" in facts
        assert "expired" in facts

    async def test_rules_structure(self, mock_pool: AsyncMock) -> None:
        """Rules section has candidate, established, proven, anti_pattern, forgotten."""
        result = await memory_stats(mock_pool)
        rules = result["rules"]
        assert "candidate" in rules
        assert "established" in rules
        assert "proven" in rules
        assert "anti_pattern" in rules
        assert "forgotten" in rules

    async def test_backlog_age_hours_none_when_no_unconsolidated(
        self, mock_pool: AsyncMock
    ) -> None:
        """backlog_age_hours is None when there are no unconsolidated episodes."""
        # The third fetchval call returns backlog_age_hours
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # 1=ep_total, 2=ep_unconsolidated, 3=backlog_age
            if call_count == 3:
                return None
            return 0

        mock_pool.fetchval = AsyncMock(side_effect=_side_effect)
        result = await memory_stats(mock_pool)
        assert result["episodes"]["backlog_age_hours"] is None

    async def test_backlog_age_hours_float_when_present(self, mock_pool: AsyncMock) -> None:
        """backlog_age_hours is a float when backlog exists."""
        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                return 48.5  # 48.5 hours
            return 10

        mock_pool.fetchval = AsyncMock(side_effect=_side_effect)
        result = await memory_stats(mock_pool)
        assert result["episodes"]["backlog_age_hours"] == 48.5

    async def test_scope_filtering_passes_scope_param(self, mock_pool: AsyncMock) -> None:
        """When scope is provided, SQL queries for facts/rules include scope filter."""
        mock_pool.fetchval = AsyncMock(return_value=0)
        await memory_stats(mock_pool, scope="my-butler")

        # Check that at least some calls passed the scope parameter
        calls_with_scope = [
            c
            for c in mock_pool.fetchval.call_args_list
            if len(c.args) > 1 and c.args[-1] == "my-butler"
        ]
        # Facts have 4 queries and rules have 5 queries that need scope
        assert len(calls_with_scope) >= 4, (
            f"Expected at least 4 calls with scope param, got {len(calls_with_scope)}"
        )

    async def test_no_scope_no_extra_params(self, mock_pool: AsyncMock) -> None:
        """When scope is None, queries for facts/rules don't pass extra params."""
        mock_pool.fetchval = AsyncMock(return_value=0)
        await memory_stats(mock_pool)

        # Episode queries (first 3) should have 1 arg each (just SQL)
        for call in mock_pool.fetchval.call_args_list[:3]:
            assert len(call.args) == 1, f"Episode query should have 1 arg, got {call.args}"

    async def test_fetchval_called_for_all_counts(self, mock_pool: AsyncMock) -> None:
        """memory_stats issues the right number of fetchval calls."""
        mock_pool.fetchval = AsyncMock(return_value=0)
        await memory_stats(mock_pool)
        # 3 episode + 4 facts + 5 rules = 12 calls
        assert mock_pool.fetchval.call_count == 12


# ---------------------------------------------------------------------------
# memory_context tests
# ---------------------------------------------------------------------------


def _make_fact(
    id_: uuid.UUID | None = None,
    *,
    subject: str = "user",
    predicate: str = "prefers",
    content: str = "dark mode",
    confidence: float = 0.9,
    composite_score: float = 0.8,
    **extra,
) -> dict:
    if id_ is None:
        id_ = uuid.uuid4()
    return {
        "id": id_,
        "memory_type": "fact",
        "subject": subject,
        "predicate": predicate,
        "content": content,
        "confidence": confidence,
        "composite_score": composite_score,
        **extra,
    }


def _make_rule(
    id_: uuid.UUID | None = None,
    *,
    content: str = "Always greet warmly",
    maturity: str = "established",
    effectiveness_score: float = 0.75,
    composite_score: float = 0.6,
    **extra,
) -> dict:
    if id_ is None:
        id_ = uuid.uuid4()
    return {
        "id": id_,
        "memory_type": "rule",
        "content": content,
        "maturity": maturity,
        "effectiveness_score": effectiveness_score,
        "composite_score": composite_score,
        **extra,
    }


class TestMemoryContext:
    """Tests for memory_context tool."""

    async def test_formats_facts_section(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """memory_context includes a Task-Relevant Facts section with formatted entries."""
        _helpers._search.recall = AsyncMock(
            return_value=[
                _make_fact(
                    subject="user", predicate="prefers", content="dark mode", confidence=0.9
                ),
            ]
        )
        result = await memory_context(
            mock_pool, mock_embedding_engine, "user preferences", "butler-1"
        )
        assert "# Memory Context" in result
        assert "## Task-Relevant Facts" in result
        assert "[user] [prefers]: dark mode (confidence: 0.90)" in result

    async def test_formats_rules_section(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """memory_context includes an Active Rules section with formatted entries."""
        _helpers._search.recall = AsyncMock(
            return_value=[
                _make_rule(
                    content="Always greet warmly",
                    maturity="established",
                    effectiveness_score=0.75,
                ),
            ]
        )
        result = await memory_context(
            mock_pool, mock_embedding_engine, "greeting behavior", "butler-1"
        )
        assert "## Active Rules" in result
        assert "Always greet warmly (maturity: established, effectiveness: 0.75)" in result

    async def test_both_sections_present(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """When recall returns both facts and rules, both sections appear."""
        _helpers._search.recall = AsyncMock(
            return_value=[
                _make_fact(composite_score=0.9),
                _make_rule(composite_score=0.7),
            ]
        )
        result = await memory_context(mock_pool, mock_embedding_engine, "anything", "butler-1")
        assert "## Task-Relevant Facts" in result
        assert "## Active Rules" in result

    async def test_empty_results_returns_header_only(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """When recall returns nothing, the context is just the header."""
        _helpers._search.recall = AsyncMock(return_value=[])
        result = await memory_context(mock_pool, mock_embedding_engine, "anything", "butler-1")
        assert "# Memory Context" in result
        assert "## Task-Relevant Facts" not in result
        assert "## Active Rules" not in result

    async def test_respects_token_budget(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """memory_context truncates output to stay within the token budget."""
        # Create many facts that would exceed a small budget
        many_facts = [
            _make_fact(
                subject=f"subject-{i}",
                predicate=f"predicate-{i}",
                content="x" * 100,
                composite_score=1.0 - i * 0.01,
            )
            for i in range(50)
        ]
        _helpers._search.recall = AsyncMock(return_value=many_facts)

        # Very small budget: 100 tokens = ~400 chars
        result = await memory_context(
            mock_pool, mock_embedding_engine, "test", "butler-1", token_budget=100
        )
        assert len(result) <= 400 + 200  # some tolerance for header

    async def test_orders_by_composite_score_descending(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """Facts and rules are rendered in composite_score descending order."""
        # recall already returns sorted, but verify the tool preserves order
        fact_high = _make_fact(
            subject="high", predicate="score", content="important", composite_score=0.95
        )
        fact_low = _make_fact(
            subject="low", predicate="score", content="trivial", composite_score=0.3
        )
        _helpers._search.recall = AsyncMock(return_value=[fact_high, fact_low])

        result = await memory_context(mock_pool, mock_embedding_engine, "test", "butler-1")

        # "important" should appear before "trivial"
        idx_high = result.index("important")
        idx_low = result.index("trivial")
        assert idx_high < idx_low

    async def test_calls_recall_with_correct_params(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """memory_context calls recall with the right arguments."""
        _helpers._search.recall = AsyncMock(return_value=[])
        await memory_context(mock_pool, mock_embedding_engine, "my topic", "butler-x")
        _helpers._search.recall.assert_awaited_once_with(
            mock_pool,
            "my topic",
            mock_embedding_engine,
            scope="butler-x",
            limit=30,
            tenant_id="shared",
        )

    async def test_no_facts_only_rules(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """When recall returns only rules, the Task-Relevant Facts section is absent."""
        _helpers._search.recall = AsyncMock(
            return_value=[
                _make_rule(content="Be helpful"),
            ]
        )
        result = await memory_context(mock_pool, mock_embedding_engine, "test", "butler-1")
        assert "## Task-Relevant Facts" not in result
        assert "## Active Rules" in result
        assert "Be helpful" in result

    async def test_no_rules_only_facts(
        self, mock_pool: AsyncMock, mock_embedding_engine: MagicMock
    ) -> None:
        """When recall returns only facts, the Active Rules section is absent."""
        _helpers._search.recall = AsyncMock(
            return_value=[
                _make_fact(content="likes coffee"),
            ]
        )
        result = await memory_context(mock_pool, mock_embedding_engine, "test", "butler-1")
        assert "## Task-Relevant Facts" in result
        assert "## Active Rules" not in result
        assert "likes coffee" in result


# ---------------------------------------------------------------------------
# predicate_list tests
# ---------------------------------------------------------------------------


def _make_predicate_row(
    name: str,
    *,
    scope: str = "global",
    expected_subject_type: str = "entity",
    expected_object_type: str = "entity",
    is_edge: bool = False,
    is_temporal: bool = False,
    description: str = "A predicate",
    example_json: dict | None = None,
) -> dict:
    return {
        "name": name,
        "scope": scope,
        "expected_subject_type": expected_subject_type,
        "expected_object_type": expected_object_type,
        "is_edge": is_edge,
        "is_temporal": is_temporal,
        "description": description,
        "example_json": example_json,
    }


class TestPredicateList:
    """Tests for predicate_list tool wrapper."""

    async def test_returns_list_of_dicts(self, mock_pool: AsyncMock) -> None:
        """predicate_list returns a list of dicts matching the row data."""
        row = _make_predicate_row("knows")
        mock_pool.fetch.return_value = [row]
        result = await predicate_list(mock_pool)
        assert result == [row]

    async def test_row_shape_includes_is_temporal(self, mock_pool: AsyncMock) -> None:
        """Every returned row includes is_temporal."""
        rows = [
            _make_predicate_row("ate", is_temporal=True),
            _make_predicate_row("knows", is_edge=True),
        ]
        mock_pool.fetch = AsyncMock(return_value=rows)
        result = await predicate_list(mock_pool)
        for row in result:
            assert "is_temporal" in row, f"Row missing is_temporal: {row}"

    async def test_row_shape_includes_all_expected_keys(self, mock_pool: AsyncMock) -> None:
        """Every returned row includes name, scope, subject/object types, is_edge, is_temporal, description, example_json."""  # noqa: E501
        expected_keys = {
            "name",
            "scope",
            "expected_subject_type",
            "expected_object_type",
            "is_edge",
            "is_temporal",
            "description",
            "example_json",
        }
        row = _make_predicate_row("prefers")
        mock_pool.fetch = AsyncMock(return_value=[row])
        result = await predicate_list(mock_pool)
        assert len(result) == 1
        assert set(result[0].keys()) == expected_keys

    async def test_ordered_by_name_asc(self, mock_pool: AsyncMock) -> None:
        """Query includes ORDER BY name ASC."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await predicate_list(mock_pool)
        call_args = mock_pool.fetch.call_args
        sql = call_args.args[0]
        assert "ORDER BY name ASC" in sql

    async def test_edges_only_false_no_where_clause(self, mock_pool: AsyncMock) -> None:
        """When edges_only=False (default), query has no WHERE clause."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await predicate_list(mock_pool, edges_only=False)
        call_args = mock_pool.fetch.call_args
        sql = call_args.args[0]
        assert "WHERE" not in sql

    async def test_edges_only_true_adds_where_clause(self, mock_pool: AsyncMock) -> None:
        """When edges_only=True, query filters to is_edge = true."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await predicate_list(mock_pool, edges_only=True)
        call_args = mock_pool.fetch.call_args
        sql = call_args.args[0]
        assert "WHERE is_edge = true" in sql

    async def test_edges_only_still_has_name_order(self, mock_pool: AsyncMock) -> None:
        """edges_only=True still orders by name ASC."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await predicate_list(mock_pool, edges_only=True)
        call_args = mock_pool.fetch.call_args
        sql = call_args.args[0]
        assert "ORDER BY name ASC" in sql

    async def test_is_temporal_value_preserved(self, mock_pool: AsyncMock) -> None:
        """is_temporal values (True/False) are passed through unchanged."""
        rows = [
            _make_predicate_row("ate", is_temporal=True),
            _make_predicate_row("knows", is_temporal=False),
        ]
        mock_pool.fetch = AsyncMock(return_value=rows)
        result = await predicate_list(mock_pool)
        by_name = {r["name"]: r for r in result}
        assert by_name["ate"]["is_temporal"] is True
        assert by_name["knows"]["is_temporal"] is False

    async def test_empty_registry_returns_empty_list(self, mock_pool: AsyncMock) -> None:
        """When no predicates are registered, returns an empty list."""
        mock_pool.fetch = AsyncMock(return_value=[])
        result = await predicate_list(mock_pool)
        assert result == []


# ---------------------------------------------------------------------------
# predicate_search tests
# ---------------------------------------------------------------------------


class TestPredicateSearch:
    """Tests for predicate_search — hybrid retrieval with RRF fusion (tasks 6.1–6.3, 7.5–7.8)."""

    # -------------------------------------------------------------------------
    # Empty-query path
    # -------------------------------------------------------------------------

    async def test_empty_query_returns_all(self, mock_pool: AsyncMock) -> None:
        """An empty query returns all registered predicates ordered by name."""
        rows = [
            _make_predicate_row("birthday"),
            _make_predicate_row("occupation"),
            _make_predicate_row("parent_of", is_edge=True),
        ]
        mock_pool.fetch = AsyncMock(return_value=rows)

        result = await predicate_search(mock_pool, "")

        assert len(result) == 3

        # Empty query must use ORDER BY name ASC on the single fetch call.
        call_args = mock_pool.fetch.call_args
        sql = call_args.args[0]
        assert "ORDER BY name ASC" in sql
        assert "lower(name)" not in sql

    async def test_scope_filter_applied(self, mock_pool: AsyncMock) -> None:
        """scope parameter adds a scope column filter to the empty-query path."""
        row = _make_predicate_row("measurement", scope="health")
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await predicate_search(mock_pool, "", scope="health")

        assert len(result) == 1
        assert result[0]["scope"] == "health"

        call_args = mock_pool.fetch.call_args
        sql = call_args.args[0]
        assert "scope =" in sql

    async def test_empty_query_results_have_zero_score(self, mock_pool: AsyncMock) -> None:
        """Empty-query results include score=0.0."""
        rows = [_make_predicate_row("birthday")]
        mock_pool.fetch = AsyncMock(return_value=rows)

        result = await predicate_search(mock_pool, "")

        assert len(result) == 1
        assert result[0]["score"] == 0.0

    async def test_scope_filter_without_query(self, mock_pool: AsyncMock) -> None:
        """scope filter works even when query is empty — adds scope column WHERE clause."""
        rows = [_make_predicate_row("bmi", scope="health")]
        mock_pool.fetch = AsyncMock(return_value=rows)

        result = await predicate_search(mock_pool, "", scope="health")

        assert len(result) == 1
        call_args = mock_pool.fetch.call_args
        sql = call_args.args[0]
        assert "scope =" in sql

    # -------------------------------------------------------------------------
    # Non-empty query: hybrid retrieval signals
    # -------------------------------------------------------------------------

    async def test_trigram_signal_uses_similarity(self, mock_pool: AsyncMock) -> None:
        """Non-empty query issues a similarity() call for the trigram signal."""
        parent_row = _make_predicate_row("parent_of", is_edge=True, description="Parent relation")
        mock_pool.fetch = AsyncMock(return_value=[parent_row])

        await predicate_search(mock_pool, "parent")

        # All pool.fetch calls should be inspectable; check at least one uses similarity.
        all_calls = mock_pool.fetch.call_args_list
        all_sqls = [c.args[0] for c in all_calls]
        assert any("similarity" in sql for sql in all_sqls)

    async def test_fts_signal_uses_tsquery(self, mock_pool: AsyncMock) -> None:
        """Non-empty query issues a plainto_tsquery() full-text search."""
        row = _make_predicate_row("parent_of", description="father or mother relation")
        mock_pool.fetch = AsyncMock(return_value=[row])

        await predicate_search(mock_pool, "father")

        all_calls = mock_pool.fetch.call_args_list
        all_sqls = [c.args[0] for c in all_calls]
        assert any("plainto_tsquery" in sql for sql in all_sqls)

    async def test_semantic_signal_issued_when_engine_provided(
        self,
        mock_pool: AsyncMock,
        mock_embedding_engine: MagicMock,
    ) -> None:
        """When embedding_engine is provided the semantic signal uses cosine distance (<=>)."""
        row = _make_predicate_row("parent_of", description="parent child relationship")
        mock_pool.fetch = AsyncMock(return_value=[row])

        await predicate_search(mock_pool, "dad", embedding_engine=mock_embedding_engine)

        all_calls = mock_pool.fetch.call_args_list
        all_sqls = [c.args[0] for c in all_calls]
        assert any("<=>" in sql for sql in all_sqls)
        mock_embedding_engine.embed.assert_called_once_with("dad")

    async def test_semantic_signal_skipped_when_no_engine(self, mock_pool: AsyncMock) -> None:
        """When embedding_engine is None no cosine distance query is issued."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await predicate_search(mock_pool, "dad")  # no embedding_engine

        all_calls = mock_pool.fetch.call_args_list
        all_sqls = [c.args[0] for c in all_calls]
        assert not any("<=>" in sql for sql in all_sqls)

    # -------------------------------------------------------------------------
    # RRF fusion and result shape
    # -------------------------------------------------------------------------

    async def test_result_includes_score(self, mock_pool: AsyncMock) -> None:
        """Every non-empty-query result includes a numeric score field."""
        row = _make_predicate_row("parent_of", is_edge=True)
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await predicate_search(mock_pool, "parent")

        assert len(result) >= 1
        for r in result:
            assert "score" in r
            assert isinstance(r["score"], float)
            assert r["score"] > 0.0

    async def test_result_shape_includes_required_keys(self, mock_pool: AsyncMock) -> None:
        """Every returned row includes all required metadata keys including scope, example_json, and score."""  # noqa: E501
        expected_keys = {
            "name",
            "scope",
            "expected_subject_type",
            "expected_object_type",
            "is_edge",
            "is_temporal",
            "description",
            "example_json",
            "score",
        }
        row = _make_predicate_row("knows", is_edge=True)
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await predicate_search(mock_pool, "knows")

        assert len(result) >= 1
        assert expected_keys.issubset(set(result[0].keys()))

    async def test_rrf_deduplicates_names_across_signals(
        self,
        mock_pool: AsyncMock,
    ) -> None:
        """A name appearing in multiple signals is fused to one result with additive score."""
        parent_row = _make_predicate_row("parent_of", is_edge=True)
        # Trigram and FTS both return parent_of; metadata fetch returns it too.
        mock_pool.fetch = AsyncMock(return_value=[parent_row])

        result = await predicate_search(mock_pool, "parent")

        names = [r["name"] for r in result]
        assert names.count("parent_of") == 1

    async def test_rrf_score_higher_when_appearing_in_multiple_signals(
        self,
        mock_pool: AsyncMock,
    ) -> None:
        """RRF score is proportional to how many signals rank a name highly.

        We test this by verifying that a name returned from all three calls
        gets a higher RRF score than 1/(60+1) (= ~0.016), the max for one signal.
        """
        row = _make_predicate_row("parent_of", is_edge=True)
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await predicate_search(mock_pool, "parent")

        assert len(result) >= 1
        # Parent appeared in trigram + FTS results (at least 2 signals) → score > 1/61.
        assert result[0]["score"] > 1.0 / 61.0

    # -------------------------------------------------------------------------
    # Fallback path (when all hybrid signals fail)
    # -------------------------------------------------------------------------

    async def test_fallback_to_ilike_when_all_signals_fail(
        self,
        mock_pool: AsyncMock,
    ) -> None:
        """When trigram / FTS / semantic all raise, falls back to ILIKE prefix search."""
        # Make every pool.fetch call raise an exception to force fallback.
        call_count = 0
        parent_row = _make_predicate_row("parent_of", is_edge=True)

        async def side_effect(sql, *args):
            nonlocal call_count
            call_count += 1
            # First two calls (trigram + FTS) raise to simulate missing extensions.
            if call_count <= 2:
                raise Exception("pg_trgm not installed")
            return [parent_row]

        mock_pool.fetch = AsyncMock(side_effect=side_effect)

        result = await predicate_search(mock_pool, "parent")

        # Result should come from the fallback query.
        assert len(result) == 1
        assert result[0]["name"] == "parent_of"
        # Fallback uses ILIKE prefix matching.
        last_sql = mock_pool.fetch.call_args.args[0]
        assert "lower(name) LIKE" in last_sql

    # -------------------------------------------------------------------------
    # Scope filtering for non-empty queries
    # -------------------------------------------------------------------------

    async def test_scope_filter_applied_to_all_signals(self, mock_pool: AsyncMock) -> None:
        """scope parameter injects scope column filter in each signal query."""
        row = _make_predicate_row("measurement", scope="health")
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await predicate_search(mock_pool, "measurement", scope="health")

        assert len(result) >= 1
        # All signal queries should include the scope filter.
        all_calls = mock_pool.fetch.call_args_list
        for call in all_calls:
            sql = call.args[0]
            assert "scope =" in sql

    async def test_no_scope_no_scope_filter_in_signals(self, mock_pool: AsyncMock) -> None:
        """When scope is None, no scope column filter appears in signal queries."""
        mock_pool.fetch = AsyncMock(return_value=[])

        await predicate_search(mock_pool, "some_query", scope=None)

        # Signal queries (not metadata query) should not filter on scope column.
        all_calls = mock_pool.fetch.call_args_list
        signal_sqls = [
            c.args[0] for c in all_calls if "similarity" in c.args[0] or "tsquery" in c.args[0]
        ]
        for sql in signal_sqls:
            assert "scope =" not in sql

    # -------------------------------------------------------------------------
    # No-match cases
    # -------------------------------------------------------------------------

    async def test_no_match_returns_empty_list(self, mock_pool: AsyncMock) -> None:
        """When no predicates match the query, an empty list is returned."""
        mock_pool.fetch = AsyncMock(return_value=[])
        result = await predicate_search(mock_pool, "xyzzy_nonexistent")
        assert result == []

    async def test_scope_param_bound_to_correct_position_in_signals(
        self,
        mock_pool: AsyncMock,
    ) -> None:
        """scope value must be bound at the position that matches its $N placeholder.

        Each signal query has the main signal param at $1 (similarity query string /
        tsquery string / embedding vector).  The scope filter is appended after,
        so the scope value must appear at $2 in the args.  If the SQL reads
        ``scope = $1`` but the scope value is at args[1] ($2),
        the DB would compare scope against the query string instead of the scope value.
        """
        import re

        scope_value = "health"
        query_str = "parent"
        row = _make_predicate_row("parent_of", scope=scope_value)
        mock_pool.fetch = AsyncMock(return_value=[row])

        await predicate_search(mock_pool, query_str, scope=scope_value)

        all_calls = mock_pool.fetch.call_args_list
        for call in all_calls:
            sql = call.args[0]
            args = call.args[1:]  # positional args; args[i] corresponds to $(i+1)
            if "scope =" not in sql:
                continue

            # Extract the placeholder number from the SQL:
            # e.g. "scope = $2" → 2
            m = re.search(r"scope\s*=\s*\$(\d+)", sql)
            assert m is not None, f"scope condition not found in SQL: {sql!r}"
            placeholder_idx = int(m.group(1)) - 1  # convert $N to 0-based index
            assert placeholder_idx < len(args), (
                f"Placeholder ${placeholder_idx + 1} out of bounds in args {args!r}"
            )
            # The value at the placeholder position must be the scope value
            assert args[placeholder_idx] == scope_value, (
                f"args[{placeholder_idx}]={args[placeholder_idx]!r} "
                f"but expected scope={scope_value!r}; "
                f"query was {sql!r} with args {args!r}"
            )

    # -------------------------------------------------------------------------
    # example_json passthrough
    # -------------------------------------------------------------------------

    async def test_example_json_included_when_populated(self, mock_pool: AsyncMock) -> None:
        """When example_json is set on a predicate, it appears in the result dict."""
        example = {"content": "82.5 kg", "metadata": {"value": 82.5, "unit": "kg"}}
        row = _make_predicate_row("measurement_weight", scope="health", example_json=example)
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await predicate_search(mock_pool, "")

        assert len(result) == 1
        assert result[0]["example_json"] == example

    async def test_example_json_none_when_not_set(self, mock_pool: AsyncMock) -> None:
        """When example_json is NULL (None), the key is present in the result with value None."""
        row = _make_predicate_row("note", example_json=None)
        mock_pool.fetch = AsyncMock(return_value=[row])

        result = await predicate_search(mock_pool, "")

        assert len(result) == 1
        assert "example_json" in result[0]
        assert result[0]["example_json"] is None

    async def test_predicate_list_sql_includes_example_json(self, mock_pool: AsyncMock) -> None:
        """predicate_list SELECT includes example_json column."""
        mock_pool.fetch = AsyncMock(return_value=[])
        await predicate_list(mock_pool)
        call_args = mock_pool.fetch.call_args
        sql = call_args.args[0]
        assert "example_json" in sql

    async def test_predicate_search_sql_includes_example_json(self, mock_pool: AsyncMock) -> None:
        """predicate_search metadata SELECT includes example_json column."""
        row = _make_predicate_row("knows", is_edge=True)
        mock_pool.fetch = AsyncMock(return_value=[row])

        await predicate_search(mock_pool, "knows")

        # At least one of the fetch calls must select example_json (the metadata fetch).
        all_calls = mock_pool.fetch.call_args_list
        sqls_with_example_json = [c.args[0] for c in all_calls if "example_json" in c.args[0]]
        assert sqls_with_example_json, (
            "No pool.fetch call selected example_json. "
            f"Calls: {[c.args[0][:80] for c in all_calls]}"
        )
