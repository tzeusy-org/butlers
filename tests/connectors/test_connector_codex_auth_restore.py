"""bu-wzbu9: standalone connector entrypoints must restore the shared codex
CLI-auth token to disk at startup (mirroring the daemon), so discretion-tier
codex calls find ``~/.codex/auth.json`` instead of 401-ing and silently failing
closed (IGNORE) for low-weight senders.

Covers the shared ``restore_connector_cli_auth`` helper (restore + codex
baseline + loud/honest degraded path) and the startup wiring of the three
connectors that build a live DiscretionDispatcher: whatsapp_user_client,
telegram_user_client, and live_listener. telegram_bot (no discretion/codex path)
and home_assistant (evaluator=None, discretion unwired) are intentionally out of
scope — they make no discretion-tier codex call to fail.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.cli_auth.persistence import restore_connector_cli_auth

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# restore_connector_cli_auth helper
# ---------------------------------------------------------------------------


async def test_helper_builds_store_with_pool_and_records_baseline_on_codex() -> None:
    """Builds a CredentialStore around the connector's pool, calls restore_tokens,
    and records the codex baseline when the codex token was restored."""
    pool = MagicMock()
    with (
        patch(
            "butlers.cli_auth.persistence.restore_tokens",
            new=AsyncMock(return_value={"codex": True, "opencode-go": False}),
        ) as restore_tokens,
        patch("butlers.cli_auth.persistence._record_codex_baseline") as baseline,
    ):
        results = await restore_connector_cli_auth(pool, context="unit")

    assert results == {"codex": True, "opencode-go": False}
    # CredentialStore was constructed around exactly the pool we passed.
    store_arg = restore_tokens.await_args.args[0]
    assert store_arg.pool is pool
    baseline.assert_called_once()


async def test_helper_warns_loudly_and_skips_baseline_when_no_codex(caplog) -> None:
    """When no codex token is restored, the helper logs a loud WARNING (honest
    degraded surface) and does NOT record a baseline — never a silent skip."""
    pool = MagicMock()
    with (
        patch(
            "butlers.cli_auth.persistence.restore_tokens",
            new=AsyncMock(return_value={"codex": False}),
        ),
        patch("butlers.cli_auth.persistence._record_codex_baseline") as baseline,
        caplog.at_level(logging.WARNING, logger="butlers.cli_auth.persistence"),
    ):
        results = await restore_connector_cli_auth(pool, context="whatsapp_user_client")

    assert results == {"codex": False}
    baseline.assert_not_called()
    assert any("no codex" in r.message.lower() for r in caplog.records), (
        "a missing codex token must be surfaced loudly, not silently"
    )


async def test_helper_warns_and_noops_when_pool_is_none(caplog) -> None:
    """A None pool (DB disabled/unreachable) is handled loudly and returns {} —
    never a CredentialStore(None) crash — per the honest fail-open contract."""
    with (
        patch("butlers.cli_auth.persistence.restore_tokens") as restore_tokens,
        caplog.at_level(logging.WARNING, logger="butlers.cli_auth.persistence"),
    ):
        results = await restore_connector_cli_auth(None, context="live_listener")

    assert results == {}
    restore_tokens.assert_not_called()
    assert any("no db pool" in r.message.lower() for r in caplog.records)


async def test_helper_is_non_fatal_and_loud_when_restore_raises(caplog) -> None:
    """Regression pin: a failed restore logs loudly and returns {} — it must NOT
    raise (crash-looping the connector) nor silently swallow at debug."""
    pool = MagicMock()
    with (
        patch(
            "butlers.cli_auth.persistence.restore_tokens",
            new=AsyncMock(side_effect=RuntimeError("credential DB unreachable")),
        ),
        caplog.at_level(logging.WARNING, logger="butlers.cli_auth.persistence"),
    ):
        results = await restore_connector_cli_auth(pool, context="live_listener")

    assert results == {}
    assert any("failed" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Connector entrypoint wiring — the restore runs at startup, on the pool the
# connector uses, before the connector begins processing messages.
# ---------------------------------------------------------------------------


async def test_whatsapp_entrypoint_restores_codex_auth() -> None:
    import butlers.connectors.whatsapp_user_client as wa

    cfg = wa.WhatsAppUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="whatsapp",
        channel="whatsapp_user_client",
        endpoint_identity="whatsapp:pending",
    )
    pool = MagicMock()
    pool.close = AsyncMock()
    conn = MagicMock()
    conn.start = AsyncMock()
    conn.stop = AsyncMock()

    with (
        patch.object(wa.WhatsAppUserClientConnectorConfig, "from_env", return_value=cfg),
        patch.object(wa, "_resolve_whatsapp_phone_from_db", new=AsyncMock(return_value=None)),
        patch(
            "butlers.connectors.cursor_store.create_cursor_pool_from_env",
            new=AsyncMock(return_value=pool),
        ),
        patch.object(wa, "WhatsAppUserClientConnector", return_value=conn),
        patch.object(wa, "_run_health_server", new=AsyncMock()),
        patch(
            "butlers.cli_auth.persistence.restore_connector_cli_auth",
            new=AsyncMock(return_value={"codex": True}),
        ) as restore,
    ):
        await wa.run_whatsapp_user_client_connector()

    restore.assert_awaited_once()
    assert restore.await_args.args[0] is pool
    conn.start.assert_awaited_once()


async def test_telegram_user_entrypoint_restores_codex_auth() -> None:
    import butlers.connectors.telegram_user_client as tg

    cfg = tg.TelegramUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_user_client",
        endpoint_identity="telegram:pending",
    )
    pool = MagicMock()
    pool.close = AsyncMock()
    control_pool = MagicMock()
    control_pool.close = AsyncMock()
    conn = MagicMock()
    conn.start = AsyncMock()
    conn.stop = AsyncMock()
    creds = {
        "TELEGRAM_API_ID": "123",
        "TELEGRAM_API_HASH": "hash",
        "TELEGRAM_USER_SESSION": "session",
    }

    with (
        patch.object(tg.TelegramUserClientConnectorConfig, "from_env", return_value=cfg),
        patch.object(
            tg,
            "_resolve_telegram_user_credentials_from_db",
            new=AsyncMock(return_value=creds),
        ),
        patch.object(tg, "_create_shared_control_pool", new=AsyncMock(return_value=control_pool)),
        patch.object(tg, "_resolve_endpoint_identity", new=AsyncMock(return_value="telegram:me")),
        patch(
            "butlers.connectors.cursor_store.create_cursor_pool_from_env",
            new=AsyncMock(return_value=pool),
        ),
        patch.object(
            tg,
            "load_account_wide_ingestion_consent",
            new=AsyncMock(
                return_value={
                    "account_wide_ingestion_consent": {
                        "version": "telegram-user-client-account-wide-v1",
                        "granted_at": "2026-07-17T00:00:00+00:00",
                    }
                }
            ),
        ),
        patch.object(tg, "TelegramUserClientConnector", return_value=conn),
        patch(
            "butlers.cli_auth.persistence.restore_connector_cli_auth",
            new=AsyncMock(return_value={"codex": True}),
        ) as restore,
    ):
        await tg.run_telegram_user_client_connector()

    restore.assert_awaited_once()
    assert restore.await_args.args[0] is pool
    conn.start.assert_awaited_once()
    control_pool.close.assert_awaited_once()


async def test_telegram_control_pool_targets_shared_db_not_checkpoint_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consent must follow shared owner credentials when cursor storage differs."""
    import butlers.connectors.telegram_user_client as tg

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "db-host")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "db-user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "db-password")
    monkeypatch.setenv("CONNECTOR_BUTLER_DB_NAME", "checkpoint-db")
    monkeypatch.setenv("BUTLER_SHARED_DB_NAME", "shared-credentials-db")
    created_pool = MagicMock()

    with patch("asyncpg.create_pool", new=AsyncMock(return_value=created_pool)) as create_pool:
        assert await tg._create_shared_control_pool() is created_pool

    assert create_pool.await_args.kwargs["database"] == "shared-credentials-db"
    assert create_pool.await_args.kwargs["server_settings"] == {"search_path": "public"}


