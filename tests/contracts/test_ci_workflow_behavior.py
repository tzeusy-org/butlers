"""CI workflow steps behave as their names claim when executed.

Each test extracts a real step's shell from ``.github/workflows/ci.yml`` (or a local make
target) and runs it against owned fixtures. YAML syntax belongs to actionlint; the required
fan-in belongs to ``test_required_checks.py``.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ENVIRONMENT_STEP = "Set up the CI Python environment"
PYTHON_ENVIRONMENT_ACTION = "./.github/actions/ci-python-environment"


def _ci_workflow() -> dict:
    return yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def _workflow_step(*, job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"Missing {name!r} step")


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


def test_go_bridge_steps_fail_closed_on_error_timeout_and_lock_drift(tmp_path: Path) -> None:
    go_bridge = _ci_workflow()["jobs"]["go-bridge"]
    assert go_bridge["env"] == {"GOTOOLCHAIN": "local"}
    setup_go = _workflow_step(job=go_bridge, name="Set up Go 1.25.11")
    assert setup_go["with"]["go-version"] == "1.25.11"
    assert setup_go["with"]["check-latest"] is False
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


def _guard_outcomes(guards: dict) -> str:
    return _workflow_step(job=guards, name="Fail if any guard failed")["env"]["GUARD_OUTCOMES"]


def test_every_identified_guard_reaches_the_guard_finalizer() -> None:
    guards = _ci_workflow()["jobs"]["guards"]
    outcomes = _guard_outcomes(guards)
    reported = {line.split("=", 1)[0] for line in outcomes.splitlines() if line}
    guard_steps = {step["id"]: step for step in guards["steps"] if "id" in step}

    # A guard step outside GUARD_OUTCOMES would fail without failing the job.
    assert reported == set(guard_steps)
    for guard_id, guard_step in guard_steps.items():
        assert "!cancelled()" in guard_step["if"]  # One failing guard never hides another.
        assert f"{guard_id}=${{{{ steps.{guard_id}.outcome }}}}" in outcomes


def test_guard_finalizer_refuses_any_failed_or_skipped_mandatory_guard() -> None:
    """Skipped mandatory checks cannot turn green; the non-PR session-link skip can."""
    guards = _ci_workflow()["jobs"]["guards"]
    finalizer = _workflow_step(job=guards, name="Fail if any guard failed")
    outcomes = {line.split("=", 1)[0]: "success" for line in _guard_outcomes(guards).splitlines()}
    for name, verdict, event, expected in [
        (None, None, "pull_request", 0),
        ("session_links", "skipped", "merge_group", 0),
        ("session_links", "skipped", "push", 0),
        ("session_links", "skipped", "pull_request", 1),
        *[
            (name, result, "pull_request", 1)
            for name in outcomes
            for result in ("failure", "skipped", "cancelled")
        ],
    ]:
        actual = dict(outcomes) if name is None else {**outcomes, name: verdict}
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
        assert result.returncode == expected, (name, verdict, event, result.stdout)


def test_local_check_guards_runs_the_hosted_static_guard_commands() -> None:
    guards = _ci_workflow()["jobs"]["guards"]
    static_commands = {
        "Check lock file is up to date": "uv lock --check",
        "Lint": "make lint",
        "Format check": "uv run ruff format --check src/ tests/ roster/ conftest.py -q",
        "SQL safety check (FOR UPDATE + outer join)": "make check-for-update-joins",
    }
    for name, command in static_commands.items():
        assert _workflow_step(job=guards, name=name)["run"] == command
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


def test_python_jobs_share_one_interpreter_identity_and_no_ambient_database() -> None:
    """REQ-testing-047 / REQ-testing-050: fixtures provision PG17; no CI ambient URL."""
    workflow = _ci_workflow()
    jobs = workflow["jobs"]
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
    assert {"guards", "smoke", "check-unit", "check-integration"} <= python_jobs
    for name in python_jobs:
        environment = _workflow_step(job=jobs[name], name=PYTHON_ENVIRONMENT_STEP)
        assert environment["uses"] == PYTHON_ENVIRONMENT_ACTION
    for name, job in jobs.items():
        assert "postgres" not in job.get("services", {}), name
        assert "DATABASE_URL" not in job.get("env", {}), name
