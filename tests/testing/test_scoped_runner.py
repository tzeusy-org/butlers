"""Contract tests for plan-only agent worktree test selection."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from butlers.testing import scoped_runner
from butlers.testing.scoped_runner import (
    FULL_SUITE_FALLBACK_ALLOWLIST,
    ScopedTestPlan,
    plan_worktree_tests,
)
from butlers.testing.source_test_map import FULL_SUITE

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
    _write(repo, "roster/health/tests/test_existing.py", "def test_existing():\n    assert True\n")
    _write(repo, "tests/api/test_survivor.py", "def test_survivor():\n    assert True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    # Synthetic inner cost-conformance values, never hosted timing evidence.
    from butlers.testing.scope_cost import PROFILE, context

    profile = {
        "schema": "test-scope-cost.v1",
        "source_head": base,
        "context": context(repo),
        "reference": {
            "workers": "auto",
            "tracer": "CTracer",
            "runs": ["synthetic"],
            "heavy_shard_seconds": [300.0],
            "affected_setup_seconds": 1.0,
        },
        "files": {
            str(p.relative_to(repo)): {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "seconds": 1.0,
            }
            for p in repo.rglob("test_*.py")
        },
    }
    _write(repo, PROFILE, json.dumps(profile))
    _git(repo, "add", PROFILE)
    _git(repo, "commit", "-qm", "synthetic cost-conformance input")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    return repo, base


def test_untracked_test_file_produces_plan_only_exact_scope(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _write(repo, "tests/api/test_new.py", "def test_new():\n    assert True\n")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/api/test_new.py"]
    report = plan.report()
    assert "[PLAN ONLY] pytest was not executed" in report
    assert "[SCOPED]" in report
    assert "Running:" not in report


def test_deleted_test_file_widens_to_existing_parent_for_collection(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "tests/api/test_existing.py").unlink()

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/api/"]
    assert "Deleted test path" in plan.reason


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
    )
    monkeypatch.setattr(scoped_runner, "plan_worktree_tests", lambda **_: plan)

    def _unexpected_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("plan-only CLI must not execute the legacy runner")

    monkeypatch.setattr(scoped_runner, "run_scoped_tests", _unexpected_run)

    assert scoped_runner.main(["--base", "origin/main"]) == 0
    output = capsys.readouterr().out
    assert "[PLAN ONLY] pytest was not executed" in output
    assert "[SCOPED] fixture plan" in output


def _synthetic_cost(repo: Path) -> None:
    """Inner admission conformance; these numbers are not hosted measurements."""
    from butlers.testing.scope_cost import PROFILE, context

    profile = {
        "schema": "test-scope-cost.v1",
        "context": context(repo),
        "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo)
        .decode()
        .strip(),
        "reference": {
            "workers": "auto",
            "tracer": "CTracer",
            "runs": ["synthetic"],
            "heavy_shard_seconds": [300.0],
            "affected_setup_seconds": 1.0,
        },
        "files": {
            str(p.relative_to(repo)): {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "seconds": 1.0,
            }
            for p in repo.rglob("test_*.py")
        },
    }
    _write(repo, PROFILE, json.dumps(profile))


def test_public_resource_readers_are_current_and_selected_before_docs_skip(tmp_path: Path) -> None:
    """REQ-pr-test-planning-001/002: actual read, wrong content and fresh route."""
    import sys

    from butlers.testing.resource_readers import DECLARATIONS, REGISTRY, discover

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from ci_route import route

    repo, _ = _repo(tmp_path)
    reader = "tests/api/test_reader.py"
    _write(
        repo,
        reader,
        "from pathlib import Path\ndef test_reader():\n"
        "    assert Path('docs/contract.md').read_text() == 'healthy'\n",
    )
    _write(repo, "docs/contract.md", "healthy")
    declarations = {"schema": "test-resource-declarations.v1", "dynamic": {reader: ["docs/**"]}}
    _write(repo, DECLARATIONS, json.dumps(declarations))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "actual reader")
    _write(repo, REGISTRY, json.dumps(discover(repo, declarations)))
    _synthetic_cost(repo)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "synthetic source-bound conformance")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    _write(repo, "docs/contract.md", "wrong")
    plan = plan_worktree_tests(base, repo_dir=repo)
    assert plan.scope == "scoped" and plan.test_paths == [reader]
    child = subprocess.run(
        [sys.executable, "-m", "pytest", reader, "-q"], cwd=repo, capture_output=True, timeout=30
    )
    assert child.returncode == 1  # The selected real reader reaches its content assertion.
    _write(repo, "docs/contract.md", "healthy")
    child = subprocess.run(
        [sys.executable, "-m", "pytest", reader, "-q"], cwd=repo, capture_output=True, timeout=30
    )
    assert child.returncode == 0
    _write(repo, "docs/contract.md", "changed")
    _git(repo, "add", "docs/contract.md")
    _git(repo, "commit", "-qm", "changed public resource")
    routed = route(
        event="pull_request",
        ref="refs/pull/1/merge",
        files=["docs/contract.md"],
        docs=["docs/contract.md"],
        base=base,
        root=repo,
    )
    assert routed["backend"] == "true" and routed["mode"] == "scoped"
    assert routed["inventory"] == "true"  # Resource-driven collection may change item membership.
    (repo / REGISTRY).unlink()
    assert (
        route(
            event="pull_request",
            ref="refs/pull/1/merge",
            files=["docs/contract.md"],
            docs=["docs/contract.md"],
            base=base,
            root=repo,
        )["mode"]
        == "full"
    )
    _write(repo, REGISTRY, json.dumps(discover(repo, declarations)))
    _write(repo, reader, (repo / reader).read_text() + "\n# changed reader\n")
    stale = plan_worktree_tests(base, repo_dir=repo)
    assert stale.scope == "full" and "READER_REGISTRY_STALE" in stale.reason_codes
    # A new undeclared dynamic reader is conservatively bound to every family.
    dynamic = "tests/api/test_dynamic.py"
    _write(repo, dynamic, "def read_unknown(path):\n    return path.read_text()\n")
    _git(repo, "add", dynamic)
    registry = discover(repo, declarations)
    assert dynamic in registry["readers"]["openspec/**"]
    assert dynamic in registry["unresolved_dynamic_readers"]


def test_manifest_and_cost_admission_preserve_provenance_and_finite_ceiling(tmp_path: Path) -> None:
    """REQ-pr-test-planning-003/004/005: verified Git delta and measured-cost protocol."""
    from butlers.testing.manifest_scope import eligible
    from butlers.testing.scope_cost import PROFILE, predict

    repo, _ = _repo(tmp_path)
    manifest = ".github/ci-test-shards/unit-1.txt"
    _write(repo, manifest, "# unit\ntests/api/test_existing.py\n")
    _git(repo, "add", manifest)
    _git(repo, "commit", "-qm", "actual historical manifest")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    new = "tests/api/test_added.py"
    _write(repo, new, "def test_added():\n    assert True\n")
    _write(repo, manifest, "# unit\ntests/api/test_existing.py\n" + new + "\n")
    _git(repo, "add", manifest, new)
    _git(repo, "commit", "-qm", "actual same-diff added test")
    assert eligible(repo, [manifest, new], base, "HEAD") == {manifest}
    with pytest.raises(ValueError, match="MANIFEST_INELIGIBLE"):
        eligible(repo, [manifest], base, "HEAD")
    duplicate = ".github/ci-test-shards/unit-2.txt"
    _write(repo, duplicate, "tests/api/test_existing.py\n")
    _git(repo, "add", duplicate)
    _git(repo, "commit", "-qm", "duplicate ownership causal negative")
    with pytest.raises(ValueError, match="MANIFEST_INELIGIBLE"):
        eligible(repo, [manifest, new, duplicate], base, "HEAD")
    assert predict(repo, [new])["prediction_state"] == "provisional-new-file"
    _synthetic_cost(repo)
    assert predict(repo, [new])["prediction_state"] == "measured-compatible"
    profile = json.loads((repo / PROFILE).read_text())
    profile["reference"]["heavy_shard_seconds"] = [0.5]
    _write(repo, PROFILE, json.dumps(profile))
    assert predict(repo, [new])["reason"] == "COST_EXCEEDED"
    profile["reference"]["heavy_shard_seconds"] = [float("nan")]
    _write(repo, PROFILE, json.dumps(profile))
    assert predict(repo, [new])["reason"] == "COST_UNKNOWN"
    (repo / PROFILE).unlink()
    assert predict(repo, [new])["reason"] == "COST_UNKNOWN"
    with pytest.raises(ValueError, match="FULL"):
        scoped_runner.build_pytest_command(ScopedTestPlan(scope="full", test_paths=FULL_SUITE))
    import build_test_scope_cost

    with pytest.raises(ValueError, match="ten compatible"):
        build_test_scope_cost.build([])
