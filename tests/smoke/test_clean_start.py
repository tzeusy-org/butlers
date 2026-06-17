"""Smoke tests: clean-start importability and CLI invocation.

Cases:
- Importing ``butlers`` succeeds without side effects.
- Importing ``butlers.cli`` succeeds without side effects.
- ``butlers --help`` exits 0 via Click's in-process test runner.
- ``butlers --help`` output lists the ``run`` command.
- ``uv run --frozen --no-dev butlers --help`` exits 0 (skipped if ``uv``
  is not found in PATH, e.g. in stripped CI environments).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from click.testing import CliRunner

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# Import checks — no Docker, no LLM, no network
# ---------------------------------------------------------------------------


def test_butlers_package_importable():
    """``butlers`` top-level package is importable without raising.

    A failure here means a syntax error or missing dependency broke the
    package before any test can run.
    """
    import butlers  # noqa: F401


def test_butlers_cli_module_importable():
    """``butlers.cli`` module is importable without raising.

    A failure here means the CLI entry-point module cannot be loaded.
    """
    import butlers.cli  # noqa: F401


# ---------------------------------------------------------------------------
# CLI help via Click's in-process test runner — no subprocess, no LLM
# ---------------------------------------------------------------------------


def test_cli_help_exits_zero():
    """``butlers --help`` exits 0 (Click entry-point resolves correctly).

    Uses Click's ``CliRunner`` so no subprocess or PATH dependency is needed.
    A non-zero exit code indicates a broken CLI entry-point.
    """
    from butlers.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, f"butlers --help exited {result.exit_code}:\n{result.output}"


def test_cli_help_exposes_run_command():
    """``butlers --help`` output lists the ``run`` command.

    The ``run`` command starts a single butler daemon; its absence from help
    would indicate a missing command registration or broken CLI structure.
    """
    from butlers.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert "run" in result.output, f"'run' command not found in --help output:\n{result.output}"


# ---------------------------------------------------------------------------
# Deployment-form invocation — subprocess, skipped if uv not on PATH
# ---------------------------------------------------------------------------

_uv_available = shutil.which("uv") is not None


@pytest.mark.skipif(
    not _uv_available,
    reason="'uv' not available in PATH — skip deployment-form invocation check",
)
def test_deployment_form_help_exits_zero():
    """``uv run --frozen --no-dev butlers --help`` exits 0 in the deployment form.

    This is the production/CI invocation: frozen lockfile, no dev extras.
    A non-zero exit code indicates the package is broken under its production
    dependency constraints.  Skipped when ``uv`` is not on PATH.
    """
    result = subprocess.run(
        ["uv", "run", "--frozen", "--no-dev", "butlers", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "uv run --frozen --no-dev butlers --help exited "
        f"{result.returncode}:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
