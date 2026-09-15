"""Real-Postgres coverage that daemon startup activates runtime-attention delivery.

REQ-runtime-attention-outbox-002; REQ-model-catalog-001;
REQ-dashboard-spend-dashboard-001.

``test_runtime_attention_delivery_worker.py`` proves ``RuntimeAttentionDeliveryWorker``
delivers correctly once directly constructed and driven. It never proves anyone
constructs or drives it: before bu-wng0z, ``SwitchboardModule.on_startup`` never
built the worker, so a breaker-open or fleet-halt episode a producer wrote sat
in the outbox forever with nothing polling it. This file drives
``SwitchboardModule.on_startup`` exactly as the daemon does and proves a
producer-written episode reaches ``sent`` with no direct call to the worker.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.modules.registry import default_registry

pytestmark = [pytest.mark.db, pytest.mark.integration]

# Roster modules are installed dynamically by ModuleRegistry; load that
# registry before importing the switchboard module's generated alias (see
# tests/daemon/test_non_messenger_approval_command_contracts.py).
default_registry()
from butlers.modules._roster_switchboard import (  # noqa: E402
    SwitchboardModule,
    SwitchboardModuleConfig,
)


async def _seed_pending_breaker_episode(pool: asyncpg.Pool, alias: str) -> uuid.UUID:
    """Create one pending outbox episode through the real breaker producer.

    Mirrors ``test_runtime_attention_delivery_worker.py``'s helper: the outbox
    has no INSERT grant outside its producers, so this drives an actual
    breaker-open edge rather than fabricating a row.
    """
    catalog_entry_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.model_catalog (id, alias, runtime_type, model_id)
        VALUES ($1, $2, 'codex', $3)
        """,
        catalog_entry_id,
        alias,
        f"{alias}-model",
    )
    ts = datetime.now(UTC)
    attempt_id: int | None = None
    for _ in range(5):
        attempt_id = await pool.fetchval(
            """
            INSERT INTO public.model_dispatch_attempts (catalog_entry_id, butler, outcome, ts)
            VALUES ($1, 'general', 'runtime_failure', $2)
            RETURNING id
            """,
            catalog_entry_id,
            ts,
        )

    async with pool.acquire() as connection:
        await connection.execute('SET ROLE "butler_general_rw"')
        try:
            episode_id = await connection.fetchval(
                "SELECT public.append_runtime_attention_model_breaker($1)", attempt_id
            )
        finally:
            await connection.execute("RESET ROLE")
    assert episode_id is not None, "producer must be enabled in the migrated fixture"
    return episode_id


async def _seed_owner_telegram_chat_id(pool: asyncpg.Pool, chat_id: str) -> None:
    """Seed a real owner entity with a deliverable Telegram chat id.

    Exercises the same ``resolve_owner_telegram_recipient`` path the wired
    worker uses in production, rather than mocking recipient resolution.
    """
    owner_id = await pool.fetchval(
        """
        INSERT INTO public.entities (canonical_name, entity_type, roles)
        VALUES ('Owner', 'person', ARRAY['owner'])
        RETURNING id
        """
    )
    await pool.execute(
        """
        INSERT INTO public.entity_info (entity_id, type, value, is_primary)
        VALUES ($1, 'telegram_chat_id', $2, true)
        """,
        owner_id,
        chat_id,
    )


async def _lifecycle_state(pool: asyncpg.Pool, episode_id: uuid.UUID) -> str:
    async with pool.acquire() as connection:
        await connection.execute('SET ROLE "butler_switchboard_rw"')
        try:
            return await connection.fetchval(
                "SELECT lifecycle_state FROM public.runtime_attention_outbox WHERE id = $1",
                episode_id,
            )
        finally:
            await connection.execute("RESET ROLE")


_TERMINAL_LIFECYCLE_STATES = frozenset({"sent", "failed", "uncertain"})


async def _wait_until_terminal(
    pool: asyncpg.Pool, episode_id: uuid.UUID, *, timeout: float = 5.0
) -> str:
    """Poll for the worker's own progress instead of sleeping a fixed guess."""
    deadline = asyncio.get_event_loop().time() + timeout
    state = await _lifecycle_state(pool, episode_id)
    while state not in _TERMINAL_LIFECYCLE_STATES and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        state = await _lifecycle_state(pool, episode_id)
    return state


async def test_daemon_startup_delivers_a_producer_written_episode_end_to_end(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        episode_id = await _seed_pending_breaker_episode(pool, "daemon-wiring")
        await _seed_owner_telegram_chat_id(pool, "123456789")

        deliver_mock = AsyncMock(return_value={"status": "sent"})
        module = SwitchboardModule()
        with patch("butlers.tools.switchboard.runtime_attention.worker.deliver", deliver_mock):
            # Exactly what the daemon calls at step 9 of ButlerDaemon.start():
            # no test-only construction of the worker, repository, or transport.
            await module.on_startup(SwitchboardModuleConfig(), SimpleNamespace(pool=pool))
            try:
                assert module._runtime_attention_task is not None
                state = await _wait_until_terminal(pool, episode_id)
            finally:
                await module.on_shutdown()

        assert state == "sent", (
            "daemon-startup wiring must deliver a producer-written episode "
            "without any direct call to the worker"
        )
        assert deliver_mock.await_count == 1
        _, kwargs = deliver_mock.await_args
        notify_request = kwargs["notify_request"]
        assert notify_request["delivery"]["channel"] == "telegram"
        assert notify_request["delivery"]["recipient"] == "123456789"
        assert "model_breaker" in notify_request["delivery"]["message"]


async def test_on_shutdown_stops_the_worker_and_further_episodes_are_not_polled(
    migrated_core_postgres_pool,
) -> None:
    async with migrated_core_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        await _seed_owner_telegram_chat_id(pool, "987654321")
        deliver_mock = AsyncMock(return_value={"status": "sent"})
        module = SwitchboardModule()
        with patch("butlers.tools.switchboard.runtime_attention.worker.deliver", deliver_mock):
            await module.on_startup(SwitchboardModuleConfig(), SimpleNamespace(pool=pool))
            task = module._runtime_attention_task
            assert task is not None and not task.done()

            await module.on_shutdown()

            assert task.done()
            assert module._runtime_attention_task is None

            episode_id = await _seed_pending_breaker_episode(pool, "post-shutdown")
            await asyncio.sleep(0.2)
            assert await _lifecycle_state(pool, episode_id) == "pending"
