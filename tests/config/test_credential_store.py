"""Unit tests for butlers.credential_store.CredentialStore.

All tests mock the asyncpg pool — no real database required.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.credential_store import (
    CredentialStore,
    EntityInfoRow,
    SecretMetadata,
    _ensure_utc,
    _is_missing_table_error,
    create_system_global_credential_store_from_env,
    shared_db_name_from_env,
)
from butlers.testing.records import row as _make_row

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_pool(
    *,
    fetchrow_return=None,
    fetch_return=None,
    execute_return: str = "DELETE 0",
) -> MagicMock:
    conn = AsyncMock()
    conn.fetchrow.return_value = fetchrow_return
    conn.fetch.return_value = fetch_return or []
    conn.execute.return_value = execute_return
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = cm
    pool._conn = conn
    return pool


# ---------------------------------------------------------------------------
# store / load / has / delete
# ---------------------------------------------------------------------------


async def test_store_validates_and_upserts(caplog: pytest.LogCaptureFixture) -> None:
    """store() upserts correctly; rejects empty keys/values; doesn't log secret values."""
    pool = _make_pool(execute_return="INSERT 0 1")
    store = CredentialStore(pool)
    await store.store("MY_KEY", "my-value")
    sql, *args = pool._conn.execute.call_args[0]
    assert "INSERT INTO" in sql and "butler_secrets" in sql and "ON CONFLICT" in sql
    assert args[0] == "MY_KEY" and args[1] == "my-value"

    # Category default and custom
    await store.store("KEY", "value")
    assert pool._conn.execute.call_args[0][3] == "general"
    await store.store("KEY", "value", category="telegram")
    assert pool._conn.execute.call_args[0][3] == "telegram"

    # Rejects empty / whitespace
    for key, value in [("", "val"), ("   ", "val"), ("KEY", "")]:
        with pytest.raises(ValueError):
            await store.store(key, value)

    # Secret value not logged
    with caplog.at_level("DEBUG"):
        await store.store("K", "SUPER_SECRET_XYZ")
    assert not any("SUPER_SECRET_XYZ" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "expected_value,execute_return,expected_sql_fragment",
    [
        ("old-value", "UPDATE 1", "UPDATE butler_secrets"),
        (None, "INSERT 0 1", "INSERT INTO butler_secrets"),
    ],
    ids=["existing-matches", "missing-row"],
)
async def test_store_shared_if_unchanged_uses_authoritative_compare_and_set(
    expected_value: str | None,
    execute_return: str,
    expected_sql_fragment: str,
) -> None:
    """Conditional shared writes prevent a stale runtime from overwriting a dashboard refresh."""
    local_pool = _make_pool()
    shared_pool = _make_pool(execute_return=execute_return)
    store = CredentialStore(local_pool, fallback_pools=[shared_pool])

    stored = await store.store_shared_if_unchanged(
        "cli-auth/codex",
        "new-value",
        expected_value=expected_value,
        category="cli-auth",
    )

    assert stored is True
    shared_sql, *shared_args = shared_pool._conn.execute.call_args[0]
    assert expected_sql_fragment in shared_sql
    assert shared_args[0] == "cli-auth/codex"
    assert shared_args[1] == "new-value"
    local_pool._conn.execute.assert_not_awaited()


async def test_store_shared_if_unchanged_reports_compare_and_set_conflict() -> None:
    """A zero-row conditional update leaves the newer shared credential untouched."""
    shared_pool = _make_pool(execute_return="UPDATE 0")
    store = CredentialStore(_make_pool(), fallback_pools=[shared_pool])

    stored = await store.store_shared_if_unchanged(
        "cli-auth/codex",
        "stale-runtime-value",
        expected_value="old-value",
        category="cli-auth",
    )

    assert stored is False


