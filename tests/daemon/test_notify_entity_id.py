"""Tests for notify() entity_id parameter and contact-based resolution.

Covers tasks 7.1-7.4 from the contacts-identity-model spec:
  7.1 - notify() accepts entity_id parameter
  7.2 - entity_id resolves to channel identifier via entity_facts (bu-tv67t migration)
  7.3 - missing identifier parks action and notifies owner
  7.4 - neither param defaults to owner resolution

Migration notes (bu-km8xr):
  Resolution queries relationship.entity_facts keyed directly on entity_id — no
  public.contacts indirection. Tests seed entity_facts rows and assert queries
  target that path.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.daemon import ButlerDaemon
from butlers.identity import ResolvedContact

pytestmark = pytest.mark.unit

_ENTITY_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


@pytest.fixture
def butler_dir(tmp_path: Path) -> Path:
    """Create a minimal butler directory for testing."""
    butler_path = tmp_path / "test-butler"
    butler_path.mkdir()
    (butler_path / "butler.toml").write_text(
        """
[butler]
name = "test-butler"
port = 9100
description = "Test butler"

[butler.db]
name = "butlers"
schema = "test_butler"

[[butler.schedule]]
name = "daily-check"
cron = "0 9 * * *"
prompt = "Do the daily check"
"""
    )
    (butler_path / "MANIFESTO.md").write_text("# Test Butler")
    (butler_path / "CLAUDE.md").write_text("Test butler instructions.")
    return butler_path


def _make_runtime_config_row(butler_name: str = "test-butler") -> dict:
    """Return a dict-like row for the runtime_config table, as returned by asyncpg.fetchrow."""
    return {
        "butler_name": butler_name,
        "core_groups": None,
        "max_concurrent": 3,
        "max_queued": 10,
        "seeded_at": None,
        "updated_at": None,
    }


def _make_fetchrow_side_effect(butler_name: str = "test-butler"):
    """Return an async side_effect for pool.fetchrow that returns runtime_config rows
    for runtime_config queries, is_primary=True for contact_info is_primary lookups,
    and None for all other queries."""

    async def _fetchrow(query: str, *args, **kwargs):
        if "runtime_config" in query:
            return _make_runtime_config_row(butler_name)
        # is_primary_contact queries public.contact_info for is_primary.
        # Default to True so owner auto-approve continues to work in tests that
        # use _known_contact_patch without explicitly overriding fetchrow.
        if "contact_info" in query and "is_primary" in query:
            return {"is_primary": True}
        return None

    return _fetchrow


def _patch_infra(mock_pool: Any = None) -> dict[str, Any]:
    """Patch infrastructure dependencies for daemon tests."""
    if mock_pool is None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=None)
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetchval = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[])

        mock_pool = AsyncMock()
        # Support `async with pool.acquire() as conn:` for _ensure_owner_entity
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_pool.fetchval = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock(return_value=None)
        mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
        mock_pool.fetch = AsyncMock(return_value=[])

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
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    mock_credential_store = AsyncMock()
    mock_credential_store.resolve = AsyncMock(return_value=None)

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
        "configure_logging": patch("butlers.core.logging.configure_logging"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "create_audit_pool": patch.object(
            ButlerDaemon, "_create_audit_pool", new_callable=AsyncMock, return_value=None
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "build_credential_store": patch.object(
            ButlerDaemon,
            "_build_credential_store",
            new_callable=AsyncMock,
            return_value=mock_credential_store,
        ),
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
    }


async def _start_daemon_with_notify(
    butler_dir: Path, patches: dict[str, Any]
) -> tuple[ButlerDaemon, Any]:
    """Start daemon and extract the notify function reference."""
    notify_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **_decorator_kwargs):
        def decorator(fn):
            nonlocal notify_fn
            if fn.__name__ == "notify":
                notify_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["configure_logging"],
        patches["sync_schedules"],
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["create_audit_pool"],
        patches["recover_route_inbox"],
        patches["build_credential_store"],
        patches["get_adapter"],
        patches["shutil_which"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()
        return daemon, notify_fn


@contextmanager
def _known_contact_patch(email: str = "user@example.com") -> Any:
    """Context manager that patches identity resolution to return a known owner contact.

    Patches both ``resolve_contact_by_channel`` (returns owner contact) and
    ``is_primary_contact`` (returns True) so the email guard auto-approves without
    a real DB hit.
    """
    contact = ResolvedContact(
        contact_id=None,
        name="Test Contact",
        roles=["owner"],
        entity_id=uuid.UUID("00000000-0000-0000-0000-eeeeeeeeeeee"),
    )

    async def _mock_resolve(pool: Any, channel_type: str, channel_value: str) -> Any:
        return contact

    with (
        patch("butlers.identity.resolve_contact_by_channel", side_effect=_mock_resolve),
        patch(
            "butlers.modules.approvals.email_guard.is_primary_contact",
            new=AsyncMock(return_value=True),
        ),
    ):
        yield


def _non_owner_contact() -> ResolvedContact:
    """Return a resolved non-owner target for notify approval-boundary tests."""
    return ResolvedContact(
        contact_id=None,
        name="Test Contact",
        roles=["contact"],
        entity_id=uuid.UUID("00000000-0000-0000-0000-dddddddddddd"),
    )


def _make_mock_client(*, is_error: bool = False) -> Any:
    """Create a mock switchboard client."""
    mock_call_result = MagicMock()
    mock_call_result.is_error = is_error
    mock_call_result.data = {"status": "sent"}
    mock_call_result.content = [MagicMock(text='{"status":"sent"}')]

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(return_value=mock_call_result)
    return mock_client


def _make_entity_facts_conn(
    entity_id: uuid.UUID | None = _ENTITY_ID,
    facts_value: str | None = None,
    fetchrow_error: Exception | None = None,
) -> AsyncMock:
    """Build a mock connection that simulates entity-direct entity_facts resolution.

    The resolver queries ``relationship.entity_facts`` keyed on the entity_id —
    there is no longer a ``public.contacts`` indirection step.

    fetchrow("SELECT ef.object FROM relationship.entity_facts ...") → {"object": facts_value}
    """
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])

    if fetchrow_error is not None:
        mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_error)
        return mock_conn

    async def _ef_fetchrow(query: str, *args, **kwargs):
        if "entity_facts" in query:
            if facts_value is None:
                return None
            return {"object": facts_value}
        return None

    mock_conn.fetchrow = AsyncMock(side_effect=_ef_fetchrow)
    return mock_conn


def _make_pool_with_entity_facts_conn(
    entity_id: uuid.UUID | None = _ENTITY_ID,
    facts_value: str | None = None,
    fetchrow_error: Exception | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    """Build a (pool, conn) pair that supports the entity-direct entity_facts resolver."""
    mock_conn = _make_entity_facts_conn(
        entity_id=entity_id,
        facts_value=facts_value,
        fetchrow_error=fetchrow_error,
    )

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock(return_value=None)
    # pool-level fetchrow must return runtime_config rows so seed_if_empty works
    mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
    mock_pool.fetch = AsyncMock(return_value=[])

    @asynccontextmanager
    async def mock_acquire():
        yield mock_conn

    mock_pool.acquire = mock_acquire
    return mock_pool, mock_conn


# Legacy helper retained for backward compat with patches that don't need two-step.
def _make_pool_with_conn(
    fetchrow_return: Any = None, fetchrow_error: Exception | None = None
) -> tuple[AsyncMock, AsyncMock]:
    """Build a mock (pool, conn) pair for resolver tests (single-step legacy)."""
    mock_conn = AsyncMock()
    if fetchrow_error:
        mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_error)
    else:
        mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock(return_value=None)
    # pool-level fetchrow must return runtime_config rows so seed_if_empty works
    mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
    mock_pool.fetch = AsyncMock(return_value=[])

    @asynccontextmanager
    async def mock_acquire():
        yield mock_conn

    mock_pool.acquire = mock_acquire
    return mock_pool, mock_conn


def _patch_db_in_patches(patches: dict, mock_pool: Any) -> None:
    """Override db_from_env in patches dict with a mock_db using mock_pool."""
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
    patches["db_from_env"] = patch("butlers.lifecycle.Database.from_env", return_value=mock_db)


@pytest.mark.asyncio
class TestNotifyEntityIdResolution:
    """Tasks 7.1+7.2 — entity_id resolves to channel identifier via entity_facts."""

    async def test_entity_id_resolves_and_delivers(self, butler_dir: Path) -> None:
        """entity_id=UUID calls resolver; resolved identifier used in delivery payload;
        DB query uses entity_facts path; returns None when not found/error."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # entity_id → resolver called with correct args, result used in delivery
        entity_id = uuid.UUID("00000000-0000-0000-0000-000000000010")
        daemon.switchboard_client = _make_mock_client()
        mock_resolver = AsyncMock(return_value="contact@example.com")
        with (
            patch.object(daemon, "_resolve_entity_channel_identifier", new=mock_resolver),
            _known_contact_patch("contact@example.com"),
        ):
            result = await notify_fn(channel="email", message="Test", entity_id=entity_id)
        assert result["status"] == "ok"
        # msg_context defaults to None when not provided
        mock_resolver.assert_awaited_once_with(
            entity_id=entity_id, channel="email", msg_context=None
        )
        delivery = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery["recipient"] == "contact@example.com"

        # DB resolver queries entity_facts (not contact_info) keyed on entity_id;
        # entity-direct (single query, no public.contacts indirection).
        mock_pool2, mock_conn2 = _make_pool_with_entity_facts_conn(
            entity_id=_ENTITY_ID,
            facts_value="user@example.com",
        )
        patches2 = _patch_infra()
        _patch_db_in_patches(patches2, mock_pool2)
        daemon2, _ = await _start_daemon_with_notify(butler_dir, patches2)
        eid = uuid.UUID("00000000-0000-0000-0000-000000000020")
        result2 = await daemon2._resolve_entity_channel_identifier(entity_id=eid, channel="email")
        assert result2 == "user@example.com"
        # Single fetchrow: entity_facts lookup — no public.contacts step.
        assert mock_conn2.fetchrow.await_count == 1
        queries = [c.args[0] for c in mock_conn2.fetchrow.await_args_list]
        assert not any("public.contacts" in q for q in queries)
        assert any("relationship.entity_facts" in q for q in queries)
        # entity_facts query must NOT reference contact_info
        ef_query = next(q for q in queries if "relationship.entity_facts" in q)
        assert "contact_info" not in ef_query

        # Returns None when the entity has no matching fact
        mock_pool3, _ = _make_pool_with_entity_facts_conn(entity_id=None, facts_value=None)
        patches3 = _patch_infra()
        _patch_db_in_patches(patches3, mock_pool3)
        daemon3, _ = await _start_daemon_with_notify(butler_dir, patches3)
        assert (
            await daemon3._resolve_entity_channel_identifier(
                entity_id=uuid.UUID("00000000-0000-0000-0000-000000000022"), channel="email"
            )
            is None
        )

    async def test_telegram_resolution_uses_handle_prefix_filter(self, butler_dir: Path) -> None:
        """Telegram channel resolution queries has-handle with 'telegram:' prefix filter.

        entity_facts stores telegram_user_id as has-handle with object 'telegram:NUMERIC_ID'.
        The resolver must filter by this prefix to disambiguate from linkedin/twitter handles,
        then strip the prefix and return the numeric delivery ID.
        """
        # Seed entity_facts with a telegram has-handle triple in 'telegram:<id>' format.
        mock_pool, mock_conn = _make_pool_with_entity_facts_conn(
            entity_id=_ENTITY_ID,
            facts_value="telegram:210454304",
        )
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, _ = await _start_daemon_with_notify(butler_dir, patches)

        result = await daemon._resolve_entity_channel_identifier(
            entity_id=uuid.UUID("00000000-0000-0000-0000-000000000030"),
            channel="telegram",
        )
        # Prefix stripped: "telegram:210454304" → "210454304"
        assert result == "210454304"

        # Assert the entity_facts query used the 'telegram:%' LIKE filter
        queries = [c.args[0] for c in mock_conn.fetchrow.await_args_list]
        ef_query = next(q for q in queries if "relationship.entity_facts" in q)
        # Must use LIKE prefix filter for telegram disambiguation
        assert "LIKE" in ef_query or "like" in ef_query.lower()
        # Must NOT reference contact_info anywhere
        assert "contact_info" not in ef_query

    async def test_telegram_no_has_handle_with_prefix_returns_none(self, butler_dir: Path) -> None:
        """Returns None if entity has no telegram: prefixed has-handle row.

        rel_019 normalised legacy telegram rows to the 'telegram:' prefix in
        production, so the daemon no longer falls back to an unprefixed all-numeric
        match (bu-3nu0x).  A contact with only linkedin/twitter handles (non-prefixed)
        must not be delivered to the wrong platform.
        """
        # entity has has-handle but NOT with telegram: prefix
        mock_pool, _ = _make_pool_with_entity_facts_conn(
            entity_id=_ENTITY_ID,
            facts_value=None,  # query returns no row (no telegram:-prefixed handle)
        )
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, _ = await _start_daemon_with_notify(butler_dir, patches)

        result = await daemon._resolve_entity_channel_identifier(
            entity_id=uuid.UUID("00000000-0000-0000-0000-000000000031"),
            channel="telegram",
        )
        assert result is None

    async def test_email_resolution_uses_has_email_predicate(self, butler_dir: Path) -> None:
        """Email channel resolution queries has-email predicate; no prefix filter needed."""
        mock_pool, mock_conn = _make_pool_with_entity_facts_conn(
            entity_id=_ENTITY_ID,
            facts_value="someone@example.com",
        )
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, _ = await _start_daemon_with_notify(butler_dir, patches)

        result = await daemon._resolve_entity_channel_identifier(
            entity_id=uuid.UUID("00000000-0000-0000-0000-000000000032"),
            channel="email",
        )
        assert result == "someone@example.com"

        # Email uses has-email predicate (passed as arg, checked in args list)
        ef_args = [c.args for c in mock_conn.fetchrow.await_args_list]
        ef_call_args = next(a for a in ef_args if any("entity_facts" in str(x) for x in a))
        assert "has-email" in ef_call_args

    async def test_unknown_channel_returns_none(self, butler_dir: Path) -> None:
        """Returns None immediately for channels with no known predicate mapping."""
        mock_pool, mock_conn = _make_pool_with_entity_facts_conn(
            entity_id=_ENTITY_ID,
            facts_value="irrelevant",
        )
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, _ = await _start_daemon_with_notify(butler_dir, patches)

        result = await daemon._resolve_entity_channel_identifier(
            entity_id=uuid.UUID("00000000-0000-0000-0000-000000000033"),
            channel="fax",  # unmapped channel
        )
        assert result is None


