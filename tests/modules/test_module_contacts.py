"""Tests for the Contacts module configuration scaffold and sync lifecycle."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.contacts import ContactsConfig, ContactsModule, ContactsSyncConfig
from butlers.modules.contacts.sync import (
    ContactsSyncError,
    ContactsSyncResult,
    ContactsSyncRuntime,
    ContactsSyncState,
)
from butlers.modules.registry import default_registry

pytestmark = pytest.mark.unit


class TestModuleABCCompliance:
    """Verify ContactsModule satisfies the shared module contract."""

    def test_is_module_subclass(self) -> None:
        assert issubclass(ContactsModule, Module)

    def test_instantiates(self) -> None:
        mod = ContactsModule()
        assert isinstance(mod, Module)

    def test_name(self) -> None:
        assert ContactsModule().name == "contacts"

    def test_config_schema(self) -> None:
        schema = ContactsModule().config_schema
        assert schema is ContactsConfig
        assert issubclass(schema, BaseModel)

    def test_dependencies_empty(self) -> None:
        assert ContactsModule().dependencies == []

    def test_credentials_env_declared(self) -> None:
        assert ContactsModule().credentials_env == []

    def test_migration_revisions_returns_contacts_chain(self) -> None:
        assert ContactsModule().migration_revisions() == "contacts"

    def test_module_discovered_in_default_registry(self) -> None:
        assert "contacts" in default_registry().available_modules


class TestContactsConfig:
    """Verify config validation defaults and strictness."""

    def test_provider_required(self) -> None:
        with pytest.raises(ValidationError):
            ContactsConfig()

    def test_defaults(self) -> None:
        config = ContactsConfig(provider="google")
        assert config.provider == "google"
        assert config.include_other_contacts is False
        assert isinstance(config.sync, ContactsSyncConfig)
        assert config.sync.enabled is True
        assert config.sync.run_on_startup is True
        assert config.sync.interval_minutes == 15
        assert config.sync.full_sync_interval_days == 6

    def test_provider_normalization(self) -> None:
        config = ContactsConfig(provider="  GOOGLE  ")
        assert config.provider == "google"

    def test_provider_non_empty_error(self) -> None:
        with pytest.raises(ValidationError, match="provider must be a non-empty string"):
            ContactsConfig(provider="   ")

    def test_top_level_forbids_unknown_fields(self) -> None:
        with pytest.raises(ValidationError) as error:
            ContactsConfig(provider="google", unsupported=True)
        assert error.value.errors()[0]["loc"] == ("unsupported",)
        assert error.value.errors()[0]["type"] == "extra_forbidden"

    def test_sync_forbids_unknown_fields(self) -> None:
        with pytest.raises(ValidationError) as error:
            ContactsConfig(provider="google", sync={"interval_minutes": 15, "bogus": 1})
        assert error.value.errors()[0]["loc"] == ("sync", "bogus")
        assert error.value.errors()[0]["type"] == "extra_forbidden"


def _make_credential_store(
    *,
    client_id: str = "cid",
    client_secret: str = "csecret",
) -> Any:
    """Build an AsyncMock credential store that returns the given values."""
    store = MagicMock()

    async def _resolve(key: str, *, env_fallback: bool = True) -> str | None:
        mapping = {
            "GOOGLE_OAUTH_CLIENT_ID": client_id,
            "GOOGLE_OAUTH_CLIENT_SECRET": client_secret,
        }
        return mapping.get(key)

    store.resolve = AsyncMock(side_effect=_resolve)
    return store


def _make_db_with_pool() -> MagicMock:
    """Build a db mock with a pool attribute for contact_info resolution."""
    db = MagicMock()
    db.pool = MagicMock()
    return db


class TestModuleStartup:
    """Verify startup provider selection behavior."""

    async def test_startup_fails_on_unsupported_provider(self) -> None:
        mod = ContactsModule()
        with pytest.raises(RuntimeError) as excinfo:
            await mod.on_startup({"provider": "outlook"}, db=None)

        error_message = str(excinfo.value)
        assert "Unsupported contacts provider 'outlook'" in error_message
        assert "Supported providers: google" in error_message

    async def test_startup_skips_runtime_when_sync_disabled(self) -> None:
        """When sync.enabled=False, no credential lookup or runtime is created."""
        mod = ContactsModule()
        await mod.on_startup({"provider": "google", "sync": {"enabled": False}}, db=None)
        config = getattr(mod, "_config")
        assert isinstance(config, ContactsConfig)
        assert config.provider == "google"
        assert getattr(mod, "_runtime") is None

    async def test_startup_creates_runtime_when_credentials_present(self) -> None:
        """on_startup() creates and starts ContactsSyncRuntime when sync.enabled=True."""
        mod = ContactsModule()
        credential_store = _make_credential_store()
        db = _make_db_with_pool()

        with (
            patch.object(ContactsSyncRuntime, "start", new_callable=AsyncMock) as mock_start,
            patch(
                "butlers.credential_store.resolve_owner_contact_info",
                new_callable=AsyncMock,
                return_value="rtoken",
            ),
        ):
            await mod.on_startup(
                {"provider": "google"},
                db=db,
                credential_store=credential_store,
            )
            mock_start.assert_awaited_once()

        runtime = getattr(mod, "_runtime")
        assert isinstance(runtime, ContactsSyncRuntime)

    async def test_startup_missing_credentials_raises_actionable_error(self) -> None:
        """Missing credentials produce an actionable RuntimeError message."""
        mod = ContactsModule()
        store = MagicMock()
        store.resolve = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError) as excinfo:
            await mod.on_startup({"provider": "google"}, db=None, credential_store=store)

        msg = str(excinfo.value)
        assert "GOOGLE_OAUTH_CLIENT_ID" in msg
        assert "GOOGLE_OAUTH_CLIENT_SECRET" in msg
        assert "refresh token" in msg
        assert "contact_info" in msg

    async def test_startup_no_credential_store_raises_actionable_error(self) -> None:
        """Absent credential_store (None) raises a clear RuntimeError."""
        mod = ContactsModule()
        with pytest.raises(RuntimeError) as excinfo:
            await mod.on_startup({"provider": "google"}, db=None, credential_store=None)

        msg = str(excinfo.value)
        assert "shared credential store" in msg
        assert "GOOGLE_OAUTH_CLIENT_ID" in msg

    async def test_startup_config_stored_on_module(self) -> None:
        """on_startup() sets _config regardless of sync.enabled."""
        mod = ContactsModule()
        await mod.on_startup({"provider": "google", "sync": {"enabled": False}}, db=None)
        config = getattr(mod, "_config")
        assert isinstance(config, ContactsConfig)
        assert config.provider == "google"

    async def test_register_tools_accepts_validated_config(self) -> None:
        mod = ContactsModule()
        cfg = ContactsConfig(provider="google")
        await mod.register_tools(mcp=_StubMCP(), config=cfg, db=None)
        assert isinstance(getattr(mod, "_config"), ContactsConfig)

    async def test_register_tools_accepts_dict_config(self) -> None:
        mod = ContactsModule()
        await mod.register_tools(mcp=_StubMCP(), config={"provider": "google"}, db=None)
        stored = getattr(mod, "_config")
        assert isinstance(stored, ContactsConfig)
        assert stored.provider == "google"


class TestModuleShutdown:
    """Verify shutdown lifecycle stops runtime and releases provider."""

    async def test_shutdown_stops_runtime(self) -> None:
        """on_shutdown() calls runtime.stop()."""
        mod = ContactsModule()
        credential_store = _make_credential_store()
        db = _make_db_with_pool()

        with (
            patch.object(ContactsSyncRuntime, "start", new_callable=AsyncMock),
            patch(
                "butlers.credential_store.resolve_owner_contact_info",
                new_callable=AsyncMock,
                return_value="rtoken",
            ),
        ):
            await mod.on_startup(
                {"provider": "google"},
                db=db,
                credential_store=credential_store,
            )

        runtime = getattr(mod, "_runtime")
        assert runtime is not None

        with patch.object(runtime, "stop", new_callable=AsyncMock) as mock_stop:
            await mod.on_shutdown()
            mock_stop.assert_awaited_once()

        assert getattr(mod, "_runtime") is None

    async def test_shutdown_releases_provider(self) -> None:
        """on_shutdown() calls provider.shutdown()."""
        mod = ContactsModule()
        credential_store = _make_credential_store()
        db = _make_db_with_pool()

        with (
            patch.object(ContactsSyncRuntime, "start", new_callable=AsyncMock),
            patch(
                "butlers.credential_store.resolve_owner_contact_info",
                new_callable=AsyncMock,
                return_value="rtoken",
            ),
        ):
            await mod.on_startup(
                {"provider": "google"},
                db=db,
                credential_store=credential_store,
            )

        provider = getattr(mod, "_provider")
        assert provider is not None

        with (
            patch.object(ContactsSyncRuntime, "stop", new_callable=AsyncMock),
            patch.object(provider, "shutdown", new_callable=AsyncMock) as mock_shutdown,
        ):
            await mod.on_shutdown()
            mock_shutdown.assert_awaited_once()

        assert getattr(mod, "_provider") is None

    async def test_shutdown_clears_config_and_db(self) -> None:
        """on_shutdown() clears _config and _db references."""
        mod = ContactsModule()
        await mod.on_startup({"provider": "google", "sync": {"enabled": False}}, db=object())

        await mod.on_shutdown()

        assert getattr(mod, "_config") is None
        assert getattr(mod, "_db") is None

    async def test_shutdown_noop_when_not_started(self) -> None:
        """on_shutdown() is safe to call without a prior on_startup()."""
        mod = ContactsModule()
        await mod.on_shutdown()  # Must not raise
        assert getattr(mod, "_runtime") is None
        assert getattr(mod, "_provider") is None

    async def test_shutdown_noop_when_sync_disabled(self) -> None:
        """on_shutdown() is safe when sync was disabled and runtime was never created."""
        mod = ContactsModule()
        await mod.on_startup({"provider": "google", "sync": {"enabled": False}}, db=None)
        await mod.on_shutdown()  # Must not raise


class TestRuntimeAccessibility:
    """Verify that _runtime is accessible for MCP tool registration."""

    async def test_runtime_accessible_after_startup(self) -> None:
        """After on_startup(), _runtime is a ContactsSyncRuntime instance."""
        mod = ContactsModule()
        credential_store = _make_credential_store()
        db = _make_db_with_pool()

        with (
            patch.object(ContactsSyncRuntime, "start", new_callable=AsyncMock),
            patch(
                "butlers.credential_store.resolve_owner_contact_info",
                new_callable=AsyncMock,
                return_value="rtoken",
            ),
        ):
            await mod.on_startup(
                {"provider": "google"},
                db=db,
                credential_store=credential_store,
            )

        runtime = getattr(mod, "_runtime")
        assert isinstance(runtime, ContactsSyncRuntime)

    async def test_runtime_is_none_after_shutdown(self) -> None:
        """After on_shutdown(), _runtime is None."""
        mod = ContactsModule()
        credential_store = _make_credential_store()
        db = _make_db_with_pool()

        with (
            patch.object(ContactsSyncRuntime, "start", new_callable=AsyncMock),
            patch(
                "butlers.credential_store.resolve_owner_contact_info",
                new_callable=AsyncMock,
                return_value="rtoken",
            ),
        ):
            await mod.on_startup(
                {"provider": "google"},
                db=db,
                credential_store=credential_store,
            )

        with patch.object(ContactsSyncRuntime, "stop", new_callable=AsyncMock):
            await mod.on_shutdown()

        assert getattr(mod, "_runtime") is None

    def test_runtime_none_before_startup(self) -> None:
        """Before on_startup(), _runtime is None."""
        mod = ContactsModule()
        assert getattr(mod, "_runtime") is None


class _StubMCP:
    """Minimal MCP stub that supports the .tool() decorator pattern."""

    def tool(self) -> Any:
        """Return a no-op decorator."""

        def decorator(fn: Any) -> Any:
            return fn

        return decorator

    def __getattr__(self, _name: str) -> Any:
        raise AttributeError


# ---------------------------------------------------------------------------
# MCP tool tests
# ---------------------------------------------------------------------------


class _CapturingMCP:
    """Minimal MCP stub that captures registered tools for testing."""

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def tool(self) -> Any:
        """Decorator that captures the tool function by name."""

        def decorator(fn: Any) -> Any:
            self._tools[fn.__name__] = fn
            return fn

        return decorator

    def __getitem__(self, name: str) -> Any:
        return self._tools[name]


def _make_runtime_mock(
    *,
    provider_name: str = "google",
    account_id: str = "default",
    state: ContactsSyncState | None = None,
) -> Any:
    """Build a mock ContactsSyncRuntime with configurable state."""
    from butlers.modules.contacts.sync import ContactsSyncState

    resolved_state = state or ContactsSyncState()
    runtime = MagicMock()
    runtime._provider_name = provider_name
    runtime._account_id = account_id
    runtime._state_store = MagicMock()
    runtime._state_store.load = AsyncMock(return_value=resolved_state)
    runtime._sync_engine = MagicMock()
    runtime.trigger_immediate_sync = MagicMock()
    return runtime


class TestContactsSyncNowTool:
    """Unit tests for the contacts_sync_now MCP tool."""

    async def test_sync_now_returns_error_when_runtime_none(self) -> None:
        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        result = await mcp["contacts_sync_now"](provider="google", mode="incremental")

        assert "error" in result
        assert "sync runtime is not running" in result["error"].lower()

    async def test_sync_now_returns_error_for_wrong_provider(self) -> None:
        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        # Simulate runtime being active
        mod._runtime = _make_runtime_mock(provider_name="google")

        result = await mcp["contacts_sync_now"](provider="outlook", mode="incremental")

        assert "error" in result
        assert "outlook" in result["error"]

    async def test_sync_now_calls_sync_engine_and_returns_summary(self) -> None:

        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        sync_result = ContactsSyncResult(
            mode="incremental",
            fetched_contacts=50,
            applied_contacts=10,
            skipped_contacts=40,
            deleted_contacts=0,
            next_sync_cursor="cursor-abc",
        )
        runtime = _make_runtime_mock(provider_name="google")
        runtime._sync_engine.sync = AsyncMock(return_value=sync_result)
        mod._runtime = runtime

        result = await mcp["contacts_sync_now"](provider="google", mode="incremental")

        assert result["provider"] == "google"
        assert result["mode"] == "incremental"
        assert result["summary"]["fetched"] == 50
        assert result["summary"]["applied"] == 10
        assert result["summary"]["skipped"] == 40
        assert result["summary"]["deleted"] == 0
        assert result["next_sync_cursor"] == "cursor-abc"

    async def test_sync_now_handles_sync_error(self) -> None:

        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        runtime = _make_runtime_mock(provider_name="google")
        runtime._sync_engine.sync = AsyncMock(side_effect=ContactsSyncError("token expired"))
        mod._runtime = runtime

        result = await mcp["contacts_sync_now"](provider="google", mode="incremental")

        assert "error" in result
        assert "token expired" in result["error"]

    async def test_sync_now_full_mode_passed_through(self) -> None:

        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        sync_result = ContactsSyncResult(
            mode="full",
            fetched_contacts=200,
            applied_contacts=200,
            skipped_contacts=0,
            deleted_contacts=0,
            next_sync_cursor="cursor-full",
        )
        runtime = _make_runtime_mock(provider_name="google")
        runtime._sync_engine.sync = AsyncMock(return_value=sync_result)
        mod._runtime = runtime

        result = await mcp["contacts_sync_now"](provider="google", mode="full")

        runtime._sync_engine.sync.assert_awaited_once_with(account_id="default", mode="full")
        assert result["mode"] == "full"
        assert result["summary"]["fetched"] == 200


class TestContactsSyncStatusTool:
    """Unit tests for the contacts_sync_status MCP tool."""

    async def test_sync_status_returns_error_when_runtime_none(self) -> None:
        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        result = await mcp["contacts_sync_status"](provider="google")

        assert "error" in result
        assert result["sync_enabled"] is False

    async def test_sync_status_returns_state_snapshot(self) -> None:
        from butlers.modules.contacts.sync import ContactsSyncState

        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        state = ContactsSyncState(
            sync_cursor="cursor-xyz",
            last_full_sync_at="2026-01-01T00:00:00+00:00",
            last_incremental_sync_at="2026-01-02T00:00:00+00:00",
            last_success_at="2026-01-02T00:00:00+00:00",
            last_error=None,
            contact_versions={"c1": "v1", "c2": "v2"},
        )
        mod._runtime = _make_runtime_mock(provider_name="google", state=state)

        result = await mcp["contacts_sync_status"](provider="google")

        assert result["provider"] == "google"
        assert result["sync_enabled"] is True
        assert result["sync_cursor"] is True
        assert result["last_full_sync_at"] == "2026-01-01T00:00:00+00:00"
        assert result["last_incremental_sync_at"] == "2026-01-02T00:00:00+00:00"
        assert result["last_error"] is None
        assert result["contact_count"] == 2

    async def test_sync_status_reports_last_error(self) -> None:
        from butlers.modules.contacts.sync import ContactsSyncState

        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        state = ContactsSyncState(last_error="401 Unauthorized")
        mod._runtime = _make_runtime_mock(state=state)

        result = await mcp["contacts_sync_status"](provider="google")

        assert result["last_error"] == "401 Unauthorized"

    async def test_sync_status_zero_contacts_when_no_versions(self) -> None:
        from butlers.modules.contacts.sync import ContactsSyncState

        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        state = ContactsSyncState()  # empty state
        mod._runtime = _make_runtime_mock(state=state)

        result = await mcp["contacts_sync_status"](provider="google")

        assert result["contact_count"] == 0
        assert result["sync_cursor"] is False

    async def test_sync_status_returns_error_for_wrong_provider(self) -> None:
        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        # Simulate runtime being active with google provider
        state = ContactsSyncState(last_success_at="2026-01-01T00:00:00+00:00")
        mod._runtime = _make_runtime_mock(provider_name="google", state=state)

        result = await mcp["contacts_sync_status"](provider="outlook")

        assert "error" in result
        assert "outlook" in result["error"]
        assert "google" in result["error"]


class TestContactsSourceListTool:
    """Unit tests for the contacts_source_list MCP tool."""

    async def test_source_list_returns_disabled_when_runtime_none(self) -> None:
        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        result = await mcp["contacts_source_list"]()

        assert len(result) == 1
        assert result[0]["sync_enabled"] is False
        assert result[0]["status"] == "sync_disabled"
        assert result[0]["provider"] == "google"

    async def test_source_list_returns_active_source(self) -> None:
        from butlers.modules.contacts.sync import ContactsSyncState

        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        state = ContactsSyncState(
            last_success_at="2026-01-01T00:00:00+00:00",
            last_error=None,
        )
        mod._runtime = _make_runtime_mock(provider_name="google", account_id="default", state=state)

        result = await mcp["contacts_source_list"]()

        assert len(result) == 1
        source = result[0]
        assert source["provider"] == "google"
        assert source["account_id"] == "default"
        assert source["sync_enabled"] is True
        assert source["status"] == "active"
        assert source["last_success_at"] == "2026-01-01T00:00:00+00:00"

    async def test_source_list_filters_by_provider(self) -> None:
        from butlers.modules.contacts.sync import ContactsSyncState

        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        state = ContactsSyncState(last_success_at="2026-01-01T00:00:00+00:00")
        mod._runtime = _make_runtime_mock(provider_name="google", state=state)

        # Filtering by the configured provider returns the source.
        result_google = await mcp["contacts_source_list"](provider="google")
        assert len(result_google) == 1

        # Filtering by a different provider returns nothing.
        result_outlook = await mcp["contacts_source_list"](provider="outlook")
        assert result_outlook == []

    async def test_source_list_never_synced_status(self) -> None:
        from butlers.modules.contacts.sync import ContactsSyncState

        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        state = ContactsSyncState()  # no last_success_at
        mod._runtime = _make_runtime_mock(state=state)

        result = await mcp["contacts_source_list"]()

        assert result[0]["status"] == "never_synced"

    async def test_source_list_error_status_on_last_error(self) -> None:
        from butlers.modules.contacts.sync import ContactsSyncState

        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        state = ContactsSyncState(
            last_success_at="2026-01-01T00:00:00+00:00",
            last_error="Connection refused",
        )
        mod._runtime = _make_runtime_mock(state=state)

        result = await mcp["contacts_source_list"]()

        assert result[0]["status"] == "error"
        assert result[0]["last_error"] == "Connection refused"


class TestContactsSourceReconcileTool:
    """Unit tests for the contacts_source_reconcile MCP tool."""

    async def test_reconcile_returns_error_when_runtime_none(self) -> None:
        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        result = await mcp["contacts_source_reconcile"]()

        assert "error" in result
        assert result["queued"] is False

    async def test_reconcile_triggers_immediate_sync_all(self) -> None:
        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        runtime = _make_runtime_mock()
        mod._runtime = runtime

        result = await mcp["contacts_source_reconcile"]()

        runtime.trigger_immediate_sync.assert_called_once()
        assert result["queued"] is True
        assert result["contact_id"] is None
        assert "Reconciliation queued" in result["message"]

    async def test_reconcile_triggers_immediate_sync_specific_contact(self) -> None:
        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        runtime = _make_runtime_mock()
        mod._runtime = runtime

        result = await mcp["contacts_source_reconcile"](contact_id="abc-123")

        runtime.trigger_immediate_sync.assert_called_once()
        assert result["queued"] is True
        assert result["contact_id"] == "abc-123"
        assert "abc-123" in result["message"]

    async def test_reconcile_message_differs_for_all_vs_specific(self) -> None:
        mod = ContactsModule()
        mcp = _CapturingMCP()
        await mod.register_tools(mcp=mcp, config={"provider": "google"}, db=None)

        runtime = _make_runtime_mock()
        mod._runtime = runtime

        result_all = await mcp["contacts_source_reconcile"]()
        runtime.trigger_immediate_sync.reset_mock()
        result_specific = await mcp["contacts_source_reconcile"](contact_id="xyz")

        # Both succeed; messages differ
        assert result_all["queued"] is True
        assert result_specific["queued"] is True
        assert result_all["message"] != result_specific["message"]
