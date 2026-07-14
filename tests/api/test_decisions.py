"""Tests for GET /api/decisions (bu-ckkpz.2, Dashboard Decisions lane).

The endpoint is a thin read-only wrapper around
``butlers.jobs.decision_review.compute_decision_digest`` (bu-ckkpz.4) -- no
database pool involved, so these tests drive it purely through the beads
export JSONL fixture, mirroring ``tests/jobs/test_decision_review.py``'s own
fixture helpers.

Covers:
- Degraded envelope: a missing/stale/unreadable export never renders a
  fabricated all-clear -- `data: []` + `meta.decisions_available: false`.
- A genuine zero (export readable, no decision-marked beads) is a real
  all-clear: `data: []` + `meta.decisions_available: true`.
- Open decision beads serialize oldest-first with `age_hours` derived from
  `created_at`.
- A decision blocking a P1 bug/deploy for >48h carries the escalation fields;
  a non-escalated decision does not.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _write_export(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _decision(id_, *, title="DECISION REQUIRED (owner): pick one", created_days_ago=10, **kw):
    # bu-uo37y: the runtime title-marker fallback is retired, so a decision bead
    # is classified solely by the `decision` label. Default fixtures to a labeled
    # decision; pass labels=[] via **kw for an intentionally unlabeled bead.
    return {
        "id": id_,
        "title": title,
        "status": "open",
        "priority": 1,
        "issue_type": "task",
        "created_at": _iso(_NOW - timedelta(days=created_days_ago)),
        "dependencies": [],
        "labels": ["decision"],
        **kw,
    }


def _blocker(id_, *, title, issue_type, priority, blocks_id, edge_age_hours, status="open"):
    return {
        "id": id_,
        "title": title,
        "status": status,
        "priority": priority,
        "issue_type": issue_type,
        "created_at": _iso(_NOW - timedelta(days=30)),
        "dependencies": [
            {
                "issue_id": id_,
                "depends_on_id": blocks_id,
                "type": "blocks",
                "created_at": _iso(_NOW - timedelta(hours=edge_age_hours)),
            }
        ],
    }


async def _get_decisions(app, export_path: Path):
    """GET /api/decisions with the digest pinned to a fixture export + fixed clock.

    Patches the router's own ``compute_decision_digest`` binding (not the
    live-clock default) so the endpoint's decision detection / escalation
    logic is exercised for real -- only the export path and "now" are fixed.
    """
    from butlers.jobs.decision_review import compute_decision_digest as real_compute

    with patch("butlers.api.routers.decisions.compute_decision_digest") as mock_digest:
        mock_digest.side_effect = lambda: real_compute(export_path, now=_NOW)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get("/api/decisions")


# ---------------------------------------------------------------------------
# Degraded envelope -- never a fabricated all-clear
# ---------------------------------------------------------------------------


async def test_list_decisions_flags_unavailable_when_export_missing(app, tmp_path):
    resp = await _get_decisions(app, tmp_path / "does-not-exist.jsonl")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["decisions_available"] is False
    assert body["meta"]["unavailable_reason"] == "export_missing"
    # bu-hmdqz.6: no file was ever stat'd, so there is no known age to report.
    assert body["meta"].get("export_as_of") is None


async def test_list_decisions_genuine_zero_is_available(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [{"id": "bu-x", "title": "Ordinary task", "status": "open"}])

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["decisions_available"] is True
    # bu-hmdqz.6: meta.export_as_of carries the export file's own mtime so
    # the frontend can render an honest "as of" plaque.
    assert body["meta"].get("export_as_of") is not None


# ---------------------------------------------------------------------------
# Open decisions -- oldest first, age_hours derived
# ---------------------------------------------------------------------------


async def test_list_decisions_returns_oldest_first_with_age_hours(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-newer", title="DECISION REQUIRED (owner): newer", created_days_ago=2),
            _decision("bu-older", title="DECISION REQUIRED (owner): older", created_days_ago=10),
        ],
    )

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["decisions_available"] is True
    ids = [row["id"] for row in body["data"]]
    assert ids == ["bu-older", "bu-newer"]
    # 10 days ago -> 240h
    assert body["data"][0]["age_hours"] == pytest.approx(240, abs=1)
    assert body["data"][0]["escalated"] is False
    assert body["data"][0]["escalated_blocked_id"] is None


# ---------------------------------------------------------------------------
# Escalation fields
# ---------------------------------------------------------------------------


async def test_list_decisions_carries_escalation_fields_past_48h(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=10),
            _blocker(
                "bu-wzbu9",
                title="Silent message loss",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=72,
            ),
        ],
    )

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    body = resp.json()
    row = body["data"][0]
    assert row["id"] == "bu-v4ipc"
    assert row["escalated"] is True
    assert row["escalated_blocked_id"] == "bu-wzbu9"
    assert row["escalated_blocked_title"] == "Silent message loss"
    assert row["escalated_blocked_kind"] == "p1_bug"
    assert row["escalated_block_hours"] == pytest.approx(72, abs=1)


async def test_list_decisions_not_escalated_under_48h_threshold(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _decision("bu-v4ipc", created_days_ago=10),
            _blocker(
                "bu-wzbu9",
                title="Silent message loss",
                issue_type="bug",
                priority=1,
                blocks_id="bu-v4ipc",
                edge_age_hours=10,
            ),
        ],
    )

    resp = await _get_decisions(app, export)

    assert resp.status_code == 200
    row = resp.json()["data"][0]
    assert row["escalated"] is False
    assert row["escalated_blocked_id"] is None
