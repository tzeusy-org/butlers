"""Keep local CI lanes and hosted file shards contractually aligned."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(shutil.which("make") is None, reason="requires make"),
]

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("target", "fragments"),
    [
        (
            "test-ci-unit",
            (
                "scripts/pytest_gate.py run",
                "git status --porcelain",
                "git rev-parse HEAD",
                "clean=true",
                "tests/ roster/",
                "-q",
                "--maxfail=1",
                "--tb=short",
                "--ignore=tests/e2e",
                "not integration and not e2e and not nightly and not bench and not perf",
                "--cov=src/butlers",
                "--cov-report=json:coverage.json",
                "--cov-report=term-missing",
            ),
        ),
        (
            "test-ci-integration",
            (
                "scripts/pytest_gate.py run",
                "git status --porcelain",
                "git rev-parse HEAD",
                "clean=true",
                "tests/ roster/",
                "-q",
                "--maxfail=5",
                "--tb=short",
                "integration and not nightly and not bench and not perf",
                "-n auto --dist loadfile",
                "--cov=src/butlers",
                "--cov-append",
                "--cov-report=json:coverage.json",
                "--cov-report=term-missing",
            ),
        ),
    ],
)
def test_local_ci_targets_are_receipt_producing_and_keep_full_lane_scope(
    target: str, fragments: tuple[str, ...]
) -> None:
    dry_run = subprocess.run(
        ["make", "-n", target],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    for fragment in fragments:
        assert fragment in dry_run, f"`make {target}` lost CI fragment {fragment!r}:\n{dry_run}"


PYTHON_ENVIRONMENT_STEP = "Set up the CI Python environment"
PYTHON_ENVIRONMENT_ACTION = "./.github/actions/ci-python-environment"


def _workflow_step(*, job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"Missing {name!r} step")


def _artifact_step(*, job: dict, artifact_name: str) -> dict:
    return next(
        step
        for step in job["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
        and step["with"]["name"] == artifact_name
    )


def _run_fan_in(
    *, gate: dict, needs: object, event: str, ref: str, tmp_path: Path, raw_needs: str | None = None
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Execute the actual YAML step, including the old per-result transport."""
    output = tmp_path / "gate-output"
    output.write_text("")
    env = {**os.environ, "EVENT_NAME": event, "REF": ref, "GITHUB_OUTPUT": str(output)}
    env["NEEDS_JSON"] = json.dumps(needs) if raw_needs is None else raw_needs
    # The baseline uses separate env fields. Resolve those actual expressions
    # too, so the old-code control reaches ignored verdicts rather than setup.
    for name, expression in gate["env"].items():
        match = re.fullmatch(r"\$\{\{ needs\.([\w-]+)\.(result|outputs\.\w+) \}\}", expression)
        if match and isinstance(needs, dict):
            job, field = match.groups()
            value = needs.get(job, {})
            for part in field.split("."):
                value = value.get(part, "") if isinstance(value, dict) else ""
            env[name] = value if isinstance(value, str) else ""
    result = subprocess.run(
        ["bash", "-e", "-c", gate["run"]],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return result, output.read_text()


def test_ci_gate_reads_every_needed_verdict_without_counting_preflight_as_a_shard(
    tmp_path: Path,
) -> None:
    """REQ-testing-035 / REQ-ci-shard-assurance-004: actual event verdict consumer."""
    jobs = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    gate = next(step for step in jobs["check"]["steps"] if step.get("id") == "gate")
    heavy = {name for name in jobs if name.startswith(("check-unit-", "check-integration-"))}
    full = {
        name: {"result": "success", "outputs": {}} for name in {*jobs["check"]["needs"], "guards"}
    }
    full["route"]["outputs"] = {
        "backend": "true",
        "frontend": "false",
        "mode": "full",
        "test_paths": "[]",
    }
    full["check-affected"]["result"] = "skipped"
    full["frontend-e2e"]["result"] = "skipped"
    heavy = {"check-unit", "check-integration"}
    contexts = [("full", "pull_request", "refs/pull/1/merge", full, True)]
    scoped = copy.deepcopy(full)
    for name in heavy:
        scoped[name]["result"] = "skipped"
    scoped["route"]["outputs"].update(
        mode="scoped", test_paths='["tests/contracts/test_ci_test_targets.py"]'
    )
    scoped["check-affected"]["result"] = "success"
    scoped["check-affected"]["outputs"] = {"verified": "true"}
    contexts.append(("scoped", "pull_request", "refs/pull/1/merge", scoped, False))
    docs = copy.deepcopy(scoped)
    docs["route"]["outputs"].update(backend="false", mode="docs", test_paths="[]")
    for name in ("check-preflight", "check-affected", "go-bridge"):
        docs[name] = {"result": "skipped", "outputs": {}}
    contexts.append(("docs", "pull_request", "refs/pull/1/merge", docs, False))
    push = copy.deepcopy(docs)
    push["route"]["outputs"].update(backend="true", frontend="true", mode="push")
    push["frontend-e2e"]["result"] = "success"
    push["go-bridge"]["result"] = "success"
    contexts.append(("push", "push", "refs/heads/main", push, False))
    merge_group = copy.deepcopy(full)
    merge_group["route"]["outputs"]["frontend"] = "true"
    merge_group["frontend-e2e"]["result"] = "success"
    # Required e2e (bu-w72da2 = A): a frontend pull request must pass it too.
    frontend_pr = copy.deepcopy(full)
    frontend_pr["route"]["outputs"]["frontend"] = "true"
    frontend_pr["frontend-e2e"]["result"] = "success"
    contexts.append(("frontend-pr", "pull_request", "refs/pull/1/merge", frontend_pr, True))
    contexts.append(
        ("merge_group", "merge_group", "refs/heads/gh-readonly-queue/main/x", merge_group, True)
    )

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from ci_route import verdict as script_verdict

    failures = []

    def check(label: str, needs: object, event: str, ref: str, expected: bool) -> None:
        result, output = _run_fan_in(
            gate=gate, needs=needs, event=event, ref=ref, tmp_path=tmp_path
        )
        assert script_verdict(needs=needs, event=event, ref=ref) is expected, label
        if (result.returncode == 0) != expected:
            failures.append(f"{label}: exit={result.returncode}, expected_success={expected}")
        if expected:
            ran = event == "merge_group" or (
                event == "pull_request" and needs["route"]["outputs"].get("mode") == "full"
            )
            assert f"shards_ran={str(ran).lower()}\n" == output, label

    for mode, event, ref, needs, _ in contexts:
        check(mode, needs, event, ref, True)
        for name in ("check-preflight", *sorted(set(needs) - {"check-preflight"})):
            for verdict in ("failure", "cancelled", "", "unknown", False, None):
                changed = copy.deepcopy(needs)
                changed[name]["result"] = verdict
                check(f"{mode}/{name}/{verdict!r}", changed, event, ref, False)
            changed = copy.deepcopy(needs)
            del changed[name]
            check(f"{mode}/{name}/missing", changed, event, ref, False)
            changed = copy.deepcopy(needs)
            del changed[name]["result"]
            check(f"{mode}/{name}/missing-result", changed, event, ref, False)
            changed = copy.deepcopy(needs)
            changed[name]["outputs"] = []
            check(f"{mode}/{name}/invalid-outputs", changed, event, ref, False)
        for result in ("success", "skipped", "failure", "cancelled", "unknown"):
            changed = copy.deepcopy(needs)
            changed["new-needed-job"] = {"result": result, "outputs": {}}
            check(f"{mode}/added/{result}", changed, event, ref, result == "success")
    for mode, event, ref, needs, _ in contexts:
        for name in (
            "route",
            "guards",
            "check-preflight",
            "check-affected",
            "frontend-e2e",
            "go-bridge",
            *heavy,
        ):
            changed = copy.deepcopy(needs)
            changed[name]["result"] = "skipped" if needs[name]["result"] == "success" else "success"
            check(f"{mode}/{name}/wrong-pairing", changed, event, ref, False)
    for output in (
        {},
        {"backend": "true"},
        {"backend": True, "frontend": "false"},
        {"backend": "invalid", "frontend": "false"},
        {"backend": "false", "frontend": False},
    ):
        changed = copy.deepcopy(full)
        changed["route"]["outputs"] = output
        check(
            f"invalid-classification/{output}", changed, "pull_request", "refs/pull/1/merge", False
        )
    for output in (
        {},
        {"mode": "invalid", "test_paths": "[]"},
        {"mode": "scoped", "test_paths": "[]"},
        {"mode": "full", "test_paths": '["tests/test_x.py"]'},
        {"mode": "scoped", "test_paths": "invalid"},
        {"mode": "scoped", "test_paths": '["../outside.py"]'},
    ):
        changed = copy.deepcopy(full)
        changed["route"]["outputs"] = output
        check(f"invalid-plan/{output}", changed, "pull_request", "refs/pull/1/merge", False)
    check("non-main-push", push, "push", "refs/heads/other", False)
    check("unknown-event", full, "workflow_dispatch", "refs/heads/main", False)
    for malformed in ([], None, {}, "not-an-object"):
        check(f"invalid-needs/{malformed!r}", malformed, "merge_group", "refs/heads/main", False)
    for raw in ("{", json.dumps(full)[:-1] + ', "route": {}}'):
        result, output = _run_fan_in(
            gate=gate,
            needs=full,
            raw_needs=raw,
            event="pull_request",
            ref="refs/pull/1/merge",
            tmp_path=tmp_path,
        )
        assert result.returncode != 0
        assert "shards_ran=true" not in output
    for selected in (
        ["tests/e2e/test_performance.py"],
        ["tests/"],
        ["roster/"],
        ["tests/contracts/test_ci_test_targets.py"] * 2,
        ["tests//contracts/test_ci_test_targets.py"],
        ["tests/../outside.py"],
    ):
        changed = copy.deepcopy(scoped)
        changed["route"]["outputs"]["test_paths"] = json.dumps(selected)
        check(
            f"unsupported-selected/{selected}", changed, "pull_request", "refs/pull/1/merge", False
        )
    for evidence in ({}, {"verified": "false"}, {"verified": "unknown"}):
        changed = copy.deepcopy(scoped)
        changed["check-affected"]["outputs"] = evidence
        check(f"selected-proof/{evidence}", changed, "pull_request", "refs/pull/1/merge", False)
    assert not failures, "\n".join(failures)

    # Reached fault controls prove that each independent policy check matters.
    # They edit only the extracted actual step, never another implementation.
    mutations = [
        (
            "inventory",
            gate["run"].replace(
                "needs = json.loads(os.environ['NEEDS_JSON'])",
                "needs = json.loads(os.environ['NEEDS_JSON'])\nneeds['inventory']['result']='success'",
            ),
            "inventory",
            "failure",
        ),
        (
            "preflight",
            gate["run"].replace(
                "needs = json.loads(os.environ['NEEDS_JSON'])",
                "needs = json.loads(os.environ['NEEDS_JSON'])\nneeds['check-preflight']['result']='success'",
            ),
            "check-preflight",
            "failure",
        ),
        (
            "guards",
            gate["run"].replace(
                "needs = json.loads(os.environ['NEEDS_JSON'])",
                "needs = json.loads(os.environ['NEEDS_JSON'])\nneeds['guards']['result']='success'",
            ),
            "guards",
            "failure",
        ),
        (
            "default-skip",
            gate["run"].replace('results.get(name, "success")', 'results.get(name, job["result"])'),
            "new-needed-job",
            "skipped",
        ),
        (
            "e2e",
            gate["run"].replace(
                "needs = json.loads(os.environ['NEEDS_JSON'])",
                "needs = json.loads(os.environ['NEEDS_JSON'])\nneeds['frontend-e2e']['result']='skipped'",
            ),
            "frontend-e2e",
            "failure",
        ),
        (
            "partial-heavy",
            gate["run"].replace(
                'job["result"] == results.get(name, "success")',
                'name == "check-unit" or job["result"] == results.get(name, "success")',
            ),
            "check-unit",
            "skipped",
        ),
        (
            "go-failure",
            gate["run"].replace(
                "needs = json.loads(os.environ['NEEDS_JSON'])",
                "needs = json.loads(os.environ['NEEDS_JSON'])\nneeds['go-bridge']['result']='success'",
            ),
            "go-bridge",
            "failure",
        ),
        (
            "go-omission",
            gate["run"].replace('        "go-bridge",\n', ""),
            "go-bridge",
            "missing",
        ),
    ]
    for label, mutated, job_name, verdict in mutations:
        assert mutated != gate["run"], label
        changed = copy.deepcopy(full)
        if verdict == "missing":
            del changed[job_name]
        else:
            changed[job_name] = {"result": verdict, "outputs": {}}
        ordinary, _ = _run_fan_in(
            gate=gate,
            needs=changed,
            event="pull_request",
            ref="refs/pull/1/merge",
            tmp_path=tmp_path,
        )
        neutralized, _ = _run_fan_in(
            gate={**gate, "run": mutated},
            needs=changed,
            event="pull_request",
            ref="refs/pull/1/merge",
            tmp_path=tmp_path,
        )
        assert ordinary.returncode != 0, label
        assert neutralized.returncode == 0, (label, neutralized.stderr)


def test_smoke_ci_spec_matches_the_preflight_topology() -> None:
    spec = (REPO_ROOT / "openspec/specs/testing/spec.md").read_text(encoding="utf-8")

    assert "### Requirement: Smoke Tests Run In CI As A Fast Gate" in spec
    assert "- **WHEN** the CI `check-preflight` job runs" in spec
    assert "and run alongside the independent unit and integration shards" in spec


def _integration_cleanup_script(shard: int) -> str:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    return _workflow_step(
        job=workflow["jobs"]["check-integration"],
        name="Free disk space before testcontainers",
    )["run"]


def _run_cleanup_script(
    *, tmp_path: Path, shard: int, available_kib: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    sentinel = tmp_path / "sudo-called"
    (fake_bin / "df").write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "-h" ]; then\n'
        "  printf '%s\\n' 'Filesystem Size Used Avail Use% Mounted on'\n"
        "  printf '%s\\n' '/dev/root 145G 65G 80G 45% /'\n"
        "else\n"
        "  printf '%s\\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
        "  printf '/dev/root 152043520 68157440 %s 45%% /\\n' \"$DF_AVAILABLE_KIB\"\n"
        "fi\n",
        encoding="utf-8",
    )
    (fake_bin / "sudo").write_text(
        '#!/usr/bin/env bash\nprintf "called\\n" > "$SUDO_SENTINEL"\nexit 99\n',
        encoding="utf-8",
    )
    for command in (fake_bin / "df", fake_bin / "sudo"):
        command.chmod(0o755)

    result = subprocess.run(
        ["bash", "-e", "-c", _integration_cleanup_script(shard)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "DF_AVAILABLE_KIB": available_kib,
            "SUDO_SENTINEL": str(sentinel),
        },
    )
    return result, sentinel


