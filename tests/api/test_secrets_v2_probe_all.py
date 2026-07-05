"""Tests for POST /api/secrets/probe-all (bu-a63hn).

The endpoint itself is a thin HTTP wrapper around
``butlers.jobs.secrets_staleness.run_secrets_probe_all`` (lazily imported
inside the route to avoid a module-level import cycle — see that module's
docstring). These tests exercise the wrapper's HTTP contract: response shape,
aggregate counts, and the 429 mapping for ``ProbeAllAlreadyRunning`` — the
sweep engine itself (collection, dispatch, circuit breaker) is covered by
tests/jobs/test_secrets_staleness.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.secrets_v2 import _get_db_manager
from butlers.jobs.secrets_staleness import ProbeAllAlreadyRunning, ProbeOutcome

pytestmark = pytest.mark.unit


def _build_app() -> TestClient:
    app = create_app()
    mock_db = MagicMock(spec=DatabaseManager)
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return TestClient(app)


def test_probe_all_returns_aggregate_counts_and_per_row_results():
    client = _build_app()
    outcomes = [
        ProbeOutcome(key="s:KEY_A", family="system", label="KEY_A", ok=True),
        ProbeOutcome(key="u:google", family="user", label="google", ok=False, message="expired"),
        ProbeOutcome(
            key="c:cli-auth/codex",
            family="cli",
            label="cli-auth/codex",
            ok=None,
            skipped=True,
            skip_reason="rate_limited",
        ),
    ]

    with patch(
        "butlers.jobs.secrets_staleness.run_secrets_probe_all",
        new=AsyncMock(return_value=outcomes),
    ):
        response = client.post("/api/secrets/probe-all")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["probed"] == 2
    assert body["ok"] == 1
    assert body["failed"] == 1
    assert body["skipped"] == 1
    assert len(body["results"]) == 3
    by_key = {r["key"]: r for r in body["results"]}
    assert by_key["s:KEY_A"]["ok"] is True
    assert by_key["u:google"]["message"] == "expired"
    assert by_key["c:cli-auth/codex"]["skip_reason"] == "rate_limited"


def test_probe_all_returns_429_when_a_sweep_is_already_running():
    client = _build_app()

    with patch(
        "butlers.jobs.secrets_staleness.run_secrets_probe_all",
        new=AsyncMock(side_effect=ProbeAllAlreadyRunning()),
    ):
        response = client.post("/api/secrets/probe-all")

    assert response.status_code == 429


def test_probe_all_empty_sweep_returns_zeroed_counts():
    client = _build_app()

    with patch(
        "butlers.jobs.secrets_staleness.run_secrets_probe_all",
        new=AsyncMock(return_value=[]),
    ):
        response = client.post("/api/secrets/probe-all")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body == {"results": [], "probed": 0, "ok": 0, "failed": 0, "skipped": 0}
