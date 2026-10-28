"""`butlers deployments record` exits 1 when the row cannot be written (bu-viat6h.5).

The deploy wrapper's warning depends on this exit status.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from butlers.cli import cli

pytestmark = pytest.mark.unit


def test_a_failed_write_exits_one_and_names_only_the_error(monkeypatch) -> None:
    async def _boom():
        raise ConnectionError("db down")

    monkeypatch.setattr("butlers.core.deploy._make_pool", _boom)

    result = CliRunner().invoke(
        cli, ["deployments", "record", "--source", "deploy", "--result", "success"]
    )

    assert result.exit_code == 1
    assert "Failed to record deployment: ConnectionError: db down" in result.output