async def test_dashboard_value_replacement_clears_prior_runtime_health_atomically() -> None:
    """A health-first A update cannot make a later dashboard B look tested.

    The dashboard replacement and health reset share one ``ON CONFLICT``
    statement, so row-lock ordering cannot let A's old failure/success state
    survive once ``secret_value`` becomes B.
    """
    shared_pool = _make_pool(execute_return="INSERT 0 1")
    store = CredentialStore(_make_pool(), fallback_pools=[shared_pool])

    await store.store_shared("cli-auth/codex", "dashboard-B", category="cli-auth")

    sql, *_ = shared_pool._conn.execute.call_args[0]
    assert "last_test_ok = CASE" in sql
    assert "last_verified = CASE" in sql
    assert "last_test_code = CASE" in sql
    assert "last_test_message = CASE" in sql
    assert "IS DISTINCT FROM EXCLUDED.secret_value" in sql


async def test_runtime_rotation_value_replacement_clears_prior_health_atomically() -> None:
    """A successful A→C rotation does not carry A's health onto C.

    The invocation deliberately withholds its A-scoped health result after a
    rotation, so the conditional value write itself must reset the old health
    columns for C.
    """
    shared_pool = _make_pool(execute_return="UPDATE 1")
    store = CredentialStore(_make_pool(), fallback_pools=[shared_pool])

    stored = await store.store_shared_if_unchanged(
        "cli-auth/codex",
        "rotation-C",
        expected_value="launch-A",
        category="cli-auth",
    )

    assert stored is True
    sql, *_ = shared_pool._conn.execute.call_args[0]
    assert "last_test_ok = CASE" in sql
    assert "last_verified = CASE" in sql
    assert "last_test_code = CASE" in sql
    assert "last_test_message = CASE" in sql
    assert "IS DISTINCT FROM $2" in sql


async def test_record_test_result_if_unchanged_uses_shared_authority_and_value_fence() -> None:
    """A stale runtime health result cannot update a dashboard-replaced token."""
    local_pool = _make_pool()
    shared_pool = _make_pool(execute_return="UPDATE 0")
    store = CredentialStore(local_pool, fallback_pools=[shared_pool])

    recorded = await store.record_test_result_if_unchanged(
        "cli-auth/codex",
        ok=False,
        message="old refresh failed",
        expected_value="old-token",
        use_shared_authority=True,
    )

    assert recorded is False
    sql, *args = shared_pool._conn.execute.call_args[0]
    assert "secret_value = $4" in sql
    assert args == [False, "old refresh failed", "cli-auth/codex", "old-token"]
    local_pool._conn.execute.assert_not_awaited()


async def test_codex_authority_is_explicit_and_never_falls_back_to_local_scope() -> None:
    """REQ-core-credentials-001: Codex reads/writes use only a selected authority."""
    local_pool = _make_pool(fetchrow_return=_make_row(secret_value="local-document"))
    authority_pool = _make_pool(fetchrow_return=_make_row(secret_value="authority-document"))
    store = CredentialStore(
        local_pool,
        fallback_pools=[authority_pool],
        system_global_pool=authority_pool,
    )

    assert await store.load_codex_cli_auth() == "authority-document"
    local_pool._conn.fetchrow.assert_not_awaited()

    replacement_document = '{"valid": true}'
    await store.store_codex_cli_auth(replacement_document)
    sql, *args = authority_pool._conn.execute.call_args[0]
    assert "INSERT INTO" in sql
    assert args[0] == "cli-auth/codex"
    assert args[1] == replacement_document
    local_pool._conn.execute.assert_not_awaited()

    with pytest.raises(ValueError, match="Codex CLI auth document"):
        await store.store_codex_cli_auth("not-json")

    authority_pool._conn.execute.reset_mock()
    authority_pool._conn.execute.return_value = "DELETE 1"
    assert await store.delete_codex_cli_auth() is True
    delete_sql, delete_key = authority_pool._conn.execute.call_args.args
    assert "DELETE FROM" in delete_sql
    assert delete_key == "cli-auth/codex"
    local_pool._conn.execute.assert_not_awaited()

    without_authority = CredentialStore(local_pool, fallback_pools=[authority_pool])
    with pytest.raises(RuntimeError, match="system-global.*authority"):
        await without_authority.load_codex_cli_auth()

    # A one-pool deployment is compatible, but must opt in explicitly rather
    # than inheriting generic local-first behavior.
    flat_pool = _make_pool(fetchrow_return=_make_row(secret_value="flat-authority-document"))
    flat_store = CredentialStore(flat_pool, system_global_pool=flat_pool)
    assert await flat_store.load_codex_cli_auth() == "flat-authority-document"
    flat_pool._conn.fetchrow.assert_awaited_once()


