"""Run-outcome contract for ``deploy/backup/restic_offsite_copy.sh`` (bu-ivuf9u).

The script copies the dump directory to an encrypted restic repository on the
owner's tailnet device and rewrites ``offsite_last_run.json`` after every run.
These tests run the real script under this host's ``/bin/sh`` against a stub
``restic`` -- no network, no device, no real secret -- and pin:

* every outcome records itself, parsed by the dashboard's own reader;
* missing configuration fails before restic is ever called;
* an uninitialized repository is never initialized unless asked;
* ssh pins the host key and the receipt carries no command output.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from butlers.core.backup_facts import _read_offsite_copy_facts

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "backup" / "restic_offsite_copy.sh"

#: Records argv, then behaves per FAKE_RESTIC_MODE. ``cat config`` stands for
#: "repository reachable and initialized".
_FAKE_RESTIC = """#!/bin/sh
printf '%s\\n' "$*" >> "${FAKE_RESTIC_LOG:?}"
mode="${FAKE_RESTIC_MODE:-ok}"
for arg in "$@"; do
  case "$arg" in
    cat)
      [ "$mode" = "repo_missing" ] && { echo "Fatal: repository does not exist" >&2; exit 10; }
      exit 0 ;;
    init) exit 0 ;;
    backup)
      [ "$mode" = "backup_fail" ] && { echo "Fatal: unable to save snapshot" >&2; exit 1; }
      echo '{"message_type":"status","percent_done":1}'
      echo '{"message_type":"summary","files_new":1,"snapshot_id":"0a1b2c3d4e5f60718293a4b5c6d7e8f9"}'
      exit 0 ;;
    forget)
      [ "$mode" = "forget_fail" ] && exit 1
      exit 0 ;;
  esac
done
exit 0
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    restic = bin_dir / "restic"
    restic.write_text(_FAKE_RESTIC)
    restic.chmod(restic.stat().st_mode | stat.S_IXUSR)

    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "butlers_2026-11-01T02-00-00.sql.gz").write_bytes(b"x" * 512)

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for name in ("password", "ssh-key", "known-hosts"):
        (secrets / name).write_text("synthetic\n")

    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_RESTIC_LOG": str(tmp_path / "restic.log"),
        "BACKUP_DIR": str(backups),
        "OFFSITE_HOST": "device.invalid",
        "OFFSITE_USER": "restic",
        "OFFSITE_REPO_PATH": "/srv/restic/butlers",
        "RESTIC_PASSWORD_FILE": str(secrets / "password"),
        "OFFSITE_SSH_KEY_FILE": str(secrets / "ssh-key"),
        "OFFSITE_KNOWN_HOSTS_FILE": str(secrets / "known-hosts"),
        "RESTIC_CACHE_DIR": str(tmp_path / "cache"),
    }


def _run(env: dict[str, str], **overrides: str) -> tuple[int, list[str]]:
    merged = {**env, **overrides}
    merged = {k: v for k, v in merged.items() if v != ""}
    proc = subprocess.run(
        ["/bin/sh", str(_SCRIPT)], env=merged, capture_output=True, text=True, timeout=30
    )
    log = Path(env["FAKE_RESTIC_LOG"])
    calls = log.read_text().splitlines() if log.exists() else []
    return proc.returncode, calls


def _facts(env: dict[str, str]):
    return _read_offsite_copy_facts(Path(env["BACKUP_DIR"]), expected=True)


def test_successful_copy_records_snapshot_and_pins_the_host_key(env):
    code, calls = _run(env)

    assert code == 0
    facts = _facts(env)
    assert (facts.result, facts.reason, facts.stale) == ("success", "ok", False)
    assert facts.snapshot_id == "0a1b2c3d"
    backup = next(c for c in calls if " backup " in f" {c} ")
    assert "StrictHostKeyChecking=yes" in backup
    assert any(" forget " in f" {c} " and "--prune" in c for c in calls)
    assert not any(c.split()[-1:] == ["init"] for c in calls)


@pytest.mark.parametrize(
    ("overrides", "reason", "restic_called"),
    [
        ({"OFFSITE_HOST": ""}, "config_missing", False),
        ({"RESTIC_PASSWORD_FILE": "/nonexistent/password"}, "config_missing", False),
        ({"FAKE_RESTIC_MODE": "repo_missing"}, "repo_unreachable", True),
        ({"FAKE_RESTIC_MODE": "backup_fail"}, "backup_failed", True),
        ({"FAKE_RESTIC_MODE": "forget_fail"}, "retention_failed", True),
    ],
)
def test_each_failure_records_its_own_reason(env, overrides, reason, restic_called):
    code, calls = _run(env, **overrides)

    assert code != 0
    facts = _facts(env)
    assert (facts.result, facts.reason) == ("failed", reason)
    assert bool(calls) is restic_called
    if reason == "repo_unreachable":
        # Never initialize a repository nobody asked for.
        assert not any(" backup " in f" {c} " or c.endswith("init") for c in calls)


def test_no_local_dump_fails_without_contacting_the_device(env):
    for dump in Path(env["BACKUP_DIR"]).glob("butlers_*.sql.gz"):
        dump.unlink()

    code, calls = _run(env)

    assert code != 0
    assert _facts(env).reason == "no_dumps"
    assert calls == []


def test_missing_repository_is_initialized_only_when_asked(env):
    code, calls = _run(env, FAKE_RESTIC_MODE="repo_missing", OFFSITE_INIT_REPOSITORY="true")

    assert any(c.endswith("init") for c in calls)
    # The stub keeps reporting "missing" after init; the backup still runs.
    assert code == 0
    assert _facts(env).result == "success"
