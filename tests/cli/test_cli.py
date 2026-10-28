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

        from contextlib import contextmanager

        calls: list[tuple[str, str | None]] = []
        events = []
        held_connection = object()
        migration_url = "postgresql://fixture_migrator@db.invalid/butlers"

        @contextmanager
        def selected_session(db_url):
            assert db_url == migration_url
            events.append(("enter", held_connection))
            try:
                yield held_connection
            finally:
                events.append(("exit", held_connection))

        async def record_migration(
            db_url: str,
            chain: str = "core",
            schema: str | None = None,
            *,
            connection=None,
        ):
            assert db_url == migration_url
            assert connection is held_connection
            calls.append((chain, schema))
            events.append(("migration", chain, schema, connection))

        def record_finalization(connection, *, plans):
            assert connection is held_connection
            events.append(("finalize", connection, plans))

        monkeypatch.setenv("BUTLERS_MIGRATOR_DATABASE_URL", migration_url)
        monkeypatch.setattr("butlers.migrations.has_butler_chain", lambda name: False)
        monkeypatch.setattr("butlers.migrations.migration_session", selected_session)
        monkeypatch.setattr("butlers.migrations.run_migrations", record_migration)
        monkeypatch.setattr("butlers.migrations.finalize_authority", record_finalization)

        await _migrate_all({"chronicler": butler_dir}, dry_run=False)

        assert calls == [("core", "chronicler"), ("memory", "chronicler_mem")]
        assert events == [
            ("enter", held_connection),
            ("migration", "core", "chronicler", held_connection),
            ("migration", "memory", "chronicler_mem", held_connection),
            (
                "finalize",
                held_connection,
                (("core", "chronicler", None), ("memory", "chronicler_mem", None)),
            ),
            ("exit", held_connection),
        ]


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


async def test_actual_migrate_cli_holds_one_selected_session_through_finalization(
    monkeypatch, tmp_path
):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import butlers.cli as cli_module
    import butlers.migrations as migrations

    held = object()
    events = []

    @contextmanager
    def selected(url):
        assert url == "postgresql://selected:synthetic@localhost/butlers"
        events.append("locked")
        yield held
        events.append("released")

    async def run(url, chain="core", schema=None, *, connection=None):
        assert connection is held
        events.append((chain, schema))

    def finalize(connection, *, plans):
        assert connection is held
        assert plans == (("core", "general", None), ("core", "health", None))
        events.append("finalized")

    monkeypatch.setenv(
        "BUTLERS_MIGRATOR_DATABASE_URL", "postgresql://selected:synthetic@localhost/butlers"
    )
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda path: SimpleNamespace(db_name="butlers", db_schema=path.name, modules={}),
    )
    monkeypatch.setattr(
        "butlers.modules.registry.default_registry",
        lambda: SimpleNamespace(load_from_config=lambda modules: []),
    )
    monkeypatch.setattr(migrations, "migration_session", selected, raising=False)
    monkeypatch.setattr(migrations, "finalize_authority", finalize, raising=False)
    monkeypatch.setattr(migrations, "run_migrations", AsyncMock(side_effect=run))
    monkeypatch.setattr(migrations, "has_butler_chain", lambda name: False)
    monkeypatch.setattr(
        "butlers.db.db_params_from_env",
        Mock(side_effect=AssertionError("No runtime credential fallback")),
    )
    await _migrate_all({"general": tmp_path / "general", "health": tmp_path / "health"})
    assert events == ["locked", ("core", "general"), ("core", "health"), "finalized", "released"]


def test_actual_bootstrap_cli_uses_only_explicit_admin_source_and_owned_lifetime(monkeypatch):
    """Operator bootstrap is explicit; no serving DSN is a credential fallback."""
    import json
    from unittest.mock import MagicMock

    calls = []
    raw = MagicMock()
    cursor = MagicMock()
    raw.cursor.return_value.__enter__.return_value = cursor
    cursor.execute.side_effect = lambda sql, *args: calls.append((sql, args))
    engine = MagicMock()
    engine.raw_connection.return_value = raw
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr("sqlalchemy.create_engine", factory)
    monkeypatch.delenv("BUTLERS_BOOTSTRAP_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://serving:synthetic@localhost/butlers")
    runner = CliRunner()
    result = runner.invoke(cli, ["db", "bootstrap", "--migration-role", "migration_selected"])
    assert result.exit_code != 0
    factory.assert_not_called()
    monkeypatch.setenv(
        "BUTLERS_BOOTSTRAP_DATABASE_URL", "postgresql://admin:synthetic@localhost/unlisted"
    )
    result = runner.invoke(cli, ["db", "bootstrap", "--migration-role", "migration_selected"])
    assert result.exit_code != 0
    factory.assert_not_called()
    monkeypatch.setenv(
        "BUTLERS_BOOTSTRAP_DATABASE_URL", "postgresql://admin:synthetic@localhost/butlers"
    )
    result = runner.invoke(cli, ["db", "bootstrap", "--migration-role", "migration_selected"])
    assert result.exit_code == 0, result.output
    assert factory.call_args.kwargs["connect_args"]["connect_timeout"] == 5
    assert "butlers.source_manifest" in calls[0][0]
    manifest = json.loads(calls[0][1][0][0])
    assert manifest["migration_role"] == "migration_selected"
    assert manifest["database"] == "butlers"
    assert "butler_chronicler_rw" in manifest["roles"]
    assert "explicit privileged bootstrap identity required" in calls[1][0]
    assert "CREATE TABLE" not in calls[1][0]
    raw.close.assert_called_once()
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("fault", [None, "digest", "mode", "symlink"])
def test_operator_rollback_reads_only_exact_owned_external_capsule(tmp_path, fault):
    import hashlib
    import json

    import click

    from butlers.cli import _load_rollback_capsule

    actual = tmp_path / "capsule.json"
    data = json.dumps({"format": "nonsecret-synthetic-catalog"}).encode()
    actual.write_bytes(data)
    actual.chmod(0o600)
    selected = actual
    digest = hashlib.sha256(data).hexdigest()
    if fault == "digest":
        digest = "0" * 64
    elif fault == "mode":
        actual.chmod(0o640)
    elif fault == "symlink":
        selected = tmp_path / "alias.json"
        selected.symlink_to(actual)
    if fault is None:
        assert _load_rollback_capsule(selected, digest) == json.loads(data)
    else:
        with pytest.raises(click.ClickException):
            _load_rollback_capsule(selected, digest)


def test_operator_rollback_refuses_checkout_output_and_absent_admin_before_effects(
    runner, monkeypatch
):
    from pathlib import Path
    from unittest.mock import patch

    import click

    from butlers.cli import _rollback_capsule_path

    with pytest.raises(click.ClickException):
        _rollback_capsule_path(Path(__file__).resolve().parents[2] / ".tmp" / "capsule.json")
    monkeypatch.delenv("BUTLERS_BOOTSTRAP_DATABASE_URL", raising=False)
    with patch("butlers.migrations.run_preserving_rollback") as execute:
        result = runner.invoke(
            cli,
            [
                "db",
                "rollback-catalog",
                "--transition",
                "R1",
                "--capsule",
                "/home/orca/.tmp/nonexistent-synthetic-capsule.json",
                "--sha256",
                "0" * 64,
            ],
        )
    assert result.exit_code != 0
    assert "Explicit administrative database configuration is required" in result.output
    execute.assert_not_called()