async def test_daemon_flat_topology_marks_its_pool_as_explicit_codex_authority() -> None:
    """A flat daemon still selects the independent credential purpose explicitly."""
    from butlers.daemon import ButlerDaemon
    from butlers.db_authority.roles import AuthorityError

    local_pool = _make_pool()
    dedicated = _make_pool()
    driver = dedicated._conn
    driver.fetchrow.return_value = dict.fromkeys(
        (
            "identity_ok",
            "role_set_ok",
            "attributes_ok",
            "ownership_ok",
            "create_ok",
            "target_member",
        ),
        True,
    )
    driver.fetchval.return_value = True
    dedicated.close = AsyncMock()
    daemon = ButlerDaemon.__new__(ButlerDaemon)
    daemon.config = SimpleNamespace(db_schema=None, db_name="butlers")
    daemon.db = SimpleNamespace(db_name="butlers")
    daemon._credential_purpose_pool = None

    async def native_pool(**options):
        @asynccontextmanager
        async def acquire():
            await options["init"](driver)
            await options["setup"](driver)
            yield driver

        dedicated.acquire.side_effect = acquire
        return dedicated

    with (
        patch.dict(
            os.environ,
            {
                "BUTLERS_CREDENTIAL_DATABASE_URL": "postgresql://credential_login:synthetic@localhost/butlers"
            },
        ),
        patch("asyncpg.create_pool", new=AsyncMock(side_effect=native_pool)),
    ):
        store = await daemon._build_credential_store(local_pool)
        assert store.pool is local_pool
        assert store.shared_pool is dedicated
        assert store.require_system_global_pool() is dedicated
        assert daemon._credential_purpose_pool is dedicated
        driver.execute.assert_awaited_once_with('SET ROLE "credential_store_writer"')
        local_pool.acquire.assert_not_called()
        driver.fetchrow.return_value["ownership_ok"] = False
        with pytest.raises(AuthorityError):
            async with dedicated.acquire():
                pytest.fail("Credential drift escaped the actual startup factory")
        driver.fetchrow.return_value["ownership_ok"] = True
        # A configured wrong purpose is an error, never a primary-pool alias.
        driver.fetchrow.return_value["identity_ok"] = False
        with patch.dict(
            os.environ,
            {
                "BUTLERS_CREDENTIAL_DATABASE_URL": "postgresql://dashboard_api:synthetic@localhost/butlers"
            },
        ):
            with pytest.raises(AuthorityError):
                await ButlerDaemon.__new__(ButlerDaemon)._build_credential_store(local_pool)
        driver.fetchrow.return_value["identity_ok"] = True
        # Missing configuration keeps ordinary local resolution, never Codex authority.
        with patch.dict(os.environ, {}, clear=True):
            absent = await ButlerDaemon.__new__(ButlerDaemon)._build_credential_store(local_pool)
            assert absent.pool is local_pool
            with pytest.raises(RuntimeError, match="system-global.*authority"):
                await absent.load_codex_cli_auth()
            local_pool.acquire.assert_not_called()
        with patch(
            "butlers.daemon.create_system_global_credential_store_from_env",
            new=AsyncMock(side_effect=AuthorityError("Configured authority refused")),
        ):
            with pytest.raises(AuthorityError, match="Configured authority refused"):
                await ButlerDaemon.__new__(ButlerDaemon)._build_credential_store(local_pool)


