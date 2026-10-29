"""Exercise the hosted uv installer's actual retry and process-group boundary."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_uv_installer_consumers_share_the_owned_supervisor():
    """REQ-ci-shard-assurance-008: every hosted uv installer retains a finite owner."""
    consumers = []
    sources = sorted((ROOT / ".github/workflows").glob("*.yml"))
    sources += sorted((ROOT / ".github/actions").glob("*/action.yml"))
    for path in sources:
        source = yaml.safe_load(path.read_text())
        groups = source["jobs"].values() if "jobs" in source else [source.get("runs", {})]
        for job in groups:
            for step in job.get("steps", []):
                body = step.get("run", "")
                assert "https://astral.sh/uv/install.sh" not in body
                if step.get("name") == "Install uv":
                    consumers.append(path)
                    assert body.strip() == "python3 scripts/install_ci_uv.py"
    # ci.yml sets up only through the composite action (bu-29wkwj.2); the four
    # remaining inline installers are nightly.yml jobs.
    assert ROOT / ".github/actions/ci-python-environment/action.yml" in consumers
    assert [path.name for path in consumers if path.parent.name == "workflows"] == [
        "nightly.yml"
    ] * 4


@pytest.mark.parametrize(
    ("mode", "attempts", "status"),
    [
        ("success", 1, 0),
        ("retry", 2, 0),
        ("exhaust", 5, 1),
        ("resistant", 2, 0),
        ("early-exit", 1, 0),
        ("cancel", 1, 143),
        ("cleanup-failure", 1, 1),
    ],
)
def test_uv_installer_owns_actual_shell_descendants(tmp_path, mode, attempts, status):
    """REQ-ci-shard-assurance-008: retries and shell exits cannot strand owned children."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"attempts": 0, "pids": []}))
    program = r"""import json, os, signal, subprocess, sys, time
from pathlib import Path
p = Path(os.environ["UV_TEST_LEDGER"])
if Path(sys.argv[0]).name == "curl":
    assert sys.argv[1:] == ["-LsSf", "https://astral.sh/uv/install.sh"]
    import shlex
    print("exec " + shlex.quote(sys.executable) + " " + shlex.quote(str(p.parent / "install.py")))
    sys.exit(0)
data = json.loads(p.read_text())
data["attempts"] += 1
p.write_text(json.dumps(data))
mode = os.environ["UV_TEST_MODE"]
if mode in ("early-exit", "cancel") or (mode == "resistant" and data["attempts"] == 1):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    data["pids"].append(os.getpid())
    child_source = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print('ready',flush=True);time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-c", child_source], stdout=subprocess.PIPE, text=True)
    data["pids"].append(child.pid)
    p.write_text(json.dumps(data))
    assert child.stdout.readline() == "ready\n"
    child.stdout.close()
    (p.parent / "ready").touch()
    if mode != "early-exit":
        time.sleep(60)
sys.exit(1 if mode == "exhaust" or (mode == "retry" and data["attempts"] == 1) else 0)
"""
    for executable in (binaries / "curl", tmp_path / "install.py"):
        executable.write_text(f"#!{sys.executable}\n" + program)
        executable.chmod(0o755)
    # Scale only time in the real supervisor; retain its actual curl | sh shell,
    # waitid, process group, signal handling, five retries and path publication.
    launcher = """import importlib.util, os, signal, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("installer", sys.argv[1])
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
assert (installer.ATTEMPT_SECONDS, installer.CLEANUP_SECONDS, installer.MAX_ATTEMPTS, installer.BACKOFF_SECONDS) == (120, 1, 5, 10)
installer.ATTEMPT_SECONDS = 2
installer.CLEANUP_SECONDS = 0.2
original_wait = installer.subprocess.Popen.wait
def wait(process, timeout):
    assert 0 < timeout <= installer.CLEANUP_SECONDS
    result = original_wait(process, timeout=timeout)
    if os.environ["UV_TEST_MODE"] == "cleanup-failure":
        raise installer.subprocess.TimeoutExpired(process.args, timeout)
    return result
installer.subprocess.Popen.wait = wait
original_sleep = installer.time.sleep
def sleep(seconds):
    if seconds == installer.BACKOFF_SECONDS:
        with Path(sys.argv[2]).open("a") as receipt:
            receipt.write(str(seconds) + "\\n")
    else:
        original_sleep(seconds)
installer.time.sleep = sleep
signal.signal(signal.SIGTERM, installer._terminate)
signal.signal(signal.SIGINT, installer._terminate)
raise SystemExit(installer.main())
"""
    github_path = tmp_path / "github-path"
    backoffs = tmp_path / "backoffs"
    environment = dict(
        os.environ,
        PATH=str(binaries) + os.pathsep + os.environ["PATH"],
        GITHUB_PATH=str(github_path),
        UV_TEST_LEDGER=str(ledger),
        UV_TEST_MODE=mode,
    )
    command = [
        sys.executable,
        "-c",
        launcher,
        str(ROOT / "scripts/install_ci_uv.py"),
        str(backoffs),
    ]
    if mode == "success":
        action = yaml.safe_load(
            (ROOT / ".github/actions/ci-python-environment/action.yml").read_text()
        )
        step = next(s for s in action["runs"]["steps"] if s.get("name") == "Install uv")
        command = ["bash", "-euo", "pipefail", "-c", step["run"]]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        if mode == "cancel":
            deadline = time.monotonic() + 5
            while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (tmp_path / "ready").exists()
            process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        data = json.loads(ledger.read_text())
        assert process.returncode == status, (stdout, stderr)
        assert data["attempts"] == attempts
        assert (backoffs.read_text().splitlines() if backoffs.exists() else []) == ["10"] * (
            attempts - 1
        )
        assert github_path.exists() == (status == 0)
        if status == 0:
            assert github_path.read_text() == str(Path.home() / ".local/bin") + "\n"
        if mode in ("resistant", "early-exit", "cancel"):
            assert len(data["pids"]) == 2
            assert (tmp_path / "ready").exists()
        # SIGKILL delivery can settle after the supervisor is reaped. Observe
        # bounded settlement before asserting; fixture cleanup happens only below.
        deadline = time.monotonic() + 1
        while any(_alive(pid) for pid in data["pids"]) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not [pid for pid in data["pids"] if _alive(pid)]
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        for pid in json.loads(ledger.read_text())["pids"]:
            if _alive(pid):
                os.kill(pid, signal.SIGKILL)


def _alive(pid):
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False