async def test_telegram_user_entrypoint_waits_for_missing_account_wide_scope_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing consent keeps the process observable but never starts ingestion."""
    import butlers.connectors.telegram_user_client as tg

    monkeypatch.setattr(tg, "_CONSENT_RECHECK_S", 0, raising=False)
    cfg = tg.TelegramUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_user_client",
        endpoint_identity="telegram:pending",
    )
    control_pool = MagicMock()
    control_pool.close = AsyncMock()
    cursor_pool = MagicMock()
    cursor_pool.close = AsyncMock()
    conn = MagicMock()
    conn.start = AsyncMock()
    conn.stop = AsyncMock()
    startup_order: list[str] = []
    creds = {
        "TELEGRAM_API_ID": "123",
        "TELEGRAM_API_HASH": "hash",
        "TELEGRAM_USER_SESSION": "session",
    }
    consent_values = iter(
        [
            None,
            {
                "account_wide_ingestion_consent": {
                    "version": "telegram-user-client-account-wide-v1",
                    "granted_at": "2026-07-17T00:00:00+00:00",
                }
            },
        ]
    )

    async def load_consent(*_args: object) -> dict[str, object] | None:
        consent = next(consent_values)
        startup_order.append("consent-missing" if consent is None else "consent-valid")
        return consent

    async def resolve_credentials() -> dict[str, str]:
        startup_order.append("credentials")
        return creds

    async def resolve_identity(*_args: object) -> str:
        startup_order.append("identity")
        return "telegram:me"

    with (
        patch.object(tg.TelegramUserClientConnectorConfig, "from_env", return_value=cfg),
        patch.object(
            tg,
            "_resolve_telegram_user_credentials_from_db",
            new=AsyncMock(side_effect=resolve_credentials),
        ) as resolve_credentials_mock,
        patch.object(tg, "_create_shared_control_pool", new=AsyncMock(return_value=control_pool)),
        patch.object(
            tg,
            "_resolve_endpoint_identity",
            new=AsyncMock(side_effect=resolve_identity),
        ) as resolve_identity_mock,
        patch(
            "butlers.connectors.cursor_store.create_cursor_pool_from_env",
            new=AsyncMock(return_value=cursor_pool),
        ) as create_cursor_pool,
        patch.object(
            tg,
            "load_account_wide_ingestion_consent",
            new=AsyncMock(side_effect=load_consent),
        ) as load_consent_mock,
        patch.object(tg, "TelegramUserClientConnector", return_value=conn) as connector,
        patch.object(tg, "_run_health_server", new=AsyncMock()) as run_health_server,
        patch(
            "butlers.cli_auth.persistence.restore_connector_cli_auth",
            new=AsyncMock(return_value={"codex": True}),
        ),
    ):
        await tg.run_telegram_user_client_connector()

    assert load_consent_mock.await_count == 2
    assert startup_order == ["consent-missing", "consent-valid", "credentials", "identity"]
    resolve_credentials_mock.assert_awaited_once()
    resolve_identity_mock.assert_awaited_once()
    connector.assert_called_once()
    create_cursor_pool.assert_awaited_once()
    conn.start.assert_awaited_once()
    control_pool.close.assert_awaited_once()
    assert run_health_server.call_count == 2
    pending_health = run_health_server.call_args_list[0].args[1]
    state, error = pending_health._get_health_state()
    assert state == "error"
    assert error is not None
    assert "account-wide ingestion is disabled" in error.lower()
    assert pending_health._config.endpoint_identity == "telegram:user:pending-consent"


async def test_telegram_user_entrypoint_waits_for_malformed_scope_grant_before_client_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed grant is never accepted and is rechecked without a restart loop."""
    import butlers.connectors.telegram_user_client as tg

    monkeypatch.setattr(tg, "_CONSENT_RECHECK_S", 0, raising=False)
    cfg = tg.TelegramUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_user_client",
        endpoint_identity="telegram:pending",
    )
    control_pool = MagicMock()
    control_pool.close = AsyncMock()
    conn = MagicMock()
    conn.start = AsyncMock()
    conn.stop = AsyncMock()
    creds = {
        "TELEGRAM_API_ID": "123",
        "TELEGRAM_API_HASH": "hash",
        "TELEGRAM_USER_SESSION": "session",
    }
    malformed_grant = {
        "account_wide_ingestion_consent": {
            "version": "telegram-user-client-account-wide-v1",
            "granted_at": "not-a-timestamp",
        }
    }
    valid_grant = {
        "account_wide_ingestion_consent": {
            "version": "telegram-user-client-account-wide-v1",
            "granted_at": "2026-07-17T00:00:00+00:00",
        }
    }
    load_consent = AsyncMock(side_effect=[malformed_grant, valid_grant])

    with (
        patch.object(tg.TelegramUserClientConnectorConfig, "from_env", return_value=cfg),
        patch.object(
            tg,
            "_resolve_telegram_user_credentials_from_db",
            new=AsyncMock(return_value=creds),
        ),
        patch.object(tg, "_create_shared_control_pool", new=AsyncMock(return_value=control_pool)),
        patch.object(tg, "_resolve_endpoint_identity", new=AsyncMock()) as resolve_identity,
        patch(
            "butlers.connectors.cursor_store.create_cursor_pool_from_env",
            new=AsyncMock(return_value=MagicMock(close=AsyncMock())),
        ) as create_cursor_pool,
        patch.object(tg, "load_account_wide_ingestion_consent", new=load_consent),
        patch.object(tg, "TelegramUserClientConnector", return_value=conn) as connector,
        patch.object(tg, "_run_health_server", new=AsyncMock()),
        patch(
            "butlers.cli_auth.persistence.restore_connector_cli_auth",
            new=AsyncMock(return_value={"codex": True}),
        ),
    ):
        await tg.run_telegram_user_client_connector()

    assert load_consent.await_count == 2
    resolve_identity.assert_awaited_once()
    connector.assert_called_once()
    create_cursor_pool.assert_awaited_once()
    conn.start.assert_awaited_once()
    control_pool.close.assert_awaited_once()


