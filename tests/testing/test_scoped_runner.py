"""Contract tests for plan-only agent worktree test selection."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from butlers.testing import scoped_runner
from butlers.testing.scoped_runner import (
    FULL_SUITE_FALLBACK_ALLOWLIST,
    ScopedTestPlan,
    plan_worktree_tests,
)
from butlers.testing.source_test_map import FULL_SUITE

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

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

    # P7's scratch corpus contains changed-file vectors, not historical refs.
    # Replay the CURRENT planner without claiming a historical Git diff. The
    # miniature cost profile is inner conformance, not measured scope evidence.
    from replay_ci_planner import replay

    row = {
        "id": "frozen-vector",
        "files": ["tests/api/test_existing.py"],
        "mode": "[CI DECISION] mode=full",
    }
    current = replay([row], root=repo)
    assert current["qualified_scoped"] == 1
    assert current["replay_mode"] == "current-vectors"
    assert current["records"][0]["historical_diff"] == "UNKNOWN"
    assert current["records"][0]["decision"]["base_head"] == current["planner_head"]
    malformed = replay([{**row, "mode": "not a planner mode"}], root=repo)
    assert malformed["unknown"] == 1
    assert malformed["qualified_scoped"] == 0
    historical = replay([row], root=repo, mode="historical-diffs")
    assert historical["qualified_scoped"] == 0
    assert historical["unknown"] == 1
    _write(repo, "docs/added.md", "actual changed resource")
    _git(repo, "add", "docs/added.md")
    _git(repo, "commit", "-qm", "actual historical diff control")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    actual = {
        "id": "actual-diff",
        "files": ["docs/added.md"],
        "base": current["planner_head"],
        "head": head,
    }
    verified = replay([actual], root=repo, mode="historical-diffs")
    assert verified["records"][0]["historical_diff"] == "verified"
    wrong = replay([{**actual, "files": row["files"]}], root=repo, mode="historical-diffs")
    assert wrong["unknown"] == 1
    assert wrong["records"][0]["historical_diff"] == "UNKNOWN"
    assert "BASE_UNAVAILABLE" in wrong["records"][0]["decision"]["reason_codes"]


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
    """REQ-pr-test-planning-001 REQ-pr-test-planning-002: actual read, wrong content and fresh route."""
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
    declarations = {
        "schema": "test-resource-declarations.v1",
        "dynamic": {
            reader: {
                "patterns": ["docs/**"],
                "body_sha256": {reader: hashlib.sha256((repo / reader).read_bytes()).hexdigest()},
            }
        },
    }
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
    original_reader = (repo / reader).read_text()
    _write(repo, reader, original_reader + "\n# changed reader\n")
    stale = plan_worktree_tests(base, repo_dir=repo)
    assert stale.scope == "full" and "READER_REGISTRY_STALE" in stale.reason_codes
    with pytest.raises(ValueError, match="READER_UNCLASSIFIED"):
        discover(repo, declarations)
    _write(repo, reader, original_reader)
    # A new undeclared dynamic reader is conservatively bound to every family.
    dynamic = "tests/api/test_dynamic.py"
    _write(repo, dynamic, "def read_unknown(path):\n    return path.read_text()\n")
    _git(repo, "add", dynamic)
    registry = discover(repo, declarations)
    assert dynamic in registry["readers"]["openspec/**"]
    assert dynamic in registry["unresolved_dynamic_readers"]

    # Actual CLI completeness guard: plant an undisclosed literal OpenSpec
    # reader, observe refusal, then regenerate its real edge and observe pass.
    guard = Path(__file__).resolve().parents[2] / "scripts/build_test_resource_map.py"
    _write(
        repo, REGISTRY, json.dumps(discover(repo, declarations), sort_keys=True, indent=2) + "\n"
    )
    _write(repo, "openspec/specs/control/spec.md", "real resource")
    _write(
        repo,
        "tests/api/test_openspec_reader.py",
        "from pathlib import Path\ndef test_resource():\n"
        "    assert Path('openspec/specs/control/spec.md').read_text() == 'real resource'\n",
    )
    _git(repo, "add", "tests/api/test_openspec_reader.py")

    def guard_command(*flags):
        return subprocess.run(
            [sys.executable, str(guard), "--root", str(repo), *flags],
            capture_output=True,
            timeout=30,
        ).returncode

    assert guard_command("--check") == 1
    assert guard_command() == 0
    assert guard_command("--check") == 0

    # Actual alias/finite-expression consumer: the old source visited its body
    # yet omitted the real edge. No canned row or estimated cost proves this.
    alias_repo = tmp_path / "alias-reader"
    alias_repo.mkdir()
    _git(alias_repo, "init", "-q")
    alias = "tests/test_alias_reader.py"
    _write(
        alias_repo,
        alias,
        "from builtins import open as read_resource\n"
        "def test_resource():\n"
        "    path = 'doc' + 's/contract.md'\n"
        "    with read_resource(path) as stream:\n"
        "        assert stream.read() == 'healthy'\n",
    )
    _write(alias_repo, "docs/contract.md", "healthy")
    _write(alias_repo, "pyproject.toml", '[tool.pytest.ini_options]\ntestpaths=["tests"]\n')
    _write(
        alias_repo,
        DECLARATIONS,
        json.dumps({"schema": "test-resource-declarations.v1", "dynamic": {}}),
    )
    _git(alias_repo, "add", ".")
    child_command = [sys.executable, "-m", "pytest", alias, "-q", "-n", "0"]
    assert (
        subprocess.run(child_command, cwd=alias_repo, capture_output=True, timeout=30).returncode
        == 0
    )
    _write(alias_repo, "docs/contract.md", "changed")
    assert (
        subprocess.run(child_command, cwd=alias_repo, capture_output=True, timeout=30).returncode
        == 1
    )
    from butlers.testing.resource_readers import select

    declared = json.loads((alias_repo / DECLARATIONS).read_text())
    actual = discover(alias_repo, declared)
    assert alias in select("docs/contract.md", actual)
    # No whole literal path and no .read() spelling: iteration consumes the
    # stream through an imported/assigned alias. The alias fallback itself must
    # retain this consumer, independent of finite-string discovery above.
    escaped = "tests/test_escaped_reader.py"
    _write(
        alias_repo,
        escaped,
        "from builtins import open as imported_read\n"
        "reader = imported_read\n"
        "def test_resource():\n"
        "    path = ''.join(['do', 'cs/contract.md'])\n"
        "    with reader(path) as stream:\n"
        "        assert ''.join(stream) == 'healthy'\n",
    )
    _git(alias_repo, "add", escaped)
    _write(alias_repo, "docs/contract.md", "healthy")
    escaped_command = [sys.executable, "-m", "pytest", escaped, "-q", "-n", "0"]
    assert (
        subprocess.run(escaped_command, cwd=alias_repo, capture_output=True, timeout=30).returncode
        == 0
    )
    _write(alias_repo, "docs/contract.md", "changed")
    assert (
        subprocess.run(escaped_command, cwd=alias_repo, capture_output=True, timeout=30).returncode
        == 1
    )
    actual = discover(alias_repo, declared)
    assert escaped in select("docs/contract.md", actual)
    # The actual existing guard refuses the stale/missing alias edge rather
    # than authorizing no-reader. Regenerate that real bound edge for positive.
    _write(alias_repo, REGISTRY, json.dumps({}))
    command = [sys.executable, str(guard), "--root", str(alias_repo)]
    assert subprocess.run(command + ["--check"], capture_output=True, timeout=30).returncode == 1
    assert subprocess.run(command, capture_output=True, timeout=30).returncode == 0
    assert subprocess.run(command + ["--check"], capture_output=True, timeout=30).returncode == 0

    # A root helper's actual importers are smaller than its entire tests/ tree.
    # Retain every unknown public IO family, but freeze the helper/caller bodies
    # and independently check the actual importer frontier. This is not an
    # empty-reader exemption or arbitrary dynamic-import completeness proof.
    helper = "tests/resource_helper.py"
    caller = "tests/api/test_helper_reader.py"
    unrelated = "tests/e2e/test_unrelated.py"
    _write(alias_repo, "tests/__init__.py", "")
    _write(alias_repo, helper, "def read(path):\n    return path.read_text()\n")
    _write(
        alias_repo,
        caller,
        "from pathlib import Path\nfrom tests.resource_helper import read\n"
        "def test_resource():\n    assert read(Path('docs/contract.md')) == 'healthy'\n",
    )
    _write(alias_repo, unrelated, "def test_unrelated():\n    assert 2 + 2 == 4\n")
    _git(alias_repo, "add", ".")
    helper_command = [sys.executable, "-m", "pytest", caller, "-q", "-n", "0"]
    _write(alias_repo, "docs/contract.md", "healthy")
    assert (
        subprocess.run(helper_command, cwd=alias_repo, capture_output=True, timeout=30).returncode
        == 0
    )
    _write(alias_repo, "docs/contract.md", "changed")
    assert (
        subprocess.run(helper_command, cwd=alias_repo, capture_output=True, timeout=30).returncode
        == 1
    )
    ordinary = discover(alias_repo, declared)
    assert unrelated in select("docs/contract.md", ordinary)
    owned = {
        **declared,
        "helper_owners": {
            helper: {
                "caller_sources": [caller],
                "body_sha256": {
                    path: hashlib.sha256((alias_repo / path).read_bytes()).hexdigest()
                    for path in (helper, caller)
                },
            }
        },
    }
    refined = discover(alias_repo, owned)
    assert caller in select("docs/contract.md", refined)
    assert unrelated not in select("docs/contract.md", refined)
    assert helper in refined["unresolved_dynamic_readers"]
    # Returned data cannot poison a subsequent body-keyed batch lookup.
    refined["readers"].clear()
    assert caller in select("docs/contract.md", discover(alias_repo, owned))
    _write(alias_repo, "tests/api/test_new_helper_reader.py", "from tests import resource_helper\n")
    _git(alias_repo, "add", "tests/api/test_new_helper_reader.py")
    with pytest.raises(ValueError, match="READER_UNCLASSIFIED"):
        discover(alias_repo, owned)
    (alias_repo / "tests/api/test_new_helper_reader.py").unlink()
    _git(alias_repo, "rm", "--cached", "tests/api/test_new_helper_reader.py")
    wrong_owner = copy.deepcopy(owned)
    wrong_owner["helper_owners"][helper]["caller_sources"] = [unrelated]
    with pytest.raises(ValueError, match="READER_UNCLASSIFIED"):
        discover(alias_repo, wrong_owner)
    _write(alias_repo, helper, "def read(path):\n    return path.read_bytes()\n")
    with pytest.raises(ValueError, match="READER_UNCLASSIFIED"):
        discover(alias_repo, owned)
    # A narrowed dynamic declaration also freezes its actual input producer.
    # A changed source cannot regenerate a fresh map under an old assertion.
    narrowed = {
        **declared,
        "dynamic": {
            escaped: {
                "patterns": ["docs/**"],
                "body_sha256": {
                    escaped: hashlib.sha256((alias_repo / escaped).read_bytes()).hexdigest()
                },
            }
        },
    }
    assert escaped in select("docs/contract.md", discover(alias_repo, narrowed))
    _write(alias_repo, escaped, (alias_repo / escaped).read_text() + "\n# changed admitted body\n")
    with pytest.raises(ValueError, match="READER_UNCLASSIFIED"):
        discover(alias_repo, narrowed)


def test_manifest_and_cost_admission_preserve_provenance_and_finite_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-pr-test-planning-003 REQ-pr-test-planning-004 REQ-pr-test-planning-005: verified Git delta and measured-cost protocol."""
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

    with pytest.raises(ValueError, match="complete compatible"):
        build_test_scope_cost.build([])

    # Real miniature collection, all eleven actual shard executions and a
    # separately paired affected child exercise the producer-reader seam.
    # Local miniature elapsed values are not hosted calibration or p90 proof.
    import ci_partition as partition

    from butlers.testing.scope_cost import context

    mini = tmp_path / "actual-cost-producer"
    mini.mkdir()
    (mini / "tests").mkdir()
    (mini / "roster").mkdir()
    (mini / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths=["tests","roster"]\n'
        'markers=["integration","e2e","nightly","bench","perf","smoke"]\n'
    )
    (mini / "conftest.py").write_text("def pytest_xdist_auto_num_workers():\n    return 1\n")
    for index in range(6):
        (mini / f"tests/test_{index}.py").write_text(
            "import pytest\n"
            "def test_unit(): assert 2 + 2 == 4\n"
            "@pytest.mark.integration\n"
            "def test_integration(): assert 3 + 3 == 6\n"
        )
    _git(mini, "init", "-q")
    _git(mini, "-c", "user.name=control", "-c", "user.email=control@example.invalid", "add", ".")
    _git(
        mini,
        "-c",
        "user.name=control",
        "-c",
        "user.email=control@example.invalid",
        "commit",
        "-qm",
        "actual producer fixture",
    )
    for key in ("GITHUB_SHA", "GITHUB_REPOSITORY", "GITHUB_WORKFLOW", "GITHUB_EVENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GITHUB_RUN_ID", "41")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    inventory = partition.collect_inventory(root=mini)
    assignment = partition.partition(inventory, {}, root=mini)
    project = Path(__file__).resolve().parents[2]

    def observed(label: str, files: list[str], metadata: dict, marker: str | None = None):
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "auto",
            "--cov=tests",
            "-p",
            "scripts.ci_shard_observer",
        ]
        if marker is not None:
            command += ["-m", marker]
        command += ["--", *files]
        output = mini / (label + ".json")
        started = time.monotonic()
        env = {
            **os.environ,
            "PYTHONPATH": str(project),
            "CI_SHARD_STARTED": str(started),
            "CI_SHARD_RECEIPT": str(output),
            "CI_SHARD_CONTEXT": json.dumps({**metadata, "files": files, "command": command}),
        }
        result = subprocess.run(command, cwd=mini, env=env, capture_output=True, timeout=30)
        assert result.returncode == 0
        receipt = json.loads(output.read_text())
        assert receipt["complete"] is True
        assert 0 < receipt["first_logical_test_s"] <= receipt["last_test_completed_s"]
        assert receipt["actual_tracers"] == ["CTracer"]
        return receipt, time.monotonic() - started

    receipts, clocks = {}, {}
    for lane, bins in assignment["shards"].items():
        for item in bins:
            label = f"{lane}-{item['index']}"
            receipts[label], clocks[label] = observed(
                label,
                item["files"],
                {
                    "lane": lane,
                    "shard": item["index"],
                    "inventory_digest": inventory["digest"],
                    "assignment_digest": assignment["digest"],
                    "inventory_identity": inventory["identity"],
                    "nonce": inventory["nonce"],
                },
                partition.SELECTORS[lane],
            )
    files = ["tests/test_0.py"]
    affected, elapsed = observed(
        "affected",
        files,
        {
            "kind": "affected-cost.v1",
            "run": "42",
            "attempt": "1",
            "nonce": "actual-affected",
            "source_head": inventory["identity"]["head"],
            "worker_policy": "auto",
            "cost_context": context(mini),
            "file_hashes": {
                name: hashlib.sha256((mini / name).read_bytes()).hexdigest() for name in files
            },
        },
    )
    bundle = {
        "run": "41",
        "affected_run": "42",
        "inventory": inventory,
        "assignment": assignment,
        "receipts": receipts,
        "heavy_job_seconds": clocks,
        "affected": affected,
        "affected_job_seconds": elapsed,
    }
    candidate = build_test_scope_cost.build([bundle], root=mini)
    assert candidate["reference"]["sample_count"] == 1
    assert candidate["reference"]["affected_setup_seconds"] > 0
    assert set(candidate["files"]) == set(inventory["lanes"]["unit"])
    # Exact missing-clock neutralization models the old producer's missing
    # output; actual old producer control is retained separately in the packet.
    for field in ("first_logical_test_s", "last_test_completed_s"):
        missing = copy.deepcopy(bundle)
        del missing["affected"][field]
        with pytest.raises(KeyError):
            build_test_scope_cost.build([missing], root=mini)
    for value in (math.nan, -1.0, True, elapsed + 1.0):
        malformed = copy.deepcopy(bundle)
        malformed["affected"]["first_logical_test_s"] = value
        with pytest.raises(ValueError, match="timer|clock"):
            build_test_scope_cost.build([malformed], root=mini)

    runtime_mismatch = copy.deepcopy(bundle)
    runtime_mismatch["receipts"]["unit-1"]["cost_environment"]["runtime"]["logical_cpus"] += 1
    with pytest.raises(ValueError, match="runtime/configuration"):
        build_test_scope_cost.build([runtime_mismatch], root=mini)
    stale = copy.deepcopy(bundle)
    stale["affected"]["cost_environment"]["configuration"] = "0" * 64
    with pytest.raises(ValueError, match="context"):
        build_test_scope_cost.build([stale], root=mini)
    missing_phase_clock = copy.deepcopy(bundle)
    del next(iter(missing_phase_clock["affected"]["nodes"].values()))["teardown"]["completed_s"]
    with pytest.raises(KeyError):
        build_test_scope_cost.build([missing_phase_clock], root=mini)
