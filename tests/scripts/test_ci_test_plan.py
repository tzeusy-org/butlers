"""Contract tests for the PR affected-test lane's mode decision (bu-v28ho)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ci_route  # noqa: E402
import ci_test_plan  # noqa: E402

from butlers.testing import scope_cost  # noqa: E402
from butlers.testing.scoped_runner import ScopedTestPlan  # noqa: E402

pytestmark = pytest.mark.unit


def test_cost_admission_uses_entire_empirical_serial_cost_and_refuses_incompatible_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic decoder conformance is not calibration or a timing result."""
    for path in scope_cost.CONFIG:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# configuration fixture\n")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths=["tests","roster"]\n'
    )
    (tmp_path / "conftest.py").write_text("_DEFAULT_XDIST_AUTO_WORKERS = 3\n")
    (tmp_path / "roster").mkdir()
    path = "tests/test_cost_population.py"
    target = tmp_path / path
    target.parent.mkdir(exist_ok=True)
    target.write_text("def test_population(): assert True\n")
    context = {
        **scope_cost.environment(tmp_path),
        "runner_policy": "conformance-only",
        "image": "fixture",
        "cpu_models": ["fixture"],
        "cpu_count": 4,
        "affinity": [0, 1, 2, 3],
        "workers": 3,
        "instrumentation": "untraced",
    }
    profile = {
        "schema": "scope-cost.v1",
        "state": "measured",
        "context": context,
        "references": [
            {
                "head": "1" * 40,
                "run": 1,
                "attempt": 1,
                "artifact_sha256": "2" * 64,
                "species": species,
            }
            for species in ("full", "affected")
        ],
        "files": {
            path: {
                "sha256": scope_cost.file_digest(target),
                "serial_seconds": 8,
                "references": [0, 1],
            }
        },
        "setup_startup_finalization_seconds": 7,
        "heavy_shard_p90_seconds": 20,
    }
    destination = tmp_path / scope_cost.PROFILE

    def predict(body=profile, observed=context, paths=None, new_files=()):
        destination.write_text(json.dumps(body))
        return scope_cost.predict(tmp_path, paths or [path], observed=observed, new_files=new_files)

    healthy = predict()
    assert healthy["state"] == "measured" and healthy["predicted_seconds"] == 15
    assert healthy["ceiling_seconds"] == 20 and healthy["reason"] is None
    assert scope_cost.predict(tmp_path, [path])["reason"] == "COST_UNKNOWN"
    plan = replace(_plan("scoped", [path]), prediction=healthy, selected_files=(path,))
    assert ci_test_plan.decide_mode(plan, repo_root=tmp_path) == "scoped"
    child = subprocess.run(
        [sys.executable, "-m", "pytest", path, "-q", "-n", "0", "-o", "addopts="],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    configuration = (tmp_path / "pyproject.toml").read_text()
    (tmp_path / "pyproject.toml").write_text(configuration + "# changed source\n")
    assert scope_cost.predict(tmp_path, [path], observed=context)["reason"] == "COST_UNKNOWN"
    (tmp_path / "pyproject.toml").write_text(configuration)
    for field, value in (
        ("workers", 4),
        ("instrumentation", "SysMonitor"),
        ("cpu_count", 8),
        ("image", "different"),
    ):
        assert predict(observed={**context, field: value})["reason"] == "COST_UNKNOWN"
    import copy

    for mutation in ("missing", "boolean", "nonfinite", "reference", "traversal", "low-ceiling"):
        bad = copy.deepcopy(profile)
        if mutation == "missing":
            bad["files"] = {}
        elif mutation == "boolean":
            bad["files"][path]["serial_seconds"] = True
        elif mutation == "nonfinite":
            bad["setup_startup_finalization_seconds"] = float("inf")
        elif mutation == "reference":
            bad["files"][path]["references"] = [True]
        elif mutation == "traversal":
            bad["files"]["tests/../test_escape.py"] = bad["files"].pop(path)
        else:
            bad["heavy_shard_p90_seconds"] = 10
        prediction = predict(bad)
        assert prediction["reason"] == (
            "COST_EXCEEDED" if mutation == "low-ceiling" else "COST_UNKNOWN"
        )
        assert replace(plan, prediction=prediction).mode == "full"
    new = "tests/test_true_new.py"
    (tmp_path / new).write_text("def test_new(): assert True\n")
    assert predict(paths=[new])["reason"] == "COST_UNKNOWN"
    provisional = predict(paths=[new], new_files=(new,))
    assert provisional["state"] == "provisional" and provisional["predicted_seconds"] == 15
    target.write_text("def test_population(): assert False\n")
    assert predict()["reason"] == "COST_UNKNOWN"
    target.write_text("def test_population(): assert True\n")
    assert predict() == healthy
    # The source-owned builder consumes complete authenticated-carrier shapes;
    # these values are intentionally synthetic schema controls, never timings.
    import build_test_scope_cost as builder
    import ci_scope_measurement as measurement

    artifacts = []
    for index, species in enumerate(("full", "affected")):
        body = {
            "schema": "scope-measurement.v1",
            "species": species,
            "complete": True,
            "verified": True,
            "head": "1" * 40,
            "run": index + 1,
            "attempt": 1,
            "context": context,
            "configuration": context["configuration"],
            "elapsed_seconds": 30,
            "outside_phase_union_seconds": 7,
            "complete_reference_digest": "2" * 64,
            "selected_reference_digest": "3" * 64,
            "execution_digest": "4" * 64,
            "files": profile["files"],
        }
        artifact = tmp_path / (species + ".json")
        artifact.write_text(json.dumps(body))
        artifacts.append(artifact)
    heavy = {
        "schema": "ci-lanes-heavy-timings.v1",
        "complete": True,
        "selector": "ci_lanes.py/pytest-split/5+6",
        "measurement_jobs": {
            species: {
                "head": "1" * 40,
                "run": index + 1,
                "attempt": 1,
                "artifact_sha256": builder.load(artifacts[index])[1],
                "conclusion": "success",
                "elapsed_seconds": 40,
            }
            for index, species in enumerate(("full", "affected"))
        },
        "samples": [
            {
                "run": 3,
                "attempt": 1,
                "job": index * 10 + group,
                "lane": lane,
                "group": group,
                "conclusion": "success",
                "elapsed_seconds": 100,
            }
            for index, (lane, count) in enumerate((("unit", 5), ("integration", 6)))
            for group in range(1, count + 1)
        ],
    }
    heavy_path = tmp_path / "heavy.json"
    heavy_path.write_text(json.dumps(heavy))
    result = builder.candidate(*artifacts, heavy_path, root=tmp_path)
    assert result["setup_startup_finalization_seconds"] == 17  # Entire job, not just phases.
    assert result["heavy_shard_p90_seconds"] == 100
    for mutation in ("incomplete-groups", "duplicate-group", "no-job-setup", "missing-job"):
        corrupted = copy.deepcopy(heavy)
        if mutation == "incomplete-groups":
            corrupted["samples"].pop()
        elif mutation == "duplicate-group":
            corrupted["samples"].append(corrupted["samples"][0])
        elif mutation == "no-job-setup":
            corrupted["measurement_jobs"]["full"]["elapsed_seconds"] = 29
        else:
            del corrupted["measurement_jobs"]["affected"]
        heavy_path.write_text(json.dumps(corrupted))
        with pytest.raises(ValueError):
            builder.candidate(*artifacts, heavy_path, root=tmp_path)
    heavy_path.write_text(json.dumps(heavy))
    assert builder.candidate(*artifacts, heavy_path, root=tmp_path) == result
    phases = {
        "nodes": {
            "a": {"call": {"completed_s": 5, "duration_s": 4}},
            "b": {"call": {"completed_s": 7, "duration_s": 4}},
        }
    }
    assert measurement.phase_union_seconds(phases, elapsed=10) == 6
    # Actual miniature selected execution supplies worker/tracer observations;
    # resource/profile values remain explicitly synthetic decoder fixtures.
    import ci_affected as affected

    resources = {k: v for k, v in context.items() if k not in {"workers", "instrumentation"}}
    monkeypatch.setattr(scope_cost, "current_resources", lambda _root: resources)
    for name in (
        "PYTEST_ADDOPTS",
        "PYTEST_XDIST_AUTO_WORKERS",
        "COVERAGE_PROCESS_START",
        "PYTHONTRACEMALLOC",
        "GITHUB_SHA",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
    ):
        monkeypatch.delenv(name, raising=False)
    predict()  # Restore the valid, uninstalled conformance profile.
    planned = scope_cost.predict(tmp_path, [path], planning=True)
    assert planned["reason"] is None
    assert planned["context_provenance"]["kind"] == "resources-observed/execution-planned"
    with monkeypatch.context() as overridden:
        overridden.setenv("PYTEST_XDIST_AUTO_WORKERS", "4")
        assert scope_cost.predict(tmp_path, [path], planning=True)["reason"] == "COST_UNKNOWN"
    for args in (
        ("init", "-q"),
        ("config", "user.name", "Fixture"),
        ("config", "user.email", "fixture@example.invalid"),
        ("add", "."),
        ("commit", "-qm", "cost execution fixture"),
    ):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)
    output = tmp_path / "actual-worker-proof"
    actual = affected.execute([path], root=tmp_path, output=output, workers="3")
    assert actual["verified"]
    receipt = affected.read_json(output / "selected-execution.json")
    confirmed = affected.verify_prediction(planned, receipt, [path], root=tmp_path)
    assert confirmed["verified"] is True
    assert confirmed["actual_execution_context"]["workers"] == 3
    for mutation in ("worker-count", "tracer", "missing-resources", "expected-is-not-observed"):
        wrong = copy.deepcopy(receipt)
        quote = copy.deepcopy(planned)
        if mutation == "worker-count":
            worker = wrong["effective_workers"].pop()
            del wrong["worker_resources"][worker]
        elif mutation == "tracer":
            wrong["actual_tracers"] = ["CTracer"]
        elif mutation == "missing-resources":
            wrong["worker_resources"] = {}
        else:
            quote["context_provenance"] = {"kind": "execution-observed", "context": context}
        with pytest.raises(ValueError):
            affected.verify_prediction(quote, wrong, [path], root=tmp_path)
    assert affected.verify_prediction(planned, receipt, [path], root=tmp_path) == confirmed
    for value in (True, float("inf"), -1, 20):
        bad = copy.deepcopy(phases)
        bad["nodes"]["a"]["call"]["duration_s"] = value
        with pytest.raises(ValueError):
            measurement.phase_union_seconds(bad, elapsed=10)
    assert measurement.phase_union_seconds(phases, elapsed=10) == 6


