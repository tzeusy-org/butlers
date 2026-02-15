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

            # Structured logging is configured on root logger
            root = logging.getLogger()
            assert len(root.handlers) >= 1
            assert httpx_logger.level == logging.WARNING
            assert httpcore_logger.level == logging.WARNING
        finally:
            httpx_logger.setLevel(original_httpx_level)
            httpcore_logger.setLevel(original_httpcore_level)
            logging.getLogger().handlers[:] = original_root_handlers


class TestListCommand:
    def test_list_with_configs(self, runner, butler_config_dir):
        result = runner.invoke(cli, ["list", "--dir", str(butler_config_dir)])
        assert result.exit_code == 0
        assert "test_butler" in result.output
        assert "9000" in result.output
        assert "A test butler" in result.output

    def test_list_with_no_configs(self, runner, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = runner.invoke(cli, ["list", "--dir", str(empty_dir)])
        assert result.exit_code == 0
        assert "No butler configs found" in result.output

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
    def test_init_creates_scaffold(self, runner, tmp_path):
        butlers_dir = tmp_path / "butlers"
        result = runner.invoke(cli, ["init", "mybot", "--port", "9100", "--dir", str(butlers_dir)])
        assert result.exit_code == 0
        assert "Created butler scaffold" in result.output

        butler_dir = butlers_dir / "mybot"
        assert butler_dir.is_dir()
        assert (butler_dir / "butler.toml").exists()
        assert (butler_dir / "CLAUDE.md").exists()
        assert (butler_dir / "AGENTS.md").exists()
        assert (butler_dir / "skills").is_dir()

        # Verify toml content
        toml_text = (butler_dir / "butler.toml").read_text()
        assert 'name = "mybot"' in toml_text
        assert "port = 9100" in toml_text

    def test_init_uses_default_port(self, runner, tmp_path):
        """Test that init works without --port flag, using default 8100."""
        butlers_dir = tmp_path / "butlers"
        result = runner.invoke(cli, ["init", "mybot", "--dir", str(butlers_dir)])
        assert result.exit_code == 0
        assert "Created butler scaffold" in result.output

        butler_dir = butlers_dir / "mybot"
        toml_text = (butler_dir / "butler.toml").read_text()
        assert "port = 8100" in toml_text
        assert 'name = "butler_mybot"' in toml_text

    def test_init_existing_dir_fails(self, runner, tmp_path):
        butlers_dir = tmp_path / "butlers"
        existing = butlers_dir / "existing"
        existing.mkdir(parents=True)
        result = runner.invoke(
            cli, ["init", "existing", "--port", "9100", "--dir", str(butlers_dir)]
        )
        assert result.exit_code != 0
        assert "Directory already exists" in result.output

    def test_init_claude_md_content(self, runner, tmp_path):
        butlers_dir = tmp_path / "butlers"
        runner.invoke(cli, ["init", "helper", "--port", "9200", "--dir", str(butlers_dir)])
        claude_md = (butlers_dir / "helper" / "CLAUDE.md").read_text()
        assert "# Helper Butler" in claude_md
        assert "helper" in claude_md


class TestDiscoverConfigs:
    def test_finds_configs(self, butler_config_dir):
        configs = _discover_configs(butler_config_dir)
        assert "test_butler" in configs
        assert configs["test_butler"] == butler_config_dir / "test_butler"

    def test_returns_empty_for_missing_dir(self, tmp_path):
        configs = _discover_configs(tmp_path / "nonexistent")
        assert configs == {}

    def test_skips_dirs_without_toml(self, tmp_path):
        base = tmp_path / "butlers"
        (base / "no_toml").mkdir(parents=True)
        configs = _discover_configs(base)
        assert configs == {}

    def test_skips_invalid_toml(self, tmp_path):
        base = tmp_path / "butlers"
        bad = base / "bad_butler"
        bad.mkdir(parents=True)
        (bad / "butler.toml").write_text("not valid [[[ toml content")
        configs = _discover_configs(base)
        assert configs == {}


class TestUpCommand:
    def test_up_no_configs(self, runner, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        result = runner.invoke(cli, ["up", "--dir", str(empty)])
        assert result.exit_code != 0
        assert "No butler configs found" in result.output

    def test_up_only_missing_butler(self, runner, butler_config_dir):
        result = runner.invoke(
            cli, ["up", "--dir", str(butler_config_dir), "--only", "nonexistent"]
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_up_only_comma_separated_multiple(self, runner, multi_butler_dir, monkeypatch):
        """Test --only with comma-separated butler names."""
        monkeypatch.setattr("asyncio.run", lambda coro: None)
        result = runner.invoke(cli, ["up", "--dir", str(multi_butler_dir), "--only", "alpha,gamma"])
        assert result.exit_code == 0
        assert "Starting 2 butler(s)" in result.output
        assert "alpha" in result.output
        assert "gamma" in result.output
        # beta should not be mentioned in the starting message
        assert result.output.count("beta") == 0

    def test_up_only_comma_separated_with_spaces(self, runner, multi_butler_dir, monkeypatch):
        """Test --only with comma-separated names and spaces."""
        monkeypatch.setattr("asyncio.run", lambda coro: None)
        result = runner.invoke(cli, ["up", "--dir", str(multi_butler_dir), "--only", "alpha, beta"])
        assert result.exit_code == 0
        assert "Starting 2 butler(s)" in result.output
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_up_only_comma_separated_missing_butler(self, runner, multi_butler_dir):
        """Test --only with comma-separated names including nonexistent butler."""
        result = runner.invoke(
            cli, ["up", "--dir", str(multi_butler_dir), "--only", "alpha,nonexistent"]
        )
        assert result.exit_code != 0
        assert "not found" in result.output
        assert "nonexistent" in result.output

    def test_up_shows_starting_message(self, runner, butler_config_dir, monkeypatch):
        """Test that up command outputs the starting message before trying to start daemons."""
        # Patch asyncio.run to avoid actually starting daemons
        monkeypatch.setattr("asyncio.run", lambda coro: None)
        result = runner.invoke(cli, ["up", "--dir", str(butler_config_dir)])
        assert result.exit_code == 0
        assert "Starting 1 butler(s)" in result.output
        assert "test_butler" in result.output


class TestRunCommand:
    def test_run_requires_config(self, runner):
        result = runner.invoke(cli, ["run"])
        assert result.exit_code != 0
        assert "Missing option" in result.output or "required" in result.output.lower()

    def test_run_shows_starting_message(self, runner, butler_config_dir, monkeypatch):
        """Test that run outputs starting message before daemon start."""
        monkeypatch.setattr("asyncio.run", lambda coro: None)
        config_path = str(butler_config_dir / "test_butler")
        result = runner.invoke(cli, ["run", "--config", config_path])
        assert result.exit_code == 0
        assert "Starting butler from" in result.output


class TestListCommandStatus:
    """Tests for running/stopped status detection in list command."""

    def test_list_shows_running_status(self, runner, butler_config_dir, monkeypatch):
        """Test that list shows running status when port is open."""

        # Mock socket to return success (port is open)
        class MockSocket:
            def __init__(self, *args, **kwargs):
                pass

            def settimeout(self, timeout):
                pass

            def connect(self, address):
                pass  # Success - no exception

            def close(self):
                pass

        monkeypatch.setattr("socket.socket", MockSocket)

        result = runner.invoke(cli, ["list", "--dir", str(butler_config_dir)])
        assert result.exit_code == 0
        assert "test_butler" in result.output
        assert "running" in result.output.lower()

    def test_list_shows_stopped_status(self, runner, butler_config_dir, monkeypatch):
        """Test that list shows stopped status when port is closed."""

        # Mock socket to raise exception (port is closed)
        class MockSocket:
            def __init__(self, *args, **kwargs):
                pass

            def settimeout(self, timeout):
                pass

            def connect(self, address):

                raise OSError("Connection refused")

            def close(self):
                pass

        monkeypatch.setattr("socket.socket", MockSocket)

        result = runner.invoke(cli, ["list", "--dir", str(butler_config_dir)])
        assert result.exit_code == 0
        assert "test_butler" in result.output
        assert "stopped" in result.output.lower()

    def test_list_shows_mixed_statuses(self, runner, multi_butler_dir, monkeypatch):
        """Test list shows different statuses for different butlers."""
        # Mock socket to succeed for port 9001, fail for others

        class MockSocket:
            def __init__(self, *args, **kwargs):
                self.address = None

            def settimeout(self, timeout):
                pass

            def connect(self, address):
                self.address = address
                if address[1] == 9001:  # alpha butler
                    pass  # Success
                else:
                    raise OSError("Connection refused")

            def close(self):
                pass

        monkeypatch.setattr("socket.socket", MockSocket)

        result = runner.invoke(cli, ["list", "--dir", str(multi_butler_dir)])
        assert result.exit_code == 0
        # Check that we have both running and stopped
        output_lower = result.output.lower()
        assert "running" in output_lower
        assert "stopped" in output_lower
