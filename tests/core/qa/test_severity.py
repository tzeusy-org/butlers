"""Tests for QA severity label helpers."""

from __future__ import annotations

import pytest

from butlers.core.qa.severity import map_severity

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (0, "high"),
        (1, "high"),
        (2, "medium"),
        (3, "low"),
        (4, "low"),
    ],
)
def test_severity_map(severity: int, expected: str) -> None:
    assert map_severity(severity) == expected


def test_severity_map_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="Unknown QA severity"):
        map_severity(5)