def test_ci_cleanup_skips_reclamation_when_each_runner_has_safe_free_space(
    tmp_path: Path,
) -> None:
    for shard in range(1, 7):
        result, sentinel = _run_cleanup_script(
            tmp_path=tmp_path / f"shard-{shard}",
            shard=shard,
            available_kib=str(80 * 1024 * 1024),
        )
        assert result.returncode == 0, result.stderr
        assert "Skipping disk cleanup:" in result.stdout
        assert not sentinel.exists()


def test_ci_cleanup_reclaims_when_each_runner_is_below_the_safe_floor(tmp_path: Path) -> None:
    for shard in range(1, 7):
        result, sentinel = _run_cleanup_script(
            tmp_path=tmp_path / f"shard-{shard}",
            shard=shard,
            available_kib=str(29 * 1024 * 1024),
        )
        assert result.returncode == 99
        assert "Cleaning disk:" in result.stdout
        assert sentinel.exists()


@pytest.mark.parametrize("available_kib", ["", "-1", "30.5", "N/A", "abc"])
def test_ci_cleanup_refuses_malformed_free_space_before_reclamation(
    tmp_path: Path, available_kib: str
) -> None:
    result, sentinel = _run_cleanup_script(
        tmp_path=tmp_path,
        shard=1,
        available_kib=available_kib,
    )
    assert result.returncode == 1
    assert "could not determine free disk space" in result.stdout
    assert not sentinel.exists()


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
    for mode, expected_calls, expected_probes, expected_exit in [
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
    ]:
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
    if only_mode is None:
        _exercise_apt_recovery(root / "source-policy")
        _exercise_ubuntu_apt_reader(root / "actual-ubuntu-reader")


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


def test_vitest_concurrent_failure_terminates_both_owned_groups(tmp_path: Path) -> None:
    """REQ-ci-shard-assurance-007: either failed peer closes both owned trees."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import ci_vitest as vitest

    helper = tmp_path / "peer.py"
    helper.write_text("""import os, signal, subprocess, sys, time
from pathlib import Path
root, name, failing = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
child = subprocess.Popen([sys.executable, '-c', "import os, signal, sys, time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(4); Path(sys.argv[2]).write_text('late')", str(root / (name + '-child')), str(root / (name + '-late'))])
(root / (name + '-parent')).write_text(str(os.getpid()))
limit = time.monotonic() + 3
other = 'execute' if name == 'collect-full' else 'collect-full'
while not (root / (other + '-child')).exists() or not (root / (name + '-child')).exists():
    if time.monotonic() > limit: sys.exit(7)
    time.sleep(0.01)
(root / (name + '-overlap')).write_text('ready')
if name == failing:
    time.sleep(0.1)
    sys.exit(1)
