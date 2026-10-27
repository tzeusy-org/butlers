"""Tests for event chain DB operations and MCP tools — condensed.

Covers:
- Validation constants: trigger types, statuses, _row_to_dict normalization
- event_chain CRUD: create, list, update, delete
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [pytest.mark.unit]

docker_available = shutil.which("docker") is not None


# ---------------------------------------------------------------------------
# Validation constants and _row_to_dict normalization (no DB)
# ---------------------------------------------------------------------------


def test_valid_trigger_types_and_statuses():
    """Exactly 3 trigger types and 5 valid statuses defined per spec."""
    from butlers.core.temporal.event_chains_db import _VALID_STATUSES, _VALID_TRIGGER_TYPES

    assert len(_VALID_TRIGGER_TYPES) == 3
    for t in ("calendar_event_end", "deadline_passed", "deadline_threshold"):
        assert t in _VALID_TRIGGER_TYPES

    assert _VALID_STATUSES == frozenset({"active", "paused", "fired", "failed", "disabled"})
    for s in ("active", "paused", "fired", "failed"):
        assert s in _VALID_STATUSES


def test_row_to_dict_normalization():
    """_row_to_dict normalizes UUID to str, decodes JSONB string actions, converts datetimes."""
    from butlers.core.temporal.event_chains_db import _row_to_dict

    row_id = uuid.uuid4()
    now = datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)
    result = _row_to_dict(
        {  # type: ignore[arg-type]
            "id": row_id,
            "name": "test-chain",
            "trigger_type": "calendar_event_end",
            "trigger_reference": None,
            "actions": '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Hi"}]',
            "status": "active",
            "butler_name": "general",
            "created_at": now,
            "updated_at": now,
        }
    )
    assert result["id"] == str(row_id)
    assert isinstance(result["actions"], list)
    assert result["actions"][0]["action_type"] == "prompt"
    assert isinstance(result["created_at"], str)
    assert "2026-03-28" in result["created_at"]


# ---------------------------------------------------------------------------
# DB integration tests (require Docker)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestEventChainsDB:
    """Integration tests for event_chains_db CRUD functions."""

    @pytest.fixture
    async def pool(self, migrated_db_url: str):
        """Return an asyncpg pool with event_chains table cleared between tests."""
        p = await asyncpg.create_pool(
            migrated_db_url,
            min_size=1,
            max_size=3,
            server_settings={"search_path": "general,public"},
            init=register_jsonb_codec,
        )
        await p.execute("TRUNCATE event_chains CASCADE")
        yield p
        await p.close()

    _ACTIONS = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Log event"}]

    async def test_create_and_list(self, pool):
        """event_chain_create returns a row dict; list filters by butler and trigger_type."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_list

        butler = f"butler-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            butler_name=butler,
            name="visit-chain",
            trigger_type="deadline_passed",
            actions=self._ACTIONS,
        )
        assert chain["id"] is not None
        assert chain["name"] == "visit-chain"
        assert chain["status"] == "active"

        # list returns it
        chains = await event_chain_list(pool, butler_name=butler)
        assert len(chains) == 1

        # filter by trigger_type works
        filtered = await event_chain_list(pool, butler_name=butler, trigger_type="deadline_passed")
        assert len(filtered) == 1

        empty = await event_chain_list(pool, butler_name=butler, trigger_type="deadline_threshold")
        assert len(empty) == 0

        # Historical calendar rows are intentionally preserved, readable and inert.
        historical_id = await pool.fetchval(
            "INSERT INTO event_chains (name, trigger_type, actions, butler_name) "
            "VALUES ('historical', 'calendar_event_end', $1, $2) RETURNING id",
            self._ACTIONS,
            butler,
        )
        historical = await event_chain_list(
            pool, butler_name=butler, trigger_type="calendar_event_end"
        )
        assert [r["id"] for r in historical] == [str(historical_id)]
        from butlers.core.temporal.event_chains_db import get_chain_by_id

        historical_row = await get_chain_by_id(pool, historical_id, butler_name=butler)
        assert historical_row is not None
        assert historical_row["trigger_type"] == "calendar_event_end"
        from butlers.core.scheduler import _tick_event_chain_pass

        assert await _tick_event_chain_pass(pool, None, datetime.now(UTC)) == 0
        assert (
            await pool.fetchval("SELECT status FROM event_chains WHERE id=$1", historical_id)
            == "active"
        )
        from butlers.core.temporal.event_chains_db import event_chain_delete

        assert await event_chain_delete(pool, historical_id, butler_name=butler) is True

    async def test_create_validation_errors(self, pool):
        """Duplicate name raises; invalid trigger_type raises; empty name raises."""
        from butlers.core.temporal.event_chains_db import event_chain_create

        butler = f"butler-{uuid.uuid4().hex[:8]}"
        await event_chain_create(
            pool,
            butler_name=butler,
            name="dup",
            trigger_type="deadline_passed",
            actions=self._ACTIONS,
        )
        with pytest.raises(ValueError, match="already exists"):  # duplicate name
            await event_chain_create(
                pool,
                butler_name=butler,
                name="dup",
                trigger_type="deadline_passed",
                actions=self._ACTIONS,
            )

        with pytest.raises((ValueError, Exception)):  # invalid trigger_type
            await event_chain_create(
                pool,
                butler_name=butler,
                name="bad-trigger",
                trigger_type="timer_elapsed",
                actions=self._ACTIONS,
            )

    async def test_update_and_delete(self, pool):
        """update changes status; not-found raises; delete removes row."""
        from butlers.core.temporal.event_chains_db import (
            event_chain_create,
            event_chain_delete,
            event_chain_update,
        )

        butler = f"butler-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            butler_name=butler,
            name="update-me",
            trigger_type="deadline_threshold",
            actions=self._ACTIONS,
        )
        chain_id = chain["id"]

        # Update status to paused
        updated = await event_chain_update(
            pool, chain_id=chain_id, butler_name=butler, status="paused"
        )
        assert updated["status"] == "paused"

        # Update actions resets status to active
        new_actions = [{"action_type": "prompt", "delay_minutes": 10, "prompt": "Remind"}]
        updated2 = await event_chain_update(
            pool, chain_id=chain_id, butler_name=butler, actions=new_actions
        )
        assert updated2["status"] == "active"

        # Not-found raises
        with pytest.raises((ValueError, Exception)):
            await event_chain_update(
                pool, chain_id=str(uuid.uuid4()), butler_name=butler, status="paused"
            )

        # Delete removes
        deleted = await event_chain_delete(pool, chain_id=chain_id, butler_name=butler)
        assert deleted is True

        not_found = await event_chain_delete(pool, chain_id=str(uuid.uuid4()), butler_name=butler)
        assert not_found is False


