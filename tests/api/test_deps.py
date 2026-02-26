"""Tests for MCP client manager and butler discovery dependencies."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    discover_butlers,
    get_butler_configs,
    get_mcp_manager,
    init_db_manager,
    init_dependencies,
    shutdown_dependencies,
)

pytestmark = pytest.mark.unit


def _make_mock_client(*, connected: bool = True) -> MagicMock:
    """Create a mock MCP client with sync is_connected and async enter/exit."""
    client = MagicMock()
    client.is_connected = MagicMock(return_value=connected)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.call_tool = AsyncMock()
    client.list_tools = AsyncMock(return_value=[])
    client.ping = AsyncMock(return_value=True)
    return client


# ---------------------------------------------------------------------------
# ButlerConnectionInfo
# ---------------------------------------------------------------------------


class TestButlerConnectionInfo:
    def test_sse_url(self):
        info = ButlerConnectionInfo(name="switchboard", port=40100)
        assert info.sse_url == "http://localhost:40100/sse"

    def test_sse_url_custom_port(self):
        info = ButlerConnectionInfo(name="general", port=9999)
        assert info.sse_url == "http://localhost:9999/sse"

    def test_description_optional(self):
        info = ButlerConnectionInfo(name="test", port=8000)
        assert info.description is None

    def test_frozen(self):
        info = ButlerConnectionInfo(name="test", port=8000)
        with pytest.raises(AttributeError):
            info.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MCPClientManager — registration
# ---------------------------------------------------------------------------


class TestMCPClientManagerRegistration:
    def test_register_and_list_names(self):
        mgr = MCPClientManager()
        mgr.register("alpha", ButlerConnectionInfo("alpha", 40100))
        mgr.register("beta", ButlerConnectionInfo("beta", 40101))
        assert mgr.butler_names == ["alpha", "beta"]

    def test_register_overwrites(self, caplog: pytest.LogCaptureFixture):
        mgr = MCPClientManager()
        info1 = ButlerConnectionInfo("alpha", 40100)
        info2 = ButlerConnectionInfo("alpha", 9999)
        mgr.register("alpha", info1)
        with caplog.at_level(logging.WARNING, logger="butlers.api.deps"):
            mgr.register("alpha", info2)
        assert "already registered" in caplog.text
        assert mgr.get_connection_info("alpha") is info2

    def test_get_connection_info_returns_none_for_unknown(self):
        mgr = MCPClientManager()
        assert mgr.get_connection_info("missing") is None

    def test_get_connection_info_returns_registered(self):
        mgr = MCPClientManager()
        info = ButlerConnectionInfo("test", 8000)
        mgr.register("test", info)
        assert mgr.get_connection_info("test") is info


# ---------------------------------------------------------------------------
# MCPClientManager — get_client
# ---------------------------------------------------------------------------


class TestMCPClientManagerGetClient:
    async def test_get_client_unregistered_raises(self):
        mgr = MCPClientManager()
        with pytest.raises(ButlerUnreachableError, match="not registered"):
            await mgr.get_client("ghost")

    @patch("butlers.api.deps.MCPClient")
    async def test_get_client_creates_and_caches(self, mock_client_cls: MagicMock):
        """First call creates client; second call returns cached."""
        mock_client = _make_mock_client(connected=True)
        mock_client_cls.return_value = mock_client

        mgr = MCPClientManager()
        mgr.register("sb", ButlerConnectionInfo("sb", 40100))

        # First call — creates
        client1 = await mgr.get_client("sb")
        assert client1 is mock_client
        mock_client_cls.assert_called_once_with("http://localhost:40100/sse", name="dashboard-sb")
        mock_client.__aenter__.assert_called_once()

        # Second call — returns cached
        client2 = await mgr.get_client("sb")
        assert client2 is client1
        assert mock_client.__aenter__.call_count == 1  # not called again

    @patch("butlers.api.deps.MCPClient")
    async def test_get_client_reconnects_on_disconnect(self, mock_client_cls: MagicMock):
        """If cached client reports disconnected, reconnect."""
        old_client = _make_mock_client(connected=True)
        new_client = _make_mock_client(connected=True)
        mock_client_cls.side_effect = [old_client, new_client]

        mgr = MCPClientManager()
        mgr.register("sb", ButlerConnectionInfo("sb", 40100))

        # First call — old client
        c1 = await mgr.get_client("sb")
        assert c1 is old_client

        # Mark old client as disconnected
        old_client.is_connected.return_value = False

        # Second call — should reconnect
        c2 = await mgr.get_client("sb")
        assert c2 is new_client
        # Old client should have been closed
        old_client.__aexit__.assert_called_once()

    @patch("butlers.api.deps.MCPClient")
    async def test_get_client_connection_failure_raises(self, mock_client_cls: MagicMock):
        """Connection error wrapped in ButlerUnreachableError."""
        mock_client = _make_mock_client()
        mock_client.__aenter__ = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        mock_client_cls.return_value = mock_client

        mgr = MCPClientManager()
        mgr.register("sb", ButlerConnectionInfo("sb", 40100))

        with pytest.raises(ButlerUnreachableError) as exc_info:
            await mgr.get_client("sb")

        assert exc_info.value.butler_name == "sb"
        assert isinstance(exc_info.value.cause, ConnectionRefusedError)


# ---------------------------------------------------------------------------
# MCPClientManager — close
# ---------------------------------------------------------------------------


class TestMCPClientManagerClose:
    @patch("butlers.api.deps.MCPClient")
    async def test_close_all_clients(self, mock_client_cls: MagicMock):
        client_a = _make_mock_client()
        client_b = _make_mock_client()
        mock_client_cls.side_effect = [client_a, client_b]

        mgr = MCPClientManager()
        mgr.register("a", ButlerConnectionInfo("a", 40100))
        mgr.register("b", ButlerConnectionInfo("b", 40101))

        await mgr.get_client("a")
        await mgr.get_client("b")
        await mgr.close()

        client_a.__aexit__.assert_called_once()
        client_b.__aexit__.assert_called_once()

    @patch("butlers.api.deps.MCPClient")
    async def test_close_handles_exit_error(
        self, mock_client_cls: MagicMock, caplog: pytest.LogCaptureFixture
    ):
        """close() handles errors from individual client cleanup."""
        mock_client = _make_mock_client()
        mock_client.__aexit__ = AsyncMock(side_effect=RuntimeError("boom"))
        mock_client_cls.return_value = mock_client

        mgr = MCPClientManager()
        mgr.register("bad", ButlerConnectionInfo("bad", 40100))
        await mgr.get_client("bad")

        with caplog.at_level(logging.WARNING, logger="butlers.api.deps"):
            await mgr.close()  # should not raise

        assert "Error closing client for butler: bad" in caplog.text


# ---------------------------------------------------------------------------
# discover_butlers
# ---------------------------------------------------------------------------


class TestDiscoverButlers:
    def test_discover_from_roster(self, tmp_path: Path):
        """Discovers butlers from directories containing butler.toml."""
        sb_dir = tmp_path / "switchboard"
        sb_dir.mkdir()
        (sb_dir / "butler.toml").write_text(
            '[butler]\nname = "switchboard"\nport = 40100\n'
            'description = "Routes messages"\n'
            '[runtime]\ntype = "claude-code"\n'
        )
        gen_dir = tmp_path / "general"
        gen_dir.mkdir()
        (gen_dir / "butler.toml").write_text(
            '[butler]\nname = "general"\nport = 40101\n[runtime]\ntype = "claude-code"\n'
        )
        # Create a non-butler dir (no toml)
        (tmp_path / "random-dir").mkdir()

        result = discover_butlers(roster_dir=tmp_path)
        assert len(result) == 2
        names = [b.name for b in result]
        assert "general" in names
        assert "switchboard" in names

    def test_discover_nonexistent_dir(self, caplog: pytest.LogCaptureFixture):
        """Returns empty list for missing roster dir."""
        with caplog.at_level(logging.WARNING, logger="butlers.api.deps"):
            result = discover_butlers(roster_dir=Path("/nonexistent"))
        assert result == []
        assert "Roster directory not found" in caplog.text

    def test_discover_skips_invalid_toml(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """Invalid butler.toml is skipped with a warning."""
        bad_dir = tmp_path / "broken"
        bad_dir.mkdir()
        (bad_dir / "butler.toml").write_text("this is not valid toml {{{{")

        with caplog.at_level(logging.WARNING, logger="butlers.api.deps"):
            result = discover_butlers(roster_dir=tmp_path)
        assert result == []
        assert "Skipping butler" in caplog.text

    def test_discover_skips_files_in_roster(self, tmp_path: Path):
        """Non-directory entries in roster are ignored."""
        (tmp_path / "README.md").write_text("# Roster")
        result = discover_butlers(roster_dir=tmp_path)
        assert result == []

    def test_discover_sorted_by_name(self, tmp_path: Path):
        """Results are sorted alphabetically by directory name."""
        for name, port in [("zebra", 40200), ("alpha", 40201)]:
            d = tmp_path / name
            d.mkdir()
            (d / "butler.toml").write_text(
                f'[butler]\nname = "{name}"\nport = {port}\n[runtime]\ntype = "claude-code"\n'
            )

        result = discover_butlers(roster_dir=tmp_path)
        assert [b.name for b in result] == ["alpha", "zebra"]

    def test_discover_includes_description(self, tmp_path: Path):
        """Description from butler.toml is included in connection info."""
        d = tmp_path / "mybutler"
        d.mkdir()
        (d / "butler.toml").write_text(
            '[butler]\nname = "mybutler"\nport = 40100\n'
            'description = "My awesome butler"\n'
            '[runtime]\ntype = "claude-code"\n'
        )

        result = discover_butlers(roster_dir=tmp_path)
        assert len(result) == 1
        assert result[0].description == "My awesome butler"

    def test_discover_includes_db_schema(self, tmp_path: Path):
        """Schema-aware DB config is surfaced in connection info."""
        d = tmp_path / "general"
        d.mkdir()
        (d / "butler.toml").write_text(
            '[butler]\nname = "general"\nport = 40101\n'
            '[butler.db]\nname = "butlers"\nschema = "general"\n'
            '[runtime]\ntype = "claude-code"\n'
        )

        result = discover_butlers(roster_dir=tmp_path)

        assert len(result) == 1
        assert result[0].db_name == "butlers"
        assert result[0].db_schema == "general"

    def test_discover_includes_modules(self, tmp_path: Path):
        """Module names from butler.toml are surfaced in ButlerConnectionInfo.modules."""
        d = tmp_path / "general"
        d.mkdir()
        (d / "butler.toml").write_text(
            '[butler]\nname = "general"\nport = 40101\n'
            '[butler.db]\nname = "butlers"\nschema = "general"\n'
            '[runtime]\ntype = "claude-code"\n'
            '[modules.calendar]\nprovider = "google"\n'
            "[modules.memory]\n"
        )

        result = discover_butlers(roster_dir=tmp_path)

        assert len(result) == 1
        assert result[0].modules == frozenset({"calendar", "memory"})

    def test_discover_modules_empty_when_none_configured(self, tmp_path: Path):
        """Butlers with no [modules.*] sections have an empty modules frozenset."""
        d = tmp_path / "education"
        d.mkdir()
        (d / "butler.toml").write_text(
            '[butler]\nname = "education"\nport = 40107\n'
            '[butler.db]\nname = "butlers"\nschema = "education"\n'
            '[runtime]\ntype = "claude-code"\n'
        )

        result = discover_butlers(roster_dir=tmp_path)

        assert len(result) == 1
        assert result[0].modules == frozenset()


class TestInitDbManager:
    async def test_one_db_topology_uses_shared_schema_pool(self, monkeypatch: pytest.MonkeyPatch):
        """One-db configs wire shared credentials to db=butlers schema=shared."""
        import butlers.api.deps as deps_mod

        monkeypatch.delenv("BUTLER_SHARED_DB_NAME", raising=False)

        configs = [
            ButlerConnectionInfo(
                name="general", port=40101, db_name="butlers", db_schema="general"
            ),
            ButlerConnectionInfo(
                name="switchboard",
                port=40100,
                db_name="butlers",
                db_schema="switchboard",
            ),
        ]

        mgr = MagicMock()
        mgr.add_butler = AsyncMock()
        mgr.set_credential_shared_pool = AsyncMock()
        shared_pool = AsyncMock()
        mgr.credential_shared_pool = MagicMock(return_value=shared_pool)

        def _mk_db(db_name: str) -> MagicMock:
            db = MagicMock()
            db.db_name = db_name
            db.set_schema = MagicMock()
            db.provision = AsyncMock()
            db.connect = AsyncMock(return_value=AsyncMock())
            return db

        original_db_manager = deps_mod._db_manager
        try:
            with (
                patch("butlers.api.deps.DatabaseManager", return_value=mgr),
                patch("butlers.api.deps.Database.from_env", side_effect=_mk_db),
                patch("butlers.api.deps.ensure_secrets_schema", new_callable=AsyncMock),
            ):
                await init_db_manager(configs)
        finally:
            deps_mod._db_manager = original_db_manager

        mgr.add_butler.assert_any_await(
            "general", db_name="butlers", db_schema="general", modules=None
        )
        mgr.add_butler.assert_any_await(
            "switchboard", db_name="butlers", db_schema="switchboard", modules=None
        )
        mgr.set_credential_shared_pool.assert_awaited_once_with("butlers", db_schema="shared")


# ---------------------------------------------------------------------------
# FastAPI dependency functions
# ---------------------------------------------------------------------------


class TestFastAPIDependencies:
    async def test_get_mcp_manager_before_init_raises(self):
        """Calling get_mcp_manager() before init raises RuntimeError."""
        import butlers.api.deps as deps_mod

        original_mgr = deps_mod._mcp_manager
        deps_mod._mcp_manager = None
        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                get_mcp_manager()
        finally:
            deps_mod._mcp_manager = original_mgr

    async def test_get_butler_configs_before_init_raises(self):
        """Calling get_butler_configs() before init raises RuntimeError."""
        import butlers.api.deps as deps_mod

        original_configs = deps_mod._butler_configs
        deps_mod._butler_configs = None
        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                get_butler_configs()
        finally:
            deps_mod._butler_configs = original_configs

    async def test_init_and_get(self, tmp_path: Path):
        """init_dependencies wires up the manager and configs."""
        import butlers.api.deps as deps_mod

        orig_mgr = deps_mod._mcp_manager
        orig_cfg = deps_mod._butler_configs

        try:
            d = tmp_path / "test-butler"
            d.mkdir()
            (d / "butler.toml").write_text(
                '[butler]\nname = "test-butler"\nport = 8500\n[runtime]\ntype = "claude-code"\n'
            )

            mgr, configs = init_dependencies(roster_dir=tmp_path)
            assert isinstance(mgr, MCPClientManager)
            assert len(configs) == 1
            assert configs[0].name == "test-butler"
            assert configs[0].port == 8500

            # Dependency functions should now work
            assert get_mcp_manager() is mgr
            assert get_butler_configs() is configs

            # Manager should have the butler registered
            assert "test-butler" in mgr.butler_names
        finally:
            deps_mod._mcp_manager = orig_mgr
            deps_mod._butler_configs = orig_cfg

    async def test_shutdown_cleans_up(self, tmp_path: Path):
        """shutdown_dependencies clears the singletons."""
        import butlers.api.deps as deps_mod

        orig_mgr = deps_mod._mcp_manager
        orig_cfg = deps_mod._butler_configs

        try:
            d = tmp_path / "sb"
            d.mkdir()
            (d / "butler.toml").write_text(
                '[butler]\nname = "sb"\nport = 40100\n[runtime]\ntype = "claude-code"\n'
            )
            init_dependencies(roster_dir=tmp_path)

            await shutdown_dependencies()

            assert deps_mod._mcp_manager is None
            assert deps_mod._butler_configs is None
        finally:
            deps_mod._mcp_manager = orig_mgr
            deps_mod._butler_configs = orig_cfg


# ---------------------------------------------------------------------------
# ButlerUnreachableError
# ---------------------------------------------------------------------------


class TestButlerUnreachableError:
    def test_message_without_cause(self):
        err = ButlerUnreachableError("test")
        assert "test" in str(err)
        assert err.butler_name == "test"
        assert err.cause is None

    def test_message_with_cause(self):
        cause = ConnectionRefusedError("refused")
        err = ButlerUnreachableError("test", cause=cause)
        assert "refused" in str(err)
        assert err.cause is cause
