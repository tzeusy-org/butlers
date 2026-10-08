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
from scripts import ci_test_plan

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

pytestmark = pytest.mark.unit


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, relative_path: str, content: str = "x = 1\n") -> None:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
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
    # Current v2 admission fields are explicit simulated conformance inputs,
    # never authenticated hosted measurements.
    from butlers.testing.scope_cost import PROFILE

    _synthetic_cost(repo, monkeypatch)
    _git(repo, "add", PROFILE)
    _git(repo, "commit", "-qm", "synthetic cost-conformance input")
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    return repo, base


def test_untracked_test_file_produces_plan_only_exact_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo(tmp_path, monkeypatch)
    _write(repo, "tests/api/test_new.py", "def test_new():\n    assert True\n")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/api/test_new.py"]
    report = plan.report()
    assert "[PLAN ONLY] pytest was not executed" in report
    assert "[SCOPED]" in report
    assert "Running:" not in report


def test_deleted_test_file_widens_to_existing_parent_for_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo(tmp_path, monkeypatch)
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
    # This planner refuses root-wide scope before the CI adapter is invoked.
    assert widened.scope == "full" and widened.test_paths == FULL_SUITE
    assert "ROOT_WIDENING" in widened.reason_codes
    assert ci_test_plan.decide_mode(widened) == "full"


def test_renamed_test_deduplicates_the_parent_scope_and_collects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo(tmp_path, monkeypatch)
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


def test_makefile_change_escalates_instead_of_claiming_no_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo(tmp_path, monkeypatch)
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


def test_unavailable_base_fails_closed_to_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = _repo(tmp_path, monkeypatch)

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


def test_full_scope_uses_the_requested_worktree_testpaths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo(tmp_path, monkeypatch)
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


def test_default_allowlist_scopes_a_direct_e2e_test_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo(tmp_path, monkeypatch)
    _write(repo, "tests/e2e/test_new.py", "def test_new():\n    assert True\n")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/e2e/test_new.py"]


def test_custom_fallback_allowlist_escalates_a_path_the_default_allowlist_ignores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo(tmp_path, monkeypatch)
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