def _plan(scope: str, test_paths: list[str] | None = None) -> ScopedTestPlan:
    # Conformance inputs only: these are not hosted timing/source evidence.
    return ScopedTestPlan(
        scope=scope,
        test_paths=test_paths or [],
        reason="fixture",
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


def test_decide_mode_keeps_a_clean_scoped_plan_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-ci-shard-assurance-004: actual route reuses only a conservative clean plan."""
    assert ci_test_plan.decide_mode(_plan("scoped", ["tests/api/test_foo.py"])) == "scoped"
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths=["tests","roster"]\n'
    )
    selected = tmp_path / "tests/test_selected.py"
    selected.write_text("def test_selected(): pass\n")
    calls = []

    def planned(*args, **kwargs):
        calls.append((args, kwargs))
        return replace(
            _plan("scoped", ["tests/test_selected.py"]), changed_files=["src/butlers/api/health.py"]
        )

    monkeypatch.setattr(ci_route, "plan_scoped_tests", planned)
    inputs = dict(
        event="pull_request",
        ref="refs/pull/1/merge",
        files=["src/butlers/api/health.py"],
        docs=[],
        base="fixed-base",
        root=tmp_path,
    )
    scoped = ci_route.route(**inputs)
    assert scoped["mode"] == "scoped"
    assert scoped["test_paths"] == ["tests/test_selected.py"]
    assert calls[0][0] == ("HEAD",)
    assert calls[0][1]["fallback_allowlist"][-1] == "tests/e2e/"
    e2e = tmp_path / "tests/e2e/test_performance.py"
    e2e.parent.mkdir()
    e2e.write_text("def test_e2e(): pass\n")
    for unsafe in (
        ["tests/e2e/test_performance.py"],
        ["tests/"],
        ["roster/"],
        ["tests/test_selected.py", "tests/test_selected.py"],
        ["unknown/test.py"],
        ["tests//test_selected.py"],
    ):
        monkeypatch.setattr(
            ci_route, "plan_scoped_tests", lambda *_a, unsafe=unsafe, **_k: _plan("scoped", unsafe)
        )
        refused = ci_route.route(**inputs)
        assert refused["mode"] == "full"
        assert refused["test_paths"] == []
    monkeypatch.setattr(ci_route, "plan_scoped_tests", planned)
    assert ci_route.route(**inputs)["mode"] == "scoped"
    selected.unlink()
    assert ci_route.route(**inputs)["mode"] == "full"
    for changed in (
        None,
        [],
        ["../escape.py"],
        ["tests/test_selected.py"],
        ["conftest.py"],
        ["uv.lock"],
        [".github/ci-test-weights.json"],
        ["src/butlers/core/db.py"],
    ):
        widened = ci_route.route(**{**inputs, "files": changed})
        assert widened["mode"] == "full"
        assert widened["test_paths"] == []
    assert ci_route.route(**{**inputs, "event": "merge_group", "files": None})["mode"] == "full"
    assert ci_route.route(**{**inputs, "event": "push", "ref": "refs/heads/main"})["mode"] == "push"
    docs = ci_route.route(**{**inputs, "files": ["docs/guide.md"], "docs": ["docs/guide.md"]})
    assert docs["mode"] == "full"  # A classifier label cannot suppress readers.
    monkeypatch.setattr(
        ci_route,
        "plan_scoped_tests",
        lambda *_a, **_k: replace(
            _plan("none"),
            changed_files=["docs/guide.md"],
        ),
    )
    docs = ci_route.route(**{**inputs, "files": ["docs/guide.md"], "docs": ["docs/guide.md"]})
    assert docs == dict(backend="false", frontend="false", mode="docs", test_paths=[])
    frontend = ci_route.route(**{**inputs, "files": ["frontend/src/App.tsx"]})
    assert frontend["mode"] == "full" and frontend["frontend"] == "true"
    with pytest.raises(ValueError):
        ci_route.route(**{**inputs, "event": "workflow_dispatch"})

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("ordinary planner unavailable")

    monkeypatch.setattr(ci_route, "plan_scoped_tests", unavailable)
    assert ci_route.route(**inputs)["mode"] == "full"
    for paths in (
        ["tests/api/"],
        ["roster/relationship/tests/"],
        ["./tests/api/test_foo.py"],
        ["tests/e2e_extra/test_foo.py"],
    ):
        assert ci_test_plan.decide_mode(_plan("scoped", list(paths))) == "scoped"


@pytest.mark.parametrize("scope", ["full", "none"])
def test_decide_mode_fails_closed_to_full_on_escalation_or_empty_plan(scope: str) -> None:
    assert ci_test_plan.decide_mode(_plan(scope)) == "full"
    # Selected scope, including a deleted-file ancestor, is the admission boundary.
    for paths in (
        ["tests/"],
        ["tests"],
        ["roster/"],
        ["tests/e2e/"],
        ["tests/e2e/test_foo.py"],
        [],
        ["unknown/test_foo.py"],
        ["/tests/api/test_foo.py"],
        ["tests/api/../e2e/test_foo.py"],
        ["."],
        ["tests_extra/test_foo.py"],
    ):
        assert ci_test_plan.decide_mode(_plan("scoped", list(paths))) == "full"


def test_ci_fallback_allowlist_widens_the_library_default_with_tests_e2e() -> None:
    assert "tests/e2e/" not in ci_test_plan.FULL_SUITE_FALLBACK_ALLOWLIST
    assert "tests/e2e/" in ci_test_plan.CI_FALLBACK_ALLOWLIST
    assert set(ci_test_plan.FULL_SUITE_FALLBACK_ALLOWLIST) < set(ci_test_plan.CI_FALLBACK_ALLOWLIST)


def test_write_github_output_is_a_noop_without_the_github_output_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_file = tmp_path / "github_output.txt"
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    ci_test_plan.write_github_output(mode="scoped", test_paths=["tests/api/test_foo.py"])

    assert not output_file.exists()


def test_write_github_output_writes_to_github_output_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_file = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    ci_test_plan.write_github_output(mode="scoped", test_paths=["tests/api/test_foo.py"])

    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "mode=scoped"
    assert json.loads(lines[1].removeprefix("test_paths=")) == ["tests/api/test_foo.py"]


def test_main_writes_full_mode_with_empty_test_paths_on_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output_file = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setattr(
        ci_test_plan,
        "plan_scoped_tests",
        lambda *_args, **_kwargs: _plan("full", ["tests/", "roster/"]),
    )

    assert ci_test_plan.main(["--base", "origin/main"]) == 0

    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "mode=full"
    assert json.loads(lines[1].removeprefix("test_paths=")) == []
    assert "[CI DECISION] mode=full" in capsys.readouterr().out
    output_file.unlink()
    monkeypatch.setattr(
        ci_test_plan, "plan_scoped_tests", lambda *_args, **_kwargs: _plan("scoped", ["tests/"])
    )
    assert ci_test_plan.main(["--base", "origin/main"]) == 0
    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "mode=full"
    assert json.loads(lines[1].removeprefix("test_paths=")) == []
    assert "[CI DECISION] mode=full" in capsys.readouterr().out


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_IMPORT_RE = re.compile(r"^\s*(?:from|import) tests\.", re.MULTILINE)


def test_scoped_roster_only_run_collects_roster_tests_that_import_tests(tmp_path: Path) -> None:
    """bu-3fdzpf: the check-affected lane runs ``uv run pytest <roster paths>``.

    Roster tests import shared helpers from ``tests.*``. Invoke the installed
    ``pytest`` console script, as ``uv run pytest`` does, so the working
    directory is not put on ``sys.path`` the way ``python -m pytest`` would.
    Without ``pythonpath = ["."]`` in the pytest config this fails collection
    with ``ModuleNotFoundError: No module named 'tests'``.
    """
    roster_files = sorted(
        str(path.relative_to(_REPO_ROOT))
        for path in (_REPO_ROOT / "roster").glob("*/tests/**/test_*.py")
        if _TESTS_IMPORT_RE.search(path.read_text(encoding="utf-8"))
    )
    assert roster_files, "expected roster tests that import tests.* helpers"
    pytest_script = Path(sys.executable).with_name("pytest")
    result = subprocess.run(
        [str(pytest_script), *roster_files, "--co", "-q", "-n", "0", "-p", "no:cacheprovider"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert " error" not in result.stdout.splitlines()[-1]
