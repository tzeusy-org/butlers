"""Tests for port conflict detection in CLI up command."""

import pytest
from click.testing import CliRunner

from butlers.cli import cli

pytestmark = pytest.mark.unit


@pytest.fixture
def runner():
    return CliRunner()


class TestPortConflictDetection:
    def test_no_conflict_all_unique_ports(self, runner, tmp_path):
        """Test that butlers with unique ports pass conflict check."""
        base = tmp_path / "butlers"
        for name, port in [("alpha", 9001), ("beta", 9002), ("gamma", 9003)]:
            d = base / name
            d.mkdir(parents=True)
            (d / "butler.toml").write_text(f'[butler]\nname = "{name}"\nport = {port}\n')

        # Patch asyncio.run to avoid actually starting daemons
        import asyncio

        original_run = asyncio.run
        asyncio.run = lambda coro: coro.close()

        try:
            result = runner.invoke(cli, ["up", "--dir", str(base)])
            assert result.exit_code == 0
            assert "Starting 3 butler(s)" in result.output
        finally:
            asyncio.run = original_run

    def test_conflict_two_butlers_same_port(self, runner, tmp_path):
        """Test that two butlers with the same port are detected as conflicting."""
        base = tmp_path / "butlers"
        for name, port in [("alpha", 9000), ("beta", 9000)]:
            d = base / name
            d.mkdir(parents=True)
            (d / "butler.toml").write_text(f'[butler]\nname = "{name}"\nport = {port}\n')

        result = runner.invoke(cli, ["up", "--dir", str(base)])
        assert result.exit_code != 0
        assert "Port conflict" in result.output or "port conflict" in result.output
        assert "9000" in result.output
        assert "alpha" in result.output
        assert "beta" in result.output

    def test_conflict_three_butlers_same_port(self, runner, tmp_path):
        """Test that three butlers with the same port are all reported."""
        base = tmp_path / "butlers"
        for name in ["alpha", "beta", "gamma"]:
            d = base / name
            d.mkdir(parents=True)
            (d / "butler.toml").write_text(f'[butler]\nname = "{name}"\nport = 8888\n')

        result = runner.invoke(cli, ["up", "--dir", str(base)])
        assert result.exit_code != 0
        assert "8888" in result.output
        assert "alpha" in result.output
        assert "beta" in result.output
        assert "gamma" in result.output

    def test_conflict_multiple_different_conflicts(self, runner, tmp_path):
        """Test multiple separate port conflicts."""
        base = tmp_path / "butlers"
        configs = [
            ("alpha", 7000),
            ("beta", 7000),  # conflicts with alpha
            ("gamma", 8000),
            ("delta", 8000),  # conflicts with gamma
            ("epsilon", 9000),  # no conflict
        ]
        for name, port in configs:
            d = base / name
            d.mkdir(parents=True)
            (d / "butler.toml").write_text(f'[butler]\nname = "{name}"\nport = {port}\n')

        result = runner.invoke(cli, ["up", "--dir", str(base)])
        assert result.exit_code != 0
        # Should report both conflicts
        assert "7000" in result.output
        assert "8000" in result.output
        assert "alpha" in result.output
        assert "beta" in result.output
        assert "gamma" in result.output
        assert "delta" in result.output

    def test_no_conflict_when_using_only_flag(self, runner, tmp_path):
        """Test that --only flag still checks for conflicts within selected butlers."""
        base = tmp_path / "butlers"
        configs = [
            ("alpha", 9000),
            ("beta", 9000),  # conflicts with alpha
            ("gamma", 8000),  # different port, not selected
        ]
        for name, port in configs:
            d = base / name
            d.mkdir(parents=True)
            (d / "butler.toml").write_text(f'[butler]\nname = "{name}"\nport = {port}\n')

        # Try to start only alpha and beta (which conflict)
        result = runner.invoke(cli, ["up", "--dir", str(base), "--only", "alpha", "--only", "beta"])
        assert result.exit_code != 0
        assert "9000" in result.output

    def test_no_conflict_with_only_flag_disjoint_ports(self, runner, tmp_path):
        """Test that --only flag works when selected butlers have no conflicts."""
        base = tmp_path / "butlers"
        configs = [
            ("alpha", 9000),
            ("beta", 9000),  # conflicts with alpha, but not selected
            ("gamma", 8000),
        ]
        for name, port in configs:
            d = base / name
            d.mkdir(parents=True)
            (d / "butler.toml").write_text(f'[butler]\nname = "{name}"\nport = {port}\n')

        # Patch asyncio.run to avoid actually starting daemons
        import asyncio

        original_run = asyncio.run
        asyncio.run = lambda coro: coro.close()

        try:
            # Try to start only alpha and gamma (no conflict between them)
            result = runner.invoke(
                cli, ["up", "--dir", str(base), "--only", "alpha", "--only", "gamma"]
            )
            assert result.exit_code == 0
            assert "Starting 2 butler(s)" in result.output
        finally:
            asyncio.run = original_run
