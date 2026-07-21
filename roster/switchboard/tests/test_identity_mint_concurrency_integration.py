"""Concurrent pipeline proof for unknown-sender entity minting."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.routing_context import _routing_ctx_var
from butlers.modules.pipeline import MessagePipeline

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@dataclass
class _SpawnResult:
    """Small classifier result carrying a successful route tool call."""

    output: str = "Routed to general."
    tool_calls: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {
                "name": "route_to_butler",
                "args": {"butler": "general", "prompt": "Handle this."},
                "result": {"status": "ok", "butler": "general"},
            }
        ]
    )


async def _create_identity_test_schema(pool: Any) -> None:
    """Provision only the tables exercised before pipeline activation."""
    await pool.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    await pool.execute("CREATE SCHEMA IF NOT EXISTS switchboard")
    await pool.execute("CREATE SCHEMA IF NOT EXISTS relationship")
    await pool.execute(
        """
        CREATE TABLE switchboard.state (
            key TEXT PRIMARY KEY,
            value JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            version INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    await pool.execute(
        """
        CREATE TABLE public.entities (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            aliases TEXT[] NOT NULL DEFAULT '{}',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            roles TEXT[] NOT NULL DEFAULT '{}'
        )
        """
    )
    await pool.execute(
        """
        CREATE TABLE relationship.entity_facts (
            subject UUID NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            object_kind TEXT NOT NULL DEFAULT 'literal',
            validity TEXT NOT NULL DEFAULT 'active'
        )
        """
    )


async def test_concurrent_first_messages_share_reserved_entity_and_pipeline_context(
    provisioned_postgres_pool,
) -> None:
    """Two first messages cannot mint different entities before fact assertion runs.

    The initial reverse lookups are synchronized to force the formerly racy
    window. The pipeline fact hook is deliberately inert here: the proof is
    that the Switchboard reservation supplies one identity context *before*
    the relationship-owned fact writer has a chance to establish its normal
    deduplication key.
    """
    async with provisioned_postgres_pool(schema="switchboard", max_pool_size=4) as pool:
        await _create_identity_test_schema(pool)

        initial_lookup_barrier = asyncio.Barrier(2)
        captured_contexts: list[dict[str, Any]] = []

        async def concurrent_initial_miss(*_args: Any, **_kwargs: Any) -> None:
            await initial_lookup_barrier.wait()
            return None

        async def dispatch(**_kwargs: Any) -> _SpawnResult:
            routing_context = _routing_ctx_var.get()
            assert routing_context is not None
            captured_contexts.append(dict(routing_context))
            return _SpawnResult()

        pipeline = MessagePipeline(
            switchboard_pool=pool,
            dispatch_fn=dispatch,
            source_butler="switchboard",
            enable_identity_resolution=True,
        )

        with (
            patch(
                "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
                new=concurrent_initial_miss,
            ),
            patch(
                "butlers.tools.switchboard.routing.classify._load_available_butlers",
                new=AsyncMock(return_value=[{"name": "general", "description": "General"}]),
            ),
            patch.object(
                MessagePipeline,
                "_assert_sender_channel_fact",
                new=AsyncMock(),
            ),
        ):
            first, second = await asyncio.wait_for(
                asyncio.gather(
                    pipeline.process(
                        "First concurrent message",
                        tool_args={
                            "source_channel": "telegram",
                            "source_id": "same-sender-42",
                            "sender_name": "First label",
                        },
                    ),
                    pipeline.process(
                        "Second concurrent message",
                        tool_args={
                            "source_channel": "telegram",
                            "source_id": "same-sender-42",
                            "sender_name": "Second label",
                        },
                    ),
                ),
                timeout=10,
            )

        assert first.routed_targets == ["general"]
        assert second.routed_targets == ["general"]
        assert len(captured_contexts) == 2

        entity_ids = {context["source_entity_id"] for context in captured_contexts}
        assert len(entity_ids) == 1
        entity_id = entity_ids.pop()
        assert entity_id is not None
        assert all(
            context["identity_preamble"]
            == f"[Source: Unknown sender (entity_id: {entity_id}), via telegram "
            "-- pending disambiguation]"
            for context in captured_contexts
        )

        minted_count = await pool.fetchval(
            """
            SELECT count(*)
            FROM public.entities
            WHERE metadata ->> 'source_channel' = 'telegram'
              AND metadata ->> 'source_value' = $1
            """,
            "same-sender-42",
        )
        assert minted_count == 1

        reservation = await pool.fetchrow(
            "SELECT value FROM switchboard.state WHERE key = $1",
            "identity:unknown_entity:telegram:same-sender-42",
        )
        assert reservation is not None
        assert reservation["value"] == {"entity_id": entity_id}

        # The test disabled the relationship-owned hook, proving the one entity
        # came from the Switchboard reservation rather than an ingress fact write.
        assert await pool.fetchval("SELECT count(*) FROM relationship.entity_facts") == 0
