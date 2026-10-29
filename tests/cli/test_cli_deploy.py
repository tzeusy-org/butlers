"""The retired Compose deploy command cannot perform deployment effects."""

import pytest
from click.testing import CliRunner

from butlers.cli import cli

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "arguments", [[], ["--help"], ["--allow-dirty-root"], ["--profile", "dev"]]
)
def test_deploy_is_rejected_but_helm_ledger_command_remains(arguments):
    runner = CliRunner()
    result = runner.invoke(cli, ["deploy", *arguments])
    assert result.exit_code == 2
    assert "No such command 'deploy'" in result.output
    assert "deploy" not in cli.commands
    ledger = runner.invoke(cli, ["deployments", "record", "--help"])
    assert ledger.exit_code == 0
    assert "--source" in ledger.output
    assert "--result" in ledger.output
