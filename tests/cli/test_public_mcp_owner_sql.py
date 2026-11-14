"""`butlers public-mcp owner-sql` prints the owner-step file from the manifest alone (RFC 0039 D3)."""

from __future__ import annotations

import socket
import subprocess
import sys

import pytest
from click.testing import CliRunner

from butlers.cli import cli
from butlers.knowledge_read.manifest import ManifestError, load_manifest
from butlers.migrations.public_mcp_owner import render_owner_program


def test_owner_sql_prints_the_rendered_program_byte_identically(monkeypatch):
    """No connection, no env read: the same manifest yields the same bytes."""

    def no_network(*args, **kwargs):
        raise AssertionError("owner-sql must not open a socket")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr("butlers.db.db_params_from_env", no_network)
    runner = CliRunner()
    first = runner.invoke(cli, ["public-mcp", "owner-sql"])
    second = runner.invoke(cli, ["public-mcp", "owner-sql"])
    assert first.exit_code == 0, first.output
    assert first.stdout_bytes == second.stdout_bytes
    assert first.stdout == render_owner_program(load_manifest()) + "\n"
    lines = first.stdout.splitlines()
    assert lines[0] == "\\set ON_ERROR_STOP on"
    assert not any(line.strip() in {"\\q", "\\quit"} for line in lines)


def test_owner_sql_runs_with_an_empty_environment():
    """The deploy hook execs it in a pod; it needs no database environment at all."""
    result = subprocess.run(
        [sys.executable, "-c", "from butlers.cli import cli; cli()", "public-mcp", "owner-sql"],
        capture_output=True,
        env={},
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.startswith(b"\\set ON_ERROR_STOP on\n")


def test_owner_sql_refuses_an_invalid_manifest_with_nothing_on_stdout(monkeypatch):
    def broken():
        raise ManifestError("synthetic manifest refusal")

    monkeypatch.setattr("butlers.knowledge_read.manifest.load_manifest", broken)
    result = CliRunner().invoke(cli, ["public-mcp", "owner-sql"])
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "synthetic manifest refusal" in result.stderr


@pytest.mark.parametrize("args", [["--help"], ["public-mcp", "--help"]])
def test_owner_sql_is_listed_for_the_deploy_hook(args):
    """The hook treats an image as older only when no exact `owner-sql` entry appears."""
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0
    expected = "public-mcp" if args == ["--help"] else "owner-sql"
    assert any(line.split()[:1] == [expected] for line in result.output.splitlines())


def test_subscription_drop_waits_for_workers_to_drain_after_disable():
    """Same-instance tablesync workers deadlock a DROP that holds its own xid (bu-b22sk6)."""
    program = render_owner_program(load_manifest())
    disable = program.index("ALTER SUBSCRIPTION butlers_public_sub DISABLE;")
    drain = program.index("subscription worker drain deadline", disable)
    detach = program.index("SET (slot_name=NONE)", disable)
    drop = program.index("DROP SUBSCRIPTION butlers_public_sub;", disable)
    assert disable < drain < detach < drop


def test_logical_slot_is_created_by_a_top_level_statement_not_a_do_block():
    """PL/pgSQL slot creation fails intermittently (bu-y66o00.33); keep it top-level SELECT."""
    program = render_owner_program(load_manifest())
    lines = [ln for ln in program.splitlines() if "pg_create_logical_replication_slot" in ln]
    assert len(lines) == 1
    assert lines[0].strip().startswith("SELECT pg_create_logical_replication_slot(")
    assert "PERFORM pg_create_logical" not in program
    assert "DO $mcp$" not in lines[0]
