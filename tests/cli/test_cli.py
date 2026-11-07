"""Tests for the CLI commands."""

import logging

import pytest
from click.testing import CliRunner

from butlers.cli import _configure_logging, _discover_configs, _migrate_all, cli

pytestmark = pytest.mark.unit


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def butler_config_dir(tmp_path):
    """Create a temporary butler config directory with one butler."""
    butler_dir = tmp_path / "butlers" / "test_butler"
    butler_dir.mkdir(parents=True)
    (butler_dir / "butler.toml").write_text(
        '[butler]\nname = "test_butler"\nport = 9000\ndescription = "A test butler"\n'
    )
    return tmp_path / "butlers"


@pytest.fixture
def multi_butler_dir(tmp_path):
    """Create a temporary directory with multiple butler configs."""
    base = tmp_path / "butlers"
    for name, port in [("alpha", 9001), ("beta", 9002), ("gamma", 9003)]:
        d = base / name
        d.mkdir(parents=True)
        (d / "butler.toml").write_text(
            f'[butler]\nname = "{name}"\nport = {port}\ndescription = "{name} butler"\n'
        )
    return base


class TestVersion:
    def test_version_flag(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestLoggingConfiguration:
    def test_configure_logging_suppresses_http_client_request_logs(self):
        httpx_logger = logging.getLogger("httpx")
        httpcore_logger = logging.getLogger("httpcore")
        original_httpx_level = httpx_logger.level
        original_httpcore_level = httpcore_logger.level
        original_root_handlers = logging.getLogger().handlers[:]
        httpx_logger.setLevel(logging.NOTSET)
        httpcore_logger.setLevel(logging.NOTSET)

        try:
            _configure_logging()

            root = logging.getLogger()
            assert len(root.handlers) >= 1
            assert httpx_logger.level == logging.WARNING
            assert httpcore_logger.level == logging.WARNING
        finally:
            httpx_logger.setLevel(original_httpx_level)
            httpcore_logger.setLevel(original_httpcore_level)
            logging.getLogger().handlers[:] = original_root_handlers


class TestListCommand:
    def test_list_with_configs_and_modules(self, runner, butler_config_dir, tmp_path):
        """list shows butler info and module names when present."""
        # Basic config test
        result = runner.invoke(cli, ["list", "--dir", str(butler_config_dir)])
        assert result.exit_code == 0
        assert "test_butler" in result.output
        assert "9000" in result.output

        # No configs
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result2 = runner.invoke(cli, ["list", "--dir", str(empty_dir)])
        assert result2.exit_code == 0
        assert "No butler configs found" in result2.output

    def test_list_shows_modules(self, runner, tmp_path):
        butler_dir = tmp_path / "butlers" / "modular"
        butler_dir.mkdir(parents=True)
        (butler_dir / "butler.toml").write_text(
            '[butler]\nname = "modular"\nport = 9010\n\n'
            "[modules.email]\n"
            "[modules.email.user]\nenabled = false\n"
            '[modules.email.bot]\naddress_env = "BUTLER_EMAIL_ADDRESS"\n'
            'password_env = "BUTLER_EMAIL_PASSWORD"\n\n'
            "[modules.telegram]\n"
            "[modules.telegram.user]\nenabled = false\n"
            '[modules.telegram.bot]\ntoken_env = "BUTLER_TELEGRAM_TOKEN"\n'
        )
        result = runner.invoke(cli, ["list", "--dir", str(tmp_path / "butlers")])
        assert result.exit_code == 0
        assert "email" in result.output
        assert "telegram" in result.output


class TestInitCommand:
    def test_init_creates_scaffold_and_content(self, runner, tmp_path):
        """init creates all required files with correct content."""
        butlers_dir = tmp_path / "butlers"
        result = runner.invoke(cli, ["init", "mybot", "--port", "9100", "--dir", str(butlers_dir)])
        assert result.exit_code == 0
        assert "Created butler scaffold" in result.output

        butler_dir = butlers_dir / "mybot"
        assert butler_dir.is_dir()
        assert (butler_dir / "butler.toml").exists()
        assert (butler_dir / "CLAUDE.md").exists()
        assert (butler_dir / "AGENTS.md").exists()
        assert (butler_dir / ".agents" / "skills").is_dir()
        assert (butler_dir / ".claude").is_symlink()
        assert (butler_dir / ".claude").resolve() == (butler_dir / ".agents").resolve()

        toml_text = (butler_dir / "butler.toml").read_text()
        assert 'name = "mybot"' in toml_text
        assert "port = 9100" in toml_text

        claude_md = (butler_dir / "CLAUDE.md").read_text()
        assert "mybot" in claude_md.lower() or "Mybot" in claude_md

    def test_init_default_port_and_existing_dir_fails(self, runner, tmp_path):
        """Default port is 41100; existing directory raises an error."""
        butlers_dir = tmp_path / "butlers"
        result = runner.invoke(cli, ["init", "mybot", "--dir", str(butlers_dir)])
        assert result.exit_code == 0
        assert "port = 41100" in (butlers_dir / "mybot" / "butler.toml").read_text()

        # Existing dir fails
        result2 = runner.invoke(cli, ["init", "mybot", "--port", "9100", "--dir", str(butlers_dir)])
        assert result2.exit_code != 0
        assert "Directory already exists" in result2.output


class TestDiscoverConfigs:
    def test_discovers_and_skips_invalid(self, butler_config_dir, tmp_path):
        """Finds valid configs; returns {} for missing dir; skips dirs without/invalid toml."""
        configs = _discover_configs(butler_config_dir)
        assert "test_butler" in configs

        # Missing dir
        assert _discover_configs(tmp_path / "nonexistent") == {}

        # No toml / invalid toml
        base = tmp_path / "butlers"
        (base / "no_toml").mkdir(parents=True)
        bad = base / "bad_butler"
        bad.mkdir(parents=True)
        (bad / "butler.toml").write_text("not valid [[[ toml content")
        configs2 = _discover_configs(base)
        assert configs2 == {"test_butler": base / "test_butler"}


class TestMigrateCommand:
    @pytest.mark.asyncio
    async def test_migrate_all_routes_memory_schema_override_to_module_chain(
        self, tmp_path, monkeypatch
    ):
        butler_dir = tmp_path / "butlers" / "chronicler"
        butler_dir.mkdir(parents=True)
        (butler_dir / "butler.toml").write_text(
            """
[butler]
name = "chronicler"
port = 41111
description = "Retrospective time butler"

[butler.db]
name = "butlers"
schema = "chronicler"

[modules.memory]
memory_schema = "chronicler_mem"
""".lstrip()
        )

        calls: list[tuple[str, str | None]] = []

        async def record_migration(db_url: str, chain: str = "core", schema: str | None = None):
            del db_url
            calls.append((chain, schema))

        monkeypatch.setattr(
            "butlers.db.db_params_from_env",
            lambda: {"user": "user", "password": "pw", "host": "localhost", "port": 5432},
        )
        monkeypatch.setattr("butlers.migrations.has_butler_chain", lambda name: False)
        monkeypatch.setattr("butlers.migrations.run_migrations", record_migration)

        await _migrate_all({"chronicler": butler_dir}, dry_run=False)

        assert calls == [("core", "chronicler"), ("memory", "chronicler_mem")]


class TestChroniclerMemoryPreparation:
    def _write_config(self, root, *, memory_schema="chronicler_mem"):
        butler_dir = root / "chronicler"
        butler_dir.mkdir(parents=True)
        (butler_dir / "butler.toml").write_text(
            '[butler]\nname = "chronicler"\nport = 41111\n'
            'description = "Retrospective time butler"\n'
            '[butler.db]\nname = "butlers"\nschema = "chronicler"\n'
            f'[modules.memory]\nmemory_schema = "{memory_schema}"\n'
        )
        return root

    def test_prepare_runs_core_then_memory_in_the_declared_private_schema(
        self, runner, tmp_path, monkeypatch
    ):
        butlers_dir = self._write_config(tmp_path / "butlers")
        calls = []

        async def record_migration(db_url, chain="core", schema=None):
            del db_url
            calls.append((chain, schema))

        monkeypatch.setattr(
            "butlers.db.db_params_from_env",
            lambda: {
                "user": "user",
                "password": "private-password",
                "host": "localhost",
                "port": 5432,
            },
        )
        monkeypatch.setattr("butlers.migrations.run_migrations", record_migration)
        monkeypatch.setattr(
            "butlers.migrations.chain_has_applied_revision", lambda *args, **kwargs: False
        )
        result = runner.invoke(cli, ["db", "prepare-chronicler-memory", "--dir", str(butlers_dir)])
        assert result.exit_code == 0, result.output
        assert calls == [("core", "chronicler_mem"), ("memory", "chronicler_mem")]
        assert "private-password" not in result.output

    def test_prepare_skips_core_when_the_private_schema_is_already_installed(
        self, runner, tmp_path, monkeypatch
    ):
        butlers_dir = self._write_config(tmp_path / "butlers")
        calls = []

        async def record_migration(db_url, chain="core", schema=None):
            del db_url
            calls.append((chain, schema))

        monkeypatch.setattr(
            "butlers.db.db_params_from_env",
            lambda: {"user": "user", "password": "pw", "host": "localhost", "port": 5432},
        )
        monkeypatch.setattr("butlers.migrations.run_migrations", record_migration)
        monkeypatch.setattr(
            "butlers.migrations.chain_has_applied_revision", lambda *args, **kwargs: True
        )
        result = runner.invoke(cli, ["db", "prepare-chronicler-memory", "--dir", str(butlers_dir)])
        assert result.exit_code == 0, result.output
        assert calls == [("memory", "chronicler_mem")]

    def test_prepare_failure_reports_inner_class_and_sqlstate_without_content(
        self, runner, tmp_path, monkeypatch
    ):
        butlers_dir = self._write_config(tmp_path / "butlers")

        class InsufficientPrivilege(Exception):
            pgcode = "42501"

        async def failing_migration(db_url, chain="core", schema=None):
            del db_url, chain, schema
            from sqlalchemy.exc import ProgrammingError

            raise ProgrammingError(
                "UPDATE secret-row-content", {}, InsufficientPrivilege("secret-row-content")
            )

        monkeypatch.setattr(
            "butlers.db.db_params_from_env",
            lambda: {"user": "user", "password": "pw", "host": "localhost", "port": 5432},
        )
        monkeypatch.setattr("butlers.migrations.run_migrations", failing_migration)
        monkeypatch.setattr(
            "butlers.migrations.chain_has_applied_revision", lambda *args, **kwargs: False
        )
        result = runner.invoke(cli, ["db", "prepare-chronicler-memory", "--dir", str(butlers_dir)])
        assert result.exit_code != 0
        assert "ProgrammingError, inner=InsufficientPrivilege, sqlstate=42501" in result.output
        assert "secret-row-content" not in result.output

    def test_prepare_dry_run_does_not_connect(self, runner, tmp_path, monkeypatch):
        butlers_dir = self._write_config(tmp_path / "butlers")
        monkeypatch.setattr(
            "butlers.db.db_params_from_env",
            lambda: pytest.fail("dry-run must not load database credentials"),
        )
        result = runner.invoke(
            cli,
            ["db", "prepare-chronicler-memory", "--dir", str(butlers_dir), "--dry-run"],
        )
        assert result.exit_code == 0, result.output
        assert "chronicler (schema=chronicler_mem): core, memory" in result.output

    def test_prepare_refuses_a_missing_or_different_private_schema(self, runner, tmp_path):
        butlers_dir = self._write_config(tmp_path / "butlers", memory_schema="chronicler")
        result = runner.invoke(cli, ["db", "prepare-chronicler-memory", "--dir", str(butlers_dir)])
        assert result.exit_code != 0
        assert "must explicitly be chronicler_mem" in result.output


class TestUpCommand:
    def test_up_error_cases(self, runner, tmp_path, butler_config_dir):
        """up fails with no configs and with nonexistent --only target."""
        empty = tmp_path / "empty"
        empty.mkdir()
        result = runner.invoke(cli, ["up", "--dir", str(empty)])
        assert result.exit_code != 0
        assert "No butler configs found" in result.output

        result2 = runner.invoke(
            cli, ["up", "--dir", str(butler_config_dir), "--only", "nonexistent"]
        )
        assert result2.exit_code != 0
        assert "not found" in result2.output

    def test_up_comma_separated_filter(self, runner, multi_butler_dir, monkeypatch):
        """--only with comma-separated names starts only those butlers."""
        monkeypatch.setattr("asyncio.run", lambda coro: coro.close())
        result = runner.invoke(cli, ["up", "--dir", str(multi_butler_dir), "--only", "alpha,gamma"])
        assert result.exit_code == 0
        assert "Starting 2 butler(s)" in result.output
        assert "alpha" in result.output
        assert "gamma" in result.output
        assert result.output.count("beta") == 0

        excluded = runner.invoke(
            cli, ["up", "--dir", str(multi_butler_dir), "--exclude", "alpha,gamma"]
        )
        assert excluded.exit_code == 0
        assert "Starting 1 butler(s): beta" in excluded.output
        empty = runner.invoke(
            cli, ["up", "--dir", str(multi_butler_dir), "--exclude", "alpha,beta,gamma"]
        )
        assert empty.exit_code != 0
        assert "No butlers remain" in empty.output

    def test_up_shows_starting_message(self, runner, butler_config_dir, monkeypatch):
        monkeypatch.setattr("asyncio.run", lambda coro: coro.close())
        result = runner.invoke(cli, ["up", "--dir", str(butler_config_dir)])
        assert result.exit_code == 0
        assert "Starting 1 butler(s)" in result.output
        assert "test_butler" in result.output


class TestRunCommand:
    def test_run_requires_config_and_shows_start_message(
        self, runner, butler_config_dir, monkeypatch
    ):
        result = runner.invoke(cli, ["run"])
        assert result.exit_code != 0

        monkeypatch.setattr("asyncio.run", lambda coro: coro.close())
        config_path = str(butler_config_dir / "test_butler")
        result2 = runner.invoke(cli, ["run", "--config", config_path])
        assert result2.exit_code == 0
        assert "Starting butler from" in result2.output


class TestListCommandStatus:
    """Tests for running/stopped status detection in list command."""

    def test_list_shows_running_stopped_and_mixed(
        self, runner, butler_config_dir, multi_butler_dir, monkeypatch
    ):
        """list shows running when port is open, stopped when closed, and mixed for multiple."""

        class MockSocketRunning:
            def __init__(self, *args, **kwargs):
                pass

            def settimeout(self, t):
                pass

            def connect(self, address):
                pass

            def close(self):
                pass

        monkeypatch.setattr("socket.socket", MockSocketRunning)
        result = runner.invoke(cli, ["list", "--dir", str(butler_config_dir)])
        assert result.exit_code == 0
        assert "running" in result.output.lower()

        class MockSocketStopped:
            def __init__(self, *args, **kwargs):
                pass

            def settimeout(self, t):
                pass

            def connect(self, address):
                raise OSError("Connection refused")

            def close(self):
                pass

        monkeypatch.setattr("socket.socket", MockSocketStopped)
        result2 = runner.invoke(cli, ["list", "--dir", str(butler_config_dir)])
        assert result2.exit_code == 0
        assert "stopped" in result2.output.lower()

        # Mixed: alpha (9001) is running, others stopped
        class MockSocketMixed:
            def __init__(self, *args, **kwargs):
                self.address = None

            def settimeout(self, t):
                pass

            def connect(self, address):
                self.address = address
                if address[1] == 9001:
                    pass
                else:
                    raise OSError("Connection refused")

            def close(self):
                pass

        monkeypatch.setattr("socket.socket", MockSocketMixed)
        result3 = runner.invoke(cli, ["list", "--dir", str(multi_butler_dir)])
        assert result3.exit_code == 0
        assert "running" in result3.output.lower()
        assert "stopped" in result3.output.lower()