@pytest.mark.parametrize("diagnostic_target", ["credential", "shared_reader"])
async def test_daemon_credential_cleanup_retains_release_and_dependent_close(
    diagnostic_target: str,
) -> None:
    """Cancellation leaves one physical owner and fences the dependent main DB."""
    from butlers.db_authority.catalog import observe_catalog_owned
    from butlers.lifecycle import _shutdown_chronicler_dependents

    release = asyncio.Event()
    entered = asyncio.Event()
    order = []

    async def operation():
        entered.set()
        await release.wait()
        order.append("released")

    async def close_credential():
        order.append("credential_closed")

    async def close_shared():
        order.append("shared_closed")

    async def close_main():
        order.append("main_closed")

    pool = SimpleNamespace(close=AsyncMock(side_effect=close_credential))
    shared_pool = SimpleNamespace(close=AsyncMock(side_effect=close_shared))
    shared_db = SimpleNamespace(pool=shared_pool)
    daemon = SimpleNamespace(
        _active_modules=[],
        _modules=[],
        blob_store=None,
        _db_log_handler=None,
        _audit_db=None,
        _credential_purpose_pool=pool,
        _shared_credentials_db=shared_db,
        db=SimpleNamespace(close=AsyncMock(side_effect=close_main)),
    )
    diagnostic_pool = pool if diagnostic_target == "credential" else shared_pool
    diagnostic = asyncio.create_task(observe_catalog_owned(diagnostic_pool, operation))
    await entered.wait()
    diagnostic.cancel()
    with pytest.raises(asyncio.CancelledError):
        await diagnostic
    callers = []
    try:
        for _ in range(2):
            caller = asyncio.create_task(_shutdown_chronicler_dependents(daemon))
            callers.append(caller)
            await asyncio.sleep(0)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            if diagnostic_target == "credential":
                assert daemon._credential_purpose_pool is pool
                pool.close.assert_not_awaited()
            else:
                assert daemon._credential_purpose_pool is None
                pool.close.assert_awaited_once()
            assert daemon._shared_credentials_db is shared_db
            shared_pool.close.assert_not_awaited()
            daemon.db.close.assert_not_awaited()
        release.set()
        await asyncio.wait_for(asyncio.shield(daemon._credential_dependents_close_task), 1)
        expected = (
            ["released", "credential_closed", "shared_closed", "main_closed"]
            if diagnostic_target == "credential"
            else ["credential_closed", "released", "shared_closed", "main_closed"]
        )
        assert order == expected
        assert daemon._credential_purpose_pool is None
        assert daemon._shared_credentials_db is None
        assert shared_db.pool is None
        shared_pool.close.assert_awaited_once()
        pool.close.assert_awaited_once()
        daemon.db.close.assert_awaited_once()
        await _shutdown_chronicler_dependents(daemon)
        daemon.db.close.assert_awaited_once()
    finally:
        release.set()
        if hasattr(daemon, "_credential_dependents_close_task"):
            await daemon._credential_dependents_close_task

    failure = RuntimeError("synthetic shared reader close failure")
    failed_pool = SimpleNamespace(close=AsyncMock(side_effect=failure))
    failed_reader = SimpleNamespace(pool=failed_pool)
    failed_daemon = SimpleNamespace(
        _active_modules=[],
        _modules=[],
        blob_store=None,
        _db_log_handler=None,
        _audit_db=None,
        _credential_purpose_pool=None,
        _shared_credentials_db=failed_reader,
        db=SimpleNamespace(close=AsyncMock()),
    )
    with pytest.raises(RuntimeError) as failed:
        await _shutdown_chronicler_dependents(failed_daemon)
    assert failed.value is failure
    assert failed_daemon._shared_credentials_db is failed_reader
    assert failed_reader.pool is failed_pool
    failed_daemon.db.close.assert_not_awaited()


def _established_owner(schema: str = "chronicler"):
    from butlers.db import Database

    owner = Database(db_name="butlers", schema=schema, role=f"butler_{schema}_rw")
    owner.pool = _make_pool()
    owner._role_verified = True
    owner._verified_role = owner.role
    from butlers.db_authority.roles import load_runtime_profile

    owner._runtime_profile = load_runtime_profile(owner.db_name, owner.schema, owner.role)
    return owner


