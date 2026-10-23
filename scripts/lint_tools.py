#!/usr/bin/env python3
"""Pinned standard lint tools: install, secret scan, workflow lint and self-test (bu-28av8x.4).

Stdlib only. ``install`` downloads the pinned gitleaks/actionlint release archives listed in
``scripts/lint-tool-versions.json``, verifies their SHA-256 digests and atomically publishes
each binary into a worktree-owned ignored cache. Every other command uses only that cache:
it never downloads, and a missing tool is a failure with the install command, never a skip.

``secrets-scan`` scans the checked-out source (Git-tracked plus untracked non-ignored
files), not Git history, other worktrees or ignored caches. It prints only the relative
path, line and rule of each finding; the secret, match and every other report field stay in
a private temporary directory that is deleted afterwards.

Usage:
    python3 scripts/lint_tools.py install
    python3 scripts/lint_tools.py secrets-scan
    python3 scripts/lint_tools.py lint-workflows
    python3 scripts/lint_tools.py self-test
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import io
import json
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.request
from contextlib import ExitStack
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
PINS_PATH = REPO_ROOT / "scripts" / "lint-tool-versions.json"
GITLEAKS_CONFIG = REPO_ROOT / ".gitleaks.toml"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
# Tests point the cache elsewhere; the default lives under the ignored .tmp/.
CACHE_ENV = "BUTLERS_LINT_TOOLS_DIR"
DEFAULT_CACHE_DIR = REPO_ROOT / ".tmp" / "lint-tools"
INSTALL_HINT = "make install-lint-tools"

# Explicit safety bounds, not calibrated performance claims.
DOWNLOAD_TIMEOUT_S = 60.0
TOOL_TIMEOUT_S = 300.0
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_BINARY_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024

# gitleaks exits 1 on its own fatal errors, so findings get a distinct status.
GITLEAKS_FINDINGS_EXIT = 3
# actionlint: 0 clean, 1 findings, 2 invalid command line, 3 fatal error.
ACTIONLINT_FINDINGS_EXIT = 1
# actionlint itself expands the two characters ``\n`` to a newline before parsing the
# template, so a quoted ``{{"\n"}}`` would become an unterminated string.
ACTIONLINT_FORMAT = (
    "{{range $err := .}}{{$err.Filepath}}:{{$err.Line}}:{{$err.Column}}: {{$err.Kind}}\\n{{end}}"
)

PLATFORMS = {
    ("Linux", "x86_64"): "linux-x86_64",
    ("Linux", "amd64"): "linux-x86_64",
    ("Linux", "aarch64"): "linux-aarch64",
    ("Linux", "arm64"): "linux-aarch64",
    ("Darwin", "arm64"): "darwin-arm64",
    ("Darwin", "x86_64"): "darwin-x86_64",
}


class ToolError(Exception):
    """A categorical nonpass: missing/wrong tool, bad asset, tool failure or bad output."""


# -- pins and cache ------------------------------------------------------------------------


def cache_dir() -> Path:
    return Path(os.environ.get(CACHE_ENV) or DEFAULT_CACHE_DIR)


def platform_key() -> str:
    key = PLATFORMS.get((platform.system(), platform.machine()))
    if key is None:
        raise ToolError(f"unsupported platform {platform.system()}/{platform.machine()}")
    return key


def load_pins(path: Path = PINS_PATH) -> dict[str, dict]:
    try:
        tools = json.loads(path.read_text(encoding="utf-8"))["tools"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ToolError(f"malformed pin file {path.name}") from exc
    for name, pin in tools.items():
        if not (
            isinstance(pin, dict)
            and isinstance(pin.get("version"), str)
            and pin.get("binary") == name
            and isinstance(pin.get("assets"), dict)
        ):
            raise ToolError(f"malformed pin for {name}")
        for asset in pin["assets"].values():
            digest = asset.get("sha256", "") if isinstance(asset, dict) else ""
            url = asset.get("url", "") if isinstance(asset, dict) else ""
            if len(digest) != 64 or not url.startswith("https://github.com/"):
                raise ToolError(f"malformed asset pin for {name}")
    return tools


def tool_home(name: str, pin: dict, key: str) -> Path:
    return cache_dir() / f"{name}-{pin['version']}-{key}"


def installed_binary(name: str, pins: dict[str, dict] | None = None) -> Path:
    """The verified cached binary for ``name``; refuses a missing or mismatched install."""
    pins = pins if pins is not None else load_pins()
    if name not in pins:
        raise ToolError(f"{name} is not a pinned lint tool")
    pin = pins[name]
    key = platform_key()
    asset = pin["assets"].get(key)
    if asset is None:
        raise ToolError(f"{name} has no pinned asset for {key}")
    home = tool_home(name, pin, key)
    binary = home / pin["binary"]
    try:
        marker = json.loads((home / "INSTALLED.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError(f"{name} {pin['version']} is not installed; run `{INSTALL_HINT}`") from exc
    expected = {"tool": name, "version": pin["version"], "sha256": asset["sha256"]}
    if not isinstance(marker, dict) or {k: marker.get(k) for k in expected} != expected:
        raise ToolError(f"{name} install does not match its pin; run `{INSTALL_HINT}`")
    mode = binary.lstat().st_mode if binary.exists() else 0
    if not stat.S_ISREG(mode) or not os.access(binary, os.X_OK):
        raise ToolError(f"{name} binary is missing or not executable; run `{INSTALL_HINT}`")
    # Re-hash on every use: a binary changed after its verified install is not the pinned tool.
    if hashlib.sha256(binary.read_bytes()).hexdigest() != marker.get("binary_sha256"):
        raise ToolError(f"{name} binary changed since its verified install; run `{INSTALL_HINT}`")
    return binary


# -- install ------------------------------------------------------------------------------


def download(url: str, expected_sha256: str) -> bytes:
    """Fetch ``url`` within the size/time bounds and verify its digest."""
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_S
    chunks: list[bytes] = []
    size = 0
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as response:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise ToolError(f"download exceeds {MAX_ARCHIVE_BYTES} bytes: {url}")
                if time.monotonic() > deadline:
                    raise ToolError(f"download exceeded {DOWNLOAD_TIMEOUT_S:.0f}s: {url}")
                chunks.append(chunk)
    except OSError as exc:
        raise ToolError(f"download failed: {url}") from exc
    data = b"".join(chunks)
    verify_digest(data, expected_sha256)
    return data


def verify_digest(data: bytes, expected_sha256: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ToolError(f"sha256 mismatch: expected {expected_sha256}, got {actual}")


def extract_binary(archive: bytes, binary: str) -> bytes:
    """The single top-level regular file ``binary`` from a .tar.gz, refusing anything else."""
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            matches = [
                m for m in tar.getmembers() if PurePosixPath(m.name) == PurePosixPath(binary)
            ]
            if len(matches) != 1 or not matches[0].isreg():
                raise ToolError(f"archive has no single regular top-level {binary}")
            member = matches[0]
            if member.size > MAX_BINARY_BYTES:
                raise ToolError(f"{binary} exceeds {MAX_BINARY_BYTES} bytes")
            handle = tar.extractfile(member)
            if handle is None:
                raise ToolError(f"cannot read {binary} from archive")
            data = handle.read(MAX_BINARY_BYTES + 1)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ToolError(f"unreadable archive for {binary}") from exc
    if len(data) != member.size:
        raise ToolError(f"truncated {binary} in archive")
    return data


def publish(name: str, pin: dict, key: str, binary_bytes: bytes) -> None:
    """Stage the complete tool directory, then rename it into place in one step."""
    home = tool_home(name, pin, key)
    home.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=home.parent, prefix=f".{name}-staging-") as staging:
        staged = Path(staging) / home.name
        staged.mkdir()
        binary = staged / pin["binary"]
        binary.write_bytes(binary_bytes)
        binary.chmod(0o755)
        marker = {
            "tool": name,
            "version": pin["version"],
            "sha256": pin["assets"][key]["sha256"],
            "binary_sha256": hashlib.sha256(binary_bytes).hexdigest(),
        }
        (staged / "INSTALLED.json").write_text(json.dumps(marker) + "\n", encoding="utf-8")
        if home.exists():
            # Move the rejected install aside inside the staging dir; it is removed with it.
            home.rename(Path(staging) / "rejected")
        try:
            staged.rename(home)
        except OSError:
            # A concurrent installer published first; accept it only if it verifies.
            installed_binary(name)


def install(names: list[str] | None = None) -> None:
    pins = load_pins()
    key = platform_key()
    for name in names or sorted(pins):
        pin = pins[name]
        try:
            installed_binary(name, pins)
            print(f"{name} {pin['version']}: already installed")
            continue
        except ToolError:
            pass
        asset = pin["assets"].get(key)
        if asset is None:
            raise ToolError(f"{name} has no pinned asset for {key}")
        archive = download(asset["url"], asset["sha256"])
        publish(name, pin, key, extract_binary(archive, pin["binary"]))
        installed_binary(name, pins)
        print(f"{name} {pin['version']}: installed ({key}, sha256 verified)")


# -- bounded tool execution ----------------------------------------------------------------


def run_tool(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> int:
    """Run ``cmd`` with output captured privately; refuse timeouts and oversized output.

    The captured output is discarded unread except for its size: tool messages can quote
    scanned content, so only the exit status leaves this function.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            completed = subprocess.run(
                cmd, cwd=cwd, env=env, stdout=out, stderr=err, timeout=TOOL_TIMEOUT_S, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"{Path(cmd[0]).name} exceeded {TOOL_TIMEOUT_S:.0f}s") from exc
        except OSError as exc:
            raise ToolError(f"{Path(cmd[0]).name} could not be executed") from exc
        for stream in (out, err):
            if stream.seek(0, os.SEEK_END) > MAX_OUTPUT_BYTES:
                raise ToolError(f"{Path(cmd[0]).name} output exceeds {MAX_OUTPUT_BYTES} bytes")
    return completed.returncode