time.sleep(10)
""")
    for failing in ("collect-full", "execute", "deadline"):
        directory = tmp_path / failing
        directory.mkdir()
        started = time.monotonic()
        try:
            commands = {
                name: (
                    [sys.executable, str(helper), str(directory), name, failing],
                    os.environ.copy(),
                )
                for name in ("collect-full", "execute")
            }
            with pytest.raises((vitest.VitestSubprocessFailure, vitest.VitestTimeout)):
                with vitest.concurrent_processes(
                    commands, cwd=tmp_path, deadline=started + 3
                ) as outcomes:
                    for _, result, _ in outcomes:
                        if result.returncode:
                            raise vitest.VitestSubprocessFailure("controlled peer failure")
            assert time.monotonic() - started < 3.5
            for name in commands:
                assert (directory / (name + "-overlap")).read_text() == "ready"
                for kind in ("parent", "child"):
                    pid = int((directory / (name + "-" + kind)).read_text())
                    stat = Path(f"/proc/{pid}/stat")
                    drained = time.monotonic() + 0.2
                    while True:
                        try:
                            state = stat.read_text().split()[2]
                        except (FileNotFoundError, ProcessLookupError):
                            break
                        if state == "Z":
                            break
                        assert time.monotonic() < drained
                        time.sleep(0.01)
                assert not (directory / (name + "-late")).exists()
        finally:
            for ledger in directory.glob("*-child"):
                pid = int(ledger.read_text())
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass


def _vitest_concurrent_receipt_controls(root, pair, monkeypatch, tmp_path, bounded_helper):
    """Additional interval/cache cases retain the original consumer body verbatim."""
    import ci_vitest as vitest

    # Unequal JSON lists are normal nonmatches in either direction; boolean
    # values must also remain distinct from numeric values inside equal lists.
    assert vitest.same_json_value({"nested": [[1, True], []]}, {"nested": [[1, True], []]})
    for observed, expected in (
        ([[1], []], [[1]]),
        ([[1]], [[1], []]),
        ([[1, True]], [[1, 1]]),
    ):
        assert vitest.same_json_value(observed, expected) is False

    with monkeypatch.context() as local:
        for name in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "VITEST_CONTROL"):
            local.delenv(name, raising=False)
        local.setenv("VITEST_CONTROL_STARTUP_DELAY", "0.2")
        for child in pair:
            assert (
                max(child["reference_elapsed_s"], child["execute_elapsed_s"]) <= child["elapsed_s"]
            )
            assert child["reference_elapsed_s"] + child["execute_elapsed_s"] > child["elapsed_s"]
        concurrent = copy.deepcopy(pair)
        for child in concurrent:
            child.update(reference_elapsed_s=70, execute_elapsed_s=65, elapsed_s=80)
        vitest.reconcile(root, concurrent)
        for field, invalid in (
            ("reference_elapsed_s", -1),
            ("execute_elapsed_s", -1),
            ("elapsed_s", -1),
            ("reference_elapsed_s", float("nan")),
            ("execute_elapsed_s", float("inf")),
            ("elapsed_s", True),
            ("reference_elapsed_s", "70"),
            ("execute_elapsed_s", None),
            ("elapsed_s", 69),
            ("elapsed_s", 901),
        ):
            corrupted = copy.deepcopy(concurrent)
            corrupted[0][field] = invalid
            with pytest.raises(ValueError):
                vitest.reconcile(root, corrupted)
        caches = []

        def observe(command, **kwargs):
            if "BUTLERS_VITEST_CACHE_DIR" in kwargs["env"]:
                caches.append(kwargs["env"]["BUTLERS_VITEST_CACHE_DIR"])
            return bounded_helper(command, **kwargs)

        local.setattr(vitest, "run_process", observe)
        assert vitest.run(root, 1, tmp_path / "concurrent-private-caches") == 0
        assert len(caches) == 2 and caches[0] != caches[1]
        assert all(not Path(cache).exists() for cache in caches)
    return True


def test_ci_workflow_shards_full_lanes_without_coverage_or_privacy_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-ci-shard-assurance-007: actual frontend fan-in and build binding controls."""
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    # A structurally passing topology is unusable if GitHub cannot admit a job.
    assert all(job.get("runs-on") for job in jobs.values() if "steps" in job)
    # Go has its own required result, not a pytest shard or coverage input.
    go_bridge = jobs["go-bridge"]
    assert go_bridge["needs"] == ["route"]
    assert go_bridge["if"] == "needs.route.outputs.backend == 'true'"
    assert go_bridge["permissions"] == {"contents": "read"}
    assert go_bridge["defaults"]["run"] == {
        "working-directory": "whatsapp-bridge",
        "shell": "bash",
    }
    assert go_bridge["env"] == {"GOTOOLCHAIN": "local"}
    assert not go_bridge.get("continue-on-error", False)
    assert all(not step.get("continue-on-error", False) for step in go_bridge["steps"])
    assert [step["uses"] for step in go_bridge["steps"] if "uses" in step] == [
        "actions/checkout@v6",
        "actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16",
    ]
    assert go_bridge["steps"][0]["with"] == {"persist-credentials": False}
    setup_go = _workflow_step(job=go_bridge, name="Set up Go 1.25.11")
    assert setup_go["with"] == {
        "go-version": "1.25.11",
        "check-latest": False,
        "cache": True,
        "cache-dependency-path": "whatsapp-bridge/go.sum",
    }
    assert "FROM golang:1.25.11-bookworm@sha256:" in (REPO_ROOT / "Dockerfile").read_text()
    tracked_go = subprocess.run(
        ["git", "ls-files", "whatsapp-bridge", "**/go.mod"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.splitlines()
    assert [name for name in tracked_go if name.endswith("/go.mod")] == ["whatsapp-bridge/go.mod"]
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from ci_route import route as classify_route

    for name in tracked_go:
        if name.endswith((".go", "/go.mod", "/go.sum")):
            classification = classify_route(
                event="pull_request",
                ref="refs/pull/1/merge",
                files=[name],
                docs=[],
                base=None,
            )
            assert classification["backend"] == "true" and classification["mode"] == "full"
    go_run = _workflow_step(job=go_bridge, name="Test the Go bridge")["run"]
    assert shlex.split(go_run) == [
        "timeout",
        "--signal=TERM",
        "--kill-after=30s",
        "10m",
        "go",
        "test",
        "-mod=readonly",
        "-count=1",
        "-timeout=5m",
        "./...",
    ]
    go_locks = _workflow_step(job=go_bridge, name="Verify Go lockfiles are unchanged")["run"]
    assert go_locks == "git diff --exit-code -- go.mod go.sum"
    # Execute the extracted steps in an owned Git checkout: an error in Go
    # cannot fall through to success, and real tracked lockfile drift refuses.
    go_fixture = tmp_path / "go-fixture"
    go_fixture.mkdir()
    for name in ("go.mod", "go.sum"):
        (go_fixture / name).write_text("fixture\n")
    for command in (["git", "init", "-q"], ["git", "add", "go.mod", "go.sum"]):
        subprocess.run(command, cwd=go_fixture, check=True, capture_output=True, timeout=10)
    fake_bin = go_fixture / "bin"
    fake_bin.mkdir()
    fake_go = fake_bin / "go"
    fake_go.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time\n"
        "pathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]))\n"
        "if os.environ['GO_CONTROL'] == 'timeout': time.sleep(5)\n"
        "if os.environ['GO_CONTROL'] == 'lock-mutation':\n"
        "    pathlib.Path('go.sum').write_text('modified\\n')\n"
        "raise SystemExit(23 if os.environ['GO_CONTROL'] == 'failure' else 0)\n"
    )
    fake_go.chmod(0o755)
    for control, expected in (
        ("healthy", 0),
        ("failure", 23),
        ("timeout", 124),
        ("lock-mutation", 1),
    ):
        bounded_run = go_run.replace("10m", "0.25s") if control == "timeout" else go_run
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", bounded_run + "\n" + go_locks],
            cwd=go_fixture,
            env={
                **os.environ,
                **go_bridge["env"],
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "GO_CONTROL": control,
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == expected, (control, result.stderr)
        assert json.loads((go_fixture / "argv.json").read_text()) == [
            "test",
            "-mod=readonly",
            "-count=1",
            "-timeout=5m",
            "./...",
        ]
    # Inventory and children must install the same full interpreter identity, and
    # every Python job gets it from the one composite action (bu-29wkwj.2).
    assert workflow.get("env", {}).get("UV_PYTHON") == "3.12.15"
    action = yaml.safe_load(
        (REPO_ROOT / ".github/actions/ci-python-environment/action.yml").read_text()
    )
    action_python = [
        step
        for step in action["runs"]["steps"]
        if step.get("uses", "").startswith("actions/setup-python@")
    ]
    assert [step["with"]["python-version"] for step in action_python] == [
        workflow["env"]["UV_PYTHON"]
    ]
    assert not any(
        step.get("uses", "").startswith("actions/setup-python@")
        or "install_ci_uv.py" in step.get("run", "")
        or "ci_environment.py" in step.get("run", "")
        for job in jobs.values()
        for step in job.get("steps", [])
    )
    python_jobs = {
        name
        for name, job in jobs.items()
        if any("uv run" in step.get("run", "") for step in job.get("steps", []))
    }
    assert python_jobs
    for name in python_jobs:
        assert _workflow_step(job=jobs[name], name=PYTHON_ENVIRONMENT_STEP)["uses"] == (
            PYTHON_ENVIRONMENT_ACTION
        )
    preflight = jobs["check-preflight"]
    unit_jobs = [
        json.loads(json.dumps(jobs["check-unit"]).replace("${{ matrix.group }}", str(index)))
        for index in jobs["check-unit"]["strategy"]["matrix"]["group"]
    ]
    integration_jobs = [
        json.loads(json.dumps(jobs["check-integration"]).replace("${{ matrix.group }}", str(index)))
        for index in jobs["check-integration"]["strategy"]["matrix"]["group"]
    ]
    # Required queue receipts must exist in their own jobs. Covered-workflow
    # artifacts cannot satisfy ordinary preflight or the timing observer.
    for lane, children in (("unit", unit_jobs), ("integration", integration_jobs)):
        for index, job in enumerate(children, 1):
            artifact = _artifact_step(job=job, artifact_name=f"ci-{lane}-{index}-test-evidence")
            filenames = ["junit.xml", "top-durations.txt", "collected.json"]
            if lane == "unit":
                filenames.append("condensation-refusal-*.json")
            assert artifact["if"] == "always()"
            assert artifact["uses"] == "actions/upload-artifact@v4"
            assert artifact["with"] == {
                "name": f"ci-{lane}-{index}-test-evidence",
                "path": "".join(
                    f"${{{{ runner.temp }}}}/ci-artifacts/{lane}-{index}/{filename}\n"
                    for filename in filenames
                ),
                "retention-days": 14,
                "if-no-files-found": "error",
                "overwrite": True,
            }
    check_job = jobs["check"]
    covered_workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/nightly.yml").read_text())
    coverage_job = covered_workflow["jobs"]["coverage-report"]
    covered_matrix = covered_workflow["jobs"]["coverage-shards"]
    covered_jobs = []
    for row in covered_matrix["strategy"]["matrix"]["include"]:
        expanded = json.dumps(covered_matrix)
        for key, value in row.items():
            expanded = expanded.replace("${{ matrix." + key + " }}", str(value))
        covered_jobs.append(json.loads(expanded))
    assert "coverage" not in jobs

    # Merge-queue topology (bu-r5mnn): the queue's merge_group run is the terminal
    # broad gate, so the workflow must accept that event.
    assert set(workflow[True]) == {"push", "pull_request", "merge_group"}

    # `changes` classifies the PR diff fail-closed; `guards` runs every
    # static guard script in one job with nothing upstream of it.
    changes = jobs["route"]
    assert "needs" not in changes and "if" not in changes
    assert set(changes["outputs"]) == {"backend", "frontend", "mode", "test_paths"}
    path_filter = _workflow_step(job=changes, name="Filter changed paths")
    assert path_filter["uses"].startswith("dorny/paths-filter@")
    assert path_filter["if"] == "github.event_name == 'pull_request'"
    assert path_filter["with"]["list-files"] == "json"
    classify = _workflow_step(job=changes, name="Classify and conservatively plan with stdlib only")
    assert classify["id"] == "classify"
    guards = jobs["guards"]
    assert guards["needs"] == ["route"] and "if" not in guards
    lint_cache = _workflow_step(job=guards, name="Cache pinned lint tools")
    assert lint_cache["uses"] == "actions/cache@v4"
    assert lint_cache["with"] == {
        "path": ".tmp/lint-tools",
        "key": (
            "lint-tools-v1-${{ runner.os }}-${{ runner.arch }}-"
            "${{ hashFiles('scripts/lint-tool-versions.json') }}"
        ),
    }
    assert lint_cache["continue-on-error"] is True  # Advisory transport only.
    assert "if" not in lint_cache
    lint_install = _workflow_step(job=guards, name="Install lint tools")
    assert lint_install == {"name": "Install lint tools", "run": "make install-lint-tools"}
    assert guards["steps"][guards["steps"].index(lint_install) - 1] is lint_cache
    guard_outcomes = _workflow_step(job=guards, name="Fail if any guard failed")["env"][
        "GUARD_OUTCOMES"
    ]
    for guard_id in (
        "lock",
        "lint",
        "format",
        "sql_safety",
        "session_links",
        "em_dashes",
        "spec_overwrites",
        "openspec_strict",
        "archived_requirements",
        "countable_tasks",
        "cited_requirements",
        "duplicate_names",
    ):
        guard_step = next(step for step in guards["steps"] if step.get("id") == guard_id)
        assert "!cancelled()" in guard_step["if"]  # One failing guard never hides another.
        assert f"{guard_id}=${{{{ steps.{guard_id}.outcome }}}}" in guard_outcomes

    # Execute the actual finalizer: skipped mandatory checks cannot turn green,
    # while the intentional non-PR session-link skip has a positive companion.
    finalizer = _workflow_step(job=guards, name="Fail if any guard failed")
    outcomes = {line.split("=", 1)[0]: "success" for line in guard_outcomes.splitlines()}
    for name, verdict, event, expected in [
        ("session_links", "skipped", "merge_group", 0),
        ("session_links", "skipped", "pull_request", 1),
        *[
            (name, result, "pull_request", 1)
            for name in outcomes
            for result in ("failure", "skipped", "cancelled")
        ],
    ]:
        actual = {**outcomes, name: verdict}
        result = subprocess.run(
            ["bash", "-e", "-c", finalizer["run"]],
            env={
                **os.environ,
                "EVENT_NAME": event,
                "GUARD_OUTCOMES": "\n".join(f"{key}={value}" for key, value in actual.items()),
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == expected, (name, verdict, result.stdout)

    static_commands = {
        "Check lock file is up to date": "uv lock --check",
        "Lint": "make lint",
        "Format check": "uv run ruff format --check src/ tests/ roster/ conftest.py -q",
        "SQL safety check (FOR UPDATE + outer join)": "make check-for-update-joins",
    }
    for name, command in static_commands.items():
        assert _workflow_step(job=guards, name=name)["run"] == command
        assert not any(step.get("name") == name for step in preflight["steps"])
    ordered = [step.get("name") for step in guards["steps"]]
    assert (
        ordered.index(PYTHON_ENVIRONMENT_STEP)
        < ordered.index("Check lock file is up to date")
        < ordered.index("Lint")
    )
    dry_run = subprocess.run(
        ["make", "-n", "check-guards"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    for command in (
        "uv lock --check",
        "uv run ruff check src/ tests/",
        static_commands["Format check"],
        "scripts/check_for_update_joins.py",
    ):
        assert command in dry_run

    # The totals check runs after both matrix aggregates; every group's count is
    # read before a full result is admitted.
    assert preflight["needs"] == ["route", "guards", "check-unit", "check-integration"]
    assert preflight["if"].startswith("always()")
    assert "mode == 'full'" in preflight["if"] and "mode == 'scoped'" in preflight["if"]
    # Each group runs its pytest-split slice through the one lane runner, starting
    # straight after route (bu-29wkwj.2).
    for lane, children in (("unit", unit_jobs), ("integration", integration_jobs)):
        for index, job in enumerate(children, 1):
            assert job["needs"] == ["route"]
            assert job["if"] == "needs.route.outputs.mode == 'full'"
            assert job["strategy"]["fail-fast"] is False
            assert job["env"]["CI_COVERAGE"] == "0"
            run = next(step for step in job["steps"] if "ci_lanes.py" in step.get("run", ""))
            assert run["run"] == (
                f"uv run python scripts/ci_lanes.py run --lane {lane} --group {index} "
                f'--output "${{RUNNER_TEMP}}/ci-artifacts/{lane}-{index}"'
            )
    assert len(unit_jobs) == 5 and len(integration_jobs) == 6
    assert "inventory" not in jobs
    assert not any(
        "ci_partition.py" in step.get("run", "")
        for job in jobs.values()
        for step in job.get("steps", [])
    )
    totals = _workflow_step(
        job=preflight, name="Check the lane groups add up and stay within the test budget"
    )
    assert totals["run"] == (
        'uv run --no-sync python scripts/ci_lanes.py totals --counts "${RUNNER_TEMP}/ci-test-receipts"'
    )

    # Enumerate weighted antichains of the actual job DAG. Matrix aggregates
    # carry their full concurrent row counts; affected cannot run in full mode.
    active = {name: job for name, job in jobs.items() if name != "check-affected"}
    ancestors = {}

    def upstream(name):
        if name not in ancestors:
            parents = active[name].get("needs", [])
            parents = [parents] if isinstance(parents, str) else parents
            ancestors[name] = set(parents)
            for parent in parents:
                if parent in active:
                    ancestors[name].update(upstream(parent))
        return ancestors[name]

    names = list(active)
    weights = [
        math.prod(
            len(values) for values in active[name].get("strategy", {}).get("matrix", {}).values()
        )
        for name in names
    ]
    compatible = [
        sum(
            1 << j
            for j, other in enumerate(names)
            if name != other and name not in upstream(other) and other not in upstream(name)
        )
        for name in names
    ]
    peak = 0
    for mask in range(1 << len(names)):
        selected = [i for i in range(len(names)) if mask & (1 << i)]
        if all(mask & ~(compatible[i] | (1 << i)) == 0 for i in selected):
            peak = max(peak, sum(weights[i] for i in selected))
    assert sum(weights) + 1 == 21  # Includes the mutually exclusive affected row.
    # Existing fifteen concurrent rows plus the independent Go bridge job.
    assert peak == 16
    assert "plan" not in jobs and "changes" not in jobs
    route_command = _workflow_step(
        job=changes, name="Classify and conservatively plan with stdlib only"
    )
    assert route_command["run"] == "python3 scripts/ci_route.py"
    assert not any("uv" in step.get("run", "") for step in changes["steps"])
    check_affected = jobs["check-affected"]
    assert check_affected["needs"] == ["route"]
    assert "mode == 'scoped'" in check_affected["if"]
    # The browser job builds its own bundle instead of waiting for guards.
    e2e = jobs["frontend-e2e"]
    assert e2e["needs"] == ["route"]
    assert _workflow_step(job=e2e, name="Build")["run"] == "npm run build"
    assert not any("download-artifact" in step.get("uses", "") for step in e2e["steps"])
    assert jobs["frontend"]["needs"] == ["route", "guards", "frontend-vitest"]
    assert jobs["frontend"]["if"] == "always()"
    assert jobs["frontend-vitest"]["needs"] == ["route"]
    assert jobs["frontend-vitest"]["if"] == "needs.route.outputs.frontend == 'true'"
    assert jobs["frontend-vitest"]["strategy"]["matrix"]["shard"] == [1, 2]
    frontend_gate = jobs["frontend"]["steps"][0]
    fe_needs = {name: {"result": "success", "outputs": {}} for name in jobs["frontend"]["needs"]}
    fe_needs["route"]["outputs"] = {"frontend": "true"}
    for failed in (None, "guards", "frontend-vitest", "route"):
        actual = copy.deepcopy(fe_needs)
        if failed:
            actual[failed]["result"] = "failure"
        result = subprocess.run(
            ["bash", "-e", "-c", frontend_gate["run"]],
            env={**os.environ, "NEEDS_JSON": json.dumps(actual)},
            capture_output=True,
            timeout=10,
        )
        assert (result.returncode == 0) == (failed is None)
    for flag, child, accepted in (
        ("false", "skipped", True),
        ("true", "skipped", False),
        ("false", "success", False),
        ("unknown", "success", False),
    ):
        actual = copy.deepcopy(fe_needs)
        actual["route"]["outputs"]["frontend"] = flag
        actual["frontend-vitest"]["result"] = child
        result = subprocess.run(
            ["bash", "-e", "-c", frontend_gate["run"]],
            env={**os.environ, "NEEDS_JSON": json.dumps(actual)},
            capture_output=True,
            timeout=10,
        )
        assert (result.returncode == 0) == accepted
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

    buildroot = tmp_path / "build-control"
    (buildroot / "frontend").mkdir(parents=True)
    (buildroot / "frontend/package-lock.json").write_bytes(
        (REPO_ROOT / "frontend/package-lock.json").read_bytes()
    )
    subprocess.run(["git", "init", "-q", str(buildroot)], check=True)
    subprocess.run(["git", "-C", str(buildroot), "add", "frontend/package-lock.json"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(buildroot),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    with monkeypatch.context() as local:
        for name in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
            local.delenv(name, raising=False)

        # Real bounded subprocess transport controls of the receipt protocol.
        # This helper is not Vitest and provides no installed frontend proof.
        import ci_vitest as vitest

        helper = buildroot / "frontend/node_modules/.bin/vitest"
        helper.parent.mkdir(parents=True)
        (buildroot / "frontend/control-a.test.ts").write_text("control stand-in")
        (buildroot / "frontend/control-b.test.ts").write_text("control stand-in")
        helper.write_text(
            f"#!{sys.executable}\n"
            + """import json, os, subprocess, sys, time
from pathlib import Path
root = Path(__file__).resolve().parents[2]
time.sleep(float(os.environ.get('VITEST_CONTROL_STARTUP_DELAY', '0')))
shard = next((a for a in sys.argv if a.startswith('--shard=')), None)
output = next((a[9:] for a in sys.argv if a.startswith('--output=')), None)
stage = 'collect-full' if sys.argv[1] == 'list' else 'execute'
mode = os.environ.get('VITEST_CONTROL')
(root / ('transport-ready-' + stage)).write_text('ready')
if mode == 'descendant-stall' and stage == 'collect-full':
    ready_read, ready_write = os.pipe()
    child = subprocess.Popen([sys.executable, '-c', "import os, signal, sys, time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path(sys.argv[2]).write_text('ready'); os.write(int(sys.argv[1]), b'1'); time.sleep(3); Path(sys.argv[3]).write_text('late descendant output')", str(ready_write), str(root / 'descendant-ready'), str(root / 'descendant-late')], pass_fds=(ready_write,))
    os.close(ready_write)
    assert os.read(ready_read, 1) == b'1'
    os.close(ready_read)
    print('private control text', flush=True)
    time.sleep(5)
if mode == stage or (stage == 'execute' and mode == 'collect-shard-' + shard[8]):
    print('private control text', flush=True)
    time.sleep(5)
if mode == 'collect-failed' and stage == 'collect-full':
    print('private control text', file=sys.stderr)
    sys.exit(1)
import hashlib
config = {'node':'24.21.0','vitest':'3.2.4','pool':'forks','isolate':True,'available_parallelism':4,'max_workers':None,'min_workers':None,'file_parallelism':True,'test_timeout':5000,'hook_timeout':10000,'retry':0,'sequence_shuffle':False}
def opaque(file, name):
    return hashlib.sha256(json.dumps([file, name]).encode()).hexdigest()
modules = {}
for letter in ('a', 'b'):
    file = 'frontend/control-' + letter + '.test.ts'
    modules[file] = {'file':file,'items':{opaque(file, 'occurrence'): {'key':opaque(file, 'control ' + letter),'mode':'run'}},'errors':0,'ok':True}
files = sorted(modules)
if sys.argv[1] == 'list':
    reference = {'schema':'ci-vitest-reference.v2','config':config,'files':files,'halves':{'1':[files[0]],'2':[files[1]]},'modules':modules,'unhandled_errors':0,'controller_exit':0,'complete':True}
    Path(output).write_text(json.dumps(reference))
elif mode == 'report-malformed':
    print('private control text')
    Path(os.environ['BUTLERS_VITEST_EVIDENCE']).write_text('private control text')
else:
    file = files[int(shard[8]) - 1]
    items = modules[file]['items']
    execution = {'schema':'ci-vitest-execution.v2','config':config,'modules':{file:modules[file]},'queued':{file:1},'starts':{file:1},'ends':{file:{'count':1,'errors':0,'ok':True,'state':'passed'}},'ready':{token:1 for token in items},'results':{token:{'state':'passed','declared_mode':'run'} for token in items},'terminal_files':[file],'unhandled_errors':0,'reporter_problems':{},'update_errors':0,'reason':'passed','complete':True}
    Path(os.environ['BUTLERS_VITEST_EVIDENCE']).write_text(json.dumps(execution))
"""
        )
        helper.chmod(0o755)
        native_run = vitest.run_process

        def bounded_helper(command, **kwargs):
            reference_call = command[:2] == ["node", str(vitest.REPORTER)]
            if command[0] == str(helper) or reference_call:
                # Preserve finite readiness before the positioned stall. This
                # source-owned process double proves transport only, not Vitest.
                stage = "collect-full" if reference_call else "execute"
                if reference_call:
                    command = [str(helper), "list", "--output=" + command[4]]
                ready = buildroot / "frontend" / ("transport-ready-" + stage)
                ready.unlink(missing_ok=True)
                kwargs["timeout"] = 2
                try:
                    return native_run(command, **kwargs)
                finally:
                    assert ready.is_file() and ready.read_text() == "ready", (
                        "current helper entry unavailable"
                    )
            return native_run(command, **kwargs)

        local.setattr(vitest, "run_process", bounded_helper)
        local.setenv("VITEST_CONTROL_STARTUP_DELAY", "0.2")
        # The actual PATH-selected Node probe is optional diagnostic metadata,
        # not an identity or worker-admission substitute for the real collector.
        observed = vitest.runtime_observation(buildroot)
        assert observed is None or (
            type(observed["available_parallelism"]) is int and observed["available_parallelism"] > 0
        )
        for raw in (
            b"private control text",
            b'{"node":"24.21.0","available_parallelism":true}',
            b'{"node":"private control text","available_parallelism":4}',
            b'{"node":"24.21.0","available_parallelism":0}',
            b'{"node":"24.21.0","available_parallelism":4,"extra":"private control text"}',
        ):
            with monkeypatch.context() as diagnostic:
                diagnostic.setattr(
                    vitest,
                    "run_process",
                    lambda command, *, raw=raw, **kwargs: subprocess.CompletedProcess(
                        command, 0, raw, b""
                    ),
                )
                assert vitest.runtime_observation(buildroot) is None
        with monkeypatch.context() as diagnostic:
            diagnostic.setattr(
                vitest,
                "run_process",
                lambda command, **kwargs: subprocess.CompletedProcess(
                    command, 0, b'{"node":"24.21.0","available_parallelism":4}', b""
                ),
            )
            assert vitest.runtime_observation(buildroot) == {
                "node": "24.21.0",
                "available_parallelism": 4,
            }

        # An unavailable optional probe must not block the actual full-reference,
        # shard execution and independent verifier protocol below. Only this
        # diagnostic subprocess is neutralized; the real bounded helper remains.
        def unavailable_probe(command, **kwargs):
            if command[:2] == ["node", "-e"]:
                raise FileNotFoundError("optional control unavailable")
            return bounded_helper(command, **kwargs)

        local.setattr(vitest, "run_process", unavailable_probe)
        observed = None
        for mode, stage, category in (
            ("collect-full", "collect-full", "timeout"),
            ("collect-shard-1", "execute", "timeout"),
            ("collect-shard-2", "execute", "timeout"),
            ("execute", "execute", "timeout"),
            ("collect-failed", "collect-full", "subprocess_failed"),
            ("report-malformed", "report", "invalid_evidence"),
        ):
            local.setenv("VITEST_CONTROL", mode)
            destination = tmp_path / ("vitest-" + mode)
            assert vitest.run(buildroot, 2 if mode == "collect-shard-2" else 1, destination) == 2
            receipt_text = (destination / "receipt.json").read_text()
            receipt = json.loads(receipt_text)
            assert receipt["complete"] is False
            assert (receipt["stage"], receipt["failure_category"]) == (stage, category)
            assert receipt["identity"] == vitest.identity(buildroot)
            assert receipt["runtime_observation"] == observed
            assert "private control text" not in receipt_text
            if category == "timeout":
                assert receipt["process_cleanup"]["term_sent"] is True
                assert receipt["process_cleanup"]["pipes_drained"] is True
                assert receipt["process_cleanup"]["child_reaped"] is True
        local.setenv("VITEST_CONTROL", "descendant-stall")
        destination = tmp_path / "vitest-descendant-stall"
        assert vitest.run(buildroot, 1, destination) == 2
        receipt_text = (destination / "receipt.json").read_text()
        receipt = json.loads(receipt_text)
        assert (buildroot / "frontend/descendant-ready").read_text() == "ready"
        assert (receipt["stage"], receipt["failure_category"]) == ("collect-full", "timeout")
        assert receipt["process_cleanup"]["term_sent"] is True
        assert receipt["process_cleanup"]["kill_sent"] is True
        assert receipt["process_cleanup"]["pipes_drained"] is True
        assert receipt["process_cleanup"]["child_reaped"] is True
        assert "private control text" not in receipt_text
        time.sleep(3)
        assert not (buildroot / "frontend/descendant-late").exists()
        local.delenv("VITEST_CONTROL")
        destination = tmp_path / "vitest-healthy"
        assert vitest.run(buildroot, 1, destination) == 0
        receipt = json.loads((destination / "receipt.json").read_text())
        assert receipt["complete"] is True and receipt["count"] == 1
        assert receipt["stage"] == "complete" and "failure_category" not in receipt
        second = tmp_path / "vitest-healthy-two"
        assert vitest.run(buildroot, 2, second) == 0
        pair = [receipt, json.loads((second / "receipt.json").read_text())]
        vitest.reconcile(buildroot, pair)
        # Same counts cannot hide changed parameters, missing declarations,
        # duplicated logical starts, unfinished outcomes or stale attempts.
        for mutation in (
            "parameter",
            "missing",
            "duplicate",
            "result",
            "attempt",
            "full-reference",
            "reporter-error",
            "update-error",
            "late",
            "boolean-exit",
            "float-count",
            "float-selected",
            "float-outcome",
            "integer-isolate",
            "boolean-module-errors",
            "mandatory-config",
        ):
            corrupted = copy.deepcopy(pair)
            child = corrupted[0]
            execution = child["execution"]
            file = next(iter(execution["modules"]))
            token = next(iter(execution["ready"]))
            if mutation == "parameter":
                execution["modules"][file]["items"][token]["key"] = "0" * 64
            elif mutation == "missing":
                del execution["modules"][file]
            elif mutation == "duplicate":
                execution["ready"][token] = 2
            elif mutation == "result":
                execution["results"][token]["state"] = "pending"
            elif mutation == "attempt":
                child["identity"]["attempt"] = "other-attempt"
            elif mutation == "reporter-error":
                execution["reporter_problems"] = {"ready_multiplicity": 1}
            elif mutation == "update-error":
                execution["update_errors"] = 1
            elif mutation == "late":
                child["elapsed_s"] = 901
            elif mutation == "boolean-exit":
                child["exit_code"] = False
            elif mutation == "float-count":
                child["count"] = float(child["count"])
            elif mutation == "float-selected":
                item = next(iter(child["selected"]))
                child["selected"][item] = float(child["selected"][item])
            elif mutation == "float-outcome":
                outcome = next(iter(child["outcomes"]))
                child["outcomes"][outcome] = float(child["outcomes"][outcome])
            elif mutation == "integer-isolate":
                execution["config"]["isolate"] = 1
            elif mutation == "boolean-module-errors":
                execution["modules"][file]["errors"] = False
            elif mutation == "mandatory-config":
                for sibling in corrupted:
                    sibling["reference"]["config"]["node"] = "22.0.0"
                    sibling["execution"]["config"]["node"] = "22.0.0"
            else:
                child["reference"]["modules"][file]["items"][token]["key"] = "1" * 64
            with pytest.raises(ValueError):
                vitest.reconcile(buildroot, corrupted)
        vitest.reconcile(buildroot, pair)
        for declared_mode in ("skip", "todo"):
            reference = copy.deepcopy(pair[0]["reference"])
            execution = copy.deepcopy(pair[0]["execution"])
            file = next(iter(execution["modules"]))
            token = next(iter(execution["ready"]))
            reference["modules"][file]["items"][token]["mode"] = declared_mode
            execution["modules"][file]["items"][token]["mode"] = declared_mode
            execution["results"][token]["declared_mode"] = declared_mode
            with pytest.raises(ValueError):
                vitest.validate_execution(reference, execution, 1)
            execution["results"][token]["state"] = "skipped"
            assert vitest.validate_execution(reference, execution, 1)["count"] == 1
        # Ordinary declared run can dynamically skip: this is an actual Vitest
        # terminal species and must not be mistaken for an executed pass.
        dynamic = copy.deepcopy(pair[0]["execution"])
        token = next(iter(dynamic["results"]))
        dynamic["results"][token]["state"] = "skipped"
        assert vitest.validate_execution(pair[0]["reference"], dynamic, 1)["outcomes"] == {
            "skipped": 1
        }
        # The required fan-in executes this real CLI entrypoint, not just a
        # library function that happens to accept the healthy artifacts.
        with monkeypatch.context() as cli_control:
            cli_control.setattr(vitest, "ROOT", buildroot)
            cli_control.setattr(
                sys,
                "argv",
                [
                    "ci_vitest.py",
                    "--reconcile",
                    str(destination / "receipt.json"),
                    str(second / "receipt.json"),
                ],
            )
            assert vitest.main() == 0
            bad = copy.deepcopy(pair[0])
            bad["execution"]["update_errors"] = 1
            invalid = tmp_path / "invalid-vitest-receipt.json"
            invalid.write_text(json.dumps(bad))
            cli_control.setattr(
                sys,
                "argv",
                ["ci_vitest.py", "--reconcile", str(invalid), str(second / "receipt.json")],
            )
            assert vitest.main() == 2
    protocol = subprocess.run(
        [shutil.which("node"), str(REPO_ROOT / "tests/ci_vitest_protocol_controls.mjs")],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=10,
    )
    assert protocol.returncode == 0, "Vitest ordered bookkeeping conformance refused"
    assert check_job["needs"] == [
        "route",
        "guards",
        "check-preflight",
        "check-unit",
        "check-integration",
        "check-affected",
        "frontend-e2e",
        "go-bridge",
    ]
    assert check_job["if"] == "always()"
    assert "cancelled" not in check_job["if"]
    assert all("uses" not in step for step in check_job["steps"])
    assert not any(
        "uv" in step.get("run", "") or "checkout" in str(step) for step in check_job["steps"]
    )
    for step_name in (
        PYTHON_ENVIRONMENT_STEP,
        "Smoke tests (fast gate + release evidence)",
        "Check the lane groups add up and stay within the test budget",
    ):
        _workflow_step(job=preflight, name=step_name)
    assert "check_integration_coverage.py" not in str(preflight)
    # REQ-testing-047 / REQ-testing-050: twelve former orphan-service sites retain their
    # genuine provisioned PG17 fixtures; a CI ambient URL is no authority.
    for job in [preflight, *unit_jobs, *integration_jobs, jobs["check-affected"]]:
        assert "postgres" not in job.get("services", {})
        assert "DATABASE_URL" not in job.get("env", {})
        environment = _workflow_step(job=job, name=PYTHON_ENVIRONMENT_STEP)
        assert environment["uses"] == PYTHON_ENVIRONMENT_ACTION

    expected_coverage_artifacts: list[tuple[str, str, str, str]] = []
    for index, job in enumerate(unit_jobs, start=1):
        run_step = _workflow_step(job=job, name=f"Unit tests (group {index})")
        assert run_step["run"].startswith(
            f"uv run python scripts/ci_lanes.py run --lane unit --group {index}"
        )
        assert "COVERAGE_FILE" not in run_step["env"]
        assert run_step["env"]["TEST_EVIDENCE_DIR"].endswith(f"ci-artifacts/unit-{index}")
        expected_coverage_artifacts.append(
            (
                f"ci-unit-{index}-coverage-data",
                f"coverage-unit-{index}.data",
                f"Download unit group {index} coverage data",
                f"ci-coverage/unit-{index}",
            )
        )

    for index, job in enumerate(integration_jobs, start=1):
        cleanup = _workflow_step(job=job, name="Free disk space before testcontainers")
        for fragment in (
            "MIN_FREE_GB=30",
            "available_kib=$(df -Pk / | awk 'NR == 2 {print $4}')",
            'case "$available_kib" in',
            "available_gb=$((available_kib / 1024 / 1024))",
            'if [ "$available_gb" -lt "$MIN_FREE_GB" ]; then',
            "sudo rm -rf /usr/local/lib/android",
            "Skipping disk cleanup:",
            "could not determine free disk space",
        ):
            assert fragment in cleanup["run"]
        run_step = _workflow_step(job=job, name=f"Integration tests (group {index})")
        assert run_step["run"].startswith(
            f"uv run python scripts/ci_lanes.py run --lane integration --group {index}"
        )
        assert "COVERAGE_FILE" not in run_step["env"]
        assert run_step["env"]["TEST_EVIDENCE_DIR"].endswith(f"ci-artifacts/integration-{index}")
        assert run_step["env"]["TESTCONTAINERS_RYUK_DISABLED"] == "true"
        expected_coverage_artifacts.append(
            (
                f"ci-integration-{index}-coverage-data",
                f"coverage-integration-{index}.data",
                f"Download integration group {index} coverage data",
                f"ci-coverage/integration-{index}",
            )
        )

    for row, job in zip(covered_matrix["strategy"]["matrix"]["include"], covered_jobs, strict=True):
        run_step = _workflow_step(job=job, name="Run the covered lane group")
        assert run_step["env"]["COVERAGE_FILE"].endswith(
            f"coverage-{row['lane']}-{row['group']}.data"
        )
    assert all(
        covered_workflow["jobs"][name]["env"]["CI_COVERAGE"] == "1"
        for name in ("coverage-shards", "coverage-preflight", "coverage-report")
    )
    assert len(covered_jobs) == 11
    assert not any(
        "coverage-data" in str(step)
        for job in [*unit_jobs, *integration_jobs]
        for step in job["steps"]
    )
    artifact_steps = {
        step["with"]["name"]: step
        for job in [preflight, *unit_jobs, *integration_jobs, *covered_jobs, coverage_job]
        for step in job["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
    }
    expected_artifacts = {
        "smoke-release-evidence",
        "ci-smoke-test-evidence",
        "ci-combined-coverage-report",
        *{name for name, _, _, _ in expected_coverage_artifacts},
        *{
            f"ci-{lane}-{index}-test-evidence"
            for lane, count in (("unit", 5), ("integration", 6))
            for index in range(1, count + 1)
        },
    }
    assert expected_artifacts <= artifact_steps.keys()
    for artifact_name in expected_artifacts:
        artifact = artifact_steps[artifact_name]
        assert artifact["with"]["overwrite"] is True
        assert ".tmp" not in artifact["with"]["path"]
        assert "raw-junit.xml" not in artifact["with"]["path"]

    for coverage_name, filename, download_name, directory in expected_coverage_artifacts:
        coverage_upload = artifact_steps[coverage_name]
        assert coverage_upload["if"] == "${{ always() && env.CI_COVERAGE == '1' }}"
        assert coverage_upload["with"]["if-no-files-found"] == "error"
        paths = coverage_upload["with"]["path"].splitlines()
        assert len(paths) == 2
        assert paths[0].endswith(filename)
        assert paths[1] == paths[0] + ".metadata.json"
        download = _workflow_step(job=coverage_job, name=download_name)
        assert download["uses"] == "actions/download-artifact@v4"
        assert download["with"] == {
            "name": coverage_name,
            "path": "${{ runner.temp }}/" + directory,
        }

    gate = _workflow_step(
        job=check_job,
        name="Fail closed from prerequisite verdicts only",
    )
    assert gate["id"] == "gate"
    assert gate["env"] == {
        "EVENT_NAME": "${{ github.event_name }}",
        "REF": "${{ github.ref }}",
        "NEEDS_JSON": "${{ toJSON(needs) }}",
    }
    assert check_job["steps"] == [gate]
    expected_shards = ["coverage-shards", "coverage-preflight"]
    assert coverage_job["needs"] == expected_shards
    assert coverage_job["if"] == "always()"
    assert "coverage" not in check_job["needs"]

    combine = _workflow_step(
        job=coverage_job, name="Combine coverage from all independent lane groups"
    )
    assert "coverage combine --data-file=" in combine["run"]
    for prefix, count in (("UNIT", 5), ("INTEGRATION", 6)):
        for index in range(1, count + 1):
            assert f"{prefix}_{index}_COVERAGE" in combine["run"]
    assert "scripts/check_ci_coverage.py" in combine["run"]

    smoke = _workflow_step(job=preflight, name="Smoke tests (fast gate + release evidence)")
    assert smoke["env"]["TESTCONTAINERS_RYUK_DISABLED"] == "true"
    assert smoke["env"]["SMOKE_EVIDENCE_DIR"].endswith("ci-artifacts/smoke")
    # Execute the installed shell fragments with a command-boundary receiver.
    # This proves argv, not a smoke run or a combined coverage report.
    argv_bin = tmp_path / "argv-bin"
    argv_bin.mkdir()
    receiver = argv_bin / "uv"
    receiver.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['ARGV_RECEIPT'], 'w') as output:\n"
        "    json.dump(sys.argv[1:], output)\n"
    )
    receiver.chmod(0o755)
    coverage_inputs = {
        f"{lane}_{index}_COVERAGE": str(tmp_path / f"{lane} {index} coverage.data")
        for lane, count in (("UNIT", 5), ("INTEGRATION", 6))
        for index in range(1, count + 1)
    }
    argv_receipt = tmp_path / "argv.json"
    shell_env = {
        **os.environ,
        "PATH": f"{argv_bin}{os.pathsep}{os.environ['PATH']}",
        "ARGV_RECEIPT": str(argv_receipt),
        "COMBINED_COVERAGE": str(tmp_path / "combined coverage.data"),
        "SMOKE_EVIDENCE_DIR": str(tmp_path / "smoke evidence"),
        **coverage_inputs,
    }
    fragments = (
        (
            "smoke",
            smoke["run"].split("SMOKE_EXIT=${PIPESTATUS[0]}", 1)[0],
            [
                "run",
                "pytest",
                "tests/",
                "--ignore=tests/e2e",
                "-m",
                "smoke",
                "-q",
                "--tb=short",
                f"--junit-xml={shell_env['SMOKE_EVIDENCE_DIR']}/raw-junit.xml",
            ],
        ),
        (
            "coverage",
            re.search(
                r"(?ms)^uv run coverage combine.*?(?=^uv run coverage json)", combine["run"]
            ).group(),
            [
                "run",
                "coverage",
                "combine",
                f"--data-file={shell_env['COMBINED_COVERAGE']}",
                *coverage_inputs.values(),
            ],
        ),
    )
    for name, fragment, expected_argv in fragments:
        result = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", fragment],
            env=shell_env,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, name
        assert json.loads(argv_receipt.read_text()) == expected_argv, name
    assert "--durations" not in smoke["run"]
    smoke_artifact = artifact_steps["smoke-release-evidence"]
    assert smoke_artifact["with"]["path"].endswith("smoke/release-evidence.json")

    badge = _workflow_step(job=coverage_job, name="Update coverage badge")
    assert badge["if"] == (
        "${{ success() && github.ref == 'refs/heads/main' && "
        "(github.event_name == 'schedule' || github.event_name == 'workflow_dispatch') }}"
    )

    # Every job, including rare/scheduled/reporting jobs, has a positive outer
    # watchdog. Provenance is before-only/provisional, never after calibration.
    register = json.loads((REPO_ROOT / "scripts/ci-job-timeouts.json").read_text())
    declared = {}
    for path in (REPO_ROOT / ".github/workflows").glob("*.yml"):
        source = yaml.safe_load(path.read_text())
        for name, job in source["jobs"].items():
            value = job.get("timeout-minutes")
            if value == "${{ matrix.timeout }}":
                assert set(job["strategy"]["matrix"]) == {"include"}
                caps = [row["timeout"] for row in job["strategy"]["matrix"]["include"]]
                assert caps and all(type(cap) is int and cap > 0 for cap in caps)
                value = max(caps)
            assert isinstance(value, int) and not isinstance(value, bool) and value > 0
            declared[(str(path.relative_to(REPO_ROOT)), name)] = value
    assert set(declared) == {(r["workflow"], r["job"]) for r in register["jobs"]}
    for row in register["jobs"]:
        assert declared[(row["workflow"], row["job"])] == row["installed_timeout_minutes"]
        assert row["state"] and row["sample_source"]
        if row["workflow"] == ".github/workflows/ci.yml" and row["job"] == "go-bridge":
            assert row["successful_sample_n"] == 0 and row["p95_s"] is None
            assert not row.get("healthy_job_observations")
            assert row["additional_provisional_cold_dependency_allowance_s"] == 270
            assert row["installed_timeout_minutes"] * 60 == 600 + 30 + 270
            assert "provisional" in row["state"]
        if row["p95_s"] is not None:
            reserve = (
                row["review_uv_nominal_envelope_s"] + row["review_browser_hard_retry_interval_s"]
            )
            reserve += row["additional_provisional_cold_dependency_allowance_s"]
            assert row["installed_timeout_minutes"] >= max(
                2, math.ceil((2 * row["p95_s"] + reserve) / 60)
            )
        else:
            assert "provisional" in row["state"] or row["job"] == "faketime-matrix"
        observations = row.get("healthy_job_observations", [])
        if observations:
            # A single whole-job observation is a compatibility floor, not p95.
            # Keep setup/evidence time and the full extra recovery allowance.
            durations = []
            for observation in observations:
                assert observation["conclusion"] == "success"
                elapsed = (
                    datetime.fromisoformat(observation["completed_at"])
                    - datetime.fromisoformat(observation["started_at"])
                ).total_seconds()
                assert elapsed >= observation["test_step_elapsed_s"] > 0
                durations.append(elapsed)
            envelope = max(durations)
            assert row["provisional_healthy_envelope_s"] == envelope
            reserve = (
                row["review_uv_nominal_envelope_s"]
                + row["review_browser_hard_retry_interval_s"]
                + row["additional_provisional_cold_dependency_allowance_s"]
            )
            assert row["installed_timeout_minutes"] * 60 >= 2 * envelope + reserve, (
                f"{row['job']} watchdog excludes its recorded healthy whole-job envelope "
                "and full setup/recovery headroom"
            )
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
    assert (
        browser_budget["installed_timeout_minutes"] == jobs["frontend-e2e"]["timeout-minutes"] == 20
    )
    assert "provisional" in browser_budget["state"] and "source-derived" in browser_budget["state"]
    nightly = yaml.safe_load((REPO_ROOT / ".github/workflows/nightly.yml").read_text())["jobs"][
        "faketime-matrix"
    ]
    assert nightly["timeout-minutes"] == 136
    nightly_shell = "\n".join(step.get("run", "") for step in nightly["steps"])
    assert "WATCHDOG_SECONDS=6360" in nightly_shell
    assert "--signal=SIGABRT --kill-after=30s" in nightly_shell
    assert "--timeout=300 --timeout-method=thread" in nightly_shell
    assert nightly["strategy"] == {"fail-fast": False, "matrix": {"offset": ["+45d", "+120d"]}}
    suite = _workflow_step(job=nightly, name="Full suite at faketime offset ${{ matrix.offset }}")
    suite_argv = shlex.split(
        suite["run"][suite["run"].index("uv run pytest ") :].replace("\\\n", " ")
    )
    # Both shifted PostgreSQL legs admit clock cases; retain the independent
    # exclusions and complete tests/roster scope, distribution and finite timers.
    assert suite_argv == [
        "uv",
        "run",
        "pytest",
        "tests/",
        "roster/",
        "-v",
        "-rf",
        "--tb=short",
        "--ignore=tests/e2e",
        "-m",
        "not bench and not perf and not nightly and not faketime_fragile",
        "-n",
        "auto",
        "--dist",
        "loadfile",
        "--timeout=300",
        "--timeout-method=thread",
    ]
    faketime_budget = next(row for row in register["jobs"] if row["job"] == "faketime-matrix")
    assert faketime_budget["successful_sample_n"] == 2 and faketime_budget["p95_s"] is None
    observations = faketime_budget["healthy_job_observations"]
    assert {observation["offset"] for observation in observations} == {"+45d", "+120d"}
    assert len(observations) == 2  # One historical observation per distinct offset, not p95.
    assert all(
        observation["run_id"] == 39802115884
        and observation["head_sha"] == "d5132bdeb7f12a49d969ac04a6459b29d7584d5b"
        for observation in observations
    )
    assert sorted(observation["test_step_elapsed_s"] for observation in observations) == [
        2198,
        3173,
    ]
    assert faketime_budget["provisional_healthy_envelope_s"] == 3271
    suite_envelope_s = max(observation["test_step_elapsed_s"] for observation in observations)
    assert (
        faketime_budget["installed_suite_watchdog_s"] == math.ceil(2 * suite_envelope_s / 60) * 60
    )
    assert faketime_budget["installed_suite_watchdog_s"] == 6360
    assert faketime_budget["review_uv_nominal_envelope_s"] == 640
    assert faketime_budget["review_browser_hard_retry_interval_s"] == 0
    reserves = faketime_budget["provisional_extra_reserves_s"]
    assert reserves == {"frozen_dependencies": 640, "setup_and_evidence": 300, "abort_cleanup": 30}
    assert faketime_budget["additional_provisional_cold_dependency_allowance_s"] == sum(
        reserves.values()
    )
    assert nightly["timeout-minutes"] == math.ceil((2 * 3271 + 640 + sum(reserves.values())) / 60)
    # Even a suite reaching its own cap retains the full extra reserves and
    # the largest observed setup/finalization time; no first-attempt subtraction.
    observed_other_job_s = max(
        (
            datetime.fromisoformat(observation["completed_at"])
            - datetime.fromisoformat(observation["started_at"])
        ).total_seconds()
        - observation["test_step_elapsed_s"]
        for observation in observations
    )
    assert nightly["timeout-minutes"] * 60 >= (
        faketime_budget["installed_suite_watchdog_s"]
        + faketime_budget["review_uv_nominal_envelope_s"]
        + sum(reserves.values())
        + observed_other_job_s
    )

    for browser_job in [jobs["frontend-e2e"]]:
        version_step = _workflow_step(job=browser_job, name="Resolve locked Playwright version")
        assert "npm run --silent test:e2e:cache-version" in version_step["run"]
        cache = _workflow_step(job=browser_job, name="Cache locked Playwright browsers")
        assert cache["continue-on-error"] is True  # advisory cache only
        assert cache["with"]["path"] == "~/.cache/ms-playwright"
        assert "restore-keys" not in cache["with"]
        assert cache["with"]["key"] == (
            "playwright-${{ runner.os }}-${{ runner.arch }}-"
            "${{ hashFiles('frontend/package-lock.json') }}-${{ steps.playwright-version.outputs.version }}"
        )
        installer = _workflow_step(job=browser_job, name="Install Playwright browsers")
        assert installer["run"] == "npm run test:e2e:install"
        assert "if" not in installer and not installer.get("continue-on-error")
    _exercise_browser_installer(tmp_path / "browser-install")
    assert _vitest_concurrent_receipt_controls(
        buildroot, pair, monkeypatch, tmp_path, bounded_helper
    )


def _historical_controls_coverage_receipt(tmp_path: Path) -> dict:
    """Trace genuine old bodies and current code under the repository config."""
    from coverage import CoverageData

    script = tmp_path / "trace_historical_controls.py"
    script.write_text(
        "import sys, json, hashlib\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from butlers.api.routers import model_settings\n"
        "from butlers.connectors import filtered_event_buffer\n"
        "from butlers.tools.relationship import dates\n"
        "from tests.three_seams_helpers import baseline_function\n"
        "functions = {name: baseline_function(name, vars(module)) for name, module in [\n"
        "    ('create_catalog_entry', model_settings), ('upcoming_dates', dates),\n"
        "    ('record', filtered_event_buffer)]}\n"
        "kwargs = dict(external_message_id='coverage-control', source_channel='email',\n"
        "    sender_identity='777000', subject_or_preview='482913',\n"
        "    filter_reason='validation_error', full_payload={})\n"
        "old = filtered_event_buffer.FilteredEventBuffer(\n"
        "    connector_type='telegram', endpoint_identity='synthetic:coverage')\n"
        "functions['record'](old, **kwargs)\n"
        "current = filtered_event_buffer.FilteredEventBuffer(\n"
        "    connector_type='telegram', endpoint_identity='synthetic:coverage')\n"
        "current.record(**kwargs)\n"
        "assert old._rows[-1][6] == kwargs['subject_or_preview']\n"
        "assert current._rows[-1][6] == '[auth-code withheld: telegram]'\n"
        "print(json.dumps({name: function.__code__.co_filename\n"
        "    for name, function in functions.items()}))\n"
    )
    data_file = tmp_path / "historical-controls.data"
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "run", f"--data-file={data_file}", str(script)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = CoverageData(basename=str(data_file))
    data.read()
    measured = set(data.measured_files())
    expected = {str(path.resolve()) for path in (REPO_ROOT / "src/butlers").rglob("*.py")}
    # The current-source positive prevents an absence-only green when no code ran.
    current = str(REPO_ROOT / "src/butlers/connectors/filtered_event_buffer.py")
    assert data.lines(current)
    assert measured == expected, (sorted(measured - expected), sorted(expected - measured))
    functions = json.loads(result.stdout)
    fixture = json.loads((REPO_ROOT / "tests/fixtures/three_seams_baseline.json").read_text())
    for name, filename in functions.items():
        assert fixture[name]["git_sha"] in filename and fixture[name]["path"] in filename
        assert not Path(filename).is_relative_to(REPO_ROOT / "src/butlers")
    return {"source_count": len(measured), "historical_filenames": functions}


def test_owned_coverage_workflow_retains_complete_assurance(tmp_path: Path) -> None:
    """REQ-ci-shard-assurance-006: the real covered producer and publish gates."""
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/nightly.yml").read_text())
    # Coverage runs only in the nightly schedule/manual workflow (bu-w72da2 = A).
    triggers = workflow[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert "pull_request_target" not in triggers
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert {"coverage-shards", "coverage-preflight", "coverage-report"} <= set(jobs)
    assert "coverage-inventory" not in jobs
    assert all(
        jobs[name]["env"]["CI_COVERAGE"] == "1"
        for name in ("coverage-shards", "coverage-preflight", "coverage-report")
    )
    shards = jobs["coverage-shards"]
    assert "needs" not in shards
    assert shards["strategy"]["fail-fast"] is False
    rows = shards["strategy"]["matrix"]["include"]
    assert rows == [
        {"lane": lane, "group": index, "timeout": cap}
        for lane, count, cap in (("unit", 5, 28), ("integration", 6, 36))
        for index in range(1, count + 1)
    ]
    assert shards["timeout-minutes"] == "${{ matrix.timeout }}"
    execute = _workflow_step(job=shards, name="Run the covered lane group")
    assert execute["run"] == (
        "uv run python scripts/ci_lanes.py run "
        "--lane ${{ matrix.lane }} --group ${{ matrix.group }} "
        '--output "${RUNNER_TEMP}/ci-artifacts/${{ matrix.lane }}-${{ matrix.group }}"'
    )
    assert execute["env"]["COVERAGE_FILE"].endswith(
        "coverage-${{ matrix.lane }}-${{ matrix.group }}.data"
    )
    duration = _workflow_step(
        job=shards, name="Prepare ${{ matrix.lane }} group ${{ matrix.group }} duration evidence"
    )
    evidence = _workflow_step(job=shards, name="Upload complete covered execution evidence")
    for row in rows:
        directory = tmp_path / "ci-artifacts" / f"{row['lane']}-{row['group']}"
        directory.mkdir(parents=True)
        (directory / "raw-junit.xml").write_text(
            '<testsuites><testsuite><testcase classname="tests.contract" '
            'name="test_receipt[private-value]" time="0.125">'
            "<system-out>private captured output</system-out>"
            "</testcase></testsuite></testsuites>"
        )
        command = duration["run"]
        uploads = evidence["with"]["path"]
        for key, value in row.items():
            command = command.replace("${{ matrix." + key + " }}", str(value))
            uploads = uploads.replace("${{ matrix." + key + " }}", str(value))
        result = subprocess.run(
            ["bash", "-e", "-c", command],
            cwd=REPO_ROOT,
            env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, (row, result.stderr.decode())
        sanitized = (directory / "junit.xml").read_text()
        assert 'name="test_receipt[...]"' in sanitized
        assert "private" not in sanitized
        assert "test_receipt[...]" in (directory / "top-durations.txt").read_text()
        assert f"/ci-artifacts/{row['lane']}-{row['group']}/junit.xml" in uploads
        assert f"/ci-artifacts/{row['lane']}-{row['group']}/collected.json" in uploads
        assert f"/ci-artifacts/{row['lane']}-{row['group']}/durations.json" in uploads
    assert (
        _workflow_step(job=shards, name="Upload raw covered group inputs")["with"][
            "if-no-files-found"
        ]
        == "error"
    )
    for name, prerequisites in (
        ("coverage-preflight", ["coverage-shards"]),
        ("coverage-report", ["coverage-shards", "coverage-preflight"]),
    ):
        job = jobs[name]
        assert job["needs"] == prerequisites
        assert job["if"] == "always()"
        gate = _workflow_step(job=job, name="Require every coverage prerequisite")
        assert job["steps"][0] == gate
        for failed in (None, *prerequisites, "unknown-job"):
            for outcome in ("failure", "skipped", "cancelled", "unknown", "success"):
                needs = {key: {"result": "success"} for key in prerequisites}
                if failed is not None:
                    needs[failed] = {"result": outcome}
                result = subprocess.run(
                    ["bash", "-e", "-c", gate["run"]],
                    env={**os.environ, "NEEDS_JSON": json.dumps(needs)},
                    capture_output=True,
                    timeout=10,
                )
                expected = failed is None or (failed != "unknown-job" and outcome == "success")
                assert (result.returncode == 0) is expected, (name, failed, outcome)
        missing = {key: {"result": "success"} for key in prerequisites[:-1]}
        result = subprocess.run(
            ["bash", "-e", "-c", gate["run"]],
            env={**os.environ, "NEEDS_JSON": json.dumps(missing)},
            capture_output=True,
            timeout=10,
        )
        assert result.returncode != 0
    preflight = jobs["coverage-preflight"]
    assert _workflow_step(
        job=preflight, name="Check the covered lane groups add up and stay within the test budget"
    )["run"] == (
        'uv run --no-sync python scripts/ci_lanes.py totals --counts "${RUNNER_TEMP}/ci-test-receipts"'
    )
    assert "if" not in _workflow_step(
        job=preflight, name="Smoke tests (fast gate + release evidence)"
    )
    assert (
        _workflow_step(job=preflight, name="Prepare smoke test duration evidence")["run"]
        == (
            _workflow_step(
                job=yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())["jobs"][
                    "check-preflight"
                ],
                name="Prepare smoke test duration evidence",
            )["run"]
        )
    )
    report = jobs["coverage-report"]
    badge = _workflow_step(job=report, name="Update coverage badge")
    assert badge["if"] == (
        "${{ success() && github.ref == 'refs/heads/main' && "
        "(github.event_name == 'schedule' || github.event_name == 'workflow_dispatch') }}"
    )
    assert (
        _artifact_step(job=report, artifact_name="ci-combined-coverage-report")["with"][
            "if-no-files-found"
        ]
        == "error"
    )
    action = yaml.safe_load(
        (REPO_ROOT / ".github/actions/ci-python-environment/action.yml").read_text()
    )
    assert action["runs"]["using"] == "composite"
    assert "inputs" not in action
    action_steps = {"steps": action["runs"]["steps"]}
    assert _workflow_step(job=action_steps, name="Install uv")["run"] == (
        "python3 scripts/install_ci_uv.py"
    )
    assert _workflow_step(job=action_steps, name="Install dependencies")["run"] == (
        "python3 scripts/ci_environment.py prepare"
    )
    cache = _workflow_step(job=action_steps, name="Restore advisory environment cache")
    assert cache["with"] == {
        "path": ".venv",
        "key": "${{ steps.environment-key.outputs.key }}",
    }
    assert cache["continue-on-error"] is True
    assert all(
        any(step.get("uses") == "./.github/actions/ci-python-environment" for step in job["steps"])
        for name, job in jobs.items()
        if name.startswith("coverage-")
    )


def test_ci_coverage_report_rejects_any_bad_input_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the actual report shell with ten independently traced coverage DBs.

    The immutable M1 shell demonstrates that nine valid plus one nonempty corrupt
    database produced a partial green report. This is local reporting evidence,
    not a hosted artifact-upload, badge-network or merge-group timing claim.
    """
    from coverage import CoverageData

    historical = _historical_controls_coverage_receipt(tmp_path)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import check_ci_coverage as reporter

    checkout = tmp_path / "checkout"
    source = checkout / "src/butlers/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 7\n")
    (source.parent / "__init__.py").write_text("")
    (checkout / "trace.py").write_text("import runpy\nrunpy.run_path('src/butlers/example.py')\n")
    (checkout / ".coveragerc").write_text("[run]\nsource = src/butlers\n")
    tests = checkout / "tests/test_example.py"
    tests.parent.mkdir()
    tests.write_text("def test_example(): pass\n")
    (checkout / "roster").mkdir()
    (checkout / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers=["integration"]\n')
    for index in range(6):
        (checkout / f"tests/test_seed_{index}.py").write_text(
            "import pytest\ndef test_unit(): pass\n@pytest.mark.integration\ndef test_integration(): pass\n"
        )
    specs = [
        (lane, index)
        for lane, count in (("unit", 5), ("integration", 6))
        for index in range(1, count + 1)
    ]
    scripts = checkout / "scripts"
    scripts.mkdir()
    for filename in ("check_ci_coverage.py", "ci_lanes.py"):
        shutil.copyfile(REPO_ROOT / "scripts" / filename, scripts / filename)
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CI fixture",
            "-c",
            "user.email=ci-fixture@example.invalid",
            "commit",
            "-qm",
            "coverage control fixture",
        ],
        cwd=checkout,
        check=True,
    )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    for name, value in {
        "GITHUB_SHA": head,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": "merge_group",
    }.items():
        monkeypatch.setenv(name, value)
    inputs = tmp_path / "runner/ci-coverage"
    population: dict[Path, bytes] = {}
    for lane, index in specs:
        data = inputs / f"{lane}-{index}" / f"coverage-{lane}-{index}.data"
        data.parent.mkdir(parents=True)
        subprocess.run(
            [sys.executable, "-m", "coverage", "run", f"--data-file={data}", "trace.py"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        )
        reporter.write_group_metadata(
            coverage_file=data, repo_root=checkout, lane=lane, group=index
        )
        population[data] = data.read_bytes()
        metadata = data.with_suffix(".data.metadata.json")
        population[metadata] = metadata.read_bytes()
    # Use the installed interpreter for each original `uv run` invocation. This
    # avoids installing another environment and preserves real coverage commands.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    shim = binaries / "uv"
    shim.write_text(
        '#!/bin/sh\n[ "$1" = run ] || exit 90\nshift\n'
        'if [ "$1" = python ]; then shift; exec ' + shlex.quote(sys.executable) + ' "$@"; fi\n'
        "exec " + shlex.quote(sys.executable) + ' -m "$@"\n'
    )
    shim.chmod(0o755)
    combined = inputs / "combined.data"
    report = tmp_path / "runner/ci-artifacts/coverage/coverage.json"
    output = tmp_path / "outputs"
    environment = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "RUNNER_TEMP": str(inputs.parent),
        "COMBINED_COVERAGE": str(combined),
        "COMBINED_REPORT": str(report),
        "GITHUB_OUTPUT": str(output),
        **{
            f"{lane.upper()}_{index}_COVERAGE": str(
                inputs / f"{lane}-{index}" / f"coverage-{lane}-{index}.data"
            )
            for lane, index in specs
        },
    }
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/nightly.yml").read_text())
    fixed = _workflow_step(
        job=workflow["jobs"]["coverage-report"],
        name="Combine coverage from all independent lane groups",
    )["run"]
    old_path = REPO_ROOT / "tests/fixtures/ci_coverage/m1-combine.sh"
    old = old_path.read_text()
    receipts = []

    def reset() -> None:
        shutil.rmtree(inputs)
        for path, contents in population.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        report.unlink(missing_ok=True)
        output.write_text("")

    def stamp(data: Path) -> None:
        reporter.write_group_metadata(coverage_file=data, repo_root=checkout, lane="unit", group=1)

    def run(label: str, body: str, expected: int) -> None:
        result = subprocess.run(
            ["bash", "-e", "-c", body],
            cwd=checkout,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        published = "percentage=" in output.read_text() and "color=" in output.read_text()
        assert result.returncode == expected, (label, result.stdout, result.stderr)
        assert report.exists() == (expected == 0), (label, result.stderr)
        assert published == (expected == 0), (label, result.stderr)
        receipts.append(
            {
                "case": label,
                "exit": result.returncode,
                "report": report.exists(),
                "badge_outputs": published,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )

    data = inputs / "unit-1/coverage-unit-1.data"
    metadata = data.with_suffix(".data.metadata.json")
    reset()
    run("fixed-ten-independently-traced-valid", fixed, 0)
    reset()
    data.write_bytes(b"nonempty corrupt coverage control\n")
    stamp(data)  # A valid digest cannot stand in for reading the actual SQLite DB.
    run("baseline-nine-valid-one-nonempty-corrupt", old, 0)
    reset()
    data.write_bytes(b"nonempty corrupt coverage control\n")
    stamp(data)
    run("fixed-nine-valid-one-nonempty-corrupt", fixed, 2)
    for case in (
        "missing",
        "empty",
        "extra",
        "mixed-run",
        "stale-head",
        "wrong-group",
        "wrong-config",
        "digest",
        "incompatible-tracing",
        "source-population",
    ):
        reset()
        if case == "missing":
            data.unlink()
        elif case == "empty":
            data.unlink()
            empty = CoverageData(basename=str(data))
            empty.add_lines({})
            empty.write()
            stamp(data)
        elif case == "extra":
            (inputs / "extra.data").write_bytes(b"extra")
        elif case in {"incompatible-tracing", "source-population"}:
            data.unlink()
            command = [sys.executable, "-m", "coverage", "run", f"--data-file={data}"]
            if case == "incompatible-tracing":
                command.append("--branch")
            else:
                command.append("--omit=src/butlers/__init__.py")
            subprocess.run(
                [*command, "trace.py"], cwd=checkout, check=True, capture_output=True, text=True
            )
            stamp(data)
        else:
            payload = json.loads(metadata.read_text())
            field, value = {
                "mixed-run": ("run_id", "124"),
                "stale-head": ("head", "0" * 40),
                "wrong-group": ("group", True),
                "wrong-config": ("config", {}),
                "digest": ("sha256", "0" * 64),
            }[case]
            payload[field] = value
            metadata.write_text(json.dumps(payload))
        run("fixed-" + case, fixed, 2)
    covered_workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/nightly.yml").read_text())
    covered = _workflow_step(
        job=covered_workflow["jobs"]["coverage-report"],
        name="Combine coverage from all independent lane groups",
    )["run"]
    # Each event gets eleven genuine traced inputs stamped by the production
    # writer. No merge-group event is borrowed to certify the scheduled or PR
    # assurance path.
    for event in ("schedule", "workflow_dispatch", "pull_request", "push"):
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        environment["GITHUB_EVENT_NAME"] = event
        reset()
        for lane, index in specs:
            current_data = inputs / f"{lane}-{index}" / f"coverage-{lane}-{index}.data"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "run",
                    f"--data-file={current_data}",
                    "trace.py",
                ],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
            )
            reporter.write_group_metadata(
                coverage_file=current_data, repo_root=checkout, lane=lane, group=index
            )
        run("covered-actual-" + event, covered, 2 if event == "push" else 0)
        if event != "push":
            payload = json.loads(metadata.read_text())
            payload["event"] = "merge_group"
            metadata.write_text(json.dumps(payload))
            report.unlink(missing_ok=True)
            output.write_text("")
            combined.unlink(missing_ok=True)
            run("covered-cross-event-" + event, covered, 2)
    assert all("GITHUB_EVENT_NAME=merge_group" not in body for body in (fixed, covered))
    receipt = tmp_path / "coverage-report-controls.json"
    receipt.write_text(
        json.dumps(
            {
                "baseline_shell_sha256": hashlib.sha256(old.encode()).hexdigest(),
                "fixed_shell_sha256": hashlib.sha256(fixed.encode()).hexdigest(),
                "source_checkout": head,
                "historical_control_source_identity": historical,
                "controls": receipts,
            },
            indent=2,
        )
    )
    print(f"coverage report receipt: {receipt}")