def test_owner_delegates_public_but_not_sibling_schema() -> None:
    """REQ-database-security-012: shared ``public`` reads keep the owner's role."""
    owner = _established_owner()

    shared = owner.for_owned_schema("public")
    shared._validate_runtime_role()
    assert (shared.schema, shared.role) == ("public", "butler_chronicler_rw")
    assert shared._runtime_profile is owner._runtime_profile
    assert shared._owner_pool is owner.pool

    with pytest.raises(RuntimeError, match="unowned schema"):
        owner.for_owned_schema("switchboard")


@pytest.mark.parametrize("configured_writer", [False, True])
async def test_daemon_schema_topology_reads_shared_credentials_under_its_role(
    configured_writer: bool,
) -> None:
    """Public read fallback keeps its role; only the explicit purpose writes globally."""
    from butlers.daemon import ButlerDaemon
    from butlers.db import Database
    from butlers.db_authority.roles import AuthorityError

    local_pool = _make_pool()
    shared_pool = _make_pool()
    shared_pool.close = AsyncMock()
    writer_pool = _make_pool()
    daemon = ButlerDaemon.__new__(ButlerDaemon)
    daemon.config = SimpleNamespace(db_schema="chronicler", db_name="butlers")
    daemon.db = _established_owner()
    daemon._shared_credentials_db = None

    async def connect(db: Database):
        db._validate_runtime_role()  # the real connect's first refusal
        db.pool = shared_pool
        return shared_pool

    selected = (
        {
            "BUTLERS_CREDENTIAL_DATABASE_URL": "postgresql://credential_login:synthetic@localhost/butlers",
            "BUTLER_SHARED_DB_NAME": "  butlers  ",
        }
        if configured_writer
        else {}
    )
    with (
        patch.dict(
            os.environ, {**selected, "BUTLER_SHARED_DB_NAME": "other-reader-target"}, clear=True
        ),
        patch.object(
            Database, "for_owned_schema", side_effect=AssertionError("reader allocated")
        ) as delegated,
        patch(
            "butlers.daemon.create_system_global_credential_store_from_env", new=AsyncMock()
        ) as rejected_writer,
    ):
        with pytest.raises(AuthorityError, match="reader target"):
            await daemon._build_credential_store(local_pool)
        delegated.assert_not_called()
        rejected_writer.assert_not_awaited()
        assert daemon._shared_credentials_db is None
    try:
        with (
            patch.dict(os.environ, selected, clear=True),
            patch.object(Database, "connect", new=connect),
            patch.object(Database, "provision", new=AsyncMock()) as provision,
            patch(
                "butlers.credential_store.ensure_secrets_schema", new=AsyncMock()
            ) as ensure_schema,
            patch(
                "butlers.daemon.create_system_global_credential_store_from_env",
                new=AsyncMock(
                    return_value=CredentialStore(writer_pool, system_global_pool=writer_pool)
                ),
            ) as writer_factory,
        ):
            store = await daemon._build_credential_store(local_pool)
        assert store.shared_pool is shared_pool
        shared_db = daemon._shared_credentials_db
        assert (shared_db.schema, shared_db.role) == ("public", "butler_chronicler_rw")
        assert shared_db._runtime_profile is daemon.db._runtime_profile
        provision.assert_not_awaited()
        ensure_schema.assert_not_awaited()
        if configured_writer:
            assert store.require_system_global_pool() is writer_pool
            assert daemon._credential_purpose_pool is writer_pool
            writer_factory.assert_awaited_once_with()
        else:
            assert not store.has_system_global_authority
            with pytest.raises(RuntimeError, match="system-global.*authority"):
                store.require_system_global_pool()
            writer_factory.assert_not_awaited()
    finally:
        if daemon._shared_credentials_db is not None:
            await daemon._shared_credentials_db.close()


