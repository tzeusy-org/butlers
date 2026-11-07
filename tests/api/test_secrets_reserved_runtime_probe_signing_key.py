"""The generic secrets surface does not own the runtime-probe signing key.

Covers REQ-core-credentials-002 (Asymmetric Runtime-Probe Control Capability):
``RUNTIME_PROBE_CONTROL_SIGNING_KEY`` is provisioned as a deployment secret
file, so the Secrets API must exclude it from the inventory, answer reads as if
it were absent, and refuse every mutation --- without ever emitting a value or
a value-derived fingerprint.

The synthetic value below stands in for whatever a stray row might hold; the
assertions check that it never leaves the API, never that it does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.api.routers.secrets_v2 import _fetch_system_secrets, _system_probe_timestamps
from butlers.core.runtime_probe_control.keys import RESERVED_SIGNING_KEY_SECRET_NAME
from butlers.testing.fake_pool import FakePool, fake_pool
from butlers.testing.records import Record, row
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_RESERVED = RESERVED_SIGNING_KEY_SECRET_NAME
_ORDINARY = "MY_API_KEY"
# Obviously synthetic stand-in for a stray row's value.
_PLANTED_VALUE = "synthetic-planted-value-not-a-key"


def _secrets_row(secret_key: str) -> Record:
    return row(
        secret_key=secret_key,
        secret_value=_PLANTED_VALUE,
        category="general",
        description=None,
        is_sensitive=True,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at=None,
        last_verified=_NOW,
        last_test_ok=True,
        last_test_code=None,
        last_test_message=None,
    )


def _inventory_pool(rows: list[Record]) -> FakePool:
    """butler_secrets scan, then empty probe-log and audit bulk reads."""
    return fake_pool(fetch=AsyncMock(side_effect=[rows, [], []]))


def _client(pool: FakePool) -> TestClient:
    db = MagicMock(spec=DatabaseManager)
    db.butler_names = ["switchboard"]
    db.pool = MagicMock(return_value=pool)
    db.credential_shared_pool = MagicMock(return_value=pool)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture
def client_and_pool() -> tuple[TestClient, FakePool]:
    """A pool with no configured calls: any read or write of the key fails the test."""
    pool = fake_pool()
    return _client(pool), pool


@pytest.fixture(autouse=True)
def _clear_probe_rate_limit():
    _system_probe_timestamps.clear()
    yield
    _system_probe_timestamps.clear()


# ---------------------------------------------------------------------------
# Reads behave as if the row were absent
# ---------------------------------------------------------------------------


async def test_inventory_scan_drops_a_planted_reserved_row() -> None:
    """A stray row must not reach the inventory, fingerprint and all."""
    pool = _inventory_pool([_secrets_row(_RESERVED), _secrets_row(_ORDINARY)])

    secrets = await _fetch_system_secrets(pool, "switchboard")

    assert [secret.key for secret in secrets] == [_ORDINARY]
    assert all(_RESERVED not in repr(secret) for secret in secrets)


async def test_inventory_scan_drops_a_differently_cased_reserved_row() -> None:
    """Case is not a way back onto the surface."""
    pool = _inventory_pool([_secrets_row(_RESERVED.lower()), _secrets_row(_ORDINARY)])

    secrets = await _fetch_system_secrets(pool, "switchboard")

    assert [secret.key for secret in secrets] == [_ORDINARY]


def test_detail_read_is_indistinguishable_from_absent(client_and_pool) -> None:
    client, pool = client_and_pool

    response = client.get(f"/api/secrets/system/{_RESERVED}")

    assert response.status_code == 404
    pool.fetchrow.assert_not_awaited()
    body = response.text
    assert _PLANTED_VALUE not in body
    assert "fingerprint" not in body


def test_ordinary_keys_are_unaffected() -> None:
    """The exclusion is narrow: it must not shadow neighbouring credentials."""
    client = _client(fake_pool(fetchrow=AsyncMock(side_effect=[_secrets_row(_ORDINARY), None])))

    response = client.get(f"/api/secrets/system/{_ORDINARY}")

    assert response.status_code == 200
    assert response.json()["data"]["key"] == _ORDINARY


def test_audit_history_reports_no_events(client_and_pool) -> None:
    client, pool = client_and_pool

    response = client.get(f"/api/secrets/audit/system/{_RESERVED}")

    assert response.status_code == 200
    assert response.json()["data"] == []
    assert pool.fetch.await_count == 0


# ---------------------------------------------------------------------------
# Mutations are refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", [_RESERVED, _RESERVED.lower()])
def test_set_is_refused_without_writing(client_and_pool, key: str) -> None:
    client, pool = client_and_pool

    response = client.post(
        f"/api/secrets/system/{key}",
        json={"value": "synthetic-attempted-write", "target": "shared"},
    )

    assert response.status_code == 403
    assert pool.execute.await_count == 0
    assert "synthetic-attempted-write" not in response.text


def test_delete_is_refused_without_writing(client_and_pool) -> None:
    client, pool = client_and_pool

    response = client.delete(f"/api/secrets/system/{_RESERVED}?target=shared")

    assert response.status_code == 403
    assert pool.execute.await_count == 0


def test_probe_is_refused_without_writing(client_and_pool) -> None:
    """A probe writes a probe-log row and an audit row; neither may happen."""
    client, pool = client_and_pool

    response = client.post(f"/api/secrets/system/{_RESERVED}/probe")

    assert response.status_code == 403
    assert pool.execute.await_count == 0
    assert _PLANTED_VALUE not in response.text


def test_refusal_names_the_key_but_discloses_nothing_about_it(client_and_pool) -> None:
    """The reserved name is public; the material behind it never appears."""
    client, _ = client_and_pool

    response = client.delete(f"/api/secrets/system/{_RESERVED}?target=shared")

    assert _RESERVED in response.text
    assert _PLANTED_VALUE not in response.text
    assert "fingerprint" not in response.text
