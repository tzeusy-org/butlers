"""The CI smoke runner writes truthful release evidence (REQ-testing-034/051)."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ci_smoke  # noqa: E402

JUNIT = (
    "<testsuites><testsuite>"
    '<testcase classname="tests.smoke.test_a" name="test_ok"/>'
    '<testcase classname="tests.smoke.test_b" name="test_skip"><skipped/></testcase>'
    '<testcase name="test_unclassed"><skipped/></testcase>'
    "</testsuite></testsuites>"
)


def _fake_smoke(exit_code: int, *, junit: str | None = JUNIT) -> tuple[str, ...]:
    """A real child process that writes the JUnit path it is handed, then exits."""
    program = (
        "import sys\n"
        "path = next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--junit-xml='))\n"
        f"junit = {junit!r}\n"
        "if junit is not None:\n"
        "    open(path, 'w').write(junit)\n"
        "print('smoke child output')\n"
        f"raise SystemExit({exit_code})\n"
    )
    return (sys.executable, "-c", program)


@pytest.mark.parametrize(("exit_code", "status"), [(0, "PASS"), (1, "FAIL"), (5, "FAIL")])
def test_run_passes_the_exit_code_through_and_records_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], exit_code: int, status: str
) -> None:
    command = _fake_smoke(exit_code)
    output = tmp_path / "smoke evidence"

    assert ci_smoke.run(output, "abc123", command) == exit_code

    record = json.loads((output / "release-evidence.json").read_text())
    assert record == {
        "cmd": shlex.join(command),
        "sha": "abc123",
        "duration_s": record["duration_s"],
        "status": status,
        "exit_code": exit_code,
        "skipped_classes": ["test_unclassed", "tests.smoke.test_b"],
    }
    assert type(record["duration_s"]) is int and record["duration_s"] >= 0
    assert "smoke child output" in (output / "smoke.log").read_text()
    printed = capsys.readouterr().out
    assert "smoke child output" in printed
    assert f"SMOKE-RELEASE-EVIDENCE cmd={shlex.join(command)!r} sha='abc123'" in printed


@pytest.mark.parametrize("junit", [None, "<testsuites><unclosed>"])
def test_unreadable_junit_records_unavailable_skips(tmp_path: Path, junit: str | None) -> None:
    assert ci_smoke.run(tmp_path, "sha", _fake_smoke(1, junit=junit)) == 1

    record = json.loads((tmp_path / "release-evidence.json").read_text())
    assert record["skipped_classes"] == ["unavailable"]
    assert record["status"] == "FAIL"


def test_main_runs_the_smoke_tier_command_with_its_junit_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default command is the full smoke tier; only the JUnit path is appended."""
    receiver = tmp_path / "bin/uv"
    receiver.parent.mkdir()
    receipt = tmp_path / "argv.json"
    receiver.write_text(
        f"#!{sys.executable}\nimport json, sys\n"
        f"open({str(receipt)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
    )
    receiver.chmod(0o755)
    monkeypatch.setenv("PATH", f"{receiver.parent}:{Path(sys.executable).parent}")
    output = tmp_path / "smoke evidence"

    monkeypatch.setenv("GITHUB_SHA", "from-env")

    assert ci_smoke.main(["--output", str(output)]) == 0

    assert json.loads(receipt.read_text()) == [
        "run",
        "pytest",
        "tests/",
        "--ignore=tests/e2e",
        "-m",
        "smoke",
        "-q",
        "--tb=short",
        f"--junit-xml={output}/raw-junit.xml",
    ]
    record = json.loads((output / "release-evidence.json").read_text())
    assert record["cmd"] == "uv run pytest tests/ --ignore=tests/e2e -m smoke -q --tb=short"
    assert record["sha"] == "from-env"
    assert "--durations" not in record["cmd"]
    assert ci_smoke.main(["--output", str(output), "--sha", "deadbeef"]) == 0
    assert json.loads((output / "release-evidence.json").read_text())["sha"] == "deadbeef"
