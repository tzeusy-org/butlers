"""Tests for the CLI commands."""

import logging

import pytest
from click.testing import CliRunner

from butlers.cli import _configure_logging, _discover_configs, cli

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
