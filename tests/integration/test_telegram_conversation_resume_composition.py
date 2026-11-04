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
from butlers.core import conversation_sessions
from butlers.core.model_routing import QuotaStatus
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner
from butlers.core.utils import generate_uuid7_string
from butlers.daemon import ButlerDaemon
from butlers.tools.switchboard.contracts import IngestEnvelopeV1
from butlers.tools.switchboard.ingestion.ingest import _build_request_context

pytestmark = pytest.mark.integration

# Daemon harness this composition boots on. These lived in
# tests/daemon/test_route_execute_conversation_anchor.py until that file moved onto
# booted_daemon (bu-rvgzmf.2); moving this test onto the kit is bu-rvgzmf.11.


class _NoopLeaseHeartbeat:
    async def __aenter__(self) -> asyncio.Event:
        return asyncio.Event()

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _ResumeRecordingAdapter(RuntimeAdapter):
    supports_resume = True

    def __init__(self) -> None:
        self.invoke_calls: list[str | None] = []
        self._last_process_info: dict[str, Any] | None = None
        self.two_invocations = asyncio.Event()

    @property
    def binary_name(self) -> str:
        return "mock"

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        return self._last_process_info

    async def invoke(self, *_args: Any, **kwargs: Any) -> tuple[str, list, None]:
        self.invoke_calls.append(kwargs.get("resume_session_id"))
        if len(self.invoke_calls) == 2:
            self.two_invocations.set()
        self._last_process_info = {
            "runtime_type": DEFAULT_RUNTIME_TYPE,
            "provider_session_id": "provider-session-from-first-turn",
        }
        return "ok", [], None

    async def reset(self) -> None:
        return None

    def build_config_file(self, _mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        config_file = tmp_dir / "runtime.json"
        config_file.write_text("{}")
        return config_file

    def parse_system_prompt_file(self, _config_dir: Path) -> str:
        return ""


def _make_butler_toml(tmp_path: Path, *, butler_name: str = "health", port: int = 9200) -> Path:
    toml_lines = [
        "[butler]",
        f'name = "{butler_name}"',
        f"port = {port}",
        'description = "A test butler"',
        "",
        "[butler.db]",
        'name = "butlers"',
        f'schema = "{butler_name}"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra(butler_name: str = "health"):
    mock_pool = AsyncMock()
    mock_pool.fetchval.return_value = None
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    transaction_depth = 0

    async def enter_transaction():
        nonlocal transaction_depth
        transaction_depth += 1

    async def exit_transaction(*_exc):
        nonlocal transaction_depth
        transaction_depth -= 1
        return False

    mock_conn.is_in_transaction = MagicMock(side_effect=lambda: transaction_depth > 0)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(side_effect=enter_transaction)
    transaction.__aexit__ = AsyncMock(side_effect=exit_transaction)
    mock_conn.transaction = MagicMock(return_value=transaction)
    acquisition = MagicMock()
    acquisition.__aenter__ = AsyncMock(return_value=mock_conn)
    acquisition.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=acquisition)

    async def runtime_row(query, *args):
        if query.lstrip().startswith("SELECT *") and "runtime_config" in query:
            return {
                "butler_name": butler_name,
                "core_groups": None,
                "core_groups_narrowing_reason": None,
                "catalog_read_sensitivity": "normal",
                "max_concurrent": 3,
                "max_queued": 10,
                "tool_exposure_policy": "eager_filtered",
                "blind_spot_preamble_enabled": True,
                "seeded_at": None,
                "updated_at": None,
            }
        return None

    mock_pool.fetchrow.side_effect = runtime_row
    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butlers"

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_builder = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.lifecycle.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "build_runtime_adapter": patch(
            "butlers.lifecycle.build_runtime_adapter", new=mock_adapter_builder
        ),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


async def _start_daemon_with_route_execute(butler_dir: Path, patches: dict):
    route_execute_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            nonlocal route_execute_fn
            resolved_name = declared_name or fn.__name__
            if resolved_name == "route.execute":
                route_execute_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
        patch("butlers.lifecycle.Spawner", return_value=patches["mock_spawner"]),
        patches["build_runtime_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, route_execute_fn


def _renew_processing_claim_patch():
    """Keep route tests focused on routing rather than the DB lease primitive."""
    return patch(
        "butlers.core.route_inbox.route_inbox_renew_processing_claim",
        new_callable=AsyncMock,
        return_value=True,
    )


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
            return await conversation_sessions.conversation_get_provider_session(
                db_pool, *args, **kwargs
            )

        async def set_provider_session(_pool: Any, *args: Any, **kwargs: Any):
            return await conversation_sessions.conversation_set_provider_session(
                db_pool, *args, **kwargs
            )

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
