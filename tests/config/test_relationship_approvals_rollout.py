"""Relationship roster config must support owner-gated relationship writes."""

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_relationship_enables_approvals_for_owner_carveout() -> None:
    """The relationship owner carve-out writes pending_actions via approvals migrations."""
    toml_path = REPO_ROOT / "roster" / "relationship" / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    modules = config.get("modules", {})
    assert "relationship" in modules
    assert "approvals" in modules, (
        "relationship_assert_fact owner carve-out writes pending_actions; "
        "relationship must enable [modules.approvals] so that table exists."
    )