def run_tool_stdout(cmd: list[str], *, cwd: Path) -> tuple[int, str]:
    """Like ``run_tool`` but returns stdout, for tools whose stdout is format-controlled."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            completed = subprocess.run(
                cmd, cwd=cwd, stdout=out, stderr=err, timeout=TOOL_TIMEOUT_S, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"{Path(cmd[0]).name} exceeded {TOOL_TIMEOUT_S:.0f}s") from exc
        except OSError as exc:
            raise ToolError(f"{Path(cmd[0]).name} could not be executed") from exc
        if err.seek(0, os.SEEK_END) > MAX_OUTPUT_BYTES:
            raise ToolError(f"{Path(cmd[0]).name} output exceeds {MAX_OUTPUT_BYTES} bytes")
        if out.seek(0, os.SEEK_END) > MAX_OUTPUT_BYTES:
            raise ToolError(f"{Path(cmd[0]).name} output exceeds {MAX_OUTPUT_BYTES} bytes")
        out.seek(0)
        return completed.returncode, out.read().decode("utf-8", errors="replace")


# -- secret scan ---------------------------------------------------------------------------


def source_paths(root: Path = REPO_ROOT) -> list[str]:
    """Git-tracked plus untracked non-ignored paths, validated as in-checkout relative paths."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if listing.returncode != 0:
        raise ToolError("git ls-files failed")
    paths: list[str] = []
    for raw in listing.stdout.split(b"\0"):
        if not raw:
            continue
        rel = os.fsdecode(raw)
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise ToolError("git listed a path outside the checkout")
        paths.append(rel)
    return sorted(set(paths))