def _synthetic_cost(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit v2 inner conformance; labels/numbers are not hosted measurements."""
    from butlers.testing.scope_cost import PROFILE, environment, runner_class

    for key, value in {
        "RUNNER_ENVIRONMENT": "github-hosted",
        "CI_COST_RUNNER_LABEL": "ubuntu-latest",
        "ImageOS": "ubuntu24",
        "ImageVersion": "20261001.1.0",
        "CI_COST_EXPECTED_WORKERS": "1",
        "PYTEST_XDIST_AUTO_WORKERS": "1",
        "CI_COVERAGE": "1",
        "CI_COVERAGE_CORE": "ctrace",
    }.items():
        monkeypatch.setenv(key, value)
    observed = environment(repo)
    classification = runner_class(observed)
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    model = observed["runtime"]["cpu_model_digest"]
    measurement = {
        "environment": observed,
        "run": "41",
        "attempt": "1",
        "source": source,
        "job": "affected",
        "workers": 1,
        "instrumentation": "CTracer",
    }
    profile = {
        "schema": "test-scope-cost.v2",
        "context": hashlib.sha256(json.dumps(classification, sort_keys=True).encode()).hexdigest(),
        "runner_class": classification,
        "hardware_ledger": {model: [measurement]},
        "source_head": source,
        "reference": {
            "workers": "auto",
            "tracer": "CTracer",
            "runs": ["41"],
            "heavy_shard_seconds": [300.0],
            "affected_setup_seconds": 1.0,
        },
        "files": {
            str(p.relative_to(repo)): {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "seconds": 1.0,
                "observations": [{"seconds": 1.0, "model": model, "measurement": measurement}],
            }
            for p in repo.rglob("test_*.py")
        },
    }
    _write(repo, PROFILE, json.dumps(profile))


def test_public_resource_readers_are_current_and_selected_before_docs_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-pr-test-planning-001 REQ-pr-test-planning-002: actual read, wrong content and fresh route."""
    import sys

    from butlers.testing.resource_readers import DECLARATIONS, REGISTRY, discover

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from ci_route import route

    repo, _ = _repo(tmp_path, monkeypatch)
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
    _synthetic_cost(repo, monkeypatch)
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

    # A resolved working body is not a resolved index. Three merge stages
    # cannot create repeated reader authority or populate the discovery cache.
    index_reader = "tests/test_index_reader.py"
    body = (
        "from pathlib import Path\nvalue = 'base'\ndef read():\n"
        "    return Path('docs/contract.md').read_text()\n"
    )
    _write(alias_repo, index_reader, body)
    _git(alias_repo, "add", ".")
    _git(alias_repo, "commit", "-qm", "reader index baseline")
    _git(alias_repo, "branch", "reader-companion")
    _write(alias_repo, index_reader, body.replace("'base'", "'own'"))
    _git(alias_repo, "add", index_reader)
    _git(alias_repo, "commit", "-qm", "own reader change")
    _git(alias_repo, "checkout", "reader-companion")
    _write(alias_repo, index_reader, body.replace("'base'", "'public'"))
    _git(alias_repo, "add", index_reader)
    _git(alias_repo, "commit", "-qm", "public reader change")
    _git(alias_repo, "checkout", "-")
    conflict = subprocess.run(
        ["git", "merge", "--no-commit", "reader-companion"],
        cwd=alias_repo,
        capture_output=True,
        timeout=30,
    )
    assert conflict.returncode == 1
    _write(alias_repo, index_reader, body.replace("'base'", "'resolved'"))
    with pytest.raises(ValueError, match="READER_UNCLASSIFIED"):
        discover(alias_repo, declared)
    old_registry = (alias_repo / REGISTRY).read_bytes()
    assert subprocess.run(command, capture_output=True, timeout=30).returncode == 1
    assert (alias_repo / REGISTRY).read_bytes() == old_registry
    _git(alias_repo, "add", index_reader)
    assert discover(alias_repo, declared)["unresolved_dynamic_readers"].count(index_reader) == 1
    assert subprocess.run(command, capture_output=True, timeout=30).returncode == 0
    assert subprocess.run(command + ["--check"], capture_output=True, timeout=30).returncode == 0


def test_manifest_and_cost_admission_preserve_provenance_and_finite_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-pr-test-planning-003 REQ-pr-test-planning-004 REQ-pr-test-planning-005: verified Git delta and measured-cost protocol."""
    from butlers.testing.manifest_scope import eligible
    from butlers.testing.scope_cost import PROFILE, predict

    repo, _ = _repo(tmp_path, monkeypatch)
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
    from replay_ci_planner import replay

    vector = {
        "id": "actual-manifest-vector",
        "files": [manifest, new],
        "base": base,
        "head": "HEAD",
    }
    admitted = replay([vector], root=repo)
    assert admitted["qualified_scoped"] == 1
    assert admitted["records"][0]["historical_diff"] == "verified"
    mismatch = replay([{**vector, "files": [manifest]}], root=repo)
    assert mismatch["records"][0]["historical_diff"] == "UNKNOWN"
    assert mismatch["records"][0]["decision"]["mode"] == "full"
    with pytest.raises(ValueError, match="MANIFEST_INELIGIBLE"):
        eligible(repo, [manifest], base, "HEAD")
    duplicate = ".github/ci-test-shards/unit-2.txt"
    _write(repo, duplicate, "tests/api/test_existing.py\n")
    _git(repo, "add", duplicate)
    _git(repo, "commit", "-qm", "duplicate ownership causal negative")
    with pytest.raises(ValueError, match="MANIFEST_INELIGIBLE"):
        eligible(repo, [manifest, new, duplicate], base, "HEAD")
    assert predict(repo, [new])["prediction_state"] == "provisional-new-file"
    _synthetic_cost(repo, monkeypatch)
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
    # Actual local pytest children, with a simulated hosted policy label only
    # for software admission conformance; never authenticated hosted evidence.
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("CI_COST_RUNNER_LABEL", "ubuntu-latest")
    monkeypatch.setenv("ImageOS", "ubuntu24")
    monkeypatch.setenv("ImageVersion", "20261001.1.0")
    monkeypatch.setenv("CI_COST_EXPECTED_WORKERS", "1")
    monkeypatch.setenv("CI_COVERAGE", "1")
    monkeypatch.setenv("CI_COVERAGE_CORE", "ctrace")
    monkeypatch.setenv("GITHUB_RUN_ID", "41")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    inventory = partition.collect_inventory(root=mini)
    assignment = partition.partition(inventory, {}, root=mini)
    project = Path(__file__).resolve().parents[2]

    def observed(
        label: str,
        files: list[str],
        metadata: dict,
        marker: str | None = None,
        *,
        coverage: str = "1",
    ):
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n",
            "auto",
            "-p",
            "scripts.ci_shard_observer",
        ]
        if coverage == "1":
            command.append("--cov=tests")
        if marker is not None:
            command += ["-m", marker]
        command += ["--", *files]
        output = mini / (label + ".json")
        started = time.monotonic()
        env = {
            **os.environ,
            "PYTHONPATH": str(project),
            "CI_COVERAGE": coverage,
            "CI_SHARD_STARTED": str(started),
            "CI_SHARD_RECEIPT": str(output),
            "CI_SHARD_CONTEXT": json.dumps({**metadata, "files": files, "command": command}),
        }
        result = subprocess.run(command, cwd=mini, env=env, capture_output=True, timeout=30)
        assert result.returncode == 0
        receipt = json.loads(output.read_text())
        assert receipt["complete"] is True
        assert 0 < receipt["first_logical_test_s"] <= receipt["last_test_completed_s"]
        if coverage == "1":
            assert receipt["actual_tracers"] == ["CTracer"]
        else:
            assert receipt["actual_tracers"] == []
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
    (mini / "software-traced-bundle.json").write_text(json.dumps(bundle))
    candidate = build_test_scope_cost.build([bundle], root=mini)
    assert candidate["reference"]["sample_count"] == 1
    _write(mini, PROFILE, json.dumps(candidate))
    assert predict(mini, files)["prediction_state"] == "measured-compatible"
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

    from butlers.testing.scope_cost import measurement_species

    assert measurement_species(affected) == "CTracer"
    untraced_receipts, untraced_clocks = {}, {}
    for label, receipt in receipts.items():
        untraced_receipts[label], untraced_clocks[label] = observed(
            "untraced-" + label,
            receipt["files"],
            {
                key: receipt[key]
                for key in (
                    "lane",
                    "shard",
                    "inventory_digest",
                    "assignment_digest",
                    "inventory_identity",
                    "nonce",
                )
            },
            partition.SELECTORS[receipt["lane"]],
            coverage="0",
        )
    untraced_affected, untraced_elapsed = observed(
        "untraced-affected",
        files,
        {
            key: affected[key]
            for key in (
                "kind",
                "run",
                "attempt",
                "nonce",
                "source_head",
                "worker_policy",
                "file_hashes",
            )
        },
        coverage="0",
    )
    untraced = {
        **bundle,
        "receipts": untraced_receipts,
        "heavy_job_seconds": untraced_clocks,
        "affected": untraced_affected,
        "affected_job_seconds": untraced_elapsed,
    }
    # Retained in pytest's disposable miniature checkout for the separate old
    # reader counterfactual; never an installable/host-authenticated profile.
    (mini / "software-untraced-bundle.json").write_text(json.dumps(untraced))
    candidate = build_test_scope_cost.build([untraced], root=mini)
    assert candidate["reference"]["tracer"] == "untraced"
    assert measurement_species(untraced_affected) == "untraced"
    assert candidate["hardware_ledger"]
    assert candidate["files"][files[0]]["observations"]
    for name, row in candidate["files"].items():
        serial_full = math.fsum(
            receipt["file_durations_s"].get(name, 0.0) for receipt in untraced_receipts.values()
        )
        assert row["seconds"] >= serial_full

    # These representation mutants are software controls, not observations of
    # different real CPUs. Every actual job/file contribution remains explicit.
    heterogeneous = copy.deepcopy(untraced)
    changed_receipt = heterogeneous["receipts"]["unit-1"]
    changed_receipt["cost_environment"]["runtime"]["cpu_model_digest"] = "a" * 64
    changed_receipt["cost_context"] = hashlib.sha256(
        json.dumps(changed_receipt["cost_environment"], sort_keys=True).encode()
    ).hexdigest()
    candidate = build_test_scope_cost.build([heterogeneous], root=mini)
    assert "a" * 64 in candidate["hardware_ledger"]
    assert candidate["hardware_ledger"]["a" * 64][0]["job"] == "unit-1"
    assert candidate["builder_environment"]["runtime"]["coverage_policy"] == "1"
    assert candidate["runner_class"]["runtime"]["coverage_policy"] == "0"
    monkeypatch.setenv("CI_COVERAGE", "0")
    # Planted magnitudes falsify partial-lane max accounting without claiming
    # those synthetic numbers are observed timing. Both actual lane populations
    # contributed to this same real miniature file.
    from butlers.testing.scope_cost import whole_file_cost

    mixed_name = next(name for name in candidate["files"] if name not in files)
    planted = copy.deepcopy(candidate)
    samples = planted["files"][mixed_name]["observations"]
    assert len(samples) == 2
    assert {sample["measurement"]["job"].split("-")[0] for sample in samples} == {
        "unit",
        "integration",
    }
    for sample in samples:
        sample["seconds"] = 12.0
    planted["files"][mixed_name]["seconds"] = whole_file_cost(samples)
    planted["reference"]["affected_setup_seconds"] = 1.0
    planted["reference"]["heavy_shard_seconds"] = [20.0]
    _write(mini, PROFILE, json.dumps(planted))
    assert predict(mini, [mixed_name])["reason"] == "COST_EXCEEDED"
    # Current admission must not fall back to the legacy context-only reader.
    # The actual historical reader's separate partial-max control is retained
    # outside production/test module identities in the author evidence packet.
    old_partial_max = copy.deepcopy(planted)
    old_partial_max["schema"] = "test-scope-cost.v1"
    old_partial_max["context"] = context(mini)
    old_partial_max["reference"]["tracer"] = "CTracer"
    old_partial_max["files"][mixed_name]["seconds"] = 12.0
    for key in ("hardware_ledger", "runner_class", "environment", "builder_environment"):
        old_partial_max.pop(key, None)
    for row in old_partial_max["files"].values():
        row.pop("observations", None)
    _write(mini, PROFILE, json.dumps(old_partial_max))
    assert predict(mini, [mixed_name])["reason"] == "COST_UNKNOWN"
    overlapping = copy.deepcopy(planted)
    overlapping["files"][mixed_name]["observations"].append(copy.deepcopy(samples[0]))
    _write(mini, PROFILE, json.dumps(overlapping))
    assert predict(mini, [mixed_name])["reason"] == "COST_UNKNOWN"
    nonfinite = copy.deepcopy(planted)
    for sample in nonfinite["files"][mixed_name]["observations"]:
        sample["seconds"] = 1e308
    _write(mini, PROFILE, json.dumps(nonfinite))
    assert predict(mini, [mixed_name])["reason"] == "COST_UNKNOWN"
    incomplete = copy.deepcopy(untraced)
    del incomplete["receipts"]["integration-1"]
    with pytest.raises(ValueError):
        build_test_scope_cost.build([incomplete], root=mini)
    _write(mini, PROFILE, json.dumps(candidate))
    assert predict(mini, files)["prediction_state"] == "measured-compatible"
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "local")
    assert predict(mini, files)["reason"] == "COST_UNKNOWN"
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    for policy, value in (
        ("CI_COST_RUNNER_LABEL", "other"),
        ("CI_COST_EXPECTED_WORKERS", "2"),
        ("CI_COVERAGE", "1"),
    ):
        previous = os.environ[policy]
        monkeypatch.setenv(policy, value)
        assert predict(mini, files)["reason"] == "COST_UNKNOWN"
        monkeypatch.setenv(policy, previous)
    missing_model = copy.deepcopy(candidate)
    del missing_model["hardware_ledger"][candidate["environment"]["runtime"]["cpu_model_digest"]]
    (mini / PROFILE).write_text(json.dumps(missing_model))
    assert predict(mini, files)["reason"] == "COST_UNKNOWN"
    _write(mini, PROFILE, json.dumps(candidate))

    for field in ("coverage_enabled", "collector_present", "tracer"):
        missing = copy.deepcopy(untraced)
        del missing["affected"]["worker_resources"]["gw0"][field]
        with pytest.raises(ValueError, match="diagnostics"):
            build_test_scope_cost.build([missing], root=mini)
    for mutate in (
        lambda b: b["affected"].pop("worker_diagnostics_consistent"),
        lambda b: b["affected"]["worker_resources"].clear(),
        lambda b: b["affected"]["worker_resources"]["gw0"].update(tracer="CTracer"),
        lambda b: b["affected"]["worker_resources"]["gw0"].update(coverage_enabled=True),
    ):
        malformed = copy.deepcopy(untraced)
        mutate(malformed)
        with pytest.raises(ValueError, match="diagnostics|instrumentation|bodies"):
            build_test_scope_cost.build([malformed], root=mini)
    for field, value in (("cpu_affinity", 0), ("expected_workers", "2"), ("runner_label", None)):
        malformed = copy.deepcopy(untraced)
        malformed["affected"]["cost_environment"]["runtime"][field] = value
        malformed["affected"]["cost_context"] = hashlib.sha256(
            json.dumps(malformed["affected"]["cost_environment"], sort_keys=True).encode()
        ).hexdigest()
        with pytest.raises(ValueError, match="runtime/configuration|diagnostics"):
            build_test_scope_cost.build([malformed], root=mini)
    stale_attempt = copy.deepcopy(untraced)
    stale_attempt["receipts"]["unit-1"]["inventory_identity"]["attempt"] = "99"
    with pytest.raises(ValueError):
        build_test_scope_cost.build([stale_attempt], root=mini)
    mixed = copy.deepcopy(untraced)
    mixed["receipts"]["unit-1"] = receipts["unit-1"]
    with pytest.raises(ValueError, match="runtime/configuration"):
        build_test_scope_cost.build([mixed], root=mini)
    (mini / files[0]).write_text("def test_changed(): assert True\n")
    assert predict(mini, files)["reason"] == "COST_UNKNOWN"
