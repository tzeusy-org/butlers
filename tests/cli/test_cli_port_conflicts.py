"""Tests for port conflict detection in CLI up command."""

import asyncio

import pytest
from click.testing import CliRunner

from butlers.cli import cli

pytestmark = pytest.mark.unit


@pytest.fixture
def runner():
    return CliRunner()


class TestPortConflictDetection:
    def test_no_conflict_unique_ports(self, runner, tmp_path):
        """Butlers with unique ports pass conflict check."""
        base = tmp_path / "butlers"
        for name, port in [("alpha", 9001), ("beta", 9002), ("gamma", 9003)]:
            d = base / name
            d.mkdir(parents=True)
            (d / "butler.toml").write_text(f'[butler]\nname = "{name}"\nport = {port}\n')

        original_run = asyncio.run
        asyncio.run = lambda coro: coro.close()
        try:
            result = runner.invoke(cli, ["up", "--dir", str(base)])
            assert result.exit_code == 0
            assert "Starting 3 butler(s)" in result.output
        finally:
            asyncio.run = original_run

    def test_conflict_detected_and_reported(self, runner, tmp_path):
        """Two or more butlers with the same port are detected and all reported."""
        base = tmp_path / "butlers"

        # Two butlers sharing same port
        for name, port in [("alpha", 9000), ("beta", 9000)]:
            d = base / name
            d.mkdir(parents=True)
            (d / "butler.toml").write_text(f'[butler]\nname = "{name}"\nport = {port}\n')
        result = runner.invoke(cli, ["up", "--dir", str(base)])
        assert result.exit_code != 0
        assert "9000" in result.output
        assert "alpha" in result.output
        assert "beta" in result.output

        # Multiple separate conflicts
        base2 = tmp_path / "butlers2"
        for name, port in [("a", 7000), ("b", 7000), ("c", 8000), ("d", 8000), ("e", 9000)]:
            d = base2 / name
            d.mkdir(parents=True)
            (d / "butler.toml").write_text(f'[butler]\nname = "{name}"\nport = {port}\n')
        result2 = runner.invoke(cli, ["up", "--dir", str(base2)])
        assert result2.exit_code != 0
        assert "7000" in result2.output
        assert "8000" in result2.output

    def test_only_flag_conflict_handling(self, runner, tmp_path):
        """--only flag checks conflicts among selected butlers only."""
        base = tmp_path / "butlers"
        for name, port in [("alpha", 9000), ("beta", 9000), ("gamma", 8000)]:
            d = base / name
            d.mkdir(parents=True)
            (d / "butler.toml").write_text(f'[butler]\nname = "{name}"\nport = {port}\n')

        # alpha+beta conflict → error
        result = runner.invoke(cli, ["up", "--dir", str(base), "--only", "alpha", "--only", "beta"])
        assert result.exit_code != 0
        assert "9000" in result.output

        # alpha+gamma no conflict → success
        original_run = asyncio.run
        asyncio.run = lambda coro: coro.close()
        try:
            result2 = runner.invoke(
                cli, ["up", "--dir", str(base), "--only", "alpha", "--only", "gamma"]
            )
            assert result2.exit_code == 0
            assert "Starting 2 butler(s)" in result2.output
        finally:
            asyncio.run = original_run
