"""Regression tests for relationship owner carve-out approval storage."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from butlers.config import parse_approval_config
from butlers.modules.approvals.module import ApprovalsConfig

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_relationship_enables_approvals_for_owner_carveout() -> None:
    """relationship_assert_fact owner carve-outs require pending_actions storage."""
    path = REPO_ROOT / "roster" / "relationship" / "butler.toml"
    with path.open("rb") as fh:
        config = tomllib.load(fh)

    modules = config.get("modules", {})
    approvals = modules.get("approvals")

    assert isinstance(approvals, dict), (
        "relationship must enable [modules.approvals] so owner carve-outs can "
        "queue pending_actions instead of failing reconciler writes"
    )
    validated = ApprovalsConfig.model_validate(approvals)
    assert "actions" in validated.groups


def test_home_enables_approvals_for_physical_consequence_gate() -> None:
    """RFC 0028 cannot park protected HA calls with the gate disabled."""
    path = REPO_ROOT / "roster" / "home" / "butler.toml"
    with path.open("rb") as fh:
        config = tomllib.load(fh)

    approval_config = parse_approval_config(config["modules"]["approvals"])
    assert approval_config is not None
    assert approval_config.enabled is True