async def test_connector_global_codex_authority_uses_its_own_writable_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-core-credentials-001: connector auth avoids a local/cursor pool.

    ``connector_writer`` has intentionally read-only access to public
    credentials. The separate authority uses its explicit dedicated login and
    freshly proves credential_store_writer before Codex rotation/health writes.
    A connector's unrelated legacy configuration is never a credential fallback.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "db-host")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "db-user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "db-password")
    monkeypatch.setenv("BUTLER_SHARED_DB_NAME", "shared-credentials-db")
    monkeypatch.setenv(
        "BUTLERS_CREDENTIAL_DATABASE_URL",
        "postgresql://credential-login:synthetic%40password@credential-host:5440/butlers",
    )
    monkeypatch.delenv("BUTLER_SHARED_DB_SCHEMA", raising=False)
    created_pool = _make_pool()
    driver = created_pool._conn
    driver.fetchrow.return_value = dict.fromkeys(
        (
            "identity_ok",
            "role_set_ok",
            "attributes_ok",
            "ownership_ok",
            "create_ok",
            "target_member",
        ),
        True,
    )
    driver.fetchval.return_value = True

    async def native_pool(**options):
        # Only the driver is doubled; real purpose selection, JSONB init and
        # fresh authority/object-catalog admission run before publication.
        @asynccontextmanager
        async def acquire():
            await options["init"](driver)
            await options["setup"](driver)
            yield driver

        created_pool.acquire.side_effect = acquire
        return created_pool

    with patch("asyncpg.create_pool", new=AsyncMock(side_effect=native_pool)) as create_pool:
        store = await create_system_global_credential_store_from_env()

    assert store.require_system_global_pool() is created_pool
    assert create_pool.await_args.kwargs["database"] == "butlers"
    assert create_pool.await_args.kwargs["server_settings"] == {"search_path": "public"}
    # Do not downgrade the designated authority to connector_writer: that
    # role deliberately cannot persist cli-auth/codex rotation/health state.
    assert callable(create_pool.await_args.kwargs["setup"])
    assert create_pool.await_args.kwargs["user"] == "credential-login"
    assert create_pool.await_args.kwargs["host"] == "credential-host"
    assert create_pool.await_args.kwargs["port"] == 5440
    assert create_pool.await_args.kwargs["password"] == "synthetic@password"
    driver.execute.assert_awaited_once_with('SET ROLE "credential_store_writer"')
    driver.set_type_codec.assert_awaited_once()


async def test_load_has_delete() -> None:
    """load/has/delete return correct values based on row presence."""
    row = _make_row(secret_value="tok_abc123")
    assert await CredentialStore(_make_pool(fetchrow_return=row)).load("K") == "tok_abc123"
    assert await CredentialStore(_make_pool(fetchrow_return=None)).load("K") is None
    assert await CredentialStore(_make_pool(fetchrow_return=row)).has("K") is True
    assert await CredentialStore(_make_pool(fetchrow_return=None)).has("K") is False
    assert await CredentialStore(_make_pool(execute_return="DELETE 1")).delete("K") is True
    assert await CredentialStore(_make_pool(execute_return="DELETE 0")).delete("K") is False


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


async def test_resolve_db_wins_env_fallback_optional_and_fallback_pool() -> None:
    """resolve(): DB wins over env; no env fallback by default; fallback pool used on local miss."""
    row = _make_row(secret_value="db-value")
    with patch.dict(os.environ, {"MY_KEY": "env-value"}):
        assert (
            await CredentialStore(_make_pool(fetchrow_return=row)).resolve("MY_KEY") == "db-value"
        )
        assert await CredentialStore(_make_pool(fetchrow_return=None)).resolve("MY_KEY") is None
        assert (
            await CredentialStore(_make_pool(fetchrow_return=None)).resolve(
                "MY_KEY", env_fallback=True
            )
            == "env-value"
        )

    fallback_pool = _make_pool(fetchrow_return=_make_row(secret_value="shared"))
    store = CredentialStore(_make_pool(fetchrow_return=None), fallback_pools=[fallback_pool])
    assert await store.resolve("K", env_fallback=False) == "shared"
    assert fallback_pool.acquire.call_count == 1

    local_pool = _make_pool(fetchrow_return=row)
    store2 = CredentialStore(local_pool, fallback_pools=[fallback_pool])
    assert await store2.resolve("K") == "db-value"
    assert fallback_pool.acquire.call_count == 1  # not called again