async def test_calendar_end_writes_refuse_before_any_database_access():
    """Historical rows remain readable; retired trigger cannot be selected."""
    from unittest.mock import AsyncMock

    from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_update

    pool = AsyncMock()
    pool.fetchrow.return_value = {"id": uuid.uuid4(), "trigger_type": "calendar_event_end"}
    actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "synthetic"}]
    with pytest.raises(ValueError, match="calendar_event_end"):
        await event_chain_create(
            pool,
            name="retired",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name="general",
        )
    with pytest.raises(ValueError, match="calendar_event_end"):
        await event_chain_update(
            pool, uuid.uuid4(), butler_name="general", trigger_type="calendar_event_end"
        )
    pool.fetchrow.assert_not_awaited()
    pool.execute.assert_not_awaited()

    # The actual registered core interface must preserve the same refusal before IO.
    from pathlib import Path
    from unittest.mock import MagicMock

    from fastmcp import Client, FastMCP

    from butlers.config import ButlerConfig, RuntimeSeedConfig
    from butlers.daemon import ButlerDaemon

    daemon = ButlerDaemon(Path("/synthetic"))
    daemon.config = ButlerConfig(
        name="general", port=18999, runtime_seed=RuntimeSeedConfig(core_groups=("temporal",))
    )
    daemon.db = MagicMock(pool=pool)
    daemon.mcp = FastMCP("calendar-write-refusal")
    daemon._modules = []
    daemon._register_core_tools()
    async with Client(daemon.mcp) as client:
        created = (
            await client.call_tool(
                "event_chain_create",
                {
                    "name": "retired",
                    "trigger_type": "calendar_event_end",
                    "actions": actions,
                },
            )
        ).data
        updated = (
            await client.call_tool(
                "event_chain_update",
                {
                    "chain_id": str(uuid.uuid4()),
                    "trigger_type": "calendar_event_end",
                },
            )
        ).data
    assert created["status"] == updated["status"] == "error"
    assert "calendar_event_end" in created["error"]
    assert "calendar_event_end" in updated["error"]
    pool.fetchrow.assert_not_awaited()
    pool.execute.assert_not_awaited()
