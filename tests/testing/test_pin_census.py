"""Executable definition and exclusions of the reconstructed census."""

import json
import subprocess
import sys

import pytest

from butlers.testing.pin_census import ADOPTED, census, summary

pytestmark = pytest.mark.unit


def test_helper_closure_nested_tests_and_exact_body_spans(tmp_path):
    path = tmp_path / "tests/test_sample.py"
    path.parent.mkdir()
    path.write_text(
        "from pathlib import Path\nP = Path('docs/contract.md')\n"
        "def helper():\n return P.read_text()\n"
        "class TestContract:\n def test_contract(self):\n  assert helper()\n"
        "def test_outer():\n def test_inner():\n  assert helper()\n assert helper()\n"
    )
    rows = census(tmp_path)
    assert [(r.owner, r.line, r.lines, r.kind, r.bucket) for r in rows] == [
        ("TestContract.test_contract", 6, 2, "doc", "IN"),
        ("test_outer", 8, 4, "doc", "IN"),
        ("test_outer.test_inner", 9, 2, "doc", "IN"),
    ]
    assert all("helper" in r.helpers and "P" in r.helpers for r in rows)


@pytest.mark.parametrize(
    "body",
    [
        "async def test_skip():\n assert path.read_text()",
        "def test_skip(tmp_path):\n assert path.read_text()",
        "def test_skip(pool):\n assert path.read_text()",
        "def test_skip(api_client):\n assert path.read_text()",
        "def test_skip():\n assert subprocess.run(['true']) and path.read_text()",
        "def helper():\n return tempfile.TemporaryDirectory()\n"
        "def test_skip():\n assert helper() and path.read_text()",
        "def test_skip():\n path.read_text()",
    ],
)
def test_excluded_populations_have_an_included_control(tmp_path, body):
    path = tmp_path / "tests/test_sample.py"
    path.parent.mkdir()
    path.write_text(body + "\ndef test_control():\n assert path.read_text()\n")
    assert [r.owner for r in census(tmp_path)] == ["test_control"]


def test_owner_exclusions_keep_full_and_adopted_denominators_separate(tmp_path):
    for filename in (
        "tests/test_in.py",
        "tests/migrations/test_sql.py",
        "tests/contracts/test_ci_node_version.py",
        "tests/config/test_init_db_roles.py",
        "tests/config/test_schema_standin_parity.py",
        "roster/general/tests/test_scan.py",
    ):
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_pin():\n assert path.read_text()\n")
    result = summary(census(tmp_path))
    assert result["full"] == {"cases": 6, "lines": 12}
    assert result["in_scope"] == {"cases": 2, "lines": 4}
    assert result["adopted"] == ADOPTED
    assert result["difference_from_adopted"]["in_scope"] == {"cases": -324, "lines": -5895}
    assert result["acceptance"].startswith("OPEN:")


def test_cli_json_and_tsv_are_reproducible_and_empty_scope_refuses(tmp_path):
    command = [sys.executable, "-m", "butlers.testing.pin_census", "--root", str(tmp_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and "empty Python source scope" in result.stderr
    path = tmp_path / "tests/test_one.py"
    path.parent.mkdir()
    path.write_text("def test_pin():\n assert path.read_text()\n")
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["full"] == {"cases": 1, "lines": 2}
    tsv = subprocess.run(command + ["--tsv"], capture_output=True, text=True, check=True).stdout
    assert tsv.splitlines()[0] == "path\tline\towner\tlines\tkind\tbucket\thelpers"
    assert (
        tsv
        == subprocess.run(command + ["--tsv"], capture_output=True, text=True, check=True).stdout
    )
