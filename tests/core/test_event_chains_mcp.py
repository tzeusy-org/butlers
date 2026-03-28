"""Tests for event chain DB operations and MCP tools.

Covers tasks §5 from openspec/changes/temporal-intelligence/tasks.md:
  §5.1  event_chain_create MCP tool with duplicate name checking
  §5.2  event_chain_update MCP tool (status reset on action change)
  §5.3  event_chain_list MCP tool with trigger_type filter
  §5.4  event_chain_delete MCP tool

Unit tests (no DB) cover validation and helper logic. Integration tests
(Docker/asyncpg) cover the DB CRUD functions.

Tasks 4.1–4.6 (validate_chain_actions, materialize_chain_actions,
should_fire_chain) are already covered by tests/core/test_temporal_intelligence.py.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime

import pytest

pytestmark = [pytest.mark.unit]

docker_available = shutil.which("docker") is not None


# ---------------------------------------------------------------------------
# §5.1 / §4.1 — event_chains_db validation (pure unit tests, no DB)
# ---------------------------------------------------------------------------


class TestEventChainCreateValidation:
    """Unit tests for event_chain_create input validation."""

    def test_invalid_trigger_type_raises(self):
        """Unknown trigger_type raises ValueError during validation."""
        from butlers.core.temporal.event_chains_db import _VALID_TRIGGER_TYPES

        assert "calendar_event_end" in _VALID_TRIGGER_TYPES
        assert "deadline_passed" in _VALID_TRIGGER_TYPES
        assert "deadline_threshold" in _VALID_TRIGGER_TYPES
        assert "timer_elapsed" not in _VALID_TRIGGER_TYPES

    def test_valid_trigger_types_are_complete(self):
        """There are exactly 3 valid trigger types."""
        from butlers.core.temporal.event_chains_db import _VALID_TRIGGER_TYPES

        assert len(_VALID_TRIGGER_TYPES) == 3

    def test_row_to_dict_normalises_uuid(self):
        """_row_to_dict converts UUID id field to string."""
        from butlers.core.temporal.event_chains_db import _row_to_dict

        row_id = uuid.uuid4()
        result = _row_to_dict(
            {  # type: ignore[arg-type]
                "id": row_id,
                "name": "test-chain",
                "trigger_type": "calendar_event_end",
                "trigger_reference": None,
                "actions": '[{"action_type": "prompt", "delay_minutes": 0, "prompt": "Hi"}]',
                "status": "active",
                "butler_name": "general",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )
        assert result["id"] == str(row_id)
        # JSON string actions should be decoded to a list
        assert isinstance(result["actions"], list)
        assert result["actions"][0]["action_type"] == "prompt"

    def test_row_to_dict_normalises_jsonb_string_actions(self):
        """_row_to_dict decodes actions JSONB string to list."""
        import json

        from butlers.core.temporal.event_chains_db import _row_to_dict

        actions = [{"action_type": "job", "delay_minutes": 60, "job_name": "archive"}]
        result = _row_to_dict(
            {  # type: ignore[arg-type]
                "id": uuid.uuid4(),
                "name": "chain",
                "trigger_type": "deadline_passed",
                "trigger_reference": "task-id-123",
                "actions": json.dumps(actions),
                "status": "active",
                "butler_name": "general",
                "created_at": None,
                "updated_at": None,
            }
        )
        assert isinstance(result["actions"], list)
        assert result["actions"][0]["job_name"] == "archive"

    def test_row_to_dict_preserves_list_actions(self):
        """_row_to_dict keeps actions already parsed as a list."""
        from butlers.core.temporal.event_chains_db import _row_to_dict

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Hi"}]
        result = _row_to_dict(
            {  # type: ignore[arg-type]
                "id": uuid.uuid4(),
                "name": "chain",
                "trigger_type": "deadline_threshold",
                "trigger_reference": None,
                "actions": actions,
                "status": "fired",
                "butler_name": "general",
                "created_at": None,
                "updated_at": None,
            }
        )
        assert result["actions"] is actions

    def test_row_to_dict_normalises_datetime_timestamps(self):
        """_row_to_dict converts datetime objects to ISO strings."""
        from butlers.core.temporal.event_chains_db import _row_to_dict

        now = datetime(2026, 3, 28, 12, 0, 0, tzinfo=UTC)
        result = _row_to_dict(
            {  # type: ignore[arg-type]
                "id": uuid.uuid4(),
                "name": "chain",
                "trigger_type": "calendar_event_end",
                "trigger_reference": None,
                "actions": [],
                "status": "active",
                "butler_name": "general",
                "created_at": now,
                "updated_at": now,
            }
        )
        assert isinstance(result["created_at"], str)
        assert "2026-03-28" in result["created_at"]


class TestEventChainUpdateValidation:
    """Unit tests for event_chain_update field validation logic."""

    def test_valid_statuses_set(self):
        """_VALID_STATUSES contains exactly the spec-mandated values plus disabled."""
        from butlers.core.temporal.event_chains_db import _VALID_STATUSES

        # Spec defines: active | paused | fired | failed
        # disabled is retained for backward compatibility
        assert _VALID_STATUSES == frozenset({"active", "paused", "fired", "failed", "disabled"})

    def test_invalid_status_not_in_valid_set(self):
        """Arbitrary statuses are not valid."""
        from butlers.core.temporal.event_chains_db import _VALID_STATUSES

        assert "pending" not in _VALID_STATUSES
        assert "completed" not in _VALID_STATUSES

    def test_spec_statuses_in_valid_set(self):
        """All spec-defined statuses (active, paused, fired, failed) are in _VALID_STATUSES."""
        from butlers.core.temporal.event_chains_db import _VALID_STATUSES

        for status in ("active", "paused", "fired", "failed"):
            assert status in _VALID_STATUSES, f"Spec status {status!r} missing from _VALID_STATUSES"


# ---------------------------------------------------------------------------
# §5 — DB-backed integration tests (require Docker/asyncpg)
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
        """Create a fresh test database with event_chains table."""
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

    # §5.1 — event_chain_create
    async def test_create_chain_returns_row(self, pool):
        """event_chain_create inserts a row and returns it."""
        from butlers.core.temporal.event_chains_db import event_chain_create

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Log visit"}]
        chain = await event_chain_create(
            pool,
            name="post-dentist",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name="general",
        )
        assert chain["name"] == "post-dentist"
        assert chain["trigger_type"] == "calendar_event_end"
        assert chain["status"] == "active"
        assert chain["butler_name"] == "general"
        assert chain["trigger_reference"] is None
        assert isinstance(chain["id"], str)
        assert isinstance(chain["actions"], list)

    async def test_create_chain_with_trigger_reference(self, pool):
        """event_chain_create stores trigger_reference when provided."""
        from butlers.core.temporal.event_chains_db import event_chain_create

        actions = [{"action_type": "job", "delay_minutes": 30, "job_name": "archive"}]
        chain = await event_chain_create(
            pool,
            name="post-meeting-archive",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name="general",
            trigger_reference="evt-abc-123",
        )
        assert chain["trigger_reference"] == "evt-abc-123"

    async def test_create_duplicate_name_raises(self, pool):
        """Creating two chains with the same name for the same butler raises ValueError."""
        from butlers.core.temporal.event_chains_db import event_chain_create

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        await event_chain_create(
            pool,
            name="dup-chain",
            trigger_type="deadline_passed",
            actions=actions,
            butler_name="general",
        )
        with pytest.raises(ValueError, match="already exists"):
            await event_chain_create(
                pool,
                name="dup-chain",
                trigger_type="deadline_passed",
                actions=actions,
                butler_name="general",
            )

    async def test_create_same_name_different_butler_ok(self, pool):
        """Same chain name is allowed for different butlers."""
        from butlers.core.temporal.event_chains_db import event_chain_create

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        chain1 = await event_chain_create(
            pool,
            name="shared-name",
            trigger_type="deadline_passed",
            actions=actions,
            butler_name="general",
        )
        chain2 = await event_chain_create(
            pool,
            name="shared-name",
            trigger_type="deadline_passed",
            actions=actions,
            butler_name="finance",
        )
        assert chain1["id"] != chain2["id"]

    async def test_create_invalid_trigger_type_raises(self, pool):
        """Invalid trigger_type raises ValueError before hitting the DB."""
        from butlers.core.temporal.event_chains_db import event_chain_create

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        with pytest.raises(ValueError, match="trigger_type"):
            await event_chain_create(
                pool,
                name="bad-trigger",
                trigger_type="webhook_called",
                actions=actions,
                butler_name="general",
            )

    async def test_create_empty_actions_raises(self, pool):
        """Empty actions list raises ValueError (via validate_chain_actions)."""
        from butlers.core.temporal.event_chains_db import event_chain_create

        with pytest.raises(ValueError, match="action"):
            await event_chain_create(
                pool,
                name="no-actions",
                trigger_type="calendar_event_end",
                actions=[],
                butler_name="general",
            )

    async def test_create_empty_name_raises(self, pool):
        """Empty name raises ValueError."""
        from butlers.core.temporal.event_chains_db import event_chain_create

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        with pytest.raises(ValueError, match="name"):
            await event_chain_create(
                pool,
                name="",
                trigger_type="calendar_event_end",
                actions=actions,
                butler_name="general",
            )

    # §5.3 — event_chain_list
    async def test_list_returns_all_chains_for_butler(self, pool):
        """event_chain_list returns all chains owned by butler."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_list

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"list-test-butler-{uuid.uuid4().hex[:8]}"
        for i in range(3):
            await event_chain_create(
                pool,
                name=f"list-chain-{i}",
                trigger_type="calendar_event_end",
                actions=actions,
                butler_name=butler,
            )
        chains = await event_chain_list(pool, butler)
        assert len(chains) == 3

    async def test_list_filters_by_trigger_type(self, pool):
        """event_chain_list with trigger_type filter returns only matching chains."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_list

        butler = f"filter-test-butler-{uuid.uuid4().hex[:8]}"
        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        await event_chain_create(
            pool,
            name="chain-cal-end",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        await event_chain_create(
            pool,
            name="chain-deadline-passed",
            trigger_type="deadline_passed",
            actions=actions,
            butler_name=butler,
        )
        cal_chains = await event_chain_list(pool, butler, trigger_type="calendar_event_end")
        assert len(cal_chains) == 1
        assert cal_chains[0]["trigger_type"] == "calendar_event_end"

        dl_chains = await event_chain_list(pool, butler, trigger_type="deadline_passed")
        assert len(dl_chains) == 1
        assert dl_chains[0]["trigger_type"] == "deadline_passed"

    async def test_list_invalid_trigger_type_raises(self, pool):
        """event_chain_list with invalid trigger_type raises ValueError."""
        from butlers.core.temporal.event_chains_db import event_chain_list

        with pytest.raises(ValueError, match="trigger_type"):
            await event_chain_list(pool, "general", trigger_type="bad_type")

    async def test_list_filters_by_status(self, pool):
        """event_chain_list with status filter returns only matching chains."""
        from butlers.core.temporal.event_chains_db import (
            event_chain_create,
            event_chain_list,
            event_chain_update,
        )

        butler = f"status-filter-{uuid.uuid4().hex[:8]}"
        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        chain = await event_chain_create(
            pool,
            name="chain-to-disable",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        await event_chain_create(
            pool,
            name="chain-active",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        # Disable the first chain
        await event_chain_update(pool, chain["id"], butler_name=butler, status="disabled")

        active = await event_chain_list(pool, butler, status="active")
        disabled = await event_chain_list(pool, butler, status="disabled")
        assert len(active) == 1
        assert active[0]["name"] == "chain-active"
        assert len(disabled) == 1
        assert disabled[0]["name"] == "chain-to-disable"

    async def test_list_returns_empty_for_unknown_butler(self, pool):
        """event_chain_list returns empty list for butler with no chains."""
        from butlers.core.temporal.event_chains_db import event_chain_list

        chains = await event_chain_list(pool, "nonexistent-butler")
        assert chains == []

    async def test_update_status_to_paused(self, pool):
        """event_chain_update accepts status='paused' per spec."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_update

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"paused-test-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="pausable-chain",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        updated = await event_chain_update(pool, chain["id"], butler_name=butler, status="paused")
        assert updated["status"] == "paused"

    async def test_update_status_to_failed(self, pool):
        """event_chain_update accepts status='failed' per spec."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_update

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"failed-test-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="failable-chain",
            trigger_type="deadline_passed",
            actions=actions,
            butler_name=butler,
        )
        updated = await event_chain_update(pool, chain["id"], butler_name=butler, status="failed")
        assert updated["status"] == "failed"

    async def test_paused_chain_can_be_resumed(self, pool):
        """A paused chain can be resumed by setting status='active'."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_update

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"resume-test-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="pause-resume-chain",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        paused = await event_chain_update(pool, chain["id"], butler_name=butler, status="paused")
        assert paused["status"] == "paused"

        resumed = await event_chain_update(pool, chain["id"], butler_name=butler, status="active")
        assert resumed["status"] == "active"

    async def test_list_filters_by_paused_status(self, pool):
        """event_chain_list with status='paused' returns only paused chains."""
        from butlers.core.temporal.event_chains_db import (
            event_chain_create,
            event_chain_list,
            event_chain_update,
        )

        butler = f"paused-filter-{uuid.uuid4().hex[:8]}"
        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        chain = await event_chain_create(
            pool,
            name="chain-to-pause",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        await event_chain_create(
            pool,
            name="chain-active-2",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        await event_chain_update(pool, chain["id"], butler_name=butler, status="paused")

        paused = await event_chain_list(pool, butler, status="paused")
        active = await event_chain_list(pool, butler, status="active")
        assert len(paused) == 1
        assert paused[0]["name"] == "chain-to-pause"
        assert len(active) == 1
        assert active[0]["name"] == "chain-active-2"

    # §5.2 — event_chain_update
    async def test_update_name(self, pool):
        """event_chain_update can rename a chain."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_update

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"update-test-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="old-name",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        updated = await event_chain_update(pool, chain["id"], butler_name=butler, name="new-name")
        assert updated["name"] == "new-name"
        assert updated["id"] == chain["id"]

    async def test_update_actions_resets_status_to_active(self, pool):
        """Updating actions on a fired chain resets status to 'active'."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_update

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Original"}]
        butler = f"status-reset-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="fired-chain",
            trigger_type="deadline_passed",
            actions=actions,
            butler_name=butler,
        )
        # Mark as fired
        await event_chain_update(pool, chain["id"], butler_name=butler, status="fired")
        # Update actions — status should reset to active
        new_actions = [{"action_type": "prompt", "delay_minutes": 5, "prompt": "Updated"}]
        updated = await event_chain_update(
            pool, chain["id"], butler_name=butler, actions=new_actions
        )
        assert updated["status"] == "active"
        assert updated["actions"][0]["prompt"] == "Updated"

    async def test_update_actions_with_explicit_status_keeps_status(self, pool):
        """Explicit status override takes precedence when updating actions."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_update

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Original"}]
        butler = f"explicit-status-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="explicit-chain",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        new_actions = [{"action_type": "prompt", "delay_minutes": 5, "prompt": "Updated"}]
        updated = await event_chain_update(
            pool,
            chain["id"],
            butler_name=butler,
            actions=new_actions,
            status="disabled",
        )
        # status='disabled' takes precedence over the actions-reset-to-active rule
        assert updated["status"] == "disabled"

    async def test_update_not_found_raises(self, pool):
        """Updating a non-existent chain raises ValueError."""
        from butlers.core.temporal.event_chains_db import event_chain_update

        fake_id = str(uuid.uuid4())
        with pytest.raises(ValueError, match="not found"):
            await event_chain_update(pool, fake_id, butler_name="general", name="new-name")

    async def test_update_invalid_trigger_type_raises(self, pool):
        """Updating to an invalid trigger_type raises ValueError."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_update

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"bad-trigger-update-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="valid-chain",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        with pytest.raises(ValueError, match="trigger_type"):
            await event_chain_update(
                pool, chain["id"], butler_name=butler, trigger_type="invalid_type"
            )

    async def test_update_invalid_status_raises(self, pool):
        """Updating to an invalid status raises ValueError."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_update

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"bad-status-update-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="valid-chain-status",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        with pytest.raises(ValueError, match="status"):
            await event_chain_update(pool, chain["id"], butler_name=butler, status="completed")

    # §5.4 — event_chain_delete
    async def test_delete_existing_chain_returns_true(self, pool):
        """event_chain_delete returns True when chain is deleted."""
        from butlers.core.temporal.event_chains_db import event_chain_create, event_chain_delete

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"delete-test-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="to-delete",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler,
        )
        result = await event_chain_delete(pool, chain["id"], butler_name=butler)
        assert result is True

    async def test_delete_removes_row_from_db(self, pool):
        """After deletion, the chain is no longer in the DB."""
        from butlers.core.temporal.event_chains_db import (
            event_chain_create,
            event_chain_delete,
            event_chain_list,
        )

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"delete-verify-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="verify-delete",
            trigger_type="deadline_passed",
            actions=actions,
            butler_name=butler,
        )
        await event_chain_delete(pool, chain["id"], butler_name=butler)
        remaining = await event_chain_list(pool, butler)
        assert all(c["id"] != chain["id"] for c in remaining)

    async def test_delete_nonexistent_chain_returns_false(self, pool):
        """event_chain_delete returns False when chain is not found."""
        from butlers.core.temporal.event_chains_db import event_chain_delete

        fake_id = str(uuid.uuid4())
        result = await event_chain_delete(pool, fake_id, butler_name="general")
        assert result is False

    async def test_delete_does_not_affect_other_butlers_chains(self, pool):
        """Deleting a chain does not remove chains owned by other butlers."""
        from butlers.core.temporal.event_chains_db import (
            event_chain_create,
            event_chain_delete,
            event_chain_list,
        )

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler_a = f"owner-a-{uuid.uuid4().hex[:8]}"
        butler_b = f"owner-b-{uuid.uuid4().hex[:8]}"
        chain_a = await event_chain_create(
            pool,
            name="shared-name-del",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler_a,
        )
        await event_chain_create(
            pool,
            name="shared-name-del",
            trigger_type="calendar_event_end",
            actions=actions,
            butler_name=butler_b,
        )
        # Delete butler_a's chain using butler_b's ownership → should not find it
        deleted = await event_chain_delete(pool, chain_a["id"], butler_name=butler_b)
        assert deleted is False

        # butler_a's chain should still exist
        chains_a = await event_chain_list(pool, butler_a)
        assert len(chains_a) == 1

    # §5.2 / §5.3 — get_chain_by_id
    async def test_get_chain_by_id_returns_row(self, pool):
        """get_chain_by_id returns the correct chain."""
        from butlers.core.temporal.event_chains_db import event_chain_create, get_chain_by_id

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"get-test-{uuid.uuid4().hex[:8]}"
        chain = await event_chain_create(
            pool,
            name="get-me",
            trigger_type="deadline_threshold",
            actions=actions,
            butler_name=butler,
        )
        fetched = await get_chain_by_id(pool, chain["id"], butler_name=butler)
        assert fetched is not None
        assert fetched["id"] == chain["id"]
        assert fetched["name"] == "get-me"

    async def test_get_chain_by_id_returns_none_when_not_found(self, pool):
        """get_chain_by_id returns None for unknown IDs."""
        from butlers.core.temporal.event_chains_db import get_chain_by_id

        result = await get_chain_by_id(pool, str(uuid.uuid4()), butler_name="general")
        assert result is None

    async def test_all_three_trigger_types_can_be_created(self, pool):
        """All three valid trigger types can be successfully created."""
        from butlers.core.temporal.event_chains_db import event_chain_create

        actions = [{"action_type": "prompt", "delay_minutes": 0, "prompt": "Do it"}]
        butler = f"all-triggers-{uuid.uuid4().hex[:8]}"
        for tt in ("calendar_event_end", "deadline_passed", "deadline_threshold"):
            chain = await event_chain_create(
                pool,
                name=f"chain-{tt}",
                trigger_type=tt,
                actions=actions,
                butler_name=butler,
            )
            assert chain["trigger_type"] == tt