# ---------------------------------------------------------------------------
# list_secrets
# ---------------------------------------------------------------------------


async def test_list_secrets() -> None:
    """list_secrets returns SecretMetadata; filters by category; never exposes values."""

    def _db_row(key, category="general"):
        r = MagicMock()
        r.__getitem__ = lambda self, k: {
            "secret_key": key,
            "category": category,
            "description": None,
            "is_sensitive": True,
            "created_at": _NOW,
            "updated_at": _NOW,
            "expires_at": None,
            "last_verified": None,
            "last_test_ok": None,
            "last_test_code": None,
            "last_test_message": None,
        }[k]
        return r

    rows = [_db_row("K1"), _db_row("K2", "telegram")]
    pool = _make_pool(fetch_return=rows)
    result = await CredentialStore(pool).list_secrets()
    assert len(result) == 2 and all(isinstance(m, SecretMetadata) for m in result)
    assert result[0].is_set is True and result[0].source == "database"
    assert not hasattr(result[0], "secret_value")
    # Test-state columns default to None when never probed
    assert result[0].last_verified is None
    assert result[0].last_test_ok is None
    assert result[0].last_test_code is None
    assert result[0].last_test_message is None


async def test_list_secrets_test_state_columns() -> None:
    """list_secrets populates test-state columns from DB rows when present."""
    verified_at = datetime(2026, 5, 1, 9, 0, 0, tzinfo=UTC)

    def _db_row_probed(key: str) -> MagicMock:
        r = MagicMock()
        r.__getitem__ = lambda self, k: {
            "secret_key": key,
            "category": "general",
            "description": None,
            "is_sensitive": True,
            "created_at": _NOW,
            "updated_at": _NOW,
            "expires_at": None,
            "last_verified": verified_at,
            "last_test_ok": True,
            "last_test_code": 200,
            "last_test_message": None,
        }[k]
        return r

    def _db_row_failing(key: str) -> MagicMock:
        r = MagicMock()
        r.__getitem__ = lambda self, k: {
            "secret_key": key,
            "category": "general",
            "description": None,
            "is_sensitive": True,
            "created_at": _NOW,
            "updated_at": _NOW,
            "expires_at": None,
            "last_verified": None,
            "last_test_ok": False,
            "last_test_code": 401,
            "last_test_message": "Unauthorized",
        }[k]
        return r

    rows = [_db_row_probed("OK_KEY"), _db_row_failing("FAIL_KEY")]
    pool = _make_pool(fetch_return=rows)
    result = await CredentialStore(pool).list_secrets()

    assert len(result) == 2

    ok_meta = result[0]
    assert ok_meta.last_verified == verified_at
    assert ok_meta.last_test_ok is True
    assert ok_meta.last_test_code == 200
    assert ok_meta.last_test_message is None

    fail_meta = result[1]
    assert fail_meta.last_verified is None
    assert fail_meta.last_test_ok is False
    assert fail_meta.last_test_code == 401
    assert fail_meta.last_test_message == "Unauthorized"


# ---------------------------------------------------------------------------
# Helpers + repr
# ---------------------------------------------------------------------------


def test_helpers_and_repr() -> None:
    """Utilities and repr are correct."""
    naive = datetime(2026, 1, 1, 12, 0, 0)
    assert _ensure_utc(naive).tzinfo == UTC
    assert _is_missing_table_error(RuntimeError('relation "butler_secrets" does not exist')) is True

    with patch.dict(os.environ, {}, clear=True):
        assert shared_db_name_from_env() == "butlers"
    with patch.dict(os.environ, {"BUTLER_SHARED_DB_NAME": "my_shared"}):
        assert shared_db_name_from_env() == "my_shared"

    store = CredentialStore(_make_pool(), fallback_pools=[_make_pool()])
    r = repr(store)
    assert "CredentialStore" in r and "fallback_pools=1" in r

    meta = SecretMetadata(
        key="MY_KEY",
        category="general",
        description=None,
        is_sensitive=True,
        is_set=True,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at=None,
        source="database",
    )
    assert "MY_KEY" in repr(meta) and "general" in repr(meta)


