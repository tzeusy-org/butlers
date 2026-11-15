"""The in-pod psql spool: a guard stop must not leave `kubectl exec -i` blocked on stdin.

Fed straight to `psql -f -`, a RAISE EXCEPTION early in a large program stops psql with the
rest unread, and `kubectl exec -i` then blocks forever writing it (observed on dev,
2026-11-15: the public MCP owner step hung for hours after its preflight refused). Both
superuser scripts therefore run psql through one shared spool that reads all of stdin first.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = ("scripts/k8s/bootstrap-db.sh", "scripts/k8s/public-mcp-owner-step.sh")
_ASSIGNMENT = re.compile(r"^PSQL_SPOOL='([^']*)'$", re.MULTILINE)


def _spool(script: str) -> str:
    matches = _ASSIGNMENT.findall((_REPO_ROOT / script).read_text(encoding="utf-8"))
    assert len(matches) == 1, f"{script} must define PSQL_SPOOL exactly once"
    return matches[0]


def test_both_superuser_scripts_share_one_spool() -> None:
    assert _spool(_SCRIPTS[0]) == _spool(_SCRIPTS[1])


def _fake_psql(tmp_path: Path, body: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    psql = bin_dir / "psql"
    psql.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    psql.chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "ARGS_LOG": str(tmp_path / "args.log")}


@pytest.mark.parametrize("script", _SCRIPTS)
def test_a_guard_stop_still_drains_stdin_and_keeps_the_exit_code(
    tmp_path: Path, script: str
) -> None:
    # psql stops at once, reading nothing; the writer must still finish and see exit 3.
    env = _fake_psql(tmp_path, 'echo "$*" > "$ARGS_LOG"\nexit 3\n')
    program = "SELECT 1;\n" * 400_000  # far beyond any pipe buffer
    result = subprocess.run(
        ["sh", "-c", _spool(script), "psql-spool", "-X", "-v", "ON_ERROR_STOP=1", "-d", "db"],
        input=program, capture_output=True, text=True, env=env, timeout=30,
    )  # fmt: skip
    assert result.returncode == 3
    args = (tmp_path / "args.log").read_text(encoding="utf-8").split()
    assert args[:5] == ["-X", "-v", "ON_ERROR_STOP=1", "-d", "db"]
    assert args[5] == "-f"
    assert not Path(args[6]).exists(), "the spooled program is removed afterwards"


def test_psql_reads_the_whole_program_from_the_spooled_file(tmp_path: Path) -> None:
    env = _fake_psql(tmp_path, 'f="${@: -1}"\ncp "$f" "$ARGS_LOG"\n')
    program = "SELECT 'a';\nSELECT 'b';\n"
    result = subprocess.run(
        ["sh", "-c", _spool(_SCRIPTS[1]), "psql-spool", "-d", "db"],
        input=program, capture_output=True, text=True, env=env, timeout=30,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "args.log").read_text(encoding="utf-8") == program
