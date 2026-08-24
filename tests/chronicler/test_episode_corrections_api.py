"""Attribution contract for POST /api/chronicler/episodes/{id}/corrections.

``overrides.submitted_by`` and the audit entry that accompanies a correction
name who submitted it.  A caller cannot be allowed to choose that value: an
actor a request supplies is not attribution, it is a claim.  The route derives
it from :func:`~butlers.api.audit_emit.authenticated_principal` and drops any
``submitted_by`` the caller sends (ignored, not 422-rejected, so older clients
keep working) — bu-6zlqt, following bu-4y9ck.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.audit_emit import authenticated_principal
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

#: A value no server-derived principal can ever equal, so an assertion against
#: it cannot pass by coincidence with the real principal.
_FORGED_ACTOR = "attacker-not-the-owner"

_EPISODE_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
_OVERRIDE_ID = uuid.UUID("99999999-8888-7777-6666-555555555555")


def _load_chronicler_router():
    module_name = "chronicler_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _override_row(submitted_by: str) -> dict[str, Any]:
    """The row the INSERT … RETURNING * hands back."""
    return {
        "id": _OVERRIDE_ID,
        "target_kind": "episode",
        "target_id": _EPISODE_ID,
        "corrected_start_at": None,
        "corrected_end_at": None,
        "corrected_title": "Corrected title",
        "corrected_privacy": None,
        "corrected_tombstone_at": None,
        "note": None,
        "submitted_by": submitted_by,
        "created_at": datetime(2026, 8, 24, tzinfo=UTC),
    }


def _make_app(pool: AsyncMock):
    chronicler_mod = _load_chronicler_router()
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    return app


async def test_submit_correction_ignores_caller_supplied_submitted_by():
    """A forged ``submitted_by`` reaches neither the override row nor the audit."""
    row = _override_row(authenticated_principal())
    record = MagicMock()
    record.__getitem__ = MagicMock(side_effect=lambda key: row[key])

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)  # episode exists
    pool.fetchrow = AsyncMock(return_value=record)

    app = _make_app(pool)
    chronicler_mod = _load_chronicler_router()

    with patch.object(chronicler_mod, "emit_dashboard_audit", new_callable=AsyncMock) as mock_audit:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/chronicler/episodes/{_EPISODE_ID}/corrections",
                json={"corrected_title": "Corrected title", "submitted_by": _FORGED_ACTOR},
            )

    # Backward compatibility: the now-ignored field is dropped, never rejected.
    assert resp.status_code == 201, resp.text

    # Persisted row: INSERT binds submitted_by last ($8).
    persisted_submitted_by = pool.fetchrow.await_args.args[8]
    assert persisted_submitted_by == authenticated_principal()
    assert persisted_submitted_by != _FORGED_ACTOR

    # Audit entry: the explicit emit records the same server-derived principal.
    mock_audit.assert_awaited_once()
    audited_body = mock_audit.await_args.kwargs["body"]
    assert audited_body["submitted_by"] == authenticated_principal()

    # Echoed response carries the server's value, not the caller's.
    assert resp.json()["submitted_by"] == authenticated_principal()