def _make_missing_id_patches(butler_dir: Path) -> tuple[dict, Any, Any]:
    """Return patches + daemon startup for missing-identifier tests."""
    mock_conn_inner = AsyncMock()
    mock_conn_inner.execute = AsyncMock(return_value=None)
    mock_conn_inner.fetchrow = AsyncMock(return_value=None)
    mock_conn_inner.fetchval = AsyncMock(return_value=None)
    mock_conn_inner.fetch = AsyncMock(return_value=[])

    mock_pool = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn_inner)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(side_effect=_make_fetchrow_side_effect())
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.fetch = AsyncMock(return_value=[])
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
    patches = _patch_infra()
    patches["db_from_env"] = patch("butlers.lifecycle.Database.from_env", return_value=mock_db)
    return patches, mock_pool, mock_db


@pytest.mark.asyncio
class TestNotifyMissingIdentifierAndOwner:
    """Tasks 7.3+7.4 — missing identifier parks; no entity_id uses owner resolution."""

    async def test_missing_identifier_fails_closed_without_approval_parking(
        self, butler_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Finance-like no-approvals daemon must not fabricate a parked action.

        Finance exposes ``notify`` but deliberately has no ``[modules.approvals]``
        section.  Once approval hooks are scoped to their owning pools, a missing
        entity channel identifier therefore cannot be persisted in
        ``pending_actions``.  The caller must receive an honest failure instead
        of a generated action id for a row and owner push that do not exist.
        """
        import butlers.core.approvals_hooks as approval_hooks

        patches, mock_pool, _ = _make_missing_id_patches(butler_dir)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()

        # Keep the daemon in the same optional-approvals state as Finance rather
        # than inheriting a legacy test hook or another butler's scoped runtime.
        monkeypatch.setattr(approval_hooks, "_park_pending_action_hook", None)
        monkeypatch.setattr(approval_hooks, "_approval_hooks_by_pool", {})
        entity_id = uuid.UUID("00000000-0000-0000-0000-000000000031")

        with (
            patch.object(
                daemon, "_resolve_entity_channel_identifier", new=AsyncMock(return_value=None)
            ),
            patch(
                "butlers.core.owner.fetch_owner_entity_id",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello contact",
                entity_id=entity_id,
                _why="The contact needs this delivery after their channel is configured.",
                _evidence=[],
            )

        assert result["status"] == "error"
        assert result["retryable"] is False
        assert "not enabled" in result["error"]
        assert "pending_action_id" not in result
        assert not any(
            "INSERT INTO pending_actions" in call.args[0]
            for call in mock_pool.execute.await_args_list
        )
        daemon.switchboard_client.call_tool.assert_not_awaited()

        failed_ledger_rows = [
            call.args
            for call in mock_pool.fetchval.await_args_list
            if "INSERT INTO public.attention_ledger" in call.args[0]
        ]
        assert len(failed_ledger_rows) == 1
        assert failed_ledger_rows[0][8] == "failed"
        assert failed_ledger_rows[0][9] == "approval_parking_unavailable"

    async def test_missing_identifier_parks_and_owner_fallback(self, butler_dir: Path) -> None:
        """Missing identifier -> pending_missing_identifier, parked through the shared
        park_pending_action choke point (bu-mda0r) so the owner push is attempted
        exactly the way every other park path attempts it; no entity_id/recipient ->
        owner default resolver called; entity_id wins over recipient.

        The push mechanism itself (whether the owner is actually reachable, quiet
        hours, burst digest, callback-secret handling) is real-Postgres-backed
        coverage owned by tests/integration/test_approval_push_on_park.py; this
        test only proves notify()'s missing-identifier path hands off to that
        one shared helper instead of building its own ad hoc owner alert.
        """
        patches, mock_pool, _ = _make_missing_id_patches(butler_dir)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        entity_id = uuid.UUID("00000000-0000-0000-0000-000000000030")
        push_runtime = object()  # opaque sentinel; identity-checked below
        daemon._approval_push_runtime = push_runtime

        with (
            patch.object(
                daemon, "_resolve_entity_channel_identifier", new=AsyncMock(return_value=None)
            ),
            patch(
                # notify() reaches the park choke point via the core_tools ->
                # core.approvals_hooks indirection (core_tools must never
                # import modules.* directly; bu-mda0r).
                "butlers.core.approvals_hooks.park_pending_action",
                new=AsyncMock(return_value=None),
            ) as mock_park,
            patch(
                "butlers.core.approvals_hooks.is_approval_parking_available",
                return_value=True,
            ),
        ):
            result = await notify_fn(
                channel="email",
                message="Hello contact",
                entity_id=entity_id,
                _why="The contact needs this delivery after their channel is configured.",
                _evidence=[],
            )
        assert result["status"] == "pending_missing_identifier"
        assert result["entity_id"] == str(entity_id)
        mock_park.assert_awaited_once()
        park_kwargs = mock_park.await_args.kwargs
        assert park_kwargs["tool_name"] == "notify"
        assert park_kwargs["origin_butler"] == daemon.config.name
        assert park_kwargs["approval_push_runtime"] is push_runtime
        assert park_kwargs["tool_args"]["entity_id"] == str(entity_id)

        # No entity_id → default owner resolver called
        patches3 = _patch_infra()
        daemon3, notify_fn3 = await _start_daemon_with_notify(butler_dir, patches3)
        daemon3.switchboard_client = _make_mock_client()
        mock_default = AsyncMock(return_value="owner@example.com")
        mock_contact = AsyncMock(return_value="ignored")
        with (
            patch.object(daemon3, "_resolve_default_notify_recipient", new=mock_default),
            patch.object(daemon3, "_resolve_entity_channel_identifier", new=mock_contact),
            _known_contact_patch(),
        ):
            r3 = await notify_fn3(channel="email", message="Hello owner")
        assert r3["status"] == "ok"
        mock_default.assert_awaited_once()
        mock_contact.assert_not_awaited()

        # entity_id wins over explicit recipient
        daemon3.switchboard_client = _make_mock_client()
        with (
            patch.object(
                daemon3,
                "_resolve_entity_channel_identifier",
                new=AsyncMock(return_value="contact-resolved@example.com"),
            ),
            _known_contact_patch("contact-resolved@example.com"),
        ):
            r4 = await notify_fn3(
                channel="email",
                message="Hello",
                entity_id=uuid.UUID("00000000-0000-0000-0000-000000000040"),
                recipient="explicit@example.com",
            )
        assert r4["status"] == "ok"
        delivery = daemon3.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery["recipient"] == "contact-resolved@example.com"


@pytest.fixture(autouse=False)
def register_email_guard_hook():
    """Register the real approvals email guard so notify() enforces recipient validation.

    The butler.toml fixture used by these tests does not enable the approvals module,
    so ``on_startup`` never calls ``register_email_guard``.  This fixture registers the
    real implementation directly against the hook slot, mirroring what the approvals
    module's ``on_startup`` would do in production.  This preserves the fail-open
    semantics for butlers that genuinely have no approvals module while keeping these
    safety-contract tests hermetic.
    """
    import butlers.core.approvals_hooks as _hooks
    from butlers.modules.approvals.email_guard import (
        check_email_recipient as _real_check,
    )

    orig = _hooks._email_guard_hook
    _hooks._email_guard_hook = _real_check
    yield
    _hooks._email_guard_hook = orig


@pytest.fixture(autouse=False)
def register_recipient_guard_hook():
    """Register the channel-general approval guard for notify-boundary tests."""
    import butlers.core.approvals_hooks as _hooks
    from butlers.modules.approvals.email_guard import check_recipient as _real_check

    orig = _hooks._recipient_guard_hook
    _hooks._recipient_guard_hook = _real_check
    yield
    _hooks._recipient_guard_hook = orig


@pytest.mark.asyncio
@pytest.mark.usefixtures("register_email_guard_hook")
class TestNotifyEmailRecipientValidation:
    """Email recipients must be known contacts; entity_id path also validated."""

    async def test_email_validation(self, butler_dir: Path) -> None:
        """Unknown email → pending_approval; known sent; telegram skips; entity_id path validates."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None

        # Unknown email → parked as pending_approval
        daemon.switchboard_client = _make_mock_client()
        with patch("butlers.identity.resolve_contact_by_channel", new=AsyncMock(return_value=None)):
            result = await notify_fn(
                channel="email",
                message="Hello stranger",
                recipient="hallucinated@example.com",
                _why="The recipient asked for this update.",
                _evidence=[],
            )
        assert result["status"] == "pending_approval"

        # Known email → delivered
        daemon.switchboard_client = _make_mock_client()
        with _known_contact_patch():
            result2 = await notify_fn(
                channel="email", message="Hello known", recipient="known@example.com"
            )
        assert result2["status"] == "ok"

        # entity_id path still validates email
        daemon.switchboard_client = _make_mock_client()
        with (
            patch.object(
                daemon,
                "_resolve_entity_channel_identifier",
                new=AsyncMock(return_value="contact-email@example.com"),
            ),
            patch("butlers.identity.resolve_contact_by_channel", new=AsyncMock(return_value=None)),
        ):
            result3 = await notify_fn(
                channel="email",
                message="Hello via entity_id",
                entity_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
                _why="The recipient asked for this update.",
                _evidence=[],
            )
        assert result3["status"] == "pending_approval"


@pytest.mark.asyncio
@pytest.mark.usefixtures("register_recipient_guard_hook")
class TestNotifyDecisionDossierBoundary:
    """notify() must apply the dossier contract before non-owner park/rule paths."""

    async def test_owner_target_remains_exempt_from_required_why(self, butler_dir: Path) -> None:
        """A resolved owner recipient still delivers without a decision dossier."""
        mock_pool, _ = _make_pool_with_entity_facts_conn()
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()

        with _known_contact_patch():
            result = await notify_fn(
                channel="telegram",
                message="Owner-only status update",
                recipient="12345",
            )

        assert result["status"] == "ok"
        pending_inserts = [
            call
            for call in mock_pool.execute.await_args_list
            if "INSERT INTO pending_actions" in call.args[0]
        ]
        assert pending_inserts == []

    async def test_owner_missing_identifier_remains_exempt_from_required_why(
        self, butler_dir: Path
    ) -> None:
        """An owner entity still parks for its missing channel without requiring why."""
        entity_id = uuid.UUID("00000000-0000-0000-0000-aaaaaaaaaaaa")
        mock_pool, _ = _make_pool_with_entity_facts_conn()
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()

        # This butler.toml fixture does not enable the approvals module, so
        # on_startup() never calls register_park_pending_action; register the
        # real implementation directly so notify()'s missing-identifier park
        # (routed through the core.approvals_hooks choke point, bu-mda0r)
        # actually issues its INSERT against this test's mock pool.
        import butlers.core.approvals_hooks as _approvals_hooks
        from butlers.modules.approvals.park import park_pending_action as _real_park

        original_park_hook = _approvals_hooks._park_pending_action_hook
        _approvals_hooks.register_park_pending_action(_real_park)
        try:
            with (
                patch.object(
                    daemon,
                    "_resolve_entity_channel_identifier",
                    new=AsyncMock(return_value=None),
                ),
                patch.object(
                    daemon,
                    "_resolve_default_notify_recipient",
                    new=AsyncMock(return_value=None),
                ),
                patch(
                    "butlers.core.owner.fetch_owner_entity_id",
                    new=AsyncMock(return_value=entity_id),
                ),
            ):
                result = await notify_fn(
                    channel="telegram",
                    message="Owner delivery awaiting channel configuration",
                    entity_id=entity_id,
                )
        finally:
            _approvals_hooks._park_pending_action_hook = original_park_hook

        assert result["status"] == "pending_missing_identifier"
        pending_insert = next(
            call
            for call in mock_pool.execute.await_args_list
            if "INSERT INTO pending_actions" in call.args[0]
        )
        assert pending_insert.args[-4:] == (None, [], None, None)

    @pytest.mark.parametrize(
        ("dossier_kwargs", "expected_code"),
        [
            ({}, "missing_required_dossier_field"),
            ({"_why": "   "}, "invalid_dossier_value"),
        ],
    )
    async def test_non_owner_invalid_why_retries_without_rule_or_persistence(
        self,
        butler_dir: Path,
        dossier_kwargs: dict[str, Any],
        expected_code: str,
    ) -> None:
        """Missing or malformed why stops notify() before any approval side effect."""
        mock_pool, _ = _make_pool_with_entity_facts_conn()
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()
        match_rules = AsyncMock(side_effect=AssertionError("rules must not be queried"))

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch("butlers.modules.approvals.rules.match_rules", new=match_rules),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Non-owner update",
                recipient="900800700",
                **dossier_kwargs,
            )

        assert result["status"] == "error"
        assert result["retryable"] is True
        assert result["error"] == {
            "code": expected_code,
            "field": "why",
            "message": (
                "why is required for a gated non-owner action; retry with _why."
                if expected_code == "missing_required_dossier_field"
                else "why must be a non-empty human-readable string."
            ),
            "retryable": True,
        }
        match_rules.assert_not_awaited()
        pending_inserts = [
            call
            for call in mock_pool.execute.await_args_list
            if "INSERT INTO pending_actions" in call.args[0]
        ]
        assert pending_inserts == []

    async def test_quiet_hours_reject_non_owner_missing_why_before_deferral(
        self, butler_dir: Path
    ) -> None:
        """Quiet-hours storage cannot bypass the non-owner dossier boundary."""
        mock_pool, _ = _make_pool_with_entity_facts_conn()
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        deferred_insert = AsyncMock(return_value=uuid.uuid4())
        match_rules = AsyncMock(side_effect=AssertionError("rules must not be queried"))

        with (
            patch(
                "butlers.core.temporal.delivery_db.get_delivery_preferences",
                new=AsyncMock(return_value={"timezone": "UTC"}),
            ),
            patch(
                "butlers.core.temporal.delivery.should_defer_notification",
                return_value=True,
            ),
            patch(
                "butlers.core.temporal.delivery.compute_deliver_at",
                return_value=datetime.now(UTC),
            ),
            patch(
                "butlers.core.temporal.delivery_db.insert_deferred_notification",
                new=deferred_insert,
            ),
            patch(
                "butlers.core_tools._notifications.record_attention_event",
                new=AsyncMock(),
            ),
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch("butlers.modules.approvals.rules.match_rules", new=match_rules),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Non-owner update",
                recipient="900800700",
                priority="medium",
            )

        assert result["status"] == "error"
        assert result["error"]["code"] == "missing_required_dossier_field"
        assert result["retryable"] is True
        deferred_insert.assert_not_awaited()
        match_rules.assert_not_awaited()
        pending_inserts = [
            call
            for call in mock_pool.execute.await_args_list
            if "INSERT INTO pending_actions" in call.args[0]
        ]
        assert pending_inserts == []

    async def test_quiet_hours_preserves_valid_dossier_in_deferred_envelope(
        self, butler_dir: Path
    ) -> None:
        """A valid non-owner dossier survives a delayed route.execute delivery."""
        mock_pool, _ = _make_pool_with_entity_facts_conn()
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        deferred_insert = AsyncMock(return_value=uuid.uuid4())
        evidence = [
            {
                "type": "text",
                "ref": "request-900800700",
                "note": "The recipient asked for this update.",
            }
        ]
        standing_rule = MagicMock(id=uuid.uuid4())
        match_rules = AsyncMock(return_value=standing_rule)

        with (
            patch(
                "butlers.core.temporal.delivery_db.get_delivery_preferences",
                new=AsyncMock(return_value={"timezone": "UTC"}),
            ),
            patch(
                "butlers.core.temporal.delivery.should_defer_notification",
                return_value=True,
            ),
            patch(
                "butlers.core.temporal.delivery.compute_deliver_at",
                return_value=datetime.now(UTC),
            ),
            patch(
                "butlers.core.temporal.delivery_db.insert_deferred_notification",
                new=deferred_insert,
            ),
            patch(
                "butlers.core_tools._notifications.record_attention_event",
                new=AsyncMock(),
            ),
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch("butlers.modules.approvals.rules.match_rules", new=match_rules),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Non-owner update",
                recipient="900800700",
                priority="medium",
                _why="The recipient asked for this update.",
                _evidence=evidence,
                _blast_radius="contact",
                _reversibility="compensable",
            )

        assert result["status"] == "deferred"
        match_rules.assert_awaited_once()
        envelope = deferred_insert.await_args.kwargs["envelope"]
        assert envelope["delivery"]["recipient"] == "900800700"
        assert envelope["decision_dossier"] == {
            "why": "The recipient asked for this update.",
            "evidence": evidence,
            "blast_radius": "contact",
            "reversibility": "compensable",
        }

    async def test_non_owner_missing_identifier_requires_dossier_before_park(
        self, butler_dir: Path
    ) -> None:
        """A missing channel identifier cannot bypass the non-owner dossier gate."""
        mock_pool, _ = _make_pool_with_entity_facts_conn()
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()

        with patch.object(
            daemon,
            "_resolve_entity_channel_identifier",
            new=AsyncMock(return_value=None),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Awaiting a target channel",
                entity_id=uuid.UUID("00000000-0000-0000-0000-bbbbbbbbbbbb"),
            )

        assert result["status"] == "error"
        assert result["error"]["code"] == "missing_required_dossier_field"
        pending_inserts = [
            call
            for call in mock_pool.execute.await_args_list
            if "INSERT INTO pending_actions" in call.args[0]
        ]
        assert pending_inserts == []

    async def test_non_owner_valid_dossier_is_persisted_on_park(self, butler_dir: Path) -> None:
        """A typed dossier survives notify()'s recipient guard into the parked action."""
        mock_pool, _ = _make_pool_with_entity_facts_conn()
        patches = _patch_infra()
        _patch_db_in_patches(patches, mock_pool)
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()
        evidence = [
            {
                "type": "text",
                "ref": "request-900800700",
                "note": "The recipient asked for this update.",
            }
        ]

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch("butlers.modules.approvals.rules.match_rules", new=AsyncMock(return_value=None)),
        ):
            result = await notify_fn(
                channel="telegram",
                message="Non-owner update",
                recipient="900800700",
                _why="The recipient asked for this update.",
                _evidence=evidence,
                _blast_radius="contact",
                _reversibility="compensable",
            )

        assert result["status"] == "pending_approval"
        pending_insert = next(
            call
            for call in mock_pool.execute.await_args_list
            if "INSERT INTO pending_actions" in call.args[0]
        )
        assert "why, evidence, blast_radius, reversibility" in pending_insert.args[0]
        assert pending_insert.args[-4:] == (
            "The recipient asked for this update.",
            evidence,
            "contact",
            "compensable",
        )


