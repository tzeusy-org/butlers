"""Unit tests for store_rule() in Memory Butler storage."""

from __future__ import annotations

import importlib.util
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load the storage module from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------
_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
store_rule = _mod.store_rule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_pool():
    pool = AsyncMock()
    pool.execute = AsyncMock()
    return pool


@pytest.fixture()
def mock_engine():
    engine = MagicMock()
    engine.embed = MagicMock(return_value=[0.1] * 384)
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStoreRuleBasic:
    """Core behavior of store_rule()."""

    async def test_returns_uuid(self, mock_pool, mock_engine):
        """store_rule should return a UUID."""
        result = await store_rule(mock_pool, "Always greet the user", mock_engine)
        assert isinstance(result, uuid.UUID)

    async def test_embedding_engine_called_with_content(self, mock_pool, mock_engine):
        """The embedding engine should be called with the rule content."""
        content = "Never reveal secrets"
        await store_rule(mock_pool, content, mock_engine)
        mock_engine.embed.assert_called_once_with(content)

    async def test_preprocess_text_called(self, mock_pool, mock_engine):
        """preprocess_text should be called for the search vector."""
        content = "Be  polite\x00 always"
        with patch.object(_mod, "preprocess_text", wraps=_mod.preprocess_text) as mock_pp:
            await store_rule(mock_pool, content, mock_engine)
            mock_pp.assert_called_once_with(content)

    async def test_pool_execute_called(self, mock_pool, mock_engine):
        """pool.execute should be called exactly once."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        mock_pool.execute.assert_called_once()


class TestStoreRuleDefaults:
    """Default parameter values for store_rule()."""

    async def test_default_scope_is_global(self, mock_pool, mock_engine):
        """When no scope is specified, it defaults to 'global'."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        args = mock_pool.execute.call_args[0]
        # positional args: sql, rule_id, content, embedding, search_text, scope, ...
        scope_arg = args[5]  # $5 is scope
        assert scope_arg == "global"

    async def test_new_rule_is_candidate(self, mock_pool, mock_engine):
        """New rules should start as 'candidate' maturity (hardcoded in SQL)."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        sql = mock_pool.execute.call_args[0][0]
        assert "'candidate'" in sql

    async def test_initial_confidence_is_half(self, mock_pool, mock_engine):
        """New rules should start with confidence=0.5 (hardcoded in SQL)."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        sql = mock_pool.execute.call_args[0][0]
        assert "0.5" in sql

    async def test_initial_effectiveness_is_zero(self, mock_pool, mock_engine):
        """New rules should start with effectiveness_score=0.0 (hardcoded in SQL)."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        sql = mock_pool.execute.call_args[0][0]
        assert "0.0" in sql

    async def test_default_tags_empty_list(self, mock_pool, mock_engine):
        """When no tags provided, tags_json should be '[]'."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        args = mock_pool.execute.call_args[0]
        tags_json = args[9]  # $9 is tags
        assert tags_json == "[]"

    async def test_default_metadata_empty_dict(self, mock_pool, mock_engine):
        """When no metadata provided, meta_json should be '{}'."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        args = mock_pool.execute.call_args[0]
        meta_json = args[10]  # $10 is metadata
        assert meta_json == "{}"

    async def test_default_source_butler_is_none(self, mock_pool, mock_engine):
        """When no source_butler provided, it defaults to None."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        args = mock_pool.execute.call_args[0]
        source_butler_arg = args[7]  # $7 is source_butler
        assert source_butler_arg is None

    async def test_default_source_episode_id_is_none(self, mock_pool, mock_engine):
        """When no source_episode_id provided, it defaults to None."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        args = mock_pool.execute.call_args[0]
        source_episode_arg = args[6]  # $6 is source_episode_id
        assert source_episode_arg is None


class TestStoreRuleCustomArgs:
    """Custom parameter values for store_rule()."""

    async def test_custom_scope_passed_through(self, mock_pool, mock_engine):
        """A custom scope value should be passed to the SQL query."""
        await store_rule(mock_pool, "Test rule", mock_engine, scope="butler-email")
        args = mock_pool.execute.call_args[0]
        assert args[5] == "butler-email"

    async def test_tags_are_json_serialized(self, mock_pool, mock_engine):
        """Tags should be JSON-serialized as a list."""
        tags = ["greeting", "polite", "ux"]
        await store_rule(mock_pool, "Test rule", mock_engine, tags=tags)
        args = mock_pool.execute.call_args[0]
        tags_json = args[9]
        assert json.loads(tags_json) == tags

    async def test_source_butler_passed_correctly(self, mock_pool, mock_engine):
        """source_butler should be passed as a positional arg."""
        await store_rule(mock_pool, "Test rule", mock_engine, source_butler="email-butler")
        args = mock_pool.execute.call_args[0]
        assert args[7] == "email-butler"

    async def test_source_episode_id_passed_correctly(self, mock_pool, mock_engine):
        """source_episode_id should be passed as a positional arg."""
        ep_id = uuid.uuid4()
        await store_rule(mock_pool, "Test rule", mock_engine, source_episode_id=ep_id)
        args = mock_pool.execute.call_args[0]
        assert args[6] == ep_id

    async def test_custom_metadata_json_serialized(self, mock_pool, mock_engine):
        """Custom metadata should be JSON-serialized as a dict."""
        meta = {"origin": "consolidation", "version": 2}
        await store_rule(mock_pool, "Test rule", mock_engine, metadata=meta)
        args = mock_pool.execute.call_args[0]
        meta_json = args[10]
        assert json.loads(meta_json) == meta


class TestStoreRuleSqlParameters:
    """Verify the SQL and positional parameters passed to pool.execute."""

    async def test_sql_contains_insert_into_rules(self, mock_pool, mock_engine):
        """The SQL should be an INSERT INTO rules statement."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        sql = mock_pool.execute.call_args[0][0]
        assert "INSERT INTO rules" in sql

    async def test_sql_uses_tsvector_for_search(self, mock_pool, mock_engine):
        """The SQL should use to_tsvector for the search_vector column."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        sql = mock_pool.execute.call_args[0][0]
        assert "to_tsvector('english', $4)" in sql

    async def test_ten_positional_args(self, mock_pool, mock_engine):
        """pool.execute should receive exactly 10 positional arguments after the SQL."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        args = mock_pool.execute.call_args[0]
        # args[0] is SQL, args[1..10] are the 10 parameters
        assert len(args) == 11

    async def test_rule_id_is_first_param(self, mock_pool, mock_engine):
        """The first positional parameter should be a UUID (the rule ID)."""
        result = await store_rule(mock_pool, "Test rule", mock_engine)
        args = mock_pool.execute.call_args[0]
        assert args[1] == result

    async def test_content_is_second_param(self, mock_pool, mock_engine):
        """The second positional parameter should be the content text."""
        content = "Always be helpful"
        await store_rule(mock_pool, content, mock_engine)
        args = mock_pool.execute.call_args[0]
        assert args[2] == content

    async def test_embedding_is_third_param(self, mock_pool, mock_engine):
        """The third positional parameter should be the stringified embedding."""
        await store_rule(mock_pool, "Test rule", mock_engine)
        args = mock_pool.execute.call_args[0]
        embedding_str = args[3]
        assert isinstance(embedding_str, str)
        assert "0.1" in embedding_str

    async def test_created_at_is_utc_datetime(self, mock_pool, mock_engine):
        """The created_at parameter ($8) should be a UTC datetime."""
        from datetime import datetime

        await store_rule(mock_pool, "Test rule", mock_engine)
        args = mock_pool.execute.call_args[0]
        created_at = args[8]
        assert isinstance(created_at, datetime)
        assert created_at.tzinfo is not None

    async def test_all_params_with_full_kwargs(self, mock_pool, mock_engine):
        """Verify all parameters when every kwarg is specified."""
        ep_id = uuid.uuid4()
        tags = ["safety", "critical"]
        meta = {"reviewed": True}

        result = await store_rule(
            mock_pool,
            "Never delete user data without confirmation",
            mock_engine,
            scope="butler-data",
            tags=tags,
            source_butler="data-butler",
            source_episode_id=ep_id,
            metadata=meta,
        )

        args = mock_pool.execute.call_args[0]
        assert args[1] == result  # rule_id
        assert args[2] == "Never delete user data without confirmation"  # content
        assert isinstance(args[3], str)  # embedding (stringified)
        assert isinstance(args[4], str)  # search_text
        assert args[5] == "butler-data"  # scope
        assert args[6] == ep_id  # source_episode_id
        assert args[7] == "data-butler"  # source_butler
        assert json.loads(args[9]) == tags  # tags
        assert json.loads(args[10]) == meta  # metadata