def _open_source_file(root_fd: int, rel: str) -> int | None:
    """Open one Git-listed file without following any source-path symlink.

    Directory descriptors retain the traversal boundary even if a path is replaced while
    exporting. The caller owns the returned file descriptor; no subsequent source path read
    may replace it. Missing working-tree paths and final symlinks are excluded as before.
    """
    parts = PurePosixPath(rel).parts
    with ExitStack() as directories:
        parent_fd = root_fd
        for part in parts[:-1]:
            try:
                directory_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
                )
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise ToolError(
                    "source export: ancestor is not an accessible real directory"
                ) from exc
            directories.callback(os.close, directory_fd)
            parent_fd = directory_fd
        try:
            return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                return None  # a final-component symlink, never followed
            raise


def export_source(dest: Path, root: Path = REPO_ROOT) -> int:
    """Copy regular source files into private ``dest`` (0600), using confined source opens."""
    copied = 0
    paths = source_paths(root)
    try:
        with ExitStack() as checkout:
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            checkout.callback(os.close, root_fd)
            for rel in paths:
                source_fd = _open_source_file(root_fd, rel)
                if source_fd is None:
                    continue
                try:
                    if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                        continue  # gitlinks/directories and special files are not source files
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with (
                        os.fdopen(source_fd, "rb", closefd=False) as source,
                        os.fdopen(target_fd, "wb") as output,
                    ):
                        shutil.copyfileobj(source, output)
                    copied += 1
                finally:
                    os.close(source_fd)
    except OSError as exc:
        # Raw filesystem errors can contain untrusted paths. Never print them.
        raise ToolError("source export failed") from exc
    return copied


