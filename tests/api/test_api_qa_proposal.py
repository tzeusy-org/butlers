"""The owner-facing QA proposal export route (bu-ejow5p.7.2)."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.core.qa.publication.export import ExportRefused
from butlers.core.qa.publication.retention import decode_objects
from butlers.core.qa.publication.state import PublicationRefused
from tests.api.auth_helpers import create_authenticated_domain_app as create_app
from tests.core.qa.publication_fakes import synthetic_export

pytestmark = pytest.mark.unit

_CASE = uuid.uuid4()
_DIFF = [
    {"kind": "meta", "text": "diff --git a/src/a.py b/src/a.py"},
    {"kind": "+", "text": "x = 1"},
    {"kind": "meta", "text": "diff --git a/docs/b.md b/docs/b.md"},
]


def _app(*, attempt: Any, evidence: Any = None):
    pool = MagicMock()

    async def fetchrow(sql: str, *_args: Any):
        if "FROM public.healing_attempts" in sql:
            return attempt
        return evidence

    pool.fetchrow = AsyncMock(side_effect=fetchrow)
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = pool
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return app


async def _get(app: Any, query: str = "") -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(f"/api/qa/cases/{_CASE}/proposal{query}")


def _attempt(**overrides: Any) -> dict[str, Any]:
    return {"branch_name": "qa/finance/abc123", "sealed_before_isolation": False, **overrides}


async def test_the_route_serves_the_verified_sealed_patch() -> None:
    export = synthetic_export()
    app = _app(
        attempt=_attempt(),
        evidence={"finding_structured_evidence": {"investigation_notes": {"diff_snapshot": _DIFF}}},
    )
    with patch("butlers.api.routers.qa.load_retained_export", new=AsyncMock(return_value=export)):
        metadata = (await _get(app)).json()["data"]
        response = await _get(app, "?bundle=true")

    assert metadata["bundle_b64"] is None and metadata["bundle_bytes"] >= 0
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["branch_name"] == "qa/finance/abc123"
    assert (data["base_sha"], data["head_sha"], data["digest"]) == (
        export.base_sha,
        export.head_sha,
        export.digest,
    )
    assert data["paths"] == ["src/a.py", "docs/b.md"]
    assert data["diff_truncated"] is False and data["sealed_before_isolation"] is False
    assert decode_objects(base64.b64decode(data["bundle_b64"])) == export.objects
    assert data["bundle_bytes"] == len(base64.b64decode(data["bundle_b64"]))


async def test_a_pre_isolation_export_is_labelled_and_truncation_is_reported() -> None:
    app = _app(
        attempt=_attempt(sealed_before_isolation=True),
        evidence={
            "finding_structured_evidence": json.dumps(
                {
                    "investigation_notes": {
                        "diff_snapshot": [*_DIFF, {"kind": "meta", "text": "... (diff truncated)"}]
                    }
                }
            )
        },
    )
    with patch(
        "butlers.api.routers.qa.load_retained_export",
        new=AsyncMock(return_value=synthetic_export()),
    ):
        data = (await _get(app)).json()["data"]

    assert data["sealed_before_isolation"] is True and data["diff_truncated"] is True


@pytest.mark.parametrize(
    ("loaded", "status"),
    [
        (None, 404),
        (PublicationRefused("export_pruned"), 410),
        (ExportRefused("export_tampered"), 409),
        (PublicationRefused("datastore_unavailable"), 503),
    ],
)
async def test_unavailable_exports_get_the_honest_status(loaded: Any, status: int) -> None:
    app = _app(attempt=_attempt())
    load = AsyncMock(return_value=loaded) if loaded is None else AsyncMock(side_effect=loaded)
    with patch("butlers.api.routers.qa.load_retained_export", new=load):
        response = await _get(app)

    assert response.status_code == status


async def test_an_unknown_case_is_not_found_and_never_touches_the_store() -> None:
    app = _app(attempt=None)
    load = AsyncMock()
    with patch("butlers.api.routers.qa.load_retained_export", new=load):
        response = await _get(app)

    assert response.status_code == 404
    load.assert_not_awaited()
