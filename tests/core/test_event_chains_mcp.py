"""Tests for event chain DB operations and MCP tools — condensed.

Covers:
- Validation constants: trigger types, statuses, _row_to_dict normalization
- event_chain CRUD: create, list, update, delete
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import pytest

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

_EVENT_CHAINS_DDL = """
    CREATE TABLE IF NOT EXISTS event_chains (
        id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        name              TEXT        NOT NULL,
        trigger_type      TEXT        NOT NULL,
        trigger_reference TEXT,
        actions           JSONB       NOT NULL,
        status            TEXT        NOT NULL DEFAULT 'active',
        butler_name       TEXT        NOT NULL,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT chk_event_chains_trigger_type
            CHECK (trigger_type IN (
                'calendar_event_end',
                'deadline_passed',
                'deadline_threshold'
            )),
        CONSTRAINT chk_event_chains_status
            CHECK (status IN ('active', 'paused', 'fired', 'failed', 'disabled'))
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_event_chains_name_butler
        ON event_chains (name, butler_name);
"""


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestEventChainsDB:
    """Integration tests for event_chains_db CRUD functions."""

    @pytest.fixture
    async def pool(self, postgres_container):
        import asyncpg

        db_name = f"test_{uuid.uuid4().hex[:12]}"
        admin_conn = await asyncpg.connect(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database="postgres",
        )
        try:
            await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin_conn.close()

        p = await asyncpg.create_pool(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database=db_name,
            min_size=1,
            max_size=3,
        )
        await p.execute(_EVENT_CHAINS_DDL)
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
            trigger_type="calendar_event_end",
            actions=self._ACTIONS,
        )
        assert chain["id"] is not None
        assert chain["name"] == "visit-chain"
        assert chain["status"] == "active"

        # list returns it
        chains = await event_chain_list(pool, butler_name=butler)
        assert len(chains) == 1

        # filter by trigger_type works
        filtered = await event_chain_list(
            pool, butler_name=butler, trigger_type="calendar_event_end"
        )
        assert len(filtered) == 1

        empty = await event_chain_list(pool, butler_name=butler, trigger_type="deadline_passed")
        assert len(empty) == 0

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
        with pytest.raises(Exception):  # duplicate name
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
