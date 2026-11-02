"""The bounded Playwright installer owns its process groups and APT recovery.

Executes ``frontend/scripts/install-playwright-browsers.mjs`` and
``install-playwright-deps.py`` against synthetic install/OS/browser boundaries, and checks
the frontend-e2e step deadline still follows the installer's native budget.
"""

from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

# (mode, installer calls, cache probes, exit) for each synthetic boundary scenario.
INSTALLER_MODES = [
    ("cold", 2, 1, 0),
    ("warm", 2, 1, 0),
    ("fail-once", 3, 1, 0),
    ("invalid-cache", 3, 2, 0),
    ("exhaust", 4, 0, 1),
    ("stall", 4, 0, 1),
    ("deps-exhaust", 2, 0, 1),
    ("deps-stall", 2, 0, 1),
    ("deps-recover", 3, 1, 0),
    ("deps-recovery-rejected", 1, 0, 1),
    ("deps-prep-cancel", 1, 0, 1),
    ("deps-supervisor-cancel", 1, 0, 1),
    ("deps-supervisor-eof", 1, 0, 1),
    ("deps-total-expiry", 1, 0, 1),
    ("deps-cleanup-denied", 1, 0, 1),
    ("deps-cancel", 1, 0, 1),
    ("deps-eof", 1, 0, -9),
    ("deps-setup-failure", 1, 0, 1),
    ("deps-crash", 1, 0, 1),
    ("deps-unproven-zero", 0, 0, 1),
]


def _installer_apt_fixture(directory: Path) -> tuple[Path, Path, Path]:
    """Private synthetic APT boundary; never read host sources/configuration."""
    apt = directory / "apt"
    parts = apt / "sources.list.d"
    parts.mkdir(parents=True)
    (apt / "apt-mirrors.txt").write_text(
        "http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n"
        "https://archive.ubuntu.com/ubuntu/\tpriority:2\n"
        "https://security.ubuntu.com/ubuntu/\tpriority:3\n"
    )
    (parts / "ubuntu.sources").write_text(
        "# Preserve runner comments\n\nTypes: deb\nURIs: mirror+file:/etc/apt/apt-mirrors.txt\n"
        "Suites: noble noble-updates noble-backports noble-security\n"
        "Components: main restricted universe multiverse\nArchitectures: amd64\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
    )
    # Nonsecret synthetic stand-in for a credential-bearing third-party source:
    # preservation must not turn it into world-readable copied configuration.
    (parts / "vendor.sources").write_text(
        "Types: deb\nURIs: https://packages.example.invalid/repo\n"
        "Suites: stable\nComponents: main\nSigned-By: /keys/vendor.gpg\n"
    )
    release = directory / "os-release"
    release.write_text('ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n')
    reader = directory / "fixture-apt-config"
    reader.write_text(
        f"#!{sys.executable}\n"
        "import os,re\nfrom pathlib import Path\n"
        "config = Path(os.environ['APT_CONFIG']).read_text() if os.environ.get('APT_CONFIG') else ''\n"
        "for key,default in [('sourcelist','sources.list'),('sourceparts','sources.list.d')]:\n"
        "    match = re.search(r'Dir::Etc::'+key+r' \"([^\"]+)\";',config)\n"
        "    value = match[1] if match else default\n"
        "    if Path(__file__).with_name('late-fragment').exists(): value = default\n"
        "    print('Dir::Etc::'+key+' \"'+value+'\";')\n"
        "status = '/outside' if os.environ.get('APT_CONFIG') and Path(__file__).with_name('state-fragment').exists() else '/var/lib/dpkg/status'\n"
        "print('Dir::State::status \"'+status+'\";')\n"
        "print('Dir::State::lists \"lists/\";')\n"
        "print('Dir::Cache::archives \"archives/\";')\n"
        "print('Acquire::Check-Valid-Until \"true\";')\n"
        "if Path(__file__).with_name('binary-fragment').exists():\n"
        "    print('Binary::apt-get::Dir::Etc::sourceparts \"/outside\";')\n"
    )
    reader.chmod(0o755)
    return apt, release, reader


