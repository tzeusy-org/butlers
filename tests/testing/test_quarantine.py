"""Contract tests for the flaky-test quarantine plugin (``tests/quarantine.py``).

The plugin is only worth having if three things hold: a listed test never runs
in a default pytest invocation, ``--quarantined`` runs exactly the listed tests,
and a registry entry cannot drift into naming nothing.  The subprocess tests
below run a real pytest session against a throwaway project, so they exercise
the hooks pytest actually calls rather than a hand-built imitation.
"""

from __future__ import annotations

import ast
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import quarantine

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_TESTS = """\
import pytest


def test_steady():
    pass


def test_flaky():
    assert False


@pytest.mark.parametrize("value", ["a", "b"])
def test_param(value):
    pass
"""


def _entry(node: str, bead: str = "bu-abc123", reason: str = "flakes") -> dict[str, str]:
    return {"node": node, "bead": bead, "reason": reason}


def _write_registry(root: Path, entries: list[dict[str, str]]) -> Path:
    path = root / quarantine.REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"note": "test", "tests": entries}))
    return path


def _sample_project(tmp_path: Path, entries: list[dict[str, str]]) -> Path:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_sample.py").write_text(SAMPLE_TESTS)
    _write_registry(tmp_path, entries)
    return tmp_path


def _run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run pytest in ``project`` with the plugin loaded the way addopts loads it."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.quarantine", "-p", "no:cacheprovider"]
        + ["-p", "no:xdist", "-rA", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _outcomes(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """Map node id to outcome from the ``-rA`` short summary."""
    outcomes = {}
    for line in result.stdout.splitlines():
        word, _, rest = line.partition(" ")
        if word in {"PASSED", "FAILED"}:
            outcomes[rest.split(" - ", 1)[0]] = word
    return outcomes


def test_default_run_deselects_listed_tests_including_every_parameter(tmp_path: Path) -> None:
    project = _sample_project(
        tmp_path, [_entry("test_sample.py::test_flaky"), _entry("test_sample.py::test_param")]
    )

    result = _run(project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _outcomes(result) == {"test_sample.py::test_steady": "PASSED"}
    assert "3 deselected" in result.stdout
    assert "quarantine: 2 test(s) listed in tests/quarantine.json are deselected" in result.stdout


def test_quarantined_option_runs_only_the_listed_tests(tmp_path: Path) -> None:
    project = _sample_project(
        tmp_path, [_entry("test_sample.py::test_flaky"), _entry("test_sample.py::test_param")]
    )

    result = _run(project, "--quarantined")

    assert result.returncode == 1, result.stdout + result.stderr
    assert _outcomes(result) == {
        "test_sample.py::test_flaky": "FAILED",
        "test_sample.py::test_param[a]": "PASSED",
        "test_sample.py::test_param[b]": "PASSED",
    }
    assert "1 deselected" in result.stdout


def test_malformed_registry_stops_the_run(tmp_path: Path) -> None:
    project = _sample_project(tmp_path, [_entry("test_sample.py::test_param[a]")])

    result = _run(project)

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "malformed entry" in result.stderr
    assert _outcomes(result) == {}


def test_missing_registry_quarantines_nothing(tmp_path: Path) -> None:
    assert quarantine.load_registry(tmp_path / quarantine.REGISTRY) == {}


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param({"tests": "test_sample.py::test_flaky"}, id="tests-not-a-list"),
        pytest.param([_entry("test_sample.py::test_flaky")], id="top-level-list"),
        pytest.param({"tests": [_entry("test_sample.py")]}, id="file-not-test"),
        pytest.param({"tests": [_entry("test_sample.py::test_param[a]")]}, id="parameter"),
        pytest.param({"tests": [_entry("test_sample.py::test_flaky", bead="")]}, id="no-bead"),
        pytest.param(
            {"tests": [_entry("test_sample.py::test_flaky", bead="JIRA-1")]}, id="foreign-bead"
        ),
        pytest.param({"tests": [_entry("test_sample.py::test_flaky", reason=" ")]}, id="no-reason"),
        pytest.param(
            {"tests": [{**_entry("test_sample.py::test_flaky"), "until": "never"}]},
            id="extra-key",
        ),
        pytest.param(
            {"tests": [_entry("test_sample.py::test_flaky"), _entry("test_sample.py::test_flaky")]},
            id="duplicate",
        ),
    ],
)
def test_malformed_registry_is_a_usage_error(tmp_path: Path, rows: object) -> None:
    path = tmp_path / "quarantine.json"
    path.write_text(json.dumps(rows))

    with pytest.raises(pytest.UsageError):
        quarantine.load_registry(path)


def _defined_tests(path: Path) -> set[str]:
    """Return ``name`` and ``Class::name`` for every test function defined in ``path``."""
    names = set()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    names.add(f"{node.name}::{member.name}")
    return {name for name in names if name.rsplit("::", 1)[-1].startswith("test")}


def test_every_live_registry_entry_names_an_existing_test() -> None:
    entries = quarantine.load_registry(REPO_ROOT / quarantine.REGISTRY)

    for node, entry in entries.items():
        file, _, name = node.partition("::")
        path = REPO_ROOT / file
        assert path.is_file(), f"{node} ({entry['bead']}): {file} does not exist"
        assert name in _defined_tests(path), f"{node} ({entry['bead']}): no such test"


def test_repo_pytest_config_deselects_the_live_registry() -> None:
    """The real addopts load the plugin, so a plain collection omits every listed test."""
    entries = quarantine.load_registry(REPO_ROOT / quarantine.REGISTRY)
    files = sorted({node.partition("::")[0] for node in entries})
    if not files:
        pytest.skip("tests/quarantine.json lists no tests")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-n", "0"]
        + ["-p", "no:cacheprovider", *files],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    collected = {line for line in result.stdout.splitlines() if "::" in line}
    assert collected, result.stdout
    assert not {node.split("[", 1)[0] for node in collected} & set(entries)


def _nightly_project(tmp_path: Path, entries: list[dict[str, str]]) -> Path:
    project = _sample_project(tmp_path, entries)
    (project / "test_sample.py").rename(project / "tests" / "test_sample.py")
    (project / "roster").mkdir()
    (project / "pytest.ini").write_text(
        "[pytest]\naddopts = -m 'not nightly and not bench and not perf'\n"
        "markers =\n    nightly: opt-in\n    bench: opt-in\n    perf: opt-in\n"
    )
    return project


def _run_nightly(
    project: Path, *, addopts: str = "", forced_exit: int | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute the actual nightly shell command, forwarding uv to real pytest."""
    workflow = (REPO_ROOT / ".github/workflows/nightly.yml").read_text()
    step = workflow.split("      - name: Quarantined tests\n", 1)[1]
    block = step.split("        run: |\n", 1)[1]
    # The run block ends at the first non-blank line indented less than its body.
    lines = itertools.takewhile(
        lambda line: not line.strip() or line.startswith("          "), block.splitlines()
    )
    command = "\n".join(line[10:] for line in lines if line.strip())
    binary_dir = project / ".bin"
    binary_dir.mkdir()
    binary = binary_dir / "uv"
    body = (
        "import os, sys\n"
        "os.execv(sys.executable, [sys.executable, '-m', 'pytest', '-p', "
        "'tests.quarantine', '-p', 'no:cacheprovider', *sys.argv[3:]])\n"
        if forced_exit is None
        else f"raise SystemExit({forced_exit})\n"
    )
    binary.write_text(f"#!{sys.executable}\n" + body)
    binary.chmod(0o755)
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(REPO_ROOT),
        PYTEST_ADDOPTS=addopts,
        PATH=str(binary_dir) + os.pathsep + env.get("PATH", ""),
    )
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", command],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    "case",
    [
        "stale",
        "partly-stale",
        "marker-filtered",
        "parameter-filtered",
        "complete",
        "empty",
        "failing-owner",
        "collection-error",
        "missing-registry",
    ],
)
def test_nightly_requires_complete_actual_quarantine_population(tmp_path: Path, case: str) -> None:
    nodes = {
        "stale": ["test_missing"],
        "partly-stale": ["test_steady", "test_missing"],
        "marker-filtered": ["test_steady"],
        "parameter-filtered": ["test_param"],
        "complete": ["test_steady", "test_param"],
        "empty": [],
        "failing-owner": ["test_flaky"],
        "collection-error": [],
        "missing-registry": [],
    }[case]
    project = _nightly_project(
        tmp_path, [_entry(f"tests/test_sample.py::{name}") for name in nodes]
    )
    if case == "marker-filtered":
        sample = project / "tests/test_sample.py"
        sample.write_text(
            sample.read_text().replace("def test_steady", "@pytest.mark.nightly\ndef test_steady")
        )
    elif case == "collection-error":
        (project / "tests/test_broken.py").write_text("def test_broken(:\n")
    elif case == "missing-registry":
        (project / quarantine.REGISTRY).unlink()

    result = _run_nightly(project, addopts="-k 'not b'" if case == "parameter-filtered" else "")

    expected_status = {"complete": 0, "empty": 0, "failing-owner": 1, "collection-error": 2}
    assert result.returncode == expected_status.get(case, 4), result.stdout + result.stderr
    # Verbose pytest reports the actual call outcomes and complete parameter identities.
    outcomes = {}
    for line in result.stdout.splitlines():
        words = line.split()
        if (
            len(words) >= 2
            and words[0].startswith("tests/test_sample.py::")
            and words[1] in {"PASSED", "FAILED"}
        ):
            outcomes[words[0]] = words[1]
    expected = {
        "complete": {
            "tests/test_sample.py::test_steady": "PASSED",
            "tests/test_sample.py::test_param[a]": "PASSED",
            "tests/test_sample.py::test_param[b]": "PASSED",
        },
        "failing-owner": {"tests/test_sample.py::test_flaky": "FAILED"},
    }
    assert outcomes == expected.get(case, {}), result.stdout + result.stderr
    if case == "empty":
        assert "validated empty" in result.stdout + result.stderr


@pytest.mark.parametrize("quarantined", [False, True], ids=["default", "diagnostic"])
def test_scoped_runs_allow_out_of_scope_registry_owners(tmp_path: Path, quarantined: bool) -> None:
    listed = "test_param" if quarantined else "test_flaky"
    project = _sample_project(
        tmp_path, [_entry(f"test_sample.py::{listed}"), _entry("missing.py::test_missing")]
    )
    args = ["test_sample.py::test_param[a]"]
    if quarantined:
        args.append("--quarantined")

    result = _run(project, *args)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _outcomes(result) == {"test_sample.py::test_param[a]": "PASSED"}
    if quarantined:
        full = _run(project, "test_sample.py::test_param[a]", "--quarantined-full")
        assert full.returncode == pytest.ExitCode.USAGE_ERROR, full.stdout + full.stderr


@pytest.mark.parametrize("status", [5, 42])
def test_nightly_command_propagates_unqualified_nonzero(tmp_path: Path, status: int) -> None:
    result = _run_nightly(tmp_path, forced_exit=status)

    assert result.returncode == status, result.stdout + result.stderr
