"""Behavior tests for the Finance-owned, one-account SimpleFIN bridge."""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

_DOCKER_AVAILABLE = shutil.which("docker") is not None
_NOW = datetime(2026, 7, 28, 4, 17, tzinfo=UTC)
_ACCESS_URL_USER = "fixture-user"
_ACCESS_URL_PASSWORD = "fixture-password"
_ACCESS_URL = f"https://{_ACCESS_URL_USER}:{_ACCESS_URL_PASSWORD}@simplefin.invalid/simplefin"
_CONN_ID = "conn-fixture"
_REMOTE_ACCOUNT_ID = "account-fixture"


class _CredentialStore:
    """Small boundary fake that proves DB-only resolution semantics."""

    def __init__(self, value: str | None) -> None:
        self.value = value
        self.calls: list[tuple[str, bool]] = []

    async def resolve(self, key: str, *, env_fallback: bool = False) -> str | None:
        self.calls.append((key, env_fallback))
        return self.value


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the existing core + finance receiving schema once."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "finance"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return a clean, migration-faithful Finance pool for every test."""
    database_pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=4,
        init=register_jsonb_codec,
    )
    await database_pool.execute("TRUNCATE TABLE transactions, accounts CASCADE")
    yield database_pool
    await database_pool.close()


@pytest.fixture
async def single_connection_pool(migrated_db_url: str):
    """Return a migration-faithful pool that exposes nested-acquisition deadlocks."""
    database_pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=1,
        init=register_jsonb_codec,
    )
    await database_pool.execute("TRUNCATE TABLE transactions, accounts CASCADE")
    yield database_pool
    await database_pool.close()


