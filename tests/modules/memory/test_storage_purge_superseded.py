"""Tests for purge_superseded_facts() in Memory Butler storage."""

from __future__ import annotations

import importlib.util
from unittest.mock import AsyncMock

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
purge_superseded_facts = _mod.purge_superseded_facts

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPurgeSupersededFacts:
    """Verify purge_superseded_facts deletes superseded and ha_state facts."""

    async def test_deletes_superseded_and_ha_state(self) -> None:
        """Both superseded facts and ha_state facts are purged."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["DELETE 5", "DELETE 12"])

        result = await purge_superseded_facts(pool, older_than_days=7)

        assert result == {"deleted": 5, "deleted_ha_state": 12}
        assert pool.execute.call_count == 2

        # First call: superseded facts
        first_call = pool.execute.call_args_list[0]
        assert "superseded" in first_call.args[0]
        assert first_call.args[1] == 7

        # Second call: ha_state facts
        second_call = pool.execute.call_args_list[1]
        assert "ha_state" in second_call.args[0]

    async def test_custom_older_than_days(self) -> None:
        """The older_than_days parameter is forwarded to the superseded query."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 0"])

        await purge_superseded_facts(pool, older_than_days=30)

        first_call = pool.execute.call_args_list[0]
        assert first_call.args[1] == 30

    async def test_zero_deletions(self) -> None:
        """Returns zero counts when nothing to delete."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["DELETE 0", "DELETE 0"])

        result = await purge_superseded_facts(pool)

        assert result == {"deleted": 0, "deleted_ha_state": 0}

    async def test_empty_result_string(self) -> None:
        """Handles empty result strings gracefully."""
        pool = AsyncMock()
        pool.execute = AsyncMock(side_effect=["", ""])

        result = await purge_superseded_facts(pool)

        assert result == {"deleted": 0, "deleted_ha_state": 0}
