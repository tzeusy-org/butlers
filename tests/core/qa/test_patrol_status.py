"""Regression coverage for the canonical QA patrol-status vocabulary."""

from __future__ import annotations

import importlib
import importlib.util

import pytest

pytestmark = pytest.mark.unit


def test_canonical_patrol_status_vocabulary_is_explicit_and_closed() -> None:
    """The writer and API share exactly the persisted patrol-status contract."""
    module_spec = importlib.util.find_spec("butlers.core.qa.patrol_status")
    assert module_spec is not None

    patrol_status = importlib.import_module("butlers.core.qa.patrol_status")
    expected = frozenset(
        {
            "running",
            "clean",
            "findings_dispatched",
            "error",
            "skipped_overlap",
            "suppressed",
        }
    )

    assert patrol_status.VALID_PATROL_STATUSES == expected
    assert all(patrol_status.is_valid_patrol_status(value) for value in expected)
    assert not patrol_status.is_valid_patrol_status("future_status")


def test_writer_status_guard_rejects_unknown_value() -> None:
    """A caller cannot persist a status outside the canonical vocabulary."""
    patrol_status = importlib.import_module("butlers.core.qa.patrol_status")

    with pytest.raises(ValueError, match="Unknown QA patrol status: future_status"):
        patrol_status.require_patrol_status("future_status")
