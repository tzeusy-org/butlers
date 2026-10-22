"""The pinned gitleaks/actionlint lint path fails closed and detects its planted faults.

The install and report-handling cases run everywhere. The detector case needs the real pinned
tools (``make install-lint-tools``); it skips without them, and CI's guards job proves the same
detectors on every run through ``make lint``'s ``lint-tools-self-test``.
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import re
import tarfile
from pathlib import Path
from types import ModuleType

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_lint_tools() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lint_tools", REPO_ROOT / "scripts/lint_tools.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lint_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load_lint_tools()
    monkeypatch.setenv(module.CACHE_ENV, str(tmp_path / "cache"))
    return module


def _tar_gz(*members: tuple[tarfile.TarInfo, bytes | None]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for info, data in members:
            if data is not None:
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def _file(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    return tarfile.TarInfo(name), data


def test_missing_mismatched_or_changed_install_fails_closed(
    lint_tools: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    pins = lint_tools.load_pins()
    with pytest.raises(lint_tools.ToolError, match="not installed"):
        lint_tools.installed_binary("actionlint", pins)
    # The commands report a named nonpass, never a clean scan or a skip.
    assert lint_tools.main(["lint-workflows"]) == 2
    assert lint_tools.main(["secrets-scan"]) == 2
    assert "make install-lint-tools" in capsys.readouterr().err

    key = lint_tools.platform_key()
    lint_tools.publish("actionlint", pins["actionlint"], key, b"#!/bin/sh\nexit 0\n")
    binary = lint_tools.installed_binary("actionlint", pins)

    repinned = copy.deepcopy(pins)
    repinned["actionlint"]["assets"][key]["sha256"] = "0" * 64
    with pytest.raises(lint_tools.ToolError, match="does not match its pin"):
        lint_tools.installed_binary("actionlint", repinned)

    binary.write_bytes(b"#!/bin/sh\nexit 1\n")
    with pytest.raises(lint_tools.ToolError, match="changed since its verified install"):
        lint_tools.installed_binary("actionlint", pins)


def test_install_refuses_unverified_or_unsafe_archives(lint_tools: ModuleType) -> None:
    good = _tar_gz(_file("actionlint", b"tool"), _file("LICENSE.txt", b"license"))
    assert lint_tools.extract_binary(good, "actionlint") == b"tool"
    with pytest.raises(lint_tools.ToolError, match="sha256 mismatch"):
        lint_tools.verify_digest(good, "0" * 64)

    link = tarfile.TarInfo("actionlint")
    link.type = tarfile.SYMTYPE
    link.linkname = "/bin/sh"
    unsafe = {
        "traversal": _tar_gz(_file("../actionlint", b"tool")),
        "nested": _tar_gz(_file("bin/actionlint", b"tool")),
        "symlink": _tar_gz((link, None)),
        "duplicate": _tar_gz(_file("actionlint", b"a"), _file("./actionlint", b"b")),
        "not a tarball": b"not a tarball",
    }
    for label, archive in unsafe.items():
        with pytest.raises(lint_tools.ToolError):
            lint_tools.extract_binary(archive, "actionlint")
            pytest.fail(f"accepted a {label} archive")


def test_secret_report_keeps_only_path_line_and_rule(
    lint_tools: ModuleType, tmp_path: Path
) -> None:
    report = tmp_path / "report.json"
    row = {
        "File": "config/app.env",
        "StartLine": 3,
        "RuleID": "generic-api-key",
        "Secret": "s3cr3t-value",
        "Match": "token = s3cr3t-value",
        "Author": "someone",
        "Email": "someone@example.invalid",
        "Entropy": 4.2,
    }
    report.write_text(json.dumps([row]), encoding="utf-8")
    assert lint_tools.parse_report(report) == [("config/app.env", 3, "generic-api-key")]

    for bad_rows in ([{**row, "File": "/etc/app.env"}], [{**row, "File": "../app.env"}], {}):
        report.write_text(json.dumps(bad_rows), encoding="utf-8")
        with pytest.raises(lint_tools.ToolError):
            lint_tools.parse_report(report)
    report.write_text("[{", encoding="utf-8")
    with pytest.raises(lint_tools.ToolError, match="missing or malformed"):
        lint_tools.parse_report(report)


def test_real_tools_detect_planted_faults_without_disclosing_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    lint_tools = _load_lint_tools()
    try:
        gitleaks = lint_tools.installed_binary("gitleaks")
        actionlint = lint_tools.installed_binary("actionlint")
    except lint_tools.ToolError as exc:
        pytest.skip(f"pinned lint tools not installed: {exc}")

    planted = tmp_path / "planted"
    (planted / "deploy").mkdir(parents=True)
    key_id = lint_tools.synthetic_access_key_id()
    (planted / "deploy" / "settings.ini").write_text(f"aws_access_key_id = {key_id}\n")
    findings = lint_tools.scan_directory(planted, gitleaks)
    assert findings == [("deploy/settings.ini", 1, "aws-access-token")]

    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "bad.yml").write_text(lint_tools.INVALID_WORKFLOW)
    lines = lint_tools.lint_files([Path(".github/workflows/bad.yml")], actionlint, tmp_path)
    assert lines and all(
        re.fullmatch(r"\.github/workflows/bad\.yml:\d+:\d+: [a-z-]+", line) for line in lines
    )

    assert lint_tools.self_test() == 0
    out = capsys.readouterr()
    assert "AKIA" not in out.out + out.err
    assert key_id not in out.out + out.err


def test_make_lint_is_the_ci_lint_step_and_runs_every_standard_tool() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = re.search(r"^lint:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert recipe is not None
    assert "uv run ruff check src/ tests/ roster/ scripts/" in recipe.group(1)
    for target in ("secrets-scan", "lint-workflows", "lint-tools-self-test"):
        assert target in recipe.group(1)

    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text())
    steps = [step.get("name") for step in workflow["jobs"]["guards"]["steps"]]
    lint = next(s for s in workflow["jobs"]["guards"]["steps"] if s.get("name") == "Lint")
    assert lint["run"] == "make lint"
    assert steps.index("Install dependencies") < steps.index("Install lint tools")
    assert steps.index("Install lint tools") < steps.index("Lint")


def test_every_secret_allowance_is_rule_scoped_to_exact_paths(
    lint_tools: ModuleType, tmp_path: Path
) -> None:
    # The reviewed config: every allowance names exact files, so the self-test can plant a
    # real-shaped value in each one and require gitleaks to catch it.
    paths = lint_tools.allowance_paths()
    assert ".github/actions/ci-shard-watchdog-capture/dist/index.js" in paths
    assert all((REPO_ROOT / path).is_file() for path in paths)

    config = tmp_path / "gitleaks.toml"
    widened = {
        "glob path": "condition = 'AND'\ntargetRules = ['generic-api-key']\npaths = ['^tests/.*$']",
        "unanchored": "condition = 'AND'\ntargetRules = ['generic-api-key']\npaths = ['tests/a\\.py']",
        "every rule": "condition = 'AND'\npaths = ['^tests/a\\.py$']",
        "or condition": "condition = 'OR'\ntargetRules = ['generic-api-key']\npaths = ['^tests/a\\.py$']",
        "no paths": "condition = 'AND'\ntargetRules = ['generic-api-key']\nregexes = ['x']",
    }
    for label, body in widened.items():
        config.write_text(f"[[allowlists]]\n{body}\n", encoding="utf-8")
        with pytest.raises(lint_tools.ToolError):
            lint_tools.allowance_paths(config)
            pytest.fail(f"accepted a {label} allowance")
