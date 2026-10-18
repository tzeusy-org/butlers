"""Memory recall and search refuse an unrecognized structured filter key (bu-m2gipz).

A misspelled key used to be dropped, so the caller got an unfiltered result set that looked
scoped. The refusal happens before any query, names the bad key and lists the allowed keys.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.modules.memory import search as _search
from butlers.modules.memory.tools import reading as _reading

pytestmark = pytest.mark.unit

_BAD = {"entity_idd": "00000000-0000-0000-0000-000000000001"}


def _pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value=None)
    return pool


def _engine() -> MagicMock:
    engine = MagicMock()
    engine.embed.return_value = [1.0] + [0.0] * 383
    return engine


def _assert_refused(exc: pytest.ExceptionInfo, pool: AsyncMock, engine: MagicMock) -> None:
    message = str(exc.value)
    assert "entity_idd" in message
    for allowed in sorted(_search._KNOWN_FILTER_KEYS):
        assert allowed in message
    assert isinstance(exc.value, ValueError)
    for method in (pool.fetch, pool.fetchrow, pool.fetchval, pool.execute):
        method.assert_not_awaited()
    engine.embed.assert_not_called()


async def test_recall_refuses_an_unknown_filter_key_before_any_query() -> None:
    pool, engine = _pool(), _engine()
    with pytest.raises(_search.UnknownMemoryFilterError) as exc:
        await _search.recall(pool, "work", engine, filters=_BAD)
    _assert_refused(exc, pool, engine)


async def test_search_refuses_an_unknown_filter_key_before_any_query() -> None:
    pool, engine = _pool(), _engine()
    with pytest.raises(_search.UnknownMemoryFilterError) as exc:
        await _search.search(pool, "work", engine, filters=_BAD)
    _assert_refused(exc, pool, engine)


async def test_the_unknown_key_is_refused_even_beside_known_keys() -> None:
    pool, engine = _pool(), _engine()
    with pytest.raises(_search.UnknownMemoryFilterError) as exc:
        await _search.recall(
            pool, "work", engine, filters={"predicate": "works_at", "entity_idd": "x"}
        )
    _assert_refused(exc, pool, engine)


async def test_the_tool_layer_surfaces_the_error_for_both_tools() -> None:
    for call in (
        _reading.memory_recall(_pool(), _engine(), "work", filters=_BAD),
        _reading.memory_search(_pool(), _engine(), "work", filters=_BAD),
    ):
        with pytest.raises(ValueError, match="entity_idd"):
            await call


@pytest.mark.parametrize("filters", [None, {}])
def test_empty_filters_are_accepted(filters: dict | None) -> None:
    _search._validate_filter_keys(filters)


def test_every_known_key_is_accepted() -> None:
    _search._validate_filter_keys(dict.fromkeys(_search._KNOWN_FILTER_KEYS, "x"))
