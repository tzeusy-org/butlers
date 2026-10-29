"""Contract tests for plan-only agent worktree test selection."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from butlers.testing import resource_readers, scoped_runner
from butlers.testing.changed_files import get_changed_files, historical_manifest_delta
from butlers.testing.scoped_runner import (
    FULL_SUITE_FALLBACK_ALLOWLIST,
    ScopedTestPlan,
    plan_worktree_tests,
)
from butlers.testing.source_test_map import FULL_SUITE
from scripts import build_test_resource_map, ci_test_plan

pytestmark = pytest.mark.unit


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, relative_path: str, content: str = "x = 1\n") -> None:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "planner-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Butlers test")
    _write(
        repo,
        "pyproject.toml",
        '[tool.pytest.ini_options]\ntestpaths = ["tests", "roster"]\n',
    )
    _write(repo, "tests/api/test_existing.py", "def test_existing():\n    assert True\n")
    _write(repo, "tests/api/test_survivor.py", "def test_survivor():\n    assert 2 + 2 == 4\n")
    _write(repo, "roster/health/tests/test_existing.py", "def test_existing():\n    assert True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, base


def test_untracked_test_file_produces_plan_only_exact_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, base = _repo(tmp_path)
    _write(repo, "tests/api/test_new.py", "def test_new():\n    assert True\n")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/api/test_new.py"]
    report = plan.report()
    assert "[PLAN ONLY] pytest was not executed" in report
    # Exact candidate ownership survives; absent cost cannot authorize execution.
    assert "[ESCALATE]" in report
    assert "COST_UNKNOWN" in report
    assert plan.mode == "full"
    assert "Running:" not in report
    assert plan_worktree_tests(base, repo_dir=repo).mode == "full"
    for residue in (".beads.gate.lock", ".claude/settings.local.json"):
        _write(repo, residue, "local harness fixture\n")
    clean = plan_worktree_tests(base, repo_dir=repo)
    assert clean.test_paths == ["tests/api/test_new.py"]
    assert clean.as_dict()["omitted_untracked_files"] == [
        ".beads.gate.lock",
        ".claude/settings.local.json",
    ]
    _git(repo, "add", ".beads.gate.lock")
    tracked = plan_worktree_tests(base, repo_dir=repo)
    assert tracked.mode == "full" and ".beads.gate.lock" in tracked.changed_files
    assert tracked.as_dict()["omitted_untracked_files"] == [".claude/settings.local.json"]
    foreign_parent = tmp_path / "foreign"
    foreign_parent.mkdir()
    foreign, _ = _repo(foreign_parent)
    with monkeypatch.context() as inherited:
        inherited.setenv("GIT_DIR", str(foreign / ".git"))
        assert plan_worktree_tests(base, repo_dir=repo).changed_files == tracked.changed_files
    unsafe = "tests/api/test_bad\nFORGED_STATUS.py"
    _write(repo, unsafe, "def test_bad(): assert True\n")
    refused = plan_worktree_tests(base, repo_dir=repo)
    assert refused.mode == "full" and "UNSAFE_DIFF" in refused.reason_codes
    assert unsafe in refused.changed_files
    assert "\nFORGED_STATUS" not in refused.report()
    assert repr(unsafe) in refused.report()


def test_historical_manifest_requires_real_git_content_and_new_test_execution(
    tmp_path: Path,
) -> None:
    """Old manifest syntax is source evidence, never current lane/cost authority."""
    repo, _ = _repo(tmp_path)
    manifest = ".github/ci-test-shards/unit-1.txt"
    original = (
        "# frozen unit ownership\n"
        + "\n".join(
            (
                "roster/health/tests/test_existing.py",
                "tests/api/test_existing.py",
                "tests/api/test_survivor.py",
            )
        )
        + "\n"
    )
    _write(repo, manifest, original)
    # The historical inputs had ten manifests; live five+six is separate.
    for lane in ("unit", "integration"):
        for shard in range(1, 6):
            if (lane, shard) == ("unit", 1):
                continue
            fixture = f"tests/api/test_{lane}_{shard}.py"
            _write(repo, fixture, "def test_fixture(): assert True\n")
            _write(repo, f".github/ci-test-shards/{lane}-{shard}.txt", fixture + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "real historical base")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    new = "tests/api/test_registered.py"
    _write(repo, new, "def test_registered():\n    assert False, 'actual-new-test-sentinel'\n")
    updated = original + new + "\n"
    _write(repo, manifest, updated)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "actual manifest and new test")
    changed = get_changed_files("HEAD", base, repo_dir=repo)
    witness = historical_manifest_delta(repo, changed)
    assert witness["eligible"] is True
    assert witness["test_paths"] == [new]
    assert changed.added_files == (new,)
    plan = scoped_runner.plan_scoped_tests("HEAD", base, repo_dir=repo)
    assert plan.changed_files == sorted([manifest, new])
    assert plan.scope == "scoped" and plan.test_paths == [new]
    assert plan.as_dict()["historical_manifest_witness"] == witness
    assert plan.mode == "full" and "COST_UNKNOWN" in plan.as_dict()["reason_codes"]
    child = subprocess.run(
        [sys.executable, "-m", "pytest", new, "-q", "-n", "0"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 1 and "actual-new-test-sentinel" in child.stdout
    # Each actual committed malformed body must refuse, even with the real test
    # addition present. Restore with an ordinary scratch-repository commit.
    for body in (
        updated.replace("# frozen", "# altered"),
        updated + new + "\n",
        updated + "tests/api/../test_escape.py\n",
        updated + "tests/api/test_missing.py\n",
        updated.replace("tests/api/test_existing.py\n", ""),
    ):
        _write(repo, manifest, body)
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "invalid historical control")
        assert (
            historical_manifest_delta(repo, get_changed_files("HEAD", base, repo_dir=repo))[
                "eligible"
            ]
            is False
        )
    _write(repo, manifest, updated)
    _write(repo, new, "def test_registered():\n    assert True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "exact valid source restored")
    assert (
        historical_manifest_delta(repo, get_changed_files("HEAD", base, repo_dir=repo))["eligible"]
        is True
    )
    healthy = subprocess.run(
        [sys.executable, "-m", "pytest", new, "-q", "-n", "0"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert healthy.returncode == 0, healthy.stdout + healthy.stderr
    # A dirty manifest cannot borrow the immutable committed witness.
    _write(repo, manifest, updated + "# dirty\n")
    assert "MANIFEST_INELIGIBLE" in plan_worktree_tests(base, repo_dir=repo).reason_codes
    incomplete_root = tmp_path / "incomplete"
    incomplete_root.mkdir()
    incomplete, _ = _repo(incomplete_root)
    _write(incomplete, manifest, original)
    _git(incomplete, "add", ".")
    _git(incomplete, "commit", "-qm", "incomplete historical base")
    incomplete_base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=incomplete,
        text=True,
    ).strip()
    _write(incomplete, new, "def test_registered(): assert True\n")
    _write(incomplete, manifest, updated)
    _git(incomplete, "add", ".")
    _git(incomplete, "commit", "-qm", "incomplete historical head")
    assert (
        historical_manifest_delta(
            incomplete,
            get_changed_files("HEAD", incomplete_base, repo_dir=incomplete),
        )["eligible"]
        is False
    )


def test_deleted_test_file_widens_to_existing_parent_for_collection(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "tests/api/test_existing.py").unlink()

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/api/"]
    assert "Deleted test path" in plan.reason

    _write(repo, "tests/test_root.py", "def test_root():\n    assert True\n")
    _write(repo, "tests/e2e/test_unsupported.py", "def test_e2e():\n    assert True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "root test and unsupported descendant")
    deleted_base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "tests/test_root.py").unlink()
    widened = plan_worktree_tests(deleted_base, repo_dir=repo)
    assert widened.changed_files == ["tests/test_root.py"]
    assert widened.scope == "full"
    assert widened.test_paths == ["tests/", "roster/"]
    assert "ROOT_WIDENING" in widened.reason_codes
    assert ci_test_plan.decide_mode(widened) == "full"


def test_renamed_test_deduplicates_the_parent_scope_and_collects(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _git(repo, "mv", "tests/api/test_existing.py", "tests/api/test_renamed.py")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/api/"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *plan.test_paths, "--collect-only", "-q"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_makefile_change_escalates_instead_of_claiming_no_tests(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _write(repo, "Makefile", "all:\n\t@true\n")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "full"
    assert plan.test_paths == FULL_SUITE
    assert "Escalate" in plan.reason
    # Real Git change discovery must not infer ownership from adjacent tests.
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "shared-infrastructure baseline")
    helper_base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    _write(repo, "tests/test_local.py", "def test_local():\n    assert True\n")
    _write(repo, "tests/three_seams_helpers.py", "def shared():\n    return 1\n")
    _write(
        repo,
        "roster/health/tests/test_existing.py",
        "from tests.three_seams_helpers import shared\ndef test_existing():\n    assert shared() == 1\n",
    )
    helper_plan = plan_worktree_tests(helper_base, repo_dir=repo)
    assert helper_plan.scope == "full"
    assert helper_plan.test_paths == FULL_SUITE
    assert "tests/three_seams_helpers.py" in helper_plan.changed_files


def test_unavailable_base_fails_closed_to_escalation(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)

    plan = plan_worktree_tests("does-not-exist", repo_dir=repo)

    assert plan.scope == "full"
    assert plan.test_paths == FULL_SUITE
    assert "unable to compute worktree diff" in plan.reason


def test_full_scope_uses_the_requested_worktree_testpaths(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _write(
        repo,
        "pyproject.toml",
        '[tool.pytest.ini_options]\ntestpaths = ["custom_tests"]\n',
    )
    _write(repo, "custom_tests/test_custom.py", "def test_custom():\n    assert True\n")
    _write(repo, "Makefile", "all:\n\t@true\n")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "full"
    assert plan.test_paths == ["custom_tests/"]


def test_default_allowlist_scopes_a_direct_e2e_test_edit(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _write(repo, "tests/e2e/test_new.py", "def test_new():\n    assert True\n")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/e2e/test_new.py"]


def test_custom_fallback_allowlist_escalates_a_path_the_default_allowlist_ignores(
    tmp_path: Path,
) -> None:
    repo, base = _repo(tmp_path)
    _write(repo, "tests/e2e/test_new.py", "def test_new():\n    assert True\n")
    widened_allowlist = FULL_SUITE_FALLBACK_ALLOWLIST + ("tests/e2e/",)

    plan = plan_worktree_tests(base, repo_dir=repo, fallback_allowlist=widened_allowlist)

    assert plan.scope == "full"
    assert plan.test_paths == FULL_SUITE
    assert "tests/e2e/test_new.py" in plan.reason
    assert "'tests/e2e/'" in plan.reason


def test_cli_main_is_plan_only_and_never_calls_legacy_runner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = ScopedTestPlan(
        scope="scoped",
        test_paths=["tests/testing/test_scoped_runner.py"],
        changed_files=["tests/testing/test_scoped_runner.py"],
        reason="fixture plan",
        source_head="1" * 40,
        base_head="2" * 40,
        prediction={
            "state": "measured",
            "reason": None,
            "predicted_seconds": 1.0,
            "ceiling_seconds": 300.0,
            "profile_digest": "3" * 64,
        },
    )
    monkeypatch.setattr(scoped_runner, "plan_worktree_tests", lambda **_: plan)

    def _unexpected_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("plan-only CLI must not execute the legacy runner")

    monkeypatch.setattr(scoped_runner, "run_scoped_tests", _unexpected_run)

    assert scoped_runner.main(["--base", "origin/main"]) == 0
    output = capsys.readouterr().out
    assert "[PLAN ONLY] pytest was not executed" in output
    assert "[SCOPED] fixture plan" in output


def test_roster_api_edit_reaches_real_dynamic_importer_and_roster_owner(tmp_path: Path) -> None:
    """A roster-only selection would miss this actual dynamically loaded router."""
    repo, _ = _repo(tmp_path)
    router = "roster/health/api/router.py"
    _write(repo, router, "def answer(): return 7\n")
    _write(
        repo,
        "tests/api/test_router_importer.py",
        (
            "import importlib.util\n"
            "from pathlib import Path\n"
            "ROOT = Path(__file__).resolve().parents[2]\n"
            "def test_router_importer():\n"
            f"    spec = importlib.util.spec_from_file_location('probe_router', ROOT / {router!r})\n"
            "    module = importlib.util.module_from_spec(spec)\n"
            "    spec.loader.exec_module(module)\n"
            "    assert module.answer() == 7\n"
        ),
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "actual roster router and importing consumer")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    _write(repo, router, "def answer(): return 99\n")
    plan = plan_worktree_tests(base, repo_dir=repo)
    assert plan.test_paths == ["roster/health/tests/", "tests/api/"]
    assert plan.mode == "full" and "COST_UNKNOWN" in plan.as_dict()["reason_codes"]
    command = [
        sys.executable,
        "-m",
        "pytest",
        *plan.test_paths,
        "-q",
        "--import-mode=importlib",
        "-p",
        "no:cacheprovider",
    ]
    environment = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    failed = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
    )
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "test_router_importer" in failed.stdout and "1 failed, 3 passed" in failed.stdout
    _write(repo, router, "def answer(): return 7\n")
    restored = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
    )
    assert restored.returncode == 0 and "4 passed" in restored.stdout


def test_resource_reader_guard_and_actual_selected_child(tmp_path: Path) -> None:
    """REQ-pr-test-planning-002: real IO, stale-edge refusal and exact restoration."""
    repo, _ = _repo(tmp_path)
    _write(repo, "docs/guide.md", "ready")
    _write(repo, "openspec/probe/spec.md", "ready")
    roots = (
        ("about/guide.md", "about"),
        (".claude/skills/probe/SKILL.md", "skill"),
        ("frontend/src/probe.ts", "frontend"),
        ("deploy/probe.md", "deploy"),
        ("AGENTS.md", "top_markdown"),
    )
    for resource, label in roots:
        _write(repo, resource, "ready")
        _write(
            repo,
            f"tests/api/test_{label}_reader.py",
            (
                "from pathlib import Path\n"
                "ROOT = Path(__file__).resolve().parents[2]\n"
                f"def test_{label}_reader():\n"
                f"    assert (ROOT / {resource!r}).read_text() == 'ready'\n"
            ),
        )
    _write(
        repo,
        "tests/api/test_reader.py",
        (
            "from pathlib import Path\n"
            "ROOT = Path(__file__).resolve().parents[2]\n"
            "def test_reader():\n"
            "    assert (ROOT / 'docs/guide.md').read_text() == 'ready'\n"
        ),
    )
    declarations = {"schema": 1, "dynamic": {}, "no_readers": {}}
    _write(repo, resource_readers.DECLARATIONS, json.dumps(declarations))
    assert build_test_resource_map.main(["--root", str(repo)]) == 0
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "actual resource reader baseline")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    _write(repo, "docs/guide.md", "broken")
    plan = plan_worktree_tests(base, repo_dir=repo)
    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/api/test_reader.py"]
    assert plan.mode == "full"  # Reader execution is not compatible hosted cost proof.
    assert "COST_UNKNOWN" in plan.as_dict()["reason_codes"]
    command = [sys.executable, "-m", "pytest", *plan.test_paths, "-q", "-p", "no:cacheprovider"]
    environment = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    failed = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
    )
    assert failed.returncode == 1
    assert "AssertionError" in failed.stdout and "1 failed" in failed.stdout
    _write(repo, "docs/guide.md", "ready")
    passed = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
    )
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert "1 passed" in passed.stdout

    # Each public root has a real reading child. These are applicability and
    # mutation controls, not cost admission or frontend execution evidence.
    for resource, label in roots:
        _write(repo, resource, "broken")
        current = plan_worktree_tests(base, repo_dir=repo)
        owner = f"tests/api/test_{label}_reader.py"
        assert current.test_paths == [owner], (resource, current.as_dict())
        assert current.mode == "full" and "COST_UNKNOWN" in current.as_dict()["reason_codes"]
        command = [
            sys.executable,
            "-m",
            "pytest",
            *current.test_paths,
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        failed = subprocess.run(
            command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
        )
        assert failed.returncode == 1 and "AssertionError" in failed.stdout, resource
        _write(repo, resource, "ready")
        restored = subprocess.run(
            command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
        )
        assert restored.returncode == 0 and "1 passed" in restored.stdout, resource

    registry_path = repo / resource_readers.REGISTRY
    exact = registry_path.read_bytes()
    tampered = json.loads(exact)
    tampered["readers"].pop("docs/guide.md")
    registry_path.write_text(json.dumps(tampered))
    assert build_test_resource_map.main(["--root", str(repo), "--check"]) == 1
    with pytest.raises(ValueError, match="stale"):
        resource_readers.load(repo)
    registry_path.write_bytes(exact)
    assert build_test_resource_map.main(["--root", str(repo), "--check"]) == 0

    # A genuine newly added, undeclared openspec reader must make check red.
    _write(
        repo,
        "tests/api/test_openspec.py",
        (
            "from pathlib import Path\n"
            "ROOT = Path(__file__).resolve().parents[2]\n"
            "def test_openspec():\n"
            "    assert (ROOT / 'openspec/probe/spec.md').read_text() == 'ready'\n"
        ),
    )
    assert build_test_resource_map.main(["--root", str(repo), "--check"]) == 1
    assert build_test_resource_map.main(["--root", str(repo)]) == 0
    registry = resource_readers.load(repo)
    selected = resource_readers.select("openspec/probe/spec.md", registry)
    assert selected == ["tests/api/test_openspec.py"]
    _write(repo, "openspec/probe/spec.md", "broken")
    command = [sys.executable, "-m", "pytest", *selected, "-q"]
    failed = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
    )
    assert failed.returncode == 1 and "AssertionError" in failed.stdout
    _write(repo, "openspec/probe/spec.md", "ready")
    passed = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
    )
    assert passed.returncode == 0 and "1 passed" in passed.stdout

    # An unproved helper frontier retains every real consumer, including roster.
    _write(
        repo,
        "tests/resource_helper.py",
        ("def read_from(caller_path):\n    return caller_path.read_text()\n"),
    )
    assert build_test_resource_map.main(["--root", str(repo)]) == 0
    registry = resource_readers.load(repo)
    assert resource_readers.unbounded("docs/new-unseen.md", registry)
    assert resource_readers.select("docs/new-unseen.md", registry) == registry["tests"]

    # Reviewed data vocabulary is separate from real IO. A complete-source
    # witness and the actual importer frontier are required before narrowing a
    # helper; a caller-provided consumer list cannot omit a roster importer.
    helper = "tests/resource_helper.py"
    _write(
        repo,
        helper,
        "FAMILIES = ('docs/', 'frontend/')\n"
        "def read_from(caller_path):\n    return caller_path.read_text()\n",
    )
    helper_consumers = [
        "tests/api/test_helper_reader.py",
        "roster/health/tests/test_helper_reader_roster.py",
    ]
    _write(repo, "tests/__init__.py", "")
    for owner in helper_consumers:
        levels = 2 if owner.startswith("tests/") else 3
        _write(
            repo,
            owner,
            "from pathlib import Path\n"
            "from tests.resource_helper import read_from\n"
            f"ROOT = Path(__file__).resolve().parents[{levels}]\n"
            "def test_helper_reader():\n"
            "    assert read_from(ROOT / 'docs/guide.md') == 'ready', 'helper-reader-reached'\n",
        )
    dynamic_consumer = "tests/api/test_helper_dynamic.py"
    _write(
        repo,
        dynamic_consumer,
        "from pathlib import Path\nimport importlib.util\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'fixture_reader', ROOT / 'tests' / 'resource_helper.py')\n"
        "module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
        "def test_dynamic_helper_reader():\n"
        "    assert module.read_from(ROOT / 'docs/guide.md') == 'ready', 'helper-reader-reached'\n",
    )
    helper_consumers.append(dynamic_consumer)
    assert build_test_resource_map.main(["--root", str(repo)]) == 0
    registry = resource_readers.load(repo)
    declarations["literal_data"] = {
        helper: {
            "witness": registry["source_hashes"][helper],
            "patterns": ["docs/*", "frontend/*"],
            "reason": "Exact helper fixture: FAMILIES is data; both real IO callers pass guide.md.",
        }
    }
    entry = {
        "witnesses": {
            path: registry["source_hashes"][path]["sha256"] for path in [helper, *helper_consumers]
        },
        "patterns": ["docs/guide.md"],
        "reason": "Complete isolated fixture: normal API, finite-loader API and roster callers execute.",
        "consumers": helper_consumers[:1],
        "source_set_digest": resource_readers.digest(registry["source_hashes"]),
    }
    declarations["dynamic"] = {helper: entry}
    _write(repo, resource_readers.DECLARATIONS, json.dumps(declarations))
    assert build_test_resource_map.main(["--root", str(repo)]) == 2
    entry["consumers"] = helper_consumers
    _write(repo, resource_readers.DECLARATIONS, json.dumps(declarations))
    assert build_test_resource_map.main(["--root", str(repo)]) == 0
    registry = resource_readers.load(repo)
    assert not resource_readers.unbounded("docs/guide.md", registry)
    selected = resource_readers.select("docs/guide.md", registry)
    assert set(selected) == {"tests/api/test_reader.py", *helper_consumers}
    assert resource_readers.select("frontend/src/probe.ts", registry) == [
        "tests/api/test_frontend_reader.py"
    ]
    _write(repo, "docs/guide.md", "broken")
    command = [sys.executable, "-m", "pytest", *helper_consumers, "-q"]
    failed = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
    )
    assert failed.returncode == 1 and "3 failed" in failed.stdout
    assert "helper-reader-reached" in failed.stdout
    _write(repo, "docs/guide.md", "ready")
    restored = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
    )
    assert restored.returncode == 0 and "3 passed" in restored.stdout

    # A freshly witnessed consumer list cannot omit a real importer merely
    # because its importlib loader travelled through an ordinary assignment.
    alias_consumer = "tests/api/test_helper_alias.py"
    for loader in (
        "import importlib\nloader = importlib.import_module\n",
        "from importlib import import_module as original\nloader = original\n",
        "import importlib\nloaders = {'chosen': importlib.import_module}\n"
        "loader = loaders['chosen']\n",
    ):
        _write(
            repo,
            alias_consumer,
            "from pathlib import Path\n"
            + loader
            + "module = loader('tests.' + 'resource_helper')\n"
            "ROOT = Path(__file__).resolve().parents[2]\n"
            "def test_alias_reader():\n"
            "    assert module.read_from(ROOT / 'docs/guide.md') == 'ready', "
            "'alias-reader-reached'\n",
        )
        entry["source_set_digest"] = resource_readers.digest(
            {
                path: {"sha256": hashlib.sha256((repo / path).read_bytes()).hexdigest()}
                for path in resource_readers.sources(repo)
            }
        )
        _write(repo, resource_readers.DECLARATIONS, json.dumps(declarations))
        assert build_test_resource_map.main(["--root", str(repo)]) == 2
    _write(repo, "docs/guide.md", "broken")
    alias_command = [sys.executable, "-m", "pytest", *helper_consumers, alias_consumer, "-q"]
    alias_failed = subprocess.run(
        alias_command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
    )
    assert alias_failed.returncode == 1 and "4 failed" in alias_failed.stdout
    assert "alias-reader-reached" in alias_failed.stdout
    _write(repo, "docs/guide.md", "ready")
    alias_restored = subprocess.run(
        alias_command, cwd=repo, capture_output=True, text=True, env=environment, timeout=30
    )
    assert alias_restored.returncode == 0 and "4 passed" in alias_restored.stdout
    (repo / alias_consumer).unlink()

    # A direct reader cannot be relabelled as literal data, nor can a fresh
    # importer be hidden behind the previous complete-source witness.
    declarations["literal_data"]["tests/api/test_reader.py"] = {
        "witness": registry["source_hashes"]["tests/api/test_reader.py"],
        "patterns": ["docs/guide.md"],
        "reason": "Deliberately false data-only claim must refuse.",
    }
    _write(repo, resource_readers.DECLARATIONS, json.dumps(declarations))
    assert build_test_resource_map.main(["--root", str(repo)]) == 2
    declarations["literal_data"].pop("tests/api/test_reader.py")
    _write(repo, resource_readers.DECLARATIONS, json.dumps(declarations))
    for code in (
        "import importlib\ndef test_dynamic_reader(name):\n    importlib.import_module(name)\n",
        "from importlib import import_module as load\n"
        "def test_dynamic_reader(name):\n    load(name)\n",
        "from butlers.tools.unindexed.reader import read_from\n"
        "def test_dynamic_reader():\n    read_from('docs/guide.md')\n",
        "exec('import tests.resource_helper')\ndef test_dynamic_reader():\n    assert True\n",
    ):
        _write(repo, "tests/api/test_dynamic_reader.py", code)
        entry["source_set_digest"] = resource_readers.digest(
            {
                path: {"sha256": hashlib.sha256((repo / path).read_bytes()).hexdigest()}
                for path in resource_readers.sources(repo)
            }
        )
        _write(repo, resource_readers.DECLARATIONS, json.dumps(declarations))
        assert build_test_resource_map.main(["--root", str(repo)]) == 2
    (repo / "tests/api/test_dynamic_reader.py").unlink()
    # Restore the prior unproved helper fixture for the original NONE controls.
    _write(repo, helper, "def read_from(caller_path):\n    return caller_path.read_text()\n")
    declarations["dynamic"] = {}
    declarations["literal_data"] = {}
    _write(repo, resource_readers.DECLARATIONS, json.dumps(declarations))
    assert build_test_resource_map.main(["--root", str(repo)]) == 0
    registry = resource_readers.load(repo)

    # NONE needs its own complete, current source-set audit; absence is insufficient.
    _write(repo, "docs/no-reader.md", "unread")
    declarations["no_readers"] = {
        "docs/no-reader.md": {
            "source_set_digest": resource_readers.digest(registry["source_hashes"]),
            "reason": "Complete isolated fixture: this markdown is not passed to any reader.",
        }
    }
    _write(repo, resource_readers.DECLARATIONS, json.dumps(declarations))
    assert build_test_resource_map.main(["--root", str(repo)]) == 0
    audited = resource_readers.load(repo)
    assert not resource_readers.unbounded("docs/no-reader.md", audited)
    assert resource_readers.select("docs/no-reader.md", audited) == []
    _write(repo, "tests/api/test_surprise.py", "def test_surprise():\n    assert True\n")
    assert build_test_resource_map.main(["--root", str(repo), "--check"]) == 2
    # A later public decision must rediscover this source change. The registry
    # shared privately within the earlier decision cannot outlive that call.
    later = plan_worktree_tests(base, repo_dir=repo)
    assert later.mode == "full" and "READER_REGISTRY_STALE" in later.reason_codes

    # The installed roster finder executes this exact, closed namespace. Its
    # generic loader remains opaque; a fixed import still needs its real edge.
    isolated = tmp_path / "canonical-roster"
    isolated.mkdir()
    canonical, _ = _repo(isolated)
    importer = "src/butlers/roster_imports.py"
    _write(
        canonical,
        importer,
        (Path(resource_readers.__file__).parents[1] / "roster_imports.py").read_text(),
    )
    _write(canonical, "src/butlers/__init__.py", "")
    _write(canonical, "src/butlers/tools/__init__.py", "")
    _write(canonical, "roster/probe/tools/__init__.py", "")
    reader = "roster/probe/tools/reader.py"
    _write(canonical, reader, "def read_from(path):\n    return path.read_text()\n")
    _write(canonical, "docs/guide.md", "ready")
    consumer = "tests/api/test_canonical_reader.py"
    _write(
        canonical,
        consumer,
        "from pathlib import Path\n"
        "from butlers.tools.probe.reader import read_from\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def test_canonical_reader():\n"
        "    assert read_from(ROOT / 'docs/guide.md') == 'ready', 'canonical-reader-reached'\n",
    )
    # Execute the genuine owning finder first. No import is executed by discovery.
    driver = (
        "import sys, importlib\n"
        f"sys.path.insert(0, {str(canonical / 'src')!r})\n"
        "from butlers.roster_imports import install_roster_imports\n"
        "install_roster_imports()\n"
        "module = importlib.import_module('butlers.tools.probe.reader')\n"
        "from pathlib import Path\n"
        f"assert Path(module.__file__).resolve() == Path({str(canonical / reader)!r})\n"
        "import pytest\n"
        f"raise SystemExit(pytest.main([{consumer!r}, '-q', '-p', 'no:cacheprovider']))\n"
    )
    imported = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=canonical,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert imported.returncode == 0 and "1 passed" in imported.stdout, (
        imported.stdout + imported.stderr
    )
    bodies = {p: (canonical / p).read_bytes() for p in resource_readers.sources(canonical)}
    assert resource_readers._consumer_frontier(bodies, [consumer], reader) == {consumer}, (
        "successful canonical resource consumer must retain its source edge"
    )
    _write(canonical, "docs/guide.md", "broken")
    reached = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=canonical,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert reached.returncode == 1 and "canonical-reader-reached" in reached.stdout
    _write(canonical, "docs/guide.md", "ready")

    # The real finder and the static edge both prefer a package to a file.
    package_reader = "roster/probe/tools/reader/__init__.py"
    _write(canonical, package_reader, bodies[reader].decode())
    package_driver = driver.replace(str(canonical / reader), str(canonical / package_reader))
    package_child = subprocess.run(
        [sys.executable, "-c", package_driver],
        cwd=canonical,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert package_child.returncode == 0 and "1 passed" in package_child.stdout
    bodies = {p: (canonical / p).read_bytes() for p in resource_readers.sources(canonical)}
    assert resource_readers._consumer_frontier(bodies, [consumer], package_reader) == {consumer}
    assert resource_readers._consumer_frontier(bodies, [consumer], reader) == set()
    assert resource_readers._consumer_frontier(
        bodies, [consumer], "roster/probe/tools/__init__.py"
    ) == {consumer}

    # Shared src owns collisions, including the selected parent package.
    shared_reader = "src/butlers/tools/probe/reader.py"
    _write(canonical, "src/butlers/tools/probe/__init__.py", "")
    _write(canonical, shared_reader, bodies[reader].decode())
    shared_driver = driver.replace(str(canonical / reader), str(canonical / shared_reader))
    shared_child = subprocess.run(
        [sys.executable, "-c", shared_driver],
        cwd=canonical,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert shared_child.returncode == 0 and "1 passed" in shared_child.stdout
    shared_bodies = {p: (canonical / p).read_bytes() for p in resource_readers.sources(canonical)}
    assert resource_readers._consumer_frontier(shared_bodies, [consumer], shared_reader) == {
        consumer
    }
    assert resource_readers._consumer_frontier(shared_bodies, [consumer], package_reader) == set()

    # No alias authority survives a changed/missing owner, missing parent or
    # unindexed target. Generic and custom-root loader APIs stay opaque.
    for removed in (importer, "roster/probe/tools/__init__.py"):
        subset = {p: body for p, body in bodies.items() if p != removed}
        assert resource_readers._consumer_frontier(subset, [consumer], package_reader) is None
    changed = {**bodies, importer: bodies[importer] + b"\n# changed owner\n"}
    assert resource_readers._consumer_frontier(changed, [consumer], package_reader) is None
    missing = {p: body for p, body in bodies.items() if p not in {reader, package_reader}}
    assert resource_readers._consumer_frontier(missing, [consumer], package_reader) is None
    for statement in (
        "import importlib\nimportlib.import_module('butlers.tools.Probe.reader')\n",
        "import importlib\nimportlib.import_module('butlers.tools.probe.not-valid')\n",
        "import importlib\ndef load(name):\n    return importlib.import_module(name)\n",
        "from butlers.roster_imports import roster_module\n"
        "def load(name, path, root):\n    return roster_module(name, path, api_source=root)\n",
    ):
        assert (
            resource_readers._consumer_frontier(
                {**bodies, consumer: statement.encode()}, [consumer], package_reader
            )
            is None
        )
    outside = tmp_path / "unowned.py"
    outside.write_text("pass\n")
    escape = canonical / "roster/probe/tools/escape.py"
    escape.symlink_to(outside)
    try:
        with pytest.raises(ValueError, match="symlink"):
            resource_readers.sources(canonical)
    finally:
        escape.unlink()

    # A fallback child's relative imports retain its canonical package even
    # when that package's parent was selected from shared src by the real finder.
    mixed = tmp_path / "mixed-canonical-parent"
    mixed.mkdir()
    mixed, _ = _repo(mixed)
    _write(mixed, importer, bodies[importer].decode())
    for marker in (
        "src/butlers/__init__.py",
        "src/butlers/tools/__init__.py",
        "src/butlers/tools/probe/__init__.py",
        "roster/probe/tools/__init__.py",
    ):
        _write(mixed, marker, "")
    mixed_reader = "roster/probe/tools/reader.py"
    shared_helper = "src/butlers/tools/probe/helper.py"
    _write(mixed, mixed_reader, "from .helper import read_from\n")
    _write(mixed, shared_helper, "def read_from(path):\n    return path.read_text()\n")
    _write(
        mixed,
        "roster/probe/tools/helper.py",
        "def read_from(path):\n    return 'roster-decoy'\n",
    )
    _write(mixed, "docs/guide.md", "ready")
    mixed_consumer = "tests/api/test_mixed_parent.py"
    _write(
        mixed,
        mixed_consumer,
        "from pathlib import Path\n"
        "from butlers.tools.probe.reader import read_from\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "def test_mixed_parent():\n"
        "    assert read_from(ROOT / 'docs/guide.md') == 'ready', 'mixed-parent-read-reached'\n",
    )
    mixed_driver = (
        "import sys, importlib\n"
        f"sys.path.insert(0, {str(mixed / 'src')!r})\n"
        "from butlers.roster_imports import install_roster_imports\n"
        "install_roster_imports()\n"
        "module = importlib.import_module('butlers.tools.probe.reader')\n"
        "helper = importlib.import_module('butlers.tools.probe.helper')\n"
        "from pathlib import Path\n"
        f"assert Path(module.__file__).resolve() == Path({str(mixed / mixed_reader)!r})\n"
        "assert module.__package__ == 'butlers.tools.probe'\n"
        f"assert Path(helper.__file__).resolve() == Path({str(mixed / shared_helper)!r})\n"
        "assert module.read_from.__module__ == 'butlers.tools.probe.helper'\n"
        "import pytest\n"
        f"raise SystemExit(pytest.main([{mixed_consumer!r}, '-q', '-p', 'no:cacheprovider']))\n"
    )
    mixed_child = subprocess.run(
        [sys.executable, "-c", mixed_driver],
        cwd=mixed,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert mixed_child.returncode == 0 and "1 passed" in mixed_child.stdout, (
        mixed_child.stdout + mixed_child.stderr
    )
    mixed_bodies = {p: (mixed / p).read_bytes() for p in resource_readers.sources(mixed)}
    assert resource_readers._consumer_frontier(mixed_bodies, [mixed_consumer], shared_helper) == {
        mixed_consumer
    }, "successful canonical relative helper must retain its actual consumer"
    _write(mixed, "docs/guide.md", "broken")
    mixed_read_failure = subprocess.run(
        [sys.executable, "-c", mixed_driver],
        cwd=mixed,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )
    assert mixed_read_failure.returncode == 1 and (
        "mixed-parent-read-reached" in mixed_read_failure.stdout
    )
    _write(mixed, "docs/guide.md", "ready")