def gitleaks_command(gitleaks: Path, report: Path, ignore_dir: Path) -> list[str]:
    """Scan the working directory, so reported paths (and allowlist paths) are relative."""
    return [
        str(gitleaks),
        "dir",
        ".",
        f"--config={GITLEAKS_CONFIG}",
        "--redact=100",
        "--no-banner",
        "--no-color",
        "--log-level=error",
        "--ignore-gitleaks-allow",
        "--report-format=json",
        f"--report-path={report}",
        f"--exit-code={GITLEAKS_FINDINGS_EXIT}",
        # An empty directory: no unreviewed .gitleaksignore fingerprint can bypass a rule.
        f"--gitleaks-ignore-path={ignore_dir}",
    ]


def scan_directory(target: Path, gitleaks: Path) -> list[tuple[str, int, str]]:
    """Sanitized ``(relative path, line, rule id)`` findings for ``target``."""
    with tempfile.TemporaryDirectory(prefix="butlers-gitleaks-") as private:
        report = Path(private) / "report.json"
        ignore_dir = Path(private) / "no-ignore"
        ignore_dir.mkdir()
        # Ambient GITLEAKS_* settings must not change the reviewed rules.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GITLEAKS")}
        status = run_tool(gitleaks_command(gitleaks, report, ignore_dir), cwd=target, env=env)
        if status not in (0, GITLEAKS_FINDINGS_EXIT):
            raise ToolError(f"gitleaks failed with exit status {status}")
        findings = parse_report(report)
        if (status == 0) != (not findings):
            raise ToolError("gitleaks exit status and report disagree")
        return findings


def parse_report(report: Path) -> list[tuple[str, int, str]]:
    try:
        if report.stat().st_size > MAX_OUTPUT_BYTES:
            raise ToolError("gitleaks report exceeds the size bound")
        rows = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError("gitleaks report is missing or malformed") from exc
    if not isinstance(rows, list):
        raise ToolError("gitleaks report is not a list")
    findings = []
    for row in rows:
        if not isinstance(row, dict):
            raise ToolError("gitleaks report row is malformed")
        file, line, rule = row.get("File"), row.get("StartLine"), row.get("RuleID")
        if not (isinstance(file, str) and isinstance(line, int) and isinstance(rule, str)):
            raise ToolError("gitleaks report row is malformed")
        rel = PurePosixPath(file)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise ToolError("gitleaks reported a path outside the scanned directory")
        findings.append((str(rel), line, rule))
    return sorted(findings)