# ---------------------------------------------------------------------------
# SecretMetadata test-state columns
# ---------------------------------------------------------------------------


def test_secret_metadata_test_state_columns() -> None:
    """SecretMetadata test-state columns default to None and roundtrip unchanged."""
    common = dict(
        key="K",
        category="general",
        description=None,
        is_sensitive=True,
        is_set=True,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at=None,
        source="database",
    )
    # Default (never probed).
    defaults = SecretMetadata(**common)
    assert defaults.last_verified is None
    assert defaults.last_test_ok is None
    assert defaults.last_test_code is None
    assert defaults.last_test_message is None

    # Roundtrip of explicit values.
    verified_at = datetime(2026, 5, 15, 10, 30, 0, tzinfo=UTC)
    meta = SecretMetadata(
        **common,
        last_verified=verified_at,
        last_test_ok=False,
        last_test_code=403,
        last_test_message="Permission denied",
    )
    assert meta.last_verified == verified_at
    assert meta.last_test_ok is False
    assert meta.last_test_code == 403
    assert meta.last_test_message == "Permission denied"


# ---------------------------------------------------------------------------
# EntityInfoRow dataclass
# ---------------------------------------------------------------------------


def test_entity_info_row_columns_and_repr_no_leak() -> None:
    """EntityInfoRow defaults/roundtrips test-state + nullable fields; repr hides value."""
    # Defaults (never probed) with no test-state args supplied.
    defaults = EntityInfoRow(
        id="00000000-0000-0000-0000-000000000001",
        entity_id="00000000-0000-0000-0000-000000000002",
        type="google_oauth_refresh",
        value="tok3n",
        label=None,
        is_primary=True,
        secured=True,
        created_at=_NOW,
    )
    assert defaults.last_verified is None
    assert defaults.last_test_ok is None
    assert defaults.last_test_code is None
    assert defaults.last_test_message is None
    # Security: repr must not expose the secret value.
    r = repr(defaults)
    assert "EntityInfoRow" in r
    assert "google_oauth_refresh" in r
    assert "secured=True" in r
    assert "tok3n" not in r

    # Roundtrip of explicit test-state values.
    verified_at = datetime(2026, 5, 20, 14, 0, 0, tzinfo=UTC)
    row = EntityInfoRow(
        id="00000000-0000-0000-0000-000000000001",
        entity_id="00000000-0000-0000-0000-000000000002",
        type="google_oauth_refresh",
        value=None,
        label="Personal account",
        is_primary=True,
        secured=True,
        created_at=_NOW,
        last_verified=verified_at,
        last_test_ok=True,
        last_test_code=200,
        last_test_message=None,
    )
    assert row.last_verified == verified_at
    assert row.last_test_ok is True
    assert row.last_test_code == 200

    # is_primary is nullable (DB column has no NOT NULL constraint).
    nullable = EntityInfoRow(
        id="abc",
        entity_id="def",
        type="email",
        value="user@example.com",
        label=None,
        is_primary=None,
        secured=False,
        created_at=_NOW,
    )
    assert nullable.is_primary is None


async def test_serving_secrets_admission_never_repairs_missing_schema():
    from butlers.credential_store import ensure_secrets_schema

    pool = _make_pool()
    pool._conn.fetchval.return_value = False
    with pytest.raises(RuntimeError):
        await ensure_secrets_schema(pool)
    pool._conn.execute.assert_not_awaited()
    pool._conn.fetchval.return_value = True
    await ensure_secrets_schema(pool)
    pool._conn.execute.assert_not_awaited()