def _exercise_apt_recovery(root: Path) -> None:
    """Execute trusted-input policy, private files and reader admission negatives.

    The reader here is explicitly synthetic. Real Ubuntu24 effective APT
    parsing/sandbox access and signed package transport require hosted proof.
    """
    source = (REPO_ROOT / "frontend/scripts/install-playwright-deps.py").read_text()
    for case in [
        "valid",
        "late-override",
        "binary-override",
        "state-override",
        "mirror-host",
        "mirror-port",
        "mirror-userinfo",
        "mirror-query",
        "mirror-fragment",
        "mirror-path",
        "mirror-encoded-path",
        "mirror-wrapper",
        "mirror-metadata",
        "missing-official",
        "duplicate-uris",
        "multiline-uris",
        "trust",
        "suite",
        "symlink",
        "oversized",
        "writable",
        "wrong-extension",
        "one-line",
        "platform",
    ]:
        directory = root / case
        apt, release, reader = _installer_apt_fixture(directory)
        copied = directory / "supervisor.py"
        copied.write_text(
            source.replace('Path("/etc/apt")', f"Path({str(apt)!r})")
            .replace('Path("/usr/lib/os-release")', f"Path({str(release)!r})")
            .replace('"/usr/bin/apt-config"', repr(str(reader)))
            .replace("TRUSTED_UID = 0", f"TRUSTED_UID = {os.getuid()}")
        )
        namespace = runpy.run_path(str(copied))
        ubuntu = apt / "sources.list.d/ubuntu.sources"
        mirrors = apt / "apt-mirrors.txt"
        before = {p: p.read_bytes() for p in apt.rglob("*") if p.is_file()}
        if case.startswith("mirror-"):
            bad = {
                "host": "https://archive.ubuntu.com.evil.invalid/ubuntu/",
                "port": "https://archive.ubuntu.com:8443/ubuntu/",
                "userinfo": "https://user@archive.ubuntu.com/ubuntu/",
                "query": "https://archive.ubuntu.com/ubuntu/?x=1",
                "fragment": "https://archive.ubuntu.com/ubuntu/#x",
                "path": "https://archive.ubuntu.com/else/",
                "encoded-path": "https://archive.ubuntu.com/%75buntu/",
                "wrapper": "mirror+https://archive.ubuntu.com/ubuntu/",
                "metadata": "https://archive.ubuntu.com/ubuntu/\tpriority:2 other:unknown",
            }[case.removeprefix("mirror-")]
            mirrors.write_text(
                mirrors.read_text().replace("https://archive.ubuntu.com/ubuntu/", bad)
            )
        elif case == "missing-official":
            mirrors.write_text(mirrors.read_text().splitlines()[0] + "\n")
        elif case == "duplicate-uris":
            ubuntu.write_text(ubuntu.read_text() + "URIs: mirror+file:/etc/apt/apt-mirrors.txt\n")
        elif case == "multiline-uris":
            ubuntu.write_text(ubuntu.read_text().replace("URIs: mirror", "URIs:\n mirror"))
        elif case == "trust":
            ubuntu.write_text(ubuntu.read_text().replace("ubuntu-archive-keyring.gpg", "other.gpg"))
        elif case == "suite":
            ubuntu.write_text(ubuntu.read_text().replace("noble-updates", "jammy-updates"))
        elif case == "symlink":
            ubuntu.rename(directory / "real.sources")
            ubuntu.symlink_to(directory / "real.sources")
        elif case == "oversized":
            ubuntu.write_bytes(b"x" * 65537)
        elif case == "writable":
            ubuntu.chmod(0o666)
        elif case == "wrong-extension":
            ubuntu.rename(ubuntu.with_suffix(".txt"))
        elif case == "one-line":
            (apt / "sources.list").write_text(
                "deb mirror+file:/etc/apt/apt-mirrors.txt noble main\n"
            )
        elif case == "platform":
            release.write_text('ID=ubuntu\nVERSION_ID="22.04"\nVERSION_CODENAME=jammy\n')
        overlay = directory / "owned"
        overlay.mkdir(mode=0o700)
        if case not in {"valid", "late-override", "binary-override", "state-override"}:
            with pytest.raises((ValueError, OSError)):
                namespace["prepare_recovery"](overlay)
            continue
        namespace["prepare_recovery"](overlay)
        private = overlay / "private"
        assert (private / "sources.list.d/vendor.sources").read_bytes() == before[
            apt / "sources.list.d/vendor.sources"
        ]
        expected = before[ubuntu].replace(
            b"mirror+file:/etc/apt/apt-mirrors.txt",
            f"mirror+file:{overlay / 'mirrors.txt'}".encode(),
        )
        assert (private / "sources.list.d/ubuntu.sources").read_bytes() == expected
        assert private.stat().st_mode & 0o777 == 0o700
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in private.rglob("*") if p.is_file())
        assert (overlay / "mirrors.txt").stat().st_mode & 0o777 == 0o644
        assert {p: p.read_bytes() for p in before} == before, "host-like inputs were rewritten"
        # Exercise the same recovery child admission without execing apt/CLI.
        if case == "late-override":
            (directory / "late-fragment").touch()
            with pytest.raises(ValueError, match="effective apt source override refused"):
                namespace["recovery_child"](overlay, "/not/executed")
        elif case == "state-override":
            (directory / "state-fragment").touch()
            with pytest.raises(ValueError, match="recovery changed ordinary apt configuration"):
                namespace["recovery_child"](overlay, "/not/executed")
        elif case == "binary-override":
            (directory / "binary-fragment").touch()
            with pytest.raises(ValueError, match="unsupported binary/root apt path override"):
                namespace["recovery_child"](overlay, "/not/executed")
        else:
            with pytest.raises(FileNotFoundError):
                namespace["recovery_child"](overlay, "/not/executed")


