"""Contract tests for the snapshot-only Bead detail endpoint.

These tests intentionally put sensitive-looking fields beside allowed Bead
fields. The API is safe only if it projects before serialization: a raw JSONL
record must never become a dashboard payload.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _write_export(path: Path, records: list[dict], *, mtime: datetime = _NOW) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))


def _bead(id_: str, **overrides: object) -> dict:
    return {
        "id": id_,
        "title": "Expose only the approved facts",
        "status": "open",
        "priority": 1,
        "issue_type": "task",
        "description": "Owner-visible scope.",
        "design": "A bounded reader projects before serialization.",
        "acceptance_criteria": "No raw snapshot fields leave the API.",
        "labels": ["decision", "privacy"],
        "created_at": _iso(_NOW - timedelta(days=2)),
        "updated_at": _iso(_NOW - timedelta(days=1)),
        "started_at": _iso(_NOW - timedelta(hours=12)),
        "closed_at": None,
        "due_at": _iso(_NOW + timedelta(days=1)),
        "dependencies": [],
        "external_ref": "TRACKER-123 (display only)",
        **overrides,
    }


async def _get_bead(app, export_path: Path, bead_id: str):
    """Drive the registered route with a real bounded reader and fixed clock."""
    from butlers.beads_snapshot import BeadSnapshotReader

    reader = BeadSnapshotReader(export_path=export_path, clock=lambda: _NOW)
    with patch("butlers.api.routers.beads._snapshot_reader", return_value=reader):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get(f"/api/beads/{bead_id}")


def _error_export_as_of(body: dict) -> str | None:
    return body["error"]["details"]["export_as_of"]


async def test_bead_detail_projects_only_the_explicit_allowlist(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _bead(
                "bu-safe",
                dependencies=[
                    {
                        "issue_id": "bu-safe",
                        "depends_on_id": "bu-dependency",
                        "type": "blocks",
                        "edge_note": "EDGE_SECRET_NEVER_EXPOSE",
                    }
                ],
                notes="NOTE_SECRET_NEVER_EXPOSE",
                metadata={"token": "METADATA_SECRET_NEVER_EXPOSE"},
                comments=[{"body": "COMMENT_SECRET_NEVER_EXPOSE"}],
                assignee="IDENTITY_NEVER_EXPOSE",
                credentials={"api_key": "CREDENTIAL_NEVER_EXPOSE"},
                url="https://external.invalid/RAW_HREF_NEVER_EXPOSE",
            ),
            _bead(
                "bu-dependency",
                title="Safe dependency summary",
                notes="DEPENDENCY_NOTE_NEVER_EXPOSE",
            ),
        ],
    )

    response = await _get_bead(app, export, "bu-safe")

    assert response.status_code == 200
    body = response.json()
    detail = body["data"]
    assert set(detail) == {
        "id",
        "title",
        "status",
        "priority",
        "type",
        "description",
        "design",
        "acceptance_criteria",
        "labels",
        "created_at",
        "updated_at",
        "started_at",
        "closed_at",
        "due_at",
        "dependencies",
        "external_ref",
    }
    assert detail["type"] == "task"
    assert detail["external_ref"] == "TRACKER-123 (display only)"
    assert detail["dependencies"] == [
        {
            "id": "bu-dependency",
            "title": "Safe dependency summary",
            "status": "open",
            "priority": 1,
            "type": "task",
        }
    ]
    assert body["meta"]["export_as_of"] == _iso(_NOW)

    rendered = response.text
    for sentinel in (
        "NOTE_SECRET_NEVER_EXPOSE",
        "METADATA_SECRET_NEVER_EXPOSE",
        "COMMENT_SECRET_NEVER_EXPOSE",
        "IDENTITY_NEVER_EXPOSE",
        "CREDENTIAL_NEVER_EXPOSE",
        "RAW_HREF_NEVER_EXPOSE",
        "EDGE_SECRET_NEVER_EXPOSE",
        "DEPENDENCY_NOTE_NEVER_EXPOSE",
    ):
        assert sentinel not in rendered
    assert '"href"' not in rendered


async def test_bead_detail_caps_direct_dependency_summaries_in_source_order(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    dependency_ids = [f"bu-dependency-{index}" for index in range(25)]
    _write_export(
        export,
        [
            _bead(
                "bu-parent",
                dependencies=[
                    {"issue_id": "bu-parent", "depends_on_id": dependency_id, "type": "blocks"}
                    for dependency_id in dependency_ids
                ],
            ),
            *[
                _bead(dependency_id, title=f"Dependency {index}")
                for index, dependency_id in enumerate(dependency_ids)
            ],
        ],
    )

    response = await _get_bead(app, export, "bu-parent")

    assert response.status_code == 200
    dependencies = response.json()["data"]["dependencies"]
    assert [dependency["id"] for dependency in dependencies] == dependency_ids[:20]
    assert all(
        set(dependency) == {"id", "title", "status", "priority", "type"}
        for dependency in dependencies
    )


async def test_bead_detail_exposes_only_timezone_aware_iso_timestamps(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(
        export,
        [
            _bead(
                "bu-timestamps",
                created_at="2026-08-11T12:00:00",
                updated_at="not-a-timestamp",
                started_at=42,
                closed_at="2026-08-12T12:00:00Z",
                due_at="2026-08-14T12:00:00+00:00",
            )
        ],
    )

    response = await _get_bead(app, export, "bu-timestamps")

    assert response.status_code == 200
    detail = response.json()["data"]
    assert detail["created_at"] is None
    assert detail["updated_at"] is None
    assert detail["started_at"] is None
    assert detail["closed_at"] == "2026-08-12T12:00:00Z"
    assert detail["due_at"] == "2026-08-14T12:00:00Z"


async def test_bead_detail_returns_404_only_after_a_fresh_readable_snapshot_lacks_id(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [_bead("bu-present")])

    response = await _get_bead(app, export, "bu-absent")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "BEAD_NOT_FOUND",
            "message": "Bead not found in the current snapshot.",
            "butler": None,
            "details": None,
        }
    }


@pytest.mark.parametrize(
    ("setup", "expected_export_as_of"),
    [
        ("missing", None),
        ("stale", _NOW - timedelta(days=15)),
        ("malformed", _NOW),
    ],
)
async def test_bead_detail_returns_honest_503_with_export_as_of_when_snapshot_is_unavailable(
    app, tmp_path, setup, expected_export_as_of
):
    export = tmp_path / "issues.export.jsonl"
    if setup == "stale":
        _write_export(export, [_bead("bu-present")], mtime=expected_export_as_of)
    elif setup == "malformed":
        export.write_text("{not valid json\n", encoding="utf-8")
        os.utime(export, (_NOW.timestamp(), _NOW.timestamp()))

    response = await _get_bead(app, export, "bu-present")

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "BEAD_SNAPSHOT_UNAVAILABLE"
    assert body["error"]["message"] == "Bead snapshot is unavailable."
    assert _error_export_as_of(body) == (
        _iso(expected_export_as_of) if expected_export_as_of is not None else None
    )
    assert "bu-present" not in body.get("data", {})


async def test_bead_detail_never_turns_a_malformed_snapshot_into_not_found(app, tmp_path):
    export = tmp_path / "issues.export.jsonl"
    _write_export(export, [_bead("bu-present")])
    with export.open("a", encoding="utf-8") as handle:
        handle.write("{invalid trailing source\n")
    os.utime(export, (_NOW.timestamp(), _NOW.timestamp()))

    response = await _get_bead(app, export, "bu-absent")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "BEAD_SNAPSHOT_UNAVAILABLE"


async def test_bead_detail_fails_closed_when_the_bounded_source_is_oversized(
    app, tmp_path, monkeypatch
):
    from butlers import beads_snapshot

    export = tmp_path / "issues.export.jsonl"
    monkeypatch.setattr(beads_snapshot, "MAX_EXPORT_BYTES", 64)
    export.write_bytes(b"{" + (b"x" * 64) + b"}")
    os.utime(export, (_NOW.timestamp(), _NOW.timestamp()))

    response = await _get_bead(app, export, "bu-absent")

    assert response.status_code == 503
    assert response.json()["error"]["details"] == {
        "reason": "export_oversized",
        "export_as_of": _iso(_NOW),
    }
