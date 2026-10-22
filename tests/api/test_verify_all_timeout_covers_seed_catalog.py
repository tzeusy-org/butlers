"""The dashboard's verify-all timeout covers the seeded catalog's longest account group.

A sweep probes rows sharing ``COALESCE(allowance_account, runtime_type)`` one after another, so
it lasts as long as its longest group times the per-probe budget.  The frontend constants live
in ``frontend/src/api/client.ts``; this mirror check fails when a seeded group outgrows them
(bu-3rd8wy).
"""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from pathlib import Path

import pytest

from butlers.core.runtime_probe_control.client import (
    DEFAULT_READINESS_TIMEOUT_S,
    DEFAULT_REQUEST_TIMEOUT_S,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]


def _frontend_constant(name: str) -> int:
    source = (ROOT / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = ([\d_]+);", source)
    assert match, f"{name} not found in frontend/src/api/client.ts"
    return int(match.group(1).replace("_", ""))


def _largest_enabled_seed_group() -> tuple[str, int]:
    seeds = tomllib.loads((ROOT / "model_catalog_defaults.toml").read_text(encoding="utf-8"))
    groups = Counter(
        model.get("allowance_account") or model["runtime_type"]
        for model in seeds["models"]
        if model.get("enabled", True)
    )
    return groups.most_common(1)[0]


def test_serial_probe_allowance_covers_the_largest_enabled_seed_group() -> None:
    group, size = _largest_enabled_seed_group()

    assert _frontend_constant("VERIFY_ALL_MAX_SERIAL_PROBES") >= size, (
        f"seed group {group!r} has {size} enabled rows; raise VERIFY_ALL_MAX_SERIAL_PROBES"
    )


def test_probe_budget_covers_the_server_request_and_readiness_timeouts() -> None:
    server_ms = (DEFAULT_REQUEST_TIMEOUT_S + DEFAULT_READINESS_TIMEOUT_S) * 1000

    assert _frontend_constant("VERIFY_ALL_PROBE_BUDGET_MS") >= server_ms
