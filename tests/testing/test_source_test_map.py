"""Contract tests for the source-to-test planning map."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from butlers.testing.source_test_map import FULL_SUITE, resolve_test_paths

pytestmark = pytest.mark.unit


def _configured_testpaths() -> list[str]:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    return [f"{path.rstrip('/')}/" for path in config["tool"]["pytest"]["ini_options"]["testpaths"]]


def test_full_scope_matches_pytest_testpaths() -> None:
    assert FULL_SUITE == _configured_testpaths()


@pytest.mark.parametrize(
    ("changed_file", "expected"),
    [
        ("tests/api/test_scope.py", ["tests/api/test_scope.py"]),
        ("roster/health/tests/test_scope.py", ["roster/health/tests/test_scope.py"]),
        ("tests/api/conftest.py", ["tests/api/"]),
        ("roster/relationship/tests/conftest.py", ["roster/relationship/tests/"]),
        ("roster/conftest.py", ["roster/"]),
        ("tests/modules/memory/_test_helpers.py", ["tests/modules/memory/"]),
        ("roster/relationship/tests/evidence_schema.py", ["roster/relationship/tests/"]),
    ],
)
def test_direct_test_edits_remain_narrow(changed_file: str, expected: list[str]) -> None:
    assert resolve_test_paths([changed_file]) == expected


@pytest.mark.parametrize(
    "changed_file",
    [
        "conftest.py",
        "pyproject.toml",
        "uv.lock",
        "Makefile",
        "pricing.toml",
        "docker-compose.yml",
        "Dockerfile",
        "docker/Dockerfile.dev",
        ".github/workflows/ci.yml",
        "alembic/versions/core_999.py",
        "roster/health/migrations/007_health_scope.py",
        "src/butlers/modules/calendar/migrations/001_calendar_scope.py",
        "src/butlers/db.py",
        "src/butlers/testing/scoped_runner.py",
        "src/butlers/unknown_new_boundary.py",
        "unknown_root_tool.py",
    ],
)
def test_shared_migration_and_unknown_paths_escalate(changed_file: str) -> None:
    assert resolve_test_paths([changed_file]) == FULL_SUITE


def test_known_documentation_only_change_does_not_invent_pytest_scope() -> None:
    assert resolve_test_paths(["docs/testing/testing-strategy.md"]) == []


def test_fixture_asset_without_a_test_bearing_owner_escalates() -> None:
    fixture = "tests/fixtures/audit_result_guard/roster/switchboard/violating_audit_writer.py"
    assert resolve_test_paths([fixture]) == FULL_SUITE


@pytest.mark.parametrize("changed_file", [".github/workflows/ci.yml", "./.github/workflows/ci.yml"])
def test_dot_prefixed_ci_paths_escalate_without_losing_the_dot(changed_file: str) -> None:
    assert resolve_test_paths([changed_file]) == FULL_SUITE


def test_dot_prefixed_beads_export_remains_explicitly_non_testable() -> None:
    assert resolve_test_paths(["./.beads/issues.export.jsonl"]) == []
