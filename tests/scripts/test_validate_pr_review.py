"""Regression tests for the Butler QA required-check completion gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / ".claude"
    / "skills"
    / "butlers-tooling"
    / "subskills"
    / "butler-qa-pr-review"
    / "scripts"
)
sys.path.insert(0, str(SKILL_SCRIPTS))
import validate_pr_review as validator  # noqa: E402

pytestmark = pytest.mark.unit


def _check(name: str, bucket: str) -> dict[str, str]:
    return {"name": name, "bucket": bucket}


def test_required_check_gate_accepts_a_passed_fan_in() -> None:
    missing, unsatisfied = validator._classify_required_checks(
        "tzeusy-org/butlers",
        [_check("alls-green", "pass"), _check("guards", "pass")],
    )

    assert missing == []
    assert unsatisfied == []


@pytest.mark.parametrize("bucket", ["fail", "pending", "cancel"])
def test_required_check_gate_rejects_unsatisfied_contexts(bucket: str) -> None:
    failing = _check("alls-green", bucket)
    missing, unsatisfied = validator._classify_required_checks(
        "tzeusy-org/butlers",
        [failing, _check("guards", "pass")],
    )

    assert missing == []
    assert unsatisfied == [failing]


@pytest.mark.parametrize("name", ["alls-green", "guards"])
def test_required_check_gate_rejects_any_skipped_check(name: str) -> None:
    skipped = _check(name, "skipping")
    checks = [_check("alls-green", "pass"), _check("guards", "pass")]
    checks[[check["name"] for check in checks].index(name)] = skipped

    missing, unsatisfied = validator._classify_required_checks("tzeusy-org/butlers", checks)

    assert missing == []
    assert unsatisfied == [skipped]


def test_required_check_gate_fails_closed_without_the_fan_in() -> None:
    missing, unsatisfied = validator._classify_required_checks(
        "Tzeusy/butlers",
        [_check("guards", "pass")],
    )

    assert missing == ["alls-green"]
    assert unsatisfied == []