def _exercise_browser_installer(root: Path, only_mode: str | None = None) -> None:
    """Actual shell/group lifecycle with synthetic install/OS/browser boundaries.

    Wall-clock literals are scaled; sudo, apt configuration and install/browser
    boundaries use fixtures. No host apt/sudo is invoked. This is not actual
    Ubuntu APT-reader or network proof. Healthy
    boundaries include a deliberate 200 ms startup delay within a two-second
    allowance; stall pairs witness handler readiness before TERM/KILL checks.
    The ignored one-shot receipt exercises unchanged 110/120 browser and 150/160
    OS-dependency deadlines; actual apt/Chromium execution belongs to the hosted
    frontend-e2e job. OS dependencies (``install-deps``, apt under sudo) run as
    their own phase before the browser download so slow apt cannot consume the
    browser deadline.
    """
    source = (REPO_ROOT / "frontend/scripts/install-playwright-browsers.mjs").read_text()
    assert "TERM_MS = 110_000" in source
    assert "KILL_MS = 120_000" in source
    assert "DEPS_TERM_MS = 150_000" in source
    assert "DEPS_KILL_MS = 160_000" in source
    assert "BACKOFF_MS = 10_000" in source
    assert "TOTAL_MS = 720_000" in source
    scaled = (
        source.replace("110_000", "2_000")
        .replace("120_000", "2_500")
        .replace("150_000", "2_000")
        .replace("160_000", "2_500")
        .replace("10_000", "10")
        .replace("720_000", "12_000")
    )
    for mode, expected_calls, expected_probes, expected_exit in INSTALLER_MODES:
        if only_mode is not None and mode != only_mode:
            continue
        directory = root / mode
        scripts = directory / "scripts"
        cli = directory / "node_modules/.bin/playwright"
        scripts.mkdir(parents=True)
        cli.parent.mkdir(parents=True)
        (scripts / "install-playwright-browsers.mjs").write_text(scaled)
        shutil.copy(REPO_ROOT / "frontend/scripts/signal-group.mjs", scripts / "signal-group.mjs")
        supervisor = (REPO_ROOT / "frontend/scripts/install-playwright-deps.py").read_text()
        apt_root, release, reader = _installer_apt_fixture(directory)
        supervisor = (
            supervisor.replace('Path("/etc/apt")', f"Path({str(apt_root)!r})")
            .replace('Path("/usr/lib/os-release")', f"Path({str(release)!r})")
            .replace('"/usr/bin/apt-config"', repr(str(reader)))
            .replace("TRUSTED_UID = 0", f"TRUSTED_UID = {os.getuid()}")
        )
        if mode == "deps-recovery-rejected":
            (apt_root / "sources.list.d/ubuntu.sources").write_text("URIs: unsupported\n")
        if mode == "deps-prep-cancel":
            supervisor = supervisor.replace(
                "def main() -> int:",
                "_prepare = prepare_recovery\ndef prepare_recovery(owned):\n"
                "    _prepare(owned)\n    os.kill(os.getpid(), signal.SIGTERM)\n\ndef main() -> int:",
            )
        if mode == "deps-total-expiry":
            (scripts / "install-playwright-browsers.mjs").write_text(
                scaled.replace("const TOTAL_MS = 12_000", "const TOTAL_MS = 3_000")
            )
        if mode == "deps-supervisor-eof":
            # Close the ACTUAL pipe while Node stays live. Parent death alone
            # cannot pin cancellation versus deadline admission in that state.
            eof_source = "import { existsSync } from 'node:fs'\n" + scaled.replace(
                "let expired = false",
                "let expired = false\n"
                "const eofProbe = setInterval(() => {\n"
                "  if (existsSync(path.join(path.dirname(process.env.M3_INSTALL_LEDGER), 'stall-ready')))\n"
                "    activeDeps?.stdin.end()\n"
                "}, 10)\neofProbe.unref()",
            )
            (scripts / "install-playwright-browsers.mjs").write_text(eof_source)
        if mode == "deps-cleanup-denied":
            # Real lifecycle, with only the OS signalling boundary denied. The
            # still-running dependency pair must never race another attempt.
            supervisor = supervisor.replace(
                "os.killpg(group, signal.SIGKILL)",
                "(_ for _ in ()).throw(PermissionError('synthetic EPERM'))",
            )
        # Observe the real OS boundary, including zombie leaders. No group may
        # be signalled after its leader was reaped and its identity released.
        instrumentation = r"""
_original_mkdtemp = tempfile.mkdtemp
def _record_overlay(*args, **kwargs):
    value = _original_mkdtemp(*args, **kwargs)
    with Path(os.environ['M3_INSTALL_LEDGER']).with_name('overlay-paths').open('a') as receipt:
        receipt.write(value+'\n')
    return value
tempfile.mkdtemp = _record_overlay
_original_killpg = os.killpg
def _reserved_killpg(group, sig):
    stat = Path(f'/proc/{group}/stat')
    assert stat.exists(), 'owned group identity released before signal'
    assert int(stat.read_text().split()[4]) == group, 'reserved leader lost its group'
    with Path(os.environ['M3_INSTALL_LEDGER']).with_name('reserved-signals').open('a') as receipt:
        receipt.write(str(group)+'\n')
    return _original_killpg(group, sig)
os.killpg = _reserved_killpg

def _await_fixture():
    deadline = time.monotonic() + 5
    while not Path(os.environ['M3_INSTALL_LEDGER']).with_name('stall-ready').exists():
        assert time.monotonic() < deadline, 'fault injection never reached live dependency'
        time.sleep(0.01)
"""
        if mode in {"deps-setup-failure", "deps-crash"}:
            instrumentation += r"""
_original_selector = selectors.DefaultSelector
def _fault_selector():
    value = _original_selector()
    def register(*_args):
        _await_fixture()
        if os.environ['M3_INSTALL_MODE'] == 'deps-crash':
            os._exit(1)
        raise RuntimeError('synthetic selector setup failure')
    value.register = register
    return value
selectors.DefaultSelector = _fault_selector
"""
        elif mode == "deps-unproven-zero":
            supervisor = supervisor.replace(
                "def main() -> int:", "def main() -> int:\n    os._exit(0)"
            )
        supervisor = supervisor.replace(
            "CLEANUP_SECONDS = 2", instrumentation + "\nCLEANUP_SECONDS = 2"
        )
        (scripts / "install-playwright-deps.py").write_text(supervisor)
        sudo = directory / "sudo"
        sudo.write_text('#!/bin/sh\n[ "$1" = -n ] && shift\n[ "$1" = -- ] && shift\nexec "$@"\n')
        sudo.chmod(0o755)
        (scripts / "playwright-cache-version.mjs").write_text("// synthetic browser boundary\n")
        ledger = directory / "ledger.json"
        ledger.write_text(json.dumps({"calls": [], "probes": 0, "pids": [], "ready": []}))
        program = r"""import json, os, signal, subprocess, sys, time
from pathlib import Path
time.sleep(0.2)  # Ordinary slow fixture launch must not fail a healthy boundary.
p = Path(os.environ["M3_INSTALL_LEDGER"])
data = json.loads(p.read_text())
mode = os.environ["M3_INSTALL_MODE"]
if Path(sys.argv[0]).name == "node":
    data["probes"] += 1
    data["ready"].append({"boundary": "probe", "pid": os.getpid()})
    p.write_text(json.dumps(data))
    sys.exit(1 if mode == "invalid-cache" and data["probes"] == 1 else 0)
data["calls"].append(sys.argv[1:])
deps = sys.argv[1] == "install-deps"
if deps:
    for prior_pid in data["pids"]:
        prior = Path(f"/proc/{prior_pid}/stat")
        if prior.exists() and prior.read_text().split()[2] != "Z":
            data.setdefault("overlap", []).append(prior_pid)
recovery = False
if deps:
    route = 'http://azure.archive.ubuntu.com/ubuntu/'
    if os.environ.get('APT_CONFIG'):
        import re
        config = Path(os.environ['APT_CONFIG']).read_text()
        parts = Path(re.search(r'Dir::Etc::sourceparts "([^"]+)"', config)[1])
        sources = (parts / 'ubuntu.sources').read_text()
        mirror = Path(re.search(r'URIs: mirror\+file:([^\n]+)', sources)[1])
        candidates = [line.split() for line in mirror.read_text().splitlines()]
        route = min(candidates, key=lambda pair: int(pair[1].split(':')[1]))[0]
        data.setdefault('overlays', []).append(str(parts.parent.parent))
    data.setdefault('routes', []).append(route)
    recovery = route == 'https://archive.ubuntu.com/ubuntu/'
if mode == ("deps-stall" if deps else "stall") or (deps and mode in {"deps-recover", "deps-recovery-rejected", "deps-prep-cancel"} and not recovery) or (deps and mode in {"deps-cleanup-denied", "deps-cancel", "deps-crash", "deps-setup-failure", "deps-eof", "deps-supervisor-cancel", "deps-supervisor-eof", "deps-total-expiry", "deps-prep-cancel"}):
    if mode in {"deps-cleanup-denied", "deps-crash"}:
        # Surviving processes must not keep the harness capture pipes open.
        sink = os.open(os.devnull, os.O_WRONLY)
        os.dup2(sink, 1)
        os.dup2(sink, 2)
        os.close(sink)
    signal.signal(signal.SIGTERM, lambda *_: (p.parent / f"term-{os.getpid()}").touch())
    child_program = "import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,lambda *_: Path('term-'+str(os.getpid())).touch());print('ready',flush=True);time.sleep(60)"
    child = subprocess.Popen([sys.executable, "-c", child_program], cwd=p.parent, stdout=subprocess.PIPE, text=True)
    data["pids"].extend([os.getpid(), child.pid])
    p.write_text(json.dumps(data))
    assert child.stdout.readline() == "ready\n", "stall child never installed its TERM handler"
    child.stdout.close()
    data["ready"].append({"boundary": "stall-pair", "pids": [os.getpid(), child.pid]})
    p.write_text(json.dumps(data))
    (p.parent / "stall-ready").touch()
    if mode == 'deps-supervisor-cancel':
        os.kill(os.getppid(), signal.SIGTERM)
    time.sleep(60)
data["ready"].append({"boundary": "install", "pid": os.getpid()})
p.write_text(json.dumps(data))
browser_calls = [call for call in data["calls"] if call[0] == "install"]
if mode == ("deps-exhaust" if deps else "exhaust") or (
    mode == "fail-once" and not deps and len(browser_calls) == 1
):
    sys.exit(1)
"""
        cli.write_text(f"#!{sys.executable}\n" + program)
        cli.chmod(0o755)
        node = directory / "node"
        node.write_text(f"#!{sys.executable}\n" + program)
        node.chmod(0o755)
        env = dict(
            os.environ,
            M3_INSTALL_LEDGER=str(ledger),
            M3_INSTALL_MODE=mode,
        )
        env["PATH"] = str(directory) + os.pathsep + os.environ["PATH"]
        try:
            command = [shutil.which("node"), str(scripts / "install-playwright-browsers.mjs")]
            if mode in {"deps-cancel", "deps-eof"}:
                process = subprocess.Popen(
                    command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                deadline = time.monotonic() + 5
                while not (directory / "stall-ready").exists():
                    assert time.monotonic() < deadline, "dependency cancellation never became ready"
                    time.sleep(0.02)
                if mode == "deps-eof":
                    process.kill()
                else:
                    process.terminate()
                stdout, stderr = process.communicate(timeout=10)
                result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            else:
                result = subprocess.run(
                    command, env=env, text=True, capture_output=True, timeout=20
                )
            data = json.loads(ledger.read_text())
            assert result.returncode == expected_exit, (mode, data, result.stderr)
            assert len(data["calls"]) == expected_calls, (mode, data)
            assert not data.get("overlap"), (mode, data)
            assert data["probes"] == expected_probes, (mode, data)
            assert len(data["ready"]) == expected_calls + expected_probes, (mode, data)
            deps_calls = [args for args in data["calls"] if args[0] == "install-deps"]
            browser_calls = [args for args in data["calls"] if args[0] == "install"]
            if deps_calls and mode not in {"deps-crash", "deps-cleanup-denied"}:
                assert (directory / "reserved-signals").exists(), "group ownership never checked"
            assert data["calls"] == deps_calls + browser_calls, (mode, data)  # deps first
            assert all(args == ["install-deps", "chromium"] for args in deps_calls), data
            for index, args in enumerate(browser_calls):
                assert args == ["install", "chromium"] + (["--force"] if index else [])
            if expected_exit == 0:
                assert data["probes"] >= 1
            if mode == "deps-recover":
                assert data["routes"] == [
                    "http://azure.archive.ubuntu.com/ubuntu/",
                    "https://archive.ubuntu.com/ubuntu/",
                ], data
                assert len(data["pids"]) == 2 and not data.get("overlap"), data
            elif mode == "deps-exhaust":
                assert data["routes"] == ["http://azure.archive.ubuntu.com/ubuntu/"] * 2
            elif mode in {"cold", "warm"}:
                assert data["routes"] == ["http://azure.archive.ubuntu.com/ubuntu/"]
            assert all(not Path(overlay).exists() for overlay in data.get("overlays", [])), data
            if (directory / "overlay-paths").exists():
                assert all(
                    not Path(value).exists()
                    for value in (directory / "overlay-paths").read_text().splitlines()
                )
            if mode in {
                "deps-supervisor-cancel",
                "deps-supervisor-eof",
                "deps-total-expiry",
                "deps-prep-cancel",
            }:
                assert "outcome=83" in result.stderr, result.stderr
            if mode in {
                "deps-cleanup-denied",
                "deps-crash",
                "deps-unproven-zero",
                "deps-setup-failure",
            }:
                assert "cleanup could not be confirmed" in result.stderr
            if mode in {"stall", "deps-stall"}:
                ready_pids = [pid for pair in data["ready"] for pid in pair.get("pids", [])]
                stalled_calls = 3 if mode == "stall" else 2
                assert ready_pids == data["pids"] and len(ready_pids) == 2 * stalled_calls, data
                for pid in ready_pids:
                    assert (directory / f"term-{pid}").exists(), (
                        f"ready stall member {pid} never witnessed TERM before KILL"
                    )
            for pid in data["pids"]:
                stat = Path(f"/proc/{pid}/stat")
                if mode in {"deps-cleanup-denied", "deps-crash"}:
                    assert stat.exists() and stat.read_text().split()[2] != "Z", (
                        "denied cleanup control never reached a surviving dependency"
                    )
                elif mode.startswith("deps-"):
                    assert not stat.exists(), f"dependency descendant {pid} was not reaped"
                else:
                    assert not stat.exists() or stat.read_text().split()[2] == "Z", (
                        f"ordinary installer descendant {pid} survived watchdog"
                    )
        finally:
            for pid in json.loads(ledger.read_text())["pids"]:
                stat = Path(f"/proc/{pid}/stat")
                if stat.exists() and stat.read_text().split()[2] != "Z":
                    os.kill(pid, 9)


def _exercise_ubuntu_apt_reader(root: Path) -> None:
    """CI-only actual Ubuntu reader; no acquisition/install or host config writes.

    Local dispatch forbids host apt. Do not silently replace this boundary with
    the fixture reader above. The existing hosted owner node must execute it.
    Hosted sudo probes read only this owned scratch as actual `_apt`; signed
    package transport and real install remain separate.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    release = Path("/usr/lib/os-release").read_text()
    assert 'VERSION_ID="24.04"' in release and "ID=ubuntu" in release, (
        "actual APT-reader control requires the named Ubuntu24 environment"
    )
    assert Path("/usr/bin/apt-config").is_file() and Path("/usr/bin/apt-get").is_file()
    with tempfile.TemporaryDirectory(prefix="butlers-apt-reader-", dir="/tmp") as scratch:
        _exercise_ubuntu_apt_fixture(Path(scratch))
    root.mkdir()
    (root / "executed").write_text("actual reader/source/fragment/_apt controls completed\n")


def _exercise_ubuntu_apt_fixture(root: Path) -> None:
    # The scratch is directly under /tmp so `_apt` can traverse to nonsecret
    # mirror data without opening pytest's private parent directories.
    root.chmod(0o711)
    apt, platform, _ = _installer_apt_fixture(root)
    source = (REPO_ROOT / "frontend/scripts/install-playwright-deps.py").read_text()
    copied = root / "supervisor.py"
    copied.write_text(
        source.replace('Path("/etc/apt")', f"Path({str(apt)!r})")
        .replace('Path("/usr/lib/os-release")', f"Path({str(platform)!r})")
        .replace("TRUSTED_UID = 0", f"TRUSTED_UID = {os.getuid()}")
    )
    namespace = runpy.run_path(str(copied))
    overlay = root / "owned"
    overlay.mkdir(mode=0o700)
    namespace["prepare_recovery"](overlay)
    original_sources = {p: p.read_bytes() for p in apt.rglob("*") if p.is_file()}
    fragments = root / "apt.conf.d"
    fragments.mkdir()
    main = root / "empty.conf"
    main.write_text("")
    for name in ("state", "state/lists", "state/lists/partial", "cache", "cache/archives", "log"):
        (root / name).mkdir(exist_ok=True)
    status = root / "state/status"
    status.write_text("")
    # Both reader environments use the same private ordinary configuration,
    # state/cache and normal fragment/main loading. Recovery changes only the
    # two source paths; no host preferences, state, hooks or cache are touched.
    ordinary = (
        f'Dir::Etc::parts "{fragments}";\nDir::Etc::main "{main}";\n'
        f'Dir::State "{root / "state"}";\nDir::State::status "{status}";\n'
        f'Dir::Cache "{root / "cache"}";\nDir::Log "{root / "log"}";\n'
    )
    baseline = root / "baseline.conf"
    baseline.write_text(
        ordinary + f'Dir::Etc::sourcelist "{apt / "sources.list"}";\n'
        f'Dir::Etc::sourceparts "{apt / "sources.list.d"}";\n'
    )
    recovery = overlay / "private/apt.conf"
    recovery.write_text(ordinary + recovery.read_text())
    (fragments / "10ordinary").write_text('Acquire::Retries "0";\n')
    environment = {**os.environ, "APT_CONFIG": str(baseline)}
    # Run the actual recovery admission function in a disposable interpreter;
    # its final exec is replaced only after both real readers have admitted.
    probe = (
        "import os,runpy,sys\n"
        "def admitted(*args): raise SystemExit(0)\n"
        "os.execvpe=admitted\n"
        "m=runpy.run_path(sys.argv[1])\n"
        "m['recovery_child'](__import__('pathlib').Path(sys.argv[2]),'/not/executed')\n"
    )

    def admit() -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", probe, str(copied), str(overlay)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    assert admit().returncode == 0, "actual reader rejected positive private configuration"
    recovered = {**environment, "APT_CONFIG": str(recovery)}
    actual_configuration = namespace["apt_configuration"](recovered)
    assert not any(
        b"::pre-invoke" in line.lower() or b"::post-invoke" in line.lower()
        for line in actual_configuration
    ), "controlled parser must not inherit host hooks"
    # --print-uris enumerates source targets INSTEAD of fetching/installing.
    # All potentially written state/cache paths are disposable fixture paths.
    parsed = subprocess.run(
        ["/usr/bin/apt-get", "--print-uris", "update"],
        env=recovered,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert parsed.returncode == 0, "actual APT source parser refused private deb822 fixture"
    assert f"mirror+file:{overlay / 'mirrors.txt'}" in parsed.stdout
    assert "packages.example.invalid" in parsed.stdout, "third-party source disappeared"
    assert "mirror+file:/etc/apt/apt-mirrors.txt" not in parsed.stdout
    later = fragments / "99source-override"
    later.write_text('Dir::Etc::sourceparts "/unrecognized-later-sourceparts";\n')
    assert admit().returncode != 0, "actual later-fragment override escaped recovery admission"
    later.write_text('Binary::apt-get::Dir::Etc::sourceparts "/unrecognized-binary-path";\n')
    assert admit().returncode != 0, "actual binary override escaped recovery admission"
    later.unlink()
    assert admit().returncode == 0, "negative control poisoned positive companion"
    assert {p: p.read_bytes() for p in original_sources} == original_sources
    assert status.read_bytes() == b"", "reader proof changed fixture dpkg state"
    sudo = shutil.which("sudo")
    assert sudo is not None, "actual _apt reader proof unavailable: sudo absent"
    mirror = overlay / "mirrors.txt"
    private_config = overlay / "private/apt.conf"
    probe = (
        "import os,pwd,sys\nfrom pathlib import Path\n"
        "assert os.geteuid()==pwd.getpwnam('_apt').pw_uid\n"
        "assert Path(sys.argv[1]).read_bytes().splitlines()[0].startswith(b'https://archive.ubuntu.com/ubuntu/')\n"
        "try: Path(sys.argv[2]).read_bytes()\n"
        "except PermissionError: pass\n"
        "else: raise AssertionError('private copied config readable by _apt')\n"
    )

    def sandbox_read() -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sudo,
                "-n",
                "-u",
                "_apt",
                "--",
                "/usr/bin/python3",
                "-c",
                probe,
                str(mirror),
                str(private_config),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )

    assert sandbox_read().returncode == 0, "actual _apt scratch read/private refusal unavailable"
    mirror.chmod(0o600)
    assert sandbox_read().returncode != 0, "actual _apt read negative was not enforced"
    mirror.chmod(0o644)
    assert sandbox_read().returncode == 0, "actual _apt positive companion did not recover"
    print("actual Ubuntu24 APT reader/source, later/binary negatives and _apt read executed")


@pytest.mark.parametrize("mode", [row[0] for row in INSTALLER_MODES])
def test_browser_installer_lifecycle_is_bounded(tmp_path: Path, mode: str) -> None:
    _exercise_browser_installer(tmp_path / "browser-install", only_mode=mode)


def test_apt_recovery_admits_only_trusted_private_sources(tmp_path: Path) -> None:
    _exercise_apt_recovery(tmp_path / "source-policy")


def test_actual_ubuntu_apt_reader_controls_on_hosted_runners(tmp_path: Path) -> None:
    """Runs the real reader only on GitHub-hosted Ubuntu; local hosts must not touch apt."""
    _exercise_ubuntu_apt_reader(tmp_path / "actual-ubuntu-reader")


def test_playwright_install_step_deadline_follows_native_budget() -> None:
    jobs = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    browser_job = jobs["frontend-e2e"]
    register = json.loads((REPO_ROOT / "scripts/ci-job-timeouts.json").read_text())
    browser_budget = next(
        row
        for row in register["jobs"]
        if row["workflow"] == ".github/workflows/ci.yml" and row["job"] == "frontend-e2e"
    )
    installer_source = (REPO_ROOT / "frontend/scripts/install-playwright-browsers.mjs").read_text()
    installer_budget_ms = int(
        re.search(r"const TOTAL_MS = ([\d_]+)", installer_source)[1].replace("_", "")
    )
    assert browser_budget["review_browser_hard_retry_interval_s"] * 1000 == installer_budget_ms
    assert browser_budget["installed_timeout_minutes"] == browser_job["timeout-minutes"] == 20
    # Retain twice the historical before-only whole-job envelope for npm ci,
    # build and e2e after the complete native installer budget; this is not a
    # measurement of the current job's healthy duration or p95.
    browser_job_budget_ms = browser_job["timeout-minutes"] * 60_000
    non_installer_reserve_ms = 2 * browser_budget["p95_s"] * 1000
    assert browser_job_budget_ms >= installer_budget_ms + non_installer_reserve_ms, (
        "frontend-e2e watchdog must retain the full native installer budget "
        "and accepted non-installer reserve"
    )
    assert "provisional" in browser_budget["state"] and "source-derived" in browser_budget["state"]
    installer = next(
        step for step in browser_job["steps"] if step.get("name") == "Install Playwright browsers"
    )
    step_minutes = installer.get("timeout-minutes")
    assert type(step_minutes) is int and step_minutes > 0, (
        "Playwright install step needs a finite whole-minute emergency deadline"
    )
    # Leave native cleanup/diagnostics in control, with at most one whole
    # minute of emergency margin. This does not enlarge TOTAL_MS or count
    # the wrapper margin as available installer workload.
    step_budget_ms = step_minutes * 60_000
    assert installer_budget_ms < step_budget_ms <= installer_budget_ms + 60_000, (
        "Playwright step deadline must follow native cleanup, with at most one minute margin"
    )
    assert step_budget_ms < browser_job_budget_ms
    assert installer["run"] == "npm run test:e2e:install"
    assert "if" not in installer and not installer.get("continue-on-error")
