"""Owner-only provider allowance read surface (GET /api/settings/models/allowance).

Synthetic account keys and aliases only.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app as create_guarded_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from tests.api.auth_helpers import _DomainOwnerState, create_authenticated_domain_app

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
_RESET = datetime(2026, 10, 18, 14, 0, tzinfo=UTC)
_ID_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
_ID_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")


@pytest.fixture(scope="module")
def app():
    return create_authenticated_domain_app(api_key="owner-key")


def _guarded_app(monkeypatch, configured=True):
    monkeypatch.setenv("DASHBOARD_AUTH_ORIGIN", "https://owner.test.invalid")
    monkeypatch.setenv("DASHBOARD_AUTH_RP_ID", "owner.test.invalid")
    app = create_guarded_app(api_key="owner-key" if configured else "")
    if configured:
        app.state.owner_auth_service = _DomainOwnerState("owner-key")
    return app


def _db(app, *, rows=(), side_effect=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=list(rows), side_effect=side_effect)
    manager = MagicMock(spec=DatabaseManager)
    manager.credential_shared_pool.return_value = pool
    app.dependency_overrides[get_db] = lambda: manager
    return pool


def _row(account_key, state, reset_at, reset_source, entry_ids=(), aliases=()):
    return {
        "account_key": account_key,
        "state": state,
        "reset_at": reset_at,
        "reset_source": reset_source,
        "first_seen_at": _NOW,
        "updated_at": _NOW,
        "entry_ids": [str(i) for i in entry_ids],
        "entry_aliases": list(aliases),
    }


async def _get(app, headers, base_url="http://test"):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base_url
    ) as client:
        return await client.get("/api/settings/models/allowance", headers=headers)


@pytest.mark.parametrize(
    "configured,header,expected", [(False, None, 503), (True, None, 401), (True, "wrong", 401)]
)
async def test_allowance_owner_gate_precedes_the_read(
    monkeypatch: pytest.MonkeyPatch, configured: bool, header: str | None, expected: int
) -> None:
    app = _guarded_app(monkeypatch, configured)
    pool = _db(app)
    headers = {"X-API-Key": header} if header is not None else {}

    response = await _get(app, headers, "https://owner.test.invalid")

    assert response.status_code == expected
    pool.fetch.assert_not_awaited()


async def test_allowance_returns_each_account_with_its_catalog_entries(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    _db(
        app,
        rows=[
            _row("alpha-plan", "exhausted", _RESET, "parsed", [_ID_A, _ID_B], ["fast", "deep"]),
            _row("beta-plan", "available", None, "unknown"),
        ],
    )

    response = await _get(app, {"X-API-Key": "owner-key"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    alpha, beta = data["accounts"]
    assert alpha["account_key"] == "alpha-plan"
    assert alpha["state"] == "exhausted"
    assert alpha["reset_source"] == "parsed"
    assert alpha["reset_at"].startswith("2026-10-18T14:00")
    assert alpha["entries"] == [
        {"id": str(_ID_A), "alias": "fast"},
        {"id": str(_ID_B), "alias": "deep"},
    ]
    assert beta["state"] == "available"
    assert beta["entries"] == []
    assert set(alpha) == {
        "account_key",
        "state",
        "reset_at",
        "reset_source",
        "first_seen_at",
        "updated_at",
        "entries",
    }


async def test_allowance_distinguishes_no_rows_from_an_unreadable_table(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    pool = _db(app, rows=[])
    headers = {"X-API-Key": "owner-key"}

    empty = await _get(app, headers)
    pool.fetch.side_effect = asyncpg.UndefinedTableError("redacted")
    degraded = await _get(app, headers)

    assert empty.status_code == 200
    assert empty.json()["data"] == {"available": True, "accounts": []}
    assert degraded.status_code == 200
    assert degraded.json()["data"] == {"available": False, "accounts": []}


async def test_allowance_is_read_only(app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    pool = _db(app, rows=[_row("alpha-plan", "unknown", None, "unknown")])

    await _get(app, {"X-API-Key": "owner-key"})

    (sql,) = pool.fetch.await_args.args
    assert sql.lstrip().upper().startswith("SELECT")
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", sql.upper())