@pytest.mark.asyncio
class TestNotifyChannelResolution:
    """Group 2 (bu-upbit) — optional channel + preferred-channel resolution wiring.

    notify() resolves the outbound channel when the caller omits it:
      - forced channel always wins (preference resolver is NOT consulted)
      - omitted + entity_id → resolve_outbound_channel decides the channel
      - omitted + no entity_id → telegram default (back-compat)
    """

    async def test_forced_channel_wins_resolver_not_consulted(self, butler_dir: Path) -> None:
        """An explicit channel arg bypasses preferred-channel resolution entirely."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()

        entity_id = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
        mock_resolve = AsyncMock(return_value="email")  # would pick email if asked
        with (
            patch("butlers.identity.resolve_outbound_channel", new=mock_resolve),
            patch.object(
                daemon,
                "_resolve_entity_channel_identifier",
                new=AsyncMock(return_value="210454304"),
            ),
        ):
            result = await notify_fn(channel="telegram", message="Forced", entity_id=entity_id)
        assert result["status"] == "ok"
        # Forced channel → resolver never called.
        mock_resolve.assert_not_awaited()
        delivery = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery["channel"] == "telegram"

    async def test_omitted_channel_with_entity_id_resolves_preference(
        self, butler_dir: Path
    ) -> None:
        """channel=None + entity_id → resolve_outbound_channel picks the channel."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()

        entity_id = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
        mock_resolve = AsyncMock(return_value="telegram")
        mock_identifier = AsyncMock(return_value="210454304")
        with (
            patch("butlers.identity.resolve_outbound_channel", new=mock_resolve),
            patch.object(daemon, "_resolve_entity_channel_identifier", new=mock_identifier),
        ):
            result = await notify_fn(message="Hi", entity_id=entity_id)
        assert result["status"] == "ok"
        mock_resolve.assert_awaited_once()
        # Resolver called with the contact and notify's deliverable set.
        _, kwargs = mock_resolve.await_args
        assert mock_resolve.await_args.args[1] == entity_id
        assert kwargs["deliverable_channels"] == {"telegram", "email"}
        delivery = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery["channel"] == "telegram"
        # The contact-channel identifier resolver was driven by the chosen channel.
        mock_identifier.assert_awaited_once_with(
            entity_id=entity_id, channel="telegram", msg_context=None
        )

    async def test_omitted_channel_no_entity_id_defaults_telegram(self, butler_dir: Path) -> None:
        """channel=None + no entity_id → telegram default; resolver not consulted."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()

        mock_resolve = AsyncMock(return_value="email")
        with (
            patch("butlers.identity.resolve_outbound_channel", new=mock_resolve),
            patch.object(
                daemon,
                "_resolve_default_notify_recipient",
                new=AsyncMock(return_value="210454304"),
            ),
        ):
            result = await notify_fn(message="Owner ping")
        assert result["status"] == "ok"
        # No entity_id → preference resolution is not attempted.
        mock_resolve.assert_not_awaited()
        delivery = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery["channel"] == "telegram"

    async def test_resolver_none_falls_back_to_telegram_default(self, butler_dir: Path) -> None:
        """channel=None + entity_id but resolver returns None → telegram default."""
        patches = _patch_infra()
        daemon, notify_fn = await _start_daemon_with_notify(butler_dir, patches)
        assert notify_fn is not None
        daemon.switchboard_client = _make_mock_client()

        entity_id = uuid.UUID("00000000-0000-0000-0000-0000000000a3")
        with (
            patch(
                "butlers.identity.resolve_outbound_channel",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                daemon,
                "_resolve_entity_channel_identifier",
                new=AsyncMock(return_value="210454304"),
            ),
        ):
            result = await notify_fn(message="Hi", entity_id=entity_id)
        assert result["status"] == "ok"
        delivery = daemon.switchboard_client.call_tool.await_args.args[1]["notify_request"][
            "delivery"
        ]
        assert delivery["channel"] == "telegram"
