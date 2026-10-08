"""Two Telegram messages in one chat resume one provider session (bu-7exe4.2).

Composes the production seams end to end: the real Telegram bot connector
normalizes two updates, the real Switchboard request-context builder derives
the route context, ``route.execute`` resolves the anchor through the real
helper against a migrated core schema, and the real Spawner reads and writes
the provider handle in that same database. Only process, model, and session-log
infrastructure is stubbed.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.api import conversations
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.core.model_routing import QuotaStatus
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.spawner import Spawner
from butlers.core.utils import generate_uuid7_string
from butlers.tools.switchboard.ingestion.ingest import _build_request_context
from butlers.tools.switchboard.routing.contracts import IngestEnvelopeV1
from tests.daemon.test_route_execute_conversation_anchor import (
    _make_butler_toml,
    _NoopLeaseHeartbeat,
    _patch_infra,
    _renew_processing_claim_patch,
    _ResumeRecordingAdapter,
    _start_daemon_with_route_execute,
)

pytestmark = pytest.mark.integration


def _update(update_id: int, message_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "from": {"id": 4242},
            "chat": {"id": -100777, "type": "supergroup"},
            "text": text,
        },
    }


async def _route_context(connector: TelegramBotConnector, update: dict[str, Any]) -> dict:
    envelope = await connector._normalize_to_ingest_v1(update)
    assert envelope is not None
    context = _build_request_context(
        IngestEnvelopeV1.model_validate(envelope),
        request_id=uuid.UUID(generate_uuid7_string()),
        received_at=datetime.now(UTC),
    )
    # route_to_butler forwards these ingest fields and stamps itself as caller.
    return {
        **{
            key: context[key]
            for key in (
                "request_id",
                "received_at",
                "source_channel",
                "source_sender_identity",
                "source_thread_identity",
                "external_conversation_id",
            )
        },
        "source_endpoint_identity": "switchboard",
    }


async def _handle_persisted(db_pool: Any) -> bool:
    return bool(
        await db_pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM public.dashboard_conversations
                WHERE butler_name = 'health' AND provider_session_id IS NOT NULL
            )
            """
        )
    )


async def test_two_telegram_ingests_share_one_anchor_and_resume(
    migrated_core_postgres_pool, tmp_path: Path
) -> None:
    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            provider="telegram",
            channel="telegram_bot",
            endpoint_identity="telegram:bot:123456789",
            telegram_token="test-token",
        ),
        cursor_pool=MagicMock(),
    )
    contexts = [
        await _route_context(connector, _update(1, 7, "first turn")),
        await _route_context(connector, _update(2, 8, "second turn")),
    ]
    assert contexts[0]["source_thread_identity"] != contexts[1]["source_thread_identity"]

    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    async with migrated_core_postgres_pool() as db_pool:
        # Bound before patching: the patch below replaces this module attribute.
        real_get_or_create = conversations.conversation_get_or_create_by_thread

        async def get_or_create(_pool: Any, **kwargs: Any):
            return await real_get_or_create(db_pool, **kwargs)

        async def get_provider_session(_pool: Any, *args: Any, **kwargs: Any):
            return await conversations.conversation_get_provider_session(db_pool, *args, **kwargs)

        async def set_provider_session(_pool: Any, *args: Any, **kwargs: Any):
            return await conversations.conversation_set_provider_session(db_pool, *args, **kwargs)

        adapter = _ResumeRecordingAdapter()
        spawner = Spawner(
            config=daemon.config,
            config_dir=butler_dir,
            pool=patches["mock_pool"],
            runtime=adapter,
        )
        spawner._ensure_mcp_endpoints_warmed = AsyncMock(return_value=None)
        daemon.spawner.trigger.side_effect = spawner.trigger

        with (
            patch(
                "butlers.core_tools._routing.route_inbox_insert",
                new_callable=AsyncMock,
                side_effect=[uuid.uuid4(), uuid.uuid4()],
            ),
            patch(
                "butlers.core_tools._routing.route_inbox_claim_processing",
                new_callable=AsyncMock,
                side_effect=[uuid.uuid4(), uuid.uuid4()],
            ),
            patch(
                "butlers.core_tools._routing.route_inbox_processing_lease_heartbeat",
                side_effect=lambda *_args, **_kwargs: _NoopLeaseHeartbeat(),
            ),
            _renew_processing_claim_patch(),
            patch("butlers.core_tools._routing.route_inbox_mark_processed", new_callable=AsyncMock),
            patch(
                "butlers.api.conversations.conversation_get_or_create_by_thread",
                side_effect=get_or_create,
            ),
            patch(
                "butlers.core.spawner.session_create",
                new_callable=AsyncMock,
                return_value=uuid.uuid4(),
            ),
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "test-model",
                    [],
                    uuid.uuid4(),
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=QuotaStatus(
                    allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None
                ),
            ),
            patch(
                "butlers.core.spawner.conversation_get_provider_session",
                side_effect=get_provider_session,
            ),
            patch(
                "butlers.core.spawner.conversation_set_provider_session",
                side_effect=set_provider_session,
            ),
        ):
            # The second turn is routed only after the first one has persisted
            # its provider handle, as consecutive chat messages are.
            for turn, (context, prompt) in enumerate(
                zip(contexts, ("first turn", "second turn"), strict=True), start=1
            ):
                await route_execute_fn(
                    schema_version="route.v1",
                    request_context=context,
                    input={"prompt": prompt},
                )
                for _ in range(100):
                    if len(adapter.invoke_calls) >= turn and (
                        turn == 2 or await _handle_persisted(db_pool)
                    ):
                        break
                    await asyncio.sleep(0.05)

            await asyncio.wait_for(adapter.two_invocations.wait(), timeout=5)

        anchors = await db_pool.fetch(
            """
            SELECT id, external_conversation_id, provider_session_id
            FROM public.dashboard_conversations
            WHERE butler_name = 'health' AND source_channel = 'telegram_bot'
            """
        )

    assert [row["external_conversation_id"] for row in anchors] == ["telegram:-100777"]
    assert anchors[0]["provider_session_id"] == "provider-session-from-first-turn"
    assert adapter.invoke_calls == [None, "provider-session-from-first-turn"]