def secrets_scan() -> int:
    gitleaks = installed_binary("gitleaks")
    if not GITLEAKS_CONFIG.is_file():
        raise ToolError(f"missing reviewed config {GITLEAKS_CONFIG.name}")
    with tempfile.TemporaryDirectory(prefix="butlers-secrets-scan-") as export:
        copied = export_source(Path(export))
        findings = scan_directory(Path(export), gitleaks)
    if findings:
        for rel, line, rule in findings:
            print(f"{rel}:{line}: {rule}")
        print(
            f"secrets-scan: {len(findings)} finding(s). Values are withheld; if any is a real "
            "credential, tell the owner so it can be rotated."
        )
        return 1
    print(f"secrets-scan: clean ({copied} source files)")
    return 0


# -- workflow lint -------------------------------------------------------------------------


def workflow_files(directory: Path = WORKFLOWS_DIR) -> list[Path]:
    files = sorted(p for p in directory.iterdir() if p.suffix in {".yml", ".yaml"} and p.is_file())
    if not files:
        raise ToolError(f"no workflows found in {directory}")
    return files


def actionlint_command(actionlint: Path, files: list[Path]) -> list[str]:
    # Empty -shellcheck=/-pyflakes= keep the checks identical on every host.
    return [
        str(actionlint),
        "-no-color",
        "-shellcheck=",
        "-pyflakes=",
        "-format",
        ACTIONLINT_FORMAT,
        *(str(f) for f in files),
    ]


def lint_files(files: list[Path], actionlint: Path, cwd: Path) -> list[str]:
    status, stdout = run_tool_stdout(actionlint_command(actionlint, files), cwd=cwd)
    lines = [line for line in stdout.splitlines() if line.strip()]
    if status == 0 and not lines:
        return []
    if status == ACTIONLINT_FINDINGS_EXIT and lines:
        return lines
    raise ToolError(f"actionlint failed with exit status {status}")


def lint_workflows() -> int:
    actionlint = installed_binary("actionlint")
    files = workflow_files()
    findings = lint_files([f.relative_to(REPO_ROOT) for f in files], actionlint, REPO_ROOT)
    if findings:
        print("\n".join(findings))
        print(
            f"lint-workflows: {len(findings)} finding(s); run `{actionlint} -shellcheck= "
            "-pyflakes= <file>` for the full messages"
        )
        return 1
    print(f"lint-workflows: clean ({len(files)} workflows)")
    return 0


# -- self-test -----------------------------------------------------------------------------

