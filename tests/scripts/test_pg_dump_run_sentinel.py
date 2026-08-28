"""Run-outcome contract for ``deploy/backup/pg_dump.sh`` (bu-xrqyu).

A failed run publishes no artifact and deletes none, so the backup directory
after a failure is byte-identical to the directory before it: yesterday's good
dump, sitting there looking fresh. The dashboard's freshness check therefore
cannot see a failed run at all until the last *success* crosses the 36h
staleness threshold -- up to a day and a half of consecutive failures reading
as healthy, and then an alarm that fires for the wrong reason.

The script closes that by rewriting ``BACKUP_DIR/last_run.json`` at the end of
every run, from its EXIT trap. These tests run the real script against stubbed
``pg_dump``/``gzip``/``mv`` binaries -- no database and no container -- and pin:

* every failure mode records itself, including one nobody enumerated;
* a failure leaves the previous artifact untouched (the invisible case);
* the receipt the script writes is the receipt the dashboard reads, parsed by
  the actual endpoint reader rather than by a second copy of the format;
* all of it holds under this host's ``/bin/sh`` -- the shebang says ``sh``, and
  a bashism like `set -o pipefail` kills this script on line 1 under ``dash``
  while working fine under the sidecar's Alpine ``ash`` (see AGENTS.md). The
  script is additionally parsed by BusyBox ``ash`` where available, but not
  *executed* under it: Ubuntu's BusyBox build has no ``find -delete``, so a
  run would fail in the prune step on a difference between BusyBox builds
  rather than on anything about this script. Execution in the real
  ``postgres:17-alpine`` sidecar is covered by the docker test below.

``tests/scripts/test_pg_dump_backup.py`` is the other half of this script's
coverage: what it dumps, proven against a real bootstrapped database in the
real sidecar image. This file stays deliberately DB-free and docker-free, so
the run-outcome contract is verifiable without either.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from butlers.api.routers.system import _read_backup_run_facts

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "deploy" / "backup" / "pg_dump.sh"

#: The shebang is ``#!/bin/sh``; on Debian/Ubuntu that is ``dash``, which is
#: where a bashism shows up as a line-1 death.
_SHELL = "/bin/sh"

#: pg_dump stand-in: emits incompressible hex so the gzipped artifact clears
#: the 256-byte floor regardless of compression ratio, or fails on demand.
_FAKE_PG_DUMP = """#!/bin/sh
if [ "${FAKE_PG_DUMP_MODE:-ok}" = "fail" ]; then
  echo "pg_dump: error: synthetic permission denied" >&2
  exit 1
fi
if [ "${FAKE_PG_DUMP_MODE:-ok}" = "empty" ]; then
  exit 0
fi
dd if=/dev/urandom bs=1024 count=8 2>/dev/null | od -A n -t x1
"""

#: gzip stand-in that compresses to something that is not gzip, and fails the
#: integrity read -- the bit-rot/truncation shape, without corrupting bytes by
#: hand and hoping the CRC notices.
_FAKE_GZIP = """#!/bin/sh
case "$1" in
  -dc) echo "gzip: synthetic corruption" >&2; exit 1 ;;
esac
cat > /dev/null
dd if=/dev/urandom bs=512 count=1 2>/dev/null
"""

#: mv stand-in that fails only when publishing the artifact -- a full disk at
#: the last step. The script never enumerated this one, which is the point:
#: the receipt comes from the EXIT trap, not from a list of known failures.
_FAKE_MV = """#!/bin/sh
for arg in "$@"; do
  case "${arg}" in
    *.sql.gz) echo "mv: synthetic publish failure" >&2; exit 1 ;;
  esac