async def _insert_bound_account(
    pool: asyncpg.Pool,
    *,
    last_synced_at: datetime | None = None,
    conn_id: str = _CONN_ID,
    account_id: str = _REMOTE_ACCOUNT_ID,
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO accounts (institution, type, name, currency, metadata, last_synced_at)
        VALUES (
            'Fixture Bank', 'checking', 'Fixture Checking', 'USD',
            $1::jsonb, $2
        )
        RETURNING *
        """,
        {
            "provider": {
                "name": "simplefin",
                "conn_id": conn_id,
                "account_id": account_id,
            }
        },
        last_synced_at,
    )
    assert row is not None
    return dict(row)


def _account_set(
    *,
    conn_id: str = _CONN_ID,
    account_id: str = _REMOTE_ACCOUNT_ID,
    transactions: list[dict[str, Any]] | None = None,
    errlist: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "errlist": [] if errlist is None else errlist,
        "connections": [{"conn_id": conn_id, "name": "Fixture Bank"}],
        "accounts": [
            {
                "id": account_id,
                "conn_id": conn_id,
                "name": "Fixture Checking",
                "currency": "USD",
                "balance": "42.00",
                "balance-date": int(_NOW.timestamp()),
                "transactions": transactions or [],
            }
        ],
    }


def _http_client(
    handler: Callable[[httpx.Request], Awaitable[httpx.Response]],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(2.0, connect=1.0),
    )


def test_simplefin_sync_entrypoint_is_available() -> None:
    """The Finance job has a dedicated deterministic entrypoint."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    assert callable(run_simplefin_sync)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_missing_access_url_skips_without_http_or_writes(pool: asyncpg.Pool) -> None:
    """An absent DB secret is honest and never reaches the network boundary."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    credential_store = _CredentialStore(None)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("missing configuration must not make HTTP requests")

    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=credential_store,
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "not_configured", "reason": "access_url_missing"}
    assert credential_store.calls == [("SIMPLEFIN_ACCESS_URL", False)]
    assert requests == []
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 0


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_malformed_access_url_skips_without_http_or_writes(pool: asyncpg.Pool) -> None:
    """A malformed access URL is rejected before the HTTP client is used."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    credential_store = _CredentialStore("not-a-url")
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("malformed configuration must not make HTTP requests")

    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=credential_store,
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "not_configured", "reason": "access_url_invalid"}
    assert requests == []
    assert "not-a-url" not in repr(result)
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 0


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_first_valid_response_creates_exact_provider_bound_account(
    pool: asyncpg.Pool,
) -> None:
    """The credential is enough to bootstrap one exact remote account safely."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    response = _account_set(
        transactions=[
            {
                "id": "bootstrap-fixture-1",
                "posted": int((_NOW - timedelta(hours=2)).timestamp()),
                "amount": "-7.89",
                "description": "Bootstrap Fixture",
            }
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )

    assert result == {
        "status": "ok",
        "recorded": 1,
        "skipped_pending": 0,
        "account_created": True,
    }
    account = await pool.fetchrow(
        """
        SELECT institution, type, name, currency, metadata, last_synced_at
        FROM accounts
        """
    )
    assert account is not None
    assert account["institution"] == "Fixture Bank"
    assert account["type"] == "other"
    assert account["name"] == "Fixture Checking"
    assert account["currency"] == "USD"
    assert account["metadata"] == {
        "provider": {
            "name": "simplefin",
            "conn_id": _CONN_ID,
            "account_id": _REMOTE_ACCOUNT_ID,
        }
    }
    assert account["last_synced_at"] == _NOW
    assert await pool.fetchval(
        "SELECT account_id FROM transactions WHERE external_id = 'bootstrap-fixture-1'"
    ) == await pool.fetchval("SELECT id FROM accounts")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_ambiguous_local_binding_skips_without_http(
    pool: asyncpg.Pool,
) -> None:
    """Multiple explicit provider bindings remain a no-request configuration error."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("an unbound or ambiguous account must not make HTTP requests")

    async with _http_client(handler) as client:
        await _insert_bound_account(pool)
        await _insert_bound_account(
            pool,
            conn_id="second-connection",
            account_id="second-account",
        )
        ambiguous = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )

    assert ambiguous == {"status": "not_configured", "reason": "account_binding_ambiguous"}
    assert requests == []
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 0


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_first_response_is_fully_validated_before_account_creation(
    pool: asyncpg.Pool,
) -> None:
    """Invalid discovery metadata cannot leave a partially provisioned account."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    response = _account_set()
    response["connections"] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "degraded", "reason": "invalid_response"}
    assert await pool.fetchval("SELECT COUNT(*) FROM accounts") == 0
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 0


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_upstream_403_is_sanitized_and_does_not_advance_freshness(
    pool: asyncpg.Pool,
) -> None:
    """Revocation is observable only through a sanitized failed request."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    account = await _insert_bound_account(pool)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(403, text="fixture provider details must not escape")

    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "degraded", "reason": "upstream_auth_failed"}
    assert len(requests) == 1
    assert "fixture-user" not in repr(result)
    assert "fixture-password" not in repr(result)
    assert "provider details" not in repr(result)
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 0
    assert (
        await pool.fetchval("SELECT last_synced_at FROM accounts WHERE id = $1", account["id"])
        is None
    )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_access_url_userinfo_is_not_passed_to_httpx_or_its_info_logs(
    pool: asyncpg.Pool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The credential-bearing Access URL becomes Basic auth, never request authority."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    await _insert_bound_account(pool)
    requests: list[httpx.Request] = []
    access_url = "https://fixture%40user:fixture%3Apassword@simplefin.invalid/simplefin"
    decoded_credentials = "fixture@user:fixture:password"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_account_set())

    caplog.set_level(logging.INFO, logger="httpx")
    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(access_url),
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "ok", "recorded": 0, "skipped_pending": 0}
    assert len(requests) == 1
    assert not requests[0].url.username
    assert not requests[0].url.password
    assert requests[0].headers["authorization"] == (
        "Basic " + base64.b64encode(decoded_credentials.encode()).decode()
    )
    httpx_log_messages = "\n".join(
        record.getMessage() for record in caplog.records if record.name == "httpx"
    )
    assert "fixture%40user" not in httpx_log_messages
    assert "fixture%3Apassword" not in httpx_log_messages
    assert "fixture@user" not in httpx_log_messages
    assert "fixture:password" not in httpx_log_messages


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_upstream_timeout_is_sanitized_and_does_not_advance_freshness(
    pool: asyncpg.Pool,
) -> None:
    """A transport timeout does not become a credential or provider-text disclosure."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    account = await _insert_bound_account(pool)

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fixture timeout details must not escape", request=request)

    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "degraded", "reason": "upstream_unavailable"}
    assert "fixture timeout details" not in repr(result)
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 0
    assert (
        await pool.fetchval("SELECT last_synced_at FROM accounts WHERE id = $1", account["id"])
        is None
    )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_complete_one_account_response_records_settled_transactions_with_provenance(
    pool: asyncpg.Pool,
) -> None:
    """The bridge uses provider IDs and skips pending/unposted data."""
    from butlers.tools.finance.transactions import record_transaction
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    assert "source" not in inspect.signature(record_transaction).parameters
    account = await _insert_bound_account(pool)
    requests: list[httpx.Request] = []
    response = _account_set(
        transactions=[
            {
                "id": "posted-fixture-1",
                "posted": int((_NOW - timedelta(hours=3)).timestamp()),
                "amount": "-12.34",
                "description": "Fixture Grocer",
            },
            {
                "id": "pending-fixture-1",
                "pending": True,
                "amount": "-1.00",
                "description": "Pending Fixture",
            },
            {
                "id": "unposted-fixture-1",
                "amount": "-2.00",
                "description": "Unposted Fixture",
            },
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=response)

    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "ok", "recorded": 1, "skipped_pending": 2}
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/simplefin/accounts")
    assert requests[0].url.params["version"] == "2"
    assert requests[0].url.params["start-date"] == str(int((_NOW - timedelta(days=90)).timestamp()))
    assert requests[0].url.params["end-date"] == str(int(_NOW.timestamp()))
    assert "pending" not in requests[0].url.params

    rows = await pool.fetch(
        "SELECT account_id, external_id, source, metadata FROM transactions ORDER BY posted_at"
    )
    assert len(rows) == 1
    assert rows[0]["account_id"] == account["id"]
    assert rows[0]["external_id"] == "posted-fixture-1"
    assert rows[0]["source"] == "aggregator"
    assert rows[0]["metadata"] == {
        "provider": {"name": "simplefin", "conn_id": _CONN_ID, "account_id": _REMOTE_ACCOUNT_ID}
    }
    assert (
        await pool.fetchval("SELECT last_synced_at FROM accounts WHERE id = $1", account["id"])
        == _NOW
    )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_provider_id_preserves_aggregator_provenance_after_email_collision(
    pool: asyncpg.Pool,
) -> None:
    """A new provider ID must not collapse into an equal email-ledger row."""
    from butlers.tools.finance.feed_reconciliation import reconcile_feed_vs_email
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    account = await _insert_bound_account(pool)
    sync_now = datetime.now(UTC).replace(microsecond=0)
    posted_at = sync_now - timedelta(hours=3)
    email_row = await pool.fetchrow(
        """
        INSERT INTO transactions (
            account_id, merchant, amount, currency, direction, category,
            posted_at, source, source_message_id
        ) VALUES ($1, 'Fixture Grocer', $2, 'USD', 'debit', 'uncategorized', $3, 'manual', $4)
        RETURNING id
        """,
        account["id"],
        Decimal("12.34"),
        posted_at,
        "fixture-email-receipt",
    )
    assert email_row is not None
    response = _account_set(
        transactions=[
            {
                "id": "provider-fixture-1",
                "posted": int(posted_at.timestamp()),
                "amount": "-12.34",
                "description": "Fixture Grocer",
            }
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=sync_now,
        )

    assert result == {"status": "ok", "recorded": 1, "skipped_pending": 0}
    rows = await pool.fetch(
        """
        SELECT source, source_message_id, external_id
        FROM transactions
        ORDER BY source, source_message_id NULLS FIRST
        """
    )
    assert len(rows) == 2
    assert [dict(row) for row in rows] == [
        {
            "source": "aggregator",
            "source_message_id": None,
            "external_id": "provider-fixture-1",
        },
        {
            "source": "manual",
            "source_message_id": "fixture-email-receipt",
            "external_id": None,
        },
    ]

    reconciliation = await reconcile_feed_vs_email(pool)
    assert reconciliation["configured"] is True
    assert reconciliation["feed_transactions_checked"] == 1
    assert len(reconciliation["matched"]) == 1
    assert reconciliation["unmatched_feed"] == []
    assert reconciliation["unmatched_email_count"] == 0


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_sync_records_on_a_migrated_single_connection_pool(
    single_connection_pool: asyncpg.Pool,
) -> None:
    """The held advisory-lock connection also performs the ledger write."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    account = await _insert_bound_account(single_connection_pool)
    response = _account_set(
        transactions=[
            {
                "id": "one-slot-fixture-1",
                "posted": int((_NOW - timedelta(hours=1)).timestamp()),
                "amount": "-8.76",
                "description": "One Slot Fixture",
            }
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    async with _http_client(handler) as client:
        result = await asyncio.wait_for(
            run_simplefin_sync(
                single_connection_pool,
                credential_store=_CredentialStore(_ACCESS_URL),
                http_client=client,
                now=_NOW,
            ),
            timeout=1,
        )

    assert result == {"status": "ok", "recorded": 1, "skipped_pending": 0}
    assert await single_connection_pool.fetchval("SELECT COUNT(*) FROM transactions") == 1
    assert (
        await single_connection_pool.fetchval(
            "SELECT account_id FROM transactions WHERE external_id = 'one-slot-fixture-1'"
        )
        == account["id"]
    )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_error_list_or_multiple_remote_accounts_prevents_all_writes(
    pool: asyncpg.Pool,
) -> None:
    """Any incomplete or ambiguous remote response fails before a ledger write."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    account = await _insert_bound_account(pool)
    incomplete = _account_set(
        transactions=[
            {
                "id": "would-write",
                "posted": int(_NOW.timestamp()),
                "amount": "-9.99",
                "description": "Must not persist",
            }
        ],
        errlist=[{"code": "act.missingdata", "msg": "raw text must stay private"}],
    )

    async def incomplete_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=incomplete)

    async with _http_client(incomplete_handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "degraded", "reason": "upstream_incomplete"}
    assert "raw text" not in repr(result)
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 0
    assert (
        await pool.fetchval("SELECT last_synced_at FROM accounts WHERE id = $1", account["id"])
        is None
    )

    multiple = _account_set()
    multiple["accounts"].append(
        {
            "id": "second-account",
            "conn_id": _CONN_ID,
            "name": "Unexpected",
            "currency": "USD",
            "balance": "0",
            "balance-date": int(_NOW.timestamp()),
            "transactions": [],
        }
    )

    async def multiple_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=multiple)

    async with _http_client(multiple_handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "degraded", "reason": "invalid_response"}
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 0


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_malformed_settled_transaction_prevents_earlier_valid_write(
    pool: asyncpg.Pool,
) -> None:
    """The complete payload is validated before its first transaction is recorded."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    account = await _insert_bound_account(pool)
    response = _account_set(
        transactions=[
            {
                "id": "would-be-valid",
                "posted": int(_NOW.timestamp()),
                "amount": "-9.99",
                "description": "Must not persist before validation finishes",
            },
            {
                "id": "malformed-after-valid",
                "posted": int(_NOW.timestamp()),
                "amount": "not-a-decimal",
                "description": "Malformed fixture",
            },
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "degraded", "reason": "invalid_response"}
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 0
    assert (
        await pool.fetchval("SELECT last_synced_at FROM accounts WHERE id = $1", account["id"])
        is None
    )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_storage_unrepresentable_settled_transaction_prevents_earlier_valid_write(
    pool: asyncpg.Pool,
) -> None:
    """All settled values must fit Finance storage before the first ledger write."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    account = await _insert_bound_account(pool)
    response = _account_set(
        transactions=[
            {
                "id": "would-be-valid",
                "posted": int(_NOW.timestamp()),
                "amount": "-9.99",
                "description": "Must not persist before validation finishes",
            },
            {
                "id": "too-large-for-numeric-14-2",
                "posted": int(_NOW.timestamp()),
                "amount": "9999999999999.99",
                "description": "Cannot fit the Finance amount column",
            },
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    async with _http_client(handler) as client:
        result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )

    assert result == {"status": "degraded", "reason": "invalid_response"}
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 0
    assert (
        await pool.fetchval("SELECT last_synced_at FROM accounts WHERE id = $1", account["id"])
        is None
    )


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_overlap_window_replays_by_external_id_without_duplicate(pool: asyncpg.Pool) -> None:
    """Five-day overlap retries converge on the existing provider-ID dedup key."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    previous_sync = _NOW - timedelta(days=2)
    account = await _insert_bound_account(pool, last_synced_at=previous_sync)
    response = _account_set(
        transactions=[
            {
                "id": "replayed-fixture-1",
                "posted": int((_NOW - timedelta(days=1)).timestamp()),
                "amount": "-4.56",
                "description": "Replay Fixture",
            }
        ]
    )
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=response)

    async with _http_client(handler) as client:
        first = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )
        second = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW + timedelta(hours=1),
        )

    assert first["status"] == "ok"
    assert second == {"status": "ok", "recorded": 0, "skipped_pending": 0}
    assert requests[0].url.params["start-date"] == str(
        int((previous_sync - timedelta(days=5)).timestamp())
    )
    assert await pool.fetchval("SELECT COUNT(*) FROM transactions") == 1
    assert await pool.fetchval("SELECT external_id FROM transactions") == "replayed-fixture-1"
    assert await pool.fetchval(
        "SELECT last_synced_at FROM accounts WHERE id = $1", account["id"]
    ) == (_NOW + timedelta(hours=1))


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
async def test_concurrent_sync_skips_before_second_http_request(pool: asyncpg.Pool) -> None:
    """The losing invocation leaves the winner's serialized fetch/write path alone."""
    from roster.finance.jobs.finance_jobs import run_simplefin_sync

    await _insert_bound_account(pool)
    first_request_started = asyncio.Event()
    release_first_request = asyncio.Event()
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        first_request_started.set()
        await release_first_request.wait()
        return httpx.Response(200, json=_account_set())

    async with _http_client(handler) as client:
        first_task = asyncio.create_task(
            run_simplefin_sync(
                pool,
                credential_store=_CredentialStore(_ACCESS_URL),
                http_client=client,
                now=_NOW,
            )
        )
        await first_request_started.wait()
        losing_result = await run_simplefin_sync(
            pool,
            credential_store=_CredentialStore(_ACCESS_URL),
            http_client=client,
            now=_NOW,
        )
        release_first_request.set()
        winning_result = await first_task

    assert losing_result == {"status": "skipped", "reason": "already_running"}
    assert winning_result == {"status": "ok", "recorded": 0, "skipped_pending": 0}
    assert len(requests) == 1


def test_simplefin_schedule_is_daily_off_the_hour_and_registered() -> None:
    """The TOML schedule and runtime registry expose the same internal job name."""
    import tomllib
    from pathlib import Path

    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    config_path = Path(__file__).parents[1] / "butler.toml"
    schedules = tomllib.loads(config_path.read_text())["butler"]["schedule"]
    schedule = next(item for item in schedules if item["name"] == "simplefin-sync")

    assert schedule == {
        "name": "simplefin-sync",
        "cron": "17 4 * * *",
        "dispatch_mode": "job",
        "job_name": "simplefin_sync",
    }
    assert "simplefin_sync" in get_deterministic_schedule_job_registry()["finance"]
