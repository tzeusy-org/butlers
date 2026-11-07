"""Every hosted Node setup pins one exact version, so sibling jobs share a runtime."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_setup_node_pins_one_exact_version():
    """REQ-ci-shard-assurance-007: both Vitest children run the same Node runtime.

    A floating ``node-version: "24"`` resolves to whatever 24.x the runner image
    caches. During an image rollout the two Vitest children can get different
    patches; each records its Node version in its full reference, so the
    required frontend reconcile refuses the run although every test passed.
    """
    versions = {}
    sources = sorted((ROOT / ".github/workflows").glob("*.yml"))
    sources += sorted((ROOT / ".github/actions").glob("*/action.yml"))
    for path in sources:
        source = yaml.safe_load(path.read_text())
        groups = source["jobs"].values() if "jobs" in source else [source.get("runs", {})]
        for job in groups:
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/setup-node@"):
                    version = str(step.get("with", {}).get("node-version", ""))
                    versions.setdefault(version, []).append(path.name)
    assert versions, "no setup-node step found"
    assert len(versions) == 1, f"setup-node versions differ: {versions}"
    [version] = versions
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"node-version {version!r} is not exact"
    assert len(versions[version]) == 4