done
exec /bin/mv "$@"
"""


def _install_stub(bin_dir: Path, name: str, body: str) -> None:
    stub = bin_dir / name
    stub.write_text(body, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run(
    backup_dir: Path,
    bin_dir: Path,
    **env_overrides: str,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["BACKUP_DIR"] = str(backup_dir)
    env["BACKUP_RETAIN_DAYS"] = "10000"
    env.update(env_overrides)
    return subprocess.run(
        [_SHELL, str(_SCRIPT)], env=env, capture_output=True, text=True, timeout=60
    )


def _receipt(backup_dir: Path) -> dict:
    raw = (backup_dir / "last_run.json").read_text(encoding="utf-8")
    assert raw.count("\n") == 1, "receipt must be a single JSON line"
    return json.loads(raw)


def _dumps(backup_dir: Path) -> list[Path]:
    return sorted(backup_dir.glob("butlers_*.sql.gz"))


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    _install_stub(d, "pg_dump", _FAKE_PG_DUMP)
    return d


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    d = tmp_path / "backups"
    d.mkdir()
    return d


def test_successful_run_records_success_and_names_the_artifact(backup_dir: Path, bin_dir: Path):
    proc = _run(backup_dir, bin_dir)

    assert proc.returncode == 0, proc.stderr
    published = _dumps(backup_dir)
    assert len(published) == 1
    receipt = _receipt(backup_dir)
    assert receipt["result"] == "success"
    assert receipt["reason"] == "ok"
    assert receipt["exit_code"] == 0
    assert receipt["artifact"] == published[0].name


@pytest.mark.parametrize(
    ("stubs", "env", "expected_reason"),
    [
        ({}, {"FAKE_PG_DUMP_MODE": "fail"}, "pg_dump_failed"),
        ({}, {"FAKE_PG_DUMP_MODE": "empty"}, "artifact_undersize"),
        ({"gzip": _FAKE_GZIP}, {}, "artifact_corrupt"),
        ({"mv": _FAKE_MV}, {}, "unexpected_error"),
    ],
)
def test_a_failed_run_records_why_and_leaves_the_previous_artifact_alone(
    backup_dir: Path,
    bin_dir: Path,
    stubs: dict[str, str],
    env: dict[str, str],
    expected_reason: str,
):
    """The invisible case, end to end: yesterday succeeded, tonight did not.

    The surviving artifact is asserted to be byte-identical to the one the
    successful run published -- that is precisely why the directory alone
    cannot report the failure, and why the receipt has to.
    """
    first = _run(backup_dir, bin_dir)
    assert first.returncode == 0, first.stderr
    good_dump = _dumps(backup_dir)[0]
    good_bytes = good_dump.read_bytes()

    for name, body in stubs.items():
        _install_stub(bin_dir, name, body)
    failed = _run(backup_dir, bin_dir, **env)

    assert failed.returncode != 0
    assert "[backup] FAILED" in failed.stderr or "mv: synthetic" in failed.stderr
    assert _dumps(backup_dir) == [good_dump]
    assert good_dump.read_bytes() == good_bytes

    receipt = _receipt(backup_dir)
    assert receipt["result"] == "failed"
    assert receipt["reason"] == expected_reason
    assert receipt["artifact"] is None
    assert receipt["exit_code"] == failed.returncode


def test_the_dashboard_reader_parses_what_the_script_writes(backup_dir: Path, bin_dir: Path):
    """Producer and consumer, pinned together.

    ``_read_backup_run_facts`` mirrors this script's reason vocabulary by hand
    (the script runs in an Alpine sidecar and shares no code with the API), so
    a format or vocabulary change on either side has to fail somewhere. Here.
    """
    assert _run(backup_dir, bin_dir).returncode == 0
    success = _read_backup_run_facts(backup_dir)
    assert (success.result, success.reason, success.exit_code) == ("success", "ok", 0)
    assert success.finished_at is not None

    assert _run(backup_dir, bin_dir, FAKE_PG_DUMP_MODE="fail").returncode != 0
    failure = _read_backup_run_facts(backup_dir)
    assert (failure.result, failure.reason, failure.exit_code) == ("failed", "pg_dump_failed", 1)
    assert failure.finished_at is not None


def test_the_receipt_is_not_mistaken_for_a_dump(backup_dir: Path, bin_dir: Path):
    """It lives beside the dumps, so it must not be globbed or pruned as one."""
    assert _run(backup_dir, bin_dir).returncode == 0

    assert (backup_dir / "last_run.json").exists()
    assert _dumps(backup_dir) == [next(backup_dir.glob("butlers_*.sql.gz"))]
    assert not list(backup_dir.glob("*.tmp"))


@pytest.mark.skipif(shutil.which("busybox") is None, reason="BusyBox not installed")
def test_the_script_parses_under_busybox_ash():
    """The sidecar's shell is BusyBox ``ash``; it must at least parse there.

    A parse check is weaker than a run, and deliberately so: this host's
    BusyBox is a different build from Alpine's (no ``find -delete``), so
    running the script here would fail on the build, not on the script.
    """
    proc = subprocess.run(
        [shutil.which("busybox"), "ash", "-n", str(_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
