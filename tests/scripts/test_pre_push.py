"""Composed pre-push refusal and reversible installation (REQ-testing-052).

Miniature inputs position the driver boundary; full-source collection and the
installed bd delegate are separately exercised in the author/reviewer receipts.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.unit


def _driver():
    spec = importlib.util.spec_from_file_location("pre_push", ROOT / "scripts/pre_push.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ref_updates_require_actual_head_and_preserve_multi_ref_bytes():
    driver = _driver()
    head = "a" * 40
    raw = (
        f"refs/heads/a {head} refs/heads/a {'0' * 40}\n"
        f"refs/heads/b {head} refs/heads/b {'b' * 40}\n"
        f"(delete) {'0' * 40} refs/heads/old {'b' * 40}\n"
    ).encode()
    assert driver.validate_updates(raw, head) == raw
    alias = raw.replace(b"refs/heads/a", b"HEAD", 1)
    assert driver.validate_updates(alias, head) == alias
    for invalid in (
        b"",
        raw.replace(head.encode(), b"c" * 40, 1),
        raw.replace(b"refs/heads/a", b"refs/tags/a", 1),
        raw + b"broken\n",
    ):
        with pytest.raises(driver.Refusal):
            driver.validate_updates(invalid, head)
    assert driver.validate_updates(raw, head) == raw


def test_readonly_plan_executes_real_predicates_and_never_mutating_aggregate():
    driver = _driver()
    plan = driver.guard_plan(["tests/test_example.py"])
    names = [name for name, _ in plan]
    assert names.count("fresh-inventory-partition-budget") == 1
    assert {
        "lock",
        "ruff-check",
        "ruff-format",
        "em-dashes",
        "spec-overwrites",
        "countable-tasks",
        "duplicate-names",
        "archived-landed",
        "owner-emails",
        "cited-requirements",
        "openspec-strict",
    } <= set(names)
    assert all(
        "check-guards" not in command and "extract-frontend-copy.py" not in " ".join(command)
        for _, command in plan
    )
    # Budget/partition remains the actual one-collection ordinary entrypoint.
    command = dict(plan)["fresh-inventory-partition-budget"]
    assert command[-1] == "scripts/check_ci_test_shards.py"
    assert "fresh-inventory-partition-budget" not in dict(driver.guard_plan(["docs/x.md"]))
    assert "fresh-inventory-partition-budget" in dict(driver.guard_plan(["unknown.asset"]))
    # Deletion still changes collection even though Ruff cannot read the path.
    assert "fresh-inventory-partition-budget" in dict(
        driver.guard_plan(["tests/deleted.py"], root=ROOT)
    )


def test_owned_process_failure_timeout_and_signal_cleanup(tmp_path):
    driver = _driver()
    with pytest.raises(driver.Refusal):
        driver.run_checked("positioned-failure", ["sh", "-c", "exit 17"], tmp_path)
    with pytest.raises(driver.Refusal):
        driver.run_checked("positioned-timeout", ["sh", "-c", "sleep 10"], tmp_path, timeout=0.1)
    assert driver.run_checked("restored", ["sh", "-c", "exit 0"], tmp_path) == 0
    pid_file = tmp_path / "owned-pid"
    driver.run_checked(
        "owned-success",
        ["sh", "-c", "sleep 30 </dev/null >/dev/null 2>&1 & echo $! > owned-pid"],
        tmp_path,
    )
    pid = int(pid_file.read_text())
    # Single atomic read tolerates reaping; a zombie cannot perform work.
    try:
        status = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[0]
    except FileNotFoundError:
        status = "absent"
    assert status in ("Z", "absent")


def test_installer_refuses_unknown_config_and_restores_absent_state(tmp_path, monkeypatch):
    driver = _driver()
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "core.hooksPath", "custom"], check=True)
    before = (root / ".git/config").read_bytes()
    with pytest.raises(driver.Refusal):
        driver.install(root)
    assert (root / ".git/config").read_bytes() == before

    subprocess.run(["git", "-C", str(root), "config", "--unset", "core.hooksPath"], check=True)
    before = (root / ".git/config").read_bytes()
    (root / ".githooks").mkdir()
    (root / ".beads/hooks").mkdir(parents=True)
    for name in driver.HOOKS:
        (root / ".githooks" / name).touch()
    # This isolates configuration mechanics only. Installed source/delegate
    # proof uses actual bodies/binary in separate full-source controls.
    monkeypatch.setattr(driver, "asset_binding", lambda root: {"fixture": "config-only"})
    driver.install(root)
    installed = (root / ".git/config").read_bytes()
    driver.install(root)
    assert (root / ".git/config").read_bytes() == installed
    driver.install(root, uninstall=True)
    assert (root / ".git/config").read_bytes() == before
    subprocess.run(
        ["git", "-C", str(root), "config", "extensions.worktreeConfig", "true"], check=True
    )
    before = (root / ".git/config").read_bytes()
    with pytest.raises(driver.Refusal):
        driver.install(root)
    assert (root / ".git/config").read_bytes() == before


def test_network_sandbox_denies_real_socket_and_restored_ordinary_process(tmp_path):
    sandbox = ROOT / "scripts/pre_push_sandbox.py"
    result = subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            str(sandbox),
            str(ROOT / ".venv/bin/python"),
            "-c",
            "import socket; socket.socket()",
        ],
        capture_output=True,
    )
    assert result.returncode != 0
    assert b"PermissionError" in result.stderr
    result = subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            str(sandbox),
            str(ROOT / ".venv/bin/python"),
            "-c",
            "print(4+5)",
        ],
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == b"9"