async def test_telegram_user_entrypoint_starts_after_persisted_account_wide_scope_consent() -> None:
    """A recognized persisted consent grant permits the normal startup path."""
    import butlers.connectors.telegram_user_client as tg

    cfg = tg.TelegramUserClientConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        provider="telegram",
        channel="telegram_user_client",
        endpoint_identity="telegram:pending",
    )
    pool = MagicMock()
    pool.close = AsyncMock()
    control_pool = MagicMock()
    control_pool.close = AsyncMock()
    conn = MagicMock()
    conn.start = AsyncMock()
    conn.stop = AsyncMock()
    creds = {
        "TELEGRAM_API_ID": "123",
        "TELEGRAM_API_HASH": "hash",
        "TELEGRAM_USER_SESSION": "session",
    }

    with (
        patch.object(tg.TelegramUserClientConnectorConfig, "from_env", return_value=cfg),
        patch.object(
            tg,
            "_resolve_telegram_user_credentials_from_db",
            new=AsyncMock(return_value=creds),
        ),
        patch.object(tg, "_create_shared_control_pool", new=AsyncMock(return_value=control_pool)),
        patch.object(
            tg,
            "_resolve_endpoint_identity",
            new=AsyncMock(return_value="telegram:me"),
        ) as resolve_identity,
        patch(
            "butlers.connectors.cursor_store.create_cursor_pool_from_env",
            new=AsyncMock(return_value=pool),
        ),
        patch.object(
            tg,
            "load_account_wide_ingestion_consent",
            new=AsyncMock(
                return_value={
                    "account_wide_ingestion_consent": {
                        "version": "telegram-user-client-account-wide-v1",
                        "granted_at": "2026-07-17T00:00:00+00:00",
                    }
                }
            ),
        ) as load_settings,
        patch.object(tg, "TelegramUserClientConnector", return_value=conn),
        patch(
            "butlers.cli_auth.persistence.restore_connector_cli_auth",
            new=AsyncMock(return_value={"codex": True}),
        ),
    ):
        await tg.run_telegram_user_client_connector()

    load_settings.assert_awaited_once_with(control_pool)
    resolve_identity.assert_awaited_once()
    conn.start.assert_awaited_once()
    control_pool.close.assert_awaited_once()


async def test_live_listener_entrypoint_restores_codex_auth() -> None:
    import butlers.connectors.live_listener.connector as ll

    pool = MagicMock()
    pool.close = AsyncMock()
    conn = MagicMock()
    conn.run_forever = AsyncMock()
    conn.stop = AsyncMock()

    with (
        patch.object(ll.LiveListenerConfig, "from_env", return_value=MagicMock()),
        patch(
            "butlers.connectors.mcp_client.wait_for_switchboard_ready",
            new=AsyncMock(),
        ),
        patch(
            "butlers.connectors.cursor_store.create_cursor_pool_from_env",
            new=AsyncMock(return_value=pool),
        ),
        patch.object(ll, "LiveListenerConnector", return_value=conn),
        patch(
            "butlers.cli_auth.persistence.restore_connector_cli_auth",
            new=AsyncMock(return_value={"codex": True}),
        ) as restore,
    ):
        await ll.run_connector()

    restore.assert_awaited_once()
    assert restore.await_args.args[0] is pool
    conn.run_forever.assert_awaited_once()