VALID_WORKFLOW = """\
name: valid
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

# ``runs-on`` is missing and the expression is unterminated.
INVALID_WORKFLOW = """\
name: invalid
on: push
jobs:
  build:
    steps:
      - run: echo ${{ github.sha
"""


def allowance_paths(config: Path = GITLEAKS_CONFIG) -> list[str]:
    """Every exact path a reviewed ``.gitleaks.toml`` allowance covers.

    Refuses an allowance that is not AND-scoped to named rules and exact literal paths,
    so no entry can widen to a directory, a glob or every rule.
    """
    try:
        allowances = tomllib.loads(config.read_text(encoding="utf-8")).get("allowlists", [])
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ToolError(f"unreadable reviewed config {config.name}") from exc
    paths: list[str] = []
    for allowance in allowances:
        if allowance.get("condition") != "AND" or not allowance.get("targetRules"):
            raise ToolError("self-test: every allowance must be AND-scoped to named rules")
        if not allowance.get("paths"):
            raise ToolError("self-test: every allowance must name exact paths")
        for pattern in allowance["paths"]:
            literal = re.fullmatch(r"\^((?:[^\\^$*+?()\[\]{}|.]|\\\.)+)\$", pattern)
            if literal is None:
                raise ToolError("self-test: an allowance path is not an exact literal path")
            paths.append(literal.group(1).replace("\\.", "."))
    return sorted(set(paths))


def synthetic_generic_secrets() -> list[str]:
    """Fresh random lines in two real-looking forms the default generic rule detects.

    The alphabets have no vowels, since the generic rule ignores values containing one of
    its stopwords. A few short stopwords have no vowels, so callers plant several values
    of each form and require at least one of each to be caught.
    """
    mixed = "BCDFGHJKLMNPQRSTVWXZbcdfghjklmnpqrstvwxz0123456789"
    lower = "bcdfghjklmnpqrstvwxz0123456789"
    return [
        'api_key = "' + "".join(secrets.choice(mixed) for _ in range(40)) + '"',
        "api_key: key_" + "".join(secrets.choice(lower) for _ in range(32)),
    ]


def synthetic_access_key_id() -> str:
    """A freshly random, never-issued AWS-shaped key id that the default rules detect."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "AKIA" + "".join(secrets.choice(alphabet) for _ in range(16))


def self_test() -> int:
    """Prove the pinned detectors on disposable inputs: a planted nonsecret and bad workflow."""
    gitleaks = installed_binary("gitleaks")
    actionlint = installed_binary("actionlint")
    with tempfile.TemporaryDirectory(prefix="butlers-lint-selftest-") as tmp:
        root = Path(tmp)
        clean = root / "clean"
        clean.mkdir()
        (clean / "notes.txt").write_text("nothing to see\n", encoding="utf-8")
        if scan_directory(clean, gitleaks):
            raise ToolError("self-test: gitleaks flagged a clean file")

        planted = root / "planted"
        planted.mkdir()
        key_id = synthetic_access_key_id()
        (planted / "config.txt").write_text(f"aws_access_key_id = {key_id}\n", encoding="utf-8")
        findings = scan_directory(planted, gitleaks)
        if [(rel, rule) for rel, _line, rule in findings] != [("config.txt", "aws-access-token")]:
            raise ToolError("self-test: gitleaks did not report the planted key id")
        # Redaction: the planted value must not survive in the report gitleaks writes.
        with tempfile.TemporaryDirectory(prefix="butlers-gitleaks-") as private:
            report = Path(private) / "report.json"
            ignore_dir = Path(private) / "no-ignore"
            ignore_dir.mkdir()
            cmd = gitleaks_command(gitleaks, report, ignore_dir)
            run_tool(cmd, cwd=planted)
            if key_id in report.read_text(encoding="utf-8"):
                raise ToolError("self-test: gitleaks report contains the planted value")

        # Every allowance must still catch a real-shaped value planted in its own files.
        probes = root / "allowance-probes"
        paths = allowance_paths()
        for rel in paths:
            probe = probes / rel
            probe.parent.mkdir(parents=True, exist_ok=True)
            # Lines 1-3 hold the quoted mixed-case form, lines 4-6 the prefixed lowercase form.
            planted = [synthetic_generic_secrets() for _ in range(3)]
            lines = [pair[0] for pair in planted] + [pair[1] for pair in planted]
            probe.write_text("\n".join(lines) + "\n", encoding="utf-8")
        caught = {
            (rel, line <= 3)
            for rel, line, rule in scan_directory(probes, gitleaks)
            if rule == "generic-api-key"
        }
        for rel in paths:
            if not {(rel, True), (rel, False)} <= caught:
                raise ToolError(f"self-test: an allowance hides a real-shaped value in {rel}")

        workflows = root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "valid.yml").write_text(VALID_WORKFLOW, encoding="utf-8")
        (workflows / "invalid.yml").write_text(INVALID_WORKFLOW, encoding="utf-8")
        if lint_files([Path(".github/workflows/valid.yml")], actionlint, root):
            raise ToolError("self-test: actionlint flagged a valid workflow")
        if not lint_files([Path(".github/workflows/invalid.yml")], actionlint, root):
            raise ToolError("self-test: actionlint accepted a malformed workflow")
    print("lint-tools self-test: gitleaks and actionlint detect their planted faults")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command", choices=["install", "secrets-scan", "lint-workflows", "self-test"]
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            install()
            return 0
        if args.command == "secrets-scan":
            return secrets_scan()
        if args.command == "lint-workflows":
            return lint_workflows()
        return self_test()
    except ToolError as exc:
        print(f"lint_tools {args.command}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
