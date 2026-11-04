"""Operator input boundaries; no database or credential access."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from butlers.modules.memory import retention_drain as engine
from scripts import retention_drain as cli


def _plan():
    now = datetime.now(UTC)
    return engine.Plan(
        uuid4(),
        "butlers-dev",
        "butlers",
        "orca",
        now - timedelta(days=1),
        now - timedelta(minutes=1),
        now + timedelta(minutes=5),
        1,
        10,
        10,
        60,
        2000,
        500,
        source_sha256=engine.source_identity(),
    )


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["valid", "duplicate", "source", "unknown", "isolated"])
def test_exact_plan_positive_and_refusals(tmp_path, mutation):
    path = tmp_path / "plan.json"
    p = _plan()
    if mutation == "source":
        p = replace(p, source_sha256="0" * 64)
    if mutation == "isolated":
        p = replace(p, environment="isolated")
    text = json.dumps(p.public())
    if mutation == "duplicate":
        text = text[:-1] + ',"role":"butler_switchboard_rw"}'
    if mutation == "unknown":
        text = text[:-1] + ',"approve_delete":true}'
    path.write_text(text)
    path.chmod(0o600)
    if mutation == "valid":
        assert cli.load_plan(path) == p
    else:
        with pytest.raises(engine.Refused):
            cli.load_plan(path)


@pytest.mark.unit
def test_real_module_entrypoint_has_only_readonly_surface():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.retention_drain", "--help"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0
    assert b"--dry-run" in result.stdout
    assert b"--apply" not in result.stdout
    refused = subprocess.run(
        [sys.executable, "-m", "scripts.retention_drain", "--apply"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert refused.returncode != 0


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["symlink", "fifo", "public", "oversized"])
def test_plan_file_cannot_block_or_redirect(tmp_path, kind):
    import os

    path = tmp_path / "plan"
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text(json.dumps(_plan().public()))
        target.chmod(0o600)
        path.symlink_to(target)
    else:
        path.write_text(json.dumps(_plan().public()) if kind == "public" else "x" * 8193)
        path.chmod(0o644 if kind == "public" else 0o600)
    with pytest.raises(engine.Refused, match="plan_invalid"):
        cli.load_plan(path)
