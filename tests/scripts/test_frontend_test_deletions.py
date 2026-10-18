"""Frontend test-case deletions are a loss unless this change maps a live successor (bu-f9tmbm)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "frontend_test_deletions", _SCRIPTS / "frontend_test_deletions.py"
)
fe = importlib.util.module_from_spec(_spec)
sys.modules["frontend_test_deletions"] = fe
_spec.loader.exec_module(fe)

SUITE = """\
import { describe, it, expect } from "vitest";

describe("Widget", () => {
  it("renders the title", () => {
    expect(1).toBe(1);
  });

  describe("when empty", () => {
    it("shows the empty state", () => {});
    it.each(["a", "b"])("handles %s", (v) => {});
  });

  it.skip("is skipped", () => {});
  // it("is commented out", () => {});
  it("matches /a(b/ in a string", () => {});
});
"""

PATH = "frontend/src/Widget.test.tsx"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path):
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    target = tmp_path / PATH
    target.parent.mkdir(parents=True)
    target.write_text(SUITE)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _write(root: Path, text: str) -> None:
    (root / PATH).write_text(text)


def _mapping(root: Path, name: str, deleted: dict) -> None:
    directory = root / fe.MAPPING_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps({"deleted": deleted}))


def test_case_ids_nest_describes_and_skip_comments_skips_and_regex_strings() -> None:
    ids = set(fe.case_ids(PATH, SUITE))
    assert ids == {
        f"{PATH}::Widget > renders the title",
        f"{PATH}::Widget > when empty > shows the empty state",
        f"{PATH}::Widget > when empty > handles %s",
        f"{PATH}::Widget > matches /a(b/ in a string",
    }


def test_unchanged_suite_passes(repo: Path) -> None:
    fe.check(repo, "HEAD")


def test_added_case_passes(repo: Path) -> None:
    _write(repo, SUITE.replace("});\n", '  it("new", () => {});\n});\n', 1))
    fe.check(repo, "HEAD")


def test_deleted_case_is_refused_without_a_mapping(repo: Path) -> None:
    _write(
        repo,
        SUITE.replace('  it("renders the title", () => {\n    expect(1).toBe(1);\n  });\n', ""),
    )
    with pytest.raises(fe.FrontendDeletionError, match="without-successor-mapping"):
        fe.check(repo, "HEAD")


@pytest.mark.parametrize(
    "weakened",
    [
        SUITE.replace('it("renders the title"', 'it.skip("renders the title"'),
        SUITE.replace('it("renders the title"', 'it.todo("renders the title"'),
        SUITE.replace('  it("renders the title"', '  // it("renders the title"'),
    ],
    ids=["skip", "todo", "commented-out"],
)
def test_weakening_a_case_is_a_loss(repo: Path, weakened: str) -> None:
    _write(repo, weakened)
    with pytest.raises(fe.FrontendDeletionError, match="without-successor-mapping"):
        fe.check(repo, "HEAD")


def test_deleting_the_whole_file_is_refused(repo: Path) -> None:
    (repo / PATH).unlink()
    with pytest.raises(fe.FrontendDeletionError, match="without-successor-mapping"):
        fe.check(repo, "HEAD")


def test_duplicate_title_deletion_is_counted(repo: Path) -> None:
    doubled = SUITE.replace(
        '  it("renders the title", () => {\n    expect(1).toBe(1);\n  });\n',
        '  it("renders the title", () => {});\n  it("renders the title", () => {});\n',
    )
    _write(repo, doubled)
    _git(repo, "commit", "-qam", "double")
    _write(repo, SUITE)
    with pytest.raises(fe.FrontendDeletionError, match="without-successor-mapping"):
        fe.check(repo, "HEAD")


def test_mapping_added_by_this_change_with_a_live_successor_passes(repo: Path) -> None:
    gone = f"{PATH}::Widget > renders the title"
    _write(
        repo,
        SUITE.replace('  it("renders the title", () => {\n    expect(1).toBe(1);\n  });\n', ""),
    )
    _mapping(
        repo,
        "x.json",
        {
            gone: {
                "successors": [f"{PATH}::Widget > when empty > shows the empty state"],
                "reason": "merged",
            }
        },
    )
    fe.check(repo, "HEAD")


def test_mapping_to_a_missing_successor_is_refused(repo: Path) -> None:
    gone = f"{PATH}::Widget > renders the title"
    _write(
        repo,
        SUITE.replace('  it("renders the title", () => {\n    expect(1).toBe(1);\n  });\n', ""),
    )
    _mapping(repo, "x.json", {gone: {"successors": [f"{PATH}::Widget > nope"], "reason": "r"}})
    with pytest.raises(fe.FrontendDeletionError, match="successor-not-live"):
        fe.check(repo, "HEAD")


def test_mapping_for_a_case_that_was_not_lost_is_refused(repo: Path) -> None:
    _mapping(
        repo,
        "x.json",
        {
            f"{PATH}::Widget > renders the title": {
                "successors": [f"{PATH}::Widget > matches /a(b/ in a string"],
                "reason": "r",
            }
        },
    )
    with pytest.raises(fe.FrontendDeletionError, match="not-lost"):
        fe.check(repo, "HEAD")


def test_a_mapping_already_on_the_base_excuses_nothing(repo: Path) -> None:
    gone = f"{PATH}::Widget > renders the title"
    _mapping(
        repo,
        "old.json",
        {gone: {"successors": [f"{PATH}::Widget > matches /a(b/ in a string"], "reason": "r"}},
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "old mapping")
    _write(
        repo,
        SUITE.replace('  it("renders the title", () => {\n    expect(1).toBe(1);\n  });\n', ""),
    )
    with pytest.raises(fe.FrontendDeletionError, match="without-successor-mapping"):
        fe.check(repo, "HEAD")


def test_a_malformed_mapping_is_refused(repo: Path) -> None:
    (repo / fe.MAPPING_DIRECTORY).mkdir(parents=True)
    (repo / fe.MAPPING_DIRECTORY / "bad.json").write_text('{"deleted": {"x": {"reason": ""}}}')
    with pytest.raises(fe.FrontendDeletionError, match="invalid-frontend-deletion-mapping"):
        fe.check(repo, "HEAD")


def test_ledger_entry_point_reports_the_refusal(repo: Path, capsys) -> None:
    """The real entry point refuses (exit 1) on a fixture repo that deletes a vitest case."""
    spec = importlib.util.spec_from_file_location(
        "check_condensation_ledger_fe", _SCRIPTS / "check_condensation_ledger.py"
    )
    ledger = importlib.util.module_from_spec(spec)
    sys.modules["check_condensation_ledger_fe"] = ledger
    spec.loader.exec_module(ledger)
    _write(
        repo,
        SUITE.replace('  it("renders the title", () => {\n    expect(1).toBe(1);\n  });\n', ""),
    )
    original = ledger.verify
    ledger.verify = lambda *a, **k: {"status": "PASS"}
    try:
        code = ledger.main(["--repo-root", str(repo), "--base", "HEAD"])
    finally:
        ledger.verify = original
    out = json.loads(capsys.readouterr().out)
    assert code == 1
    assert out == {
        "status": "REFUSED",
        "category": "frontend-test-deleted-without-successor-mapping",
    }
