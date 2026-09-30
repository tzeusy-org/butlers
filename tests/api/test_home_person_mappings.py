from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import shutil
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from prometheus_client import REGISTRY
from sqlalchemy import create_engine

from alembic import command
from butlers.api import dashboard_audit_middleware
from butlers.api.app import create_app
from butlers.api.routers import home_person_mappings as mapping_router
from butlers.api.routers.home_person_mappings import (
    _LOCK_NAMESPACE,
    MappingBatch,
    _decide_batch,
    _get_db_manager,
    _key_digest,
)
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import (
    create_migration_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.api.auth_helpers import _DomainOwnerState, create_authenticated_domain_app

_DOCKER_AVAILABLE = shutil.which("docker") is not None
_ROUTE = "/api/home/person-mappings"
_ORIGIN = "https://butlers.example.test"
_AUDIT_ACTION = "home_assistant_person_mapping_batch"
_RECEIPT_FIELDS = {
    "receipt",
    "complete",
    "received_count",
    "created_count",
    "unchanged_count",
    "conflict_count",
    "invalid_reference_count",
}
_TOO_LARGE = {
    "error": {
        "code": "REQUEST_BODY_TOO_LARGE",
        "message": "Request body exceeds 32 KiB.",
        "butler": None,
        "details": None,
    }
}


def _key(seed: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest()).decode().rstrip("=")


class _Manager:
    """Dashboard DB-manager seam that counts pool requests; no pool means unavailable."""

    def __init__(self) -> None:
        self.serving: asyncpg.Pool | None = None
        self.calls = 0

    def pool(self, _name: str) -> asyncpg.Pool:
        self.calls += 1
        if self.serving is None:
            raise RuntimeError("synthetic unavailable")
        return self.serving


@pytest.fixture
def seams(monkeypatch):
    """Observe actor, decoder, pool and generic-audit interaction without changing decisions."""
    observed = SimpleNamespace(manager=_Manager(), actor_calls=0, decoder_calls=0, generic=[])
    actor = mapping_router.authenticated_principal

    def counted_actor() -> str:
        observed.actor_calls += 1
        return actor()

    def counted_loads(value, *args, **kwargs):
        observed.decoder_calls += 1
        return json.loads(value, *args, **kwargs)

    async def record_generic(_db_manager, **fields) -> None:
        observed.generic.append(fields)

    monkeypatch.setattr(mapping_router, "authenticated_principal", counted_actor)
    monkeypatch.setattr(
        mapping_router,
        "json",
        SimpleNamespace(
            loads=counted_loads, dumps=json.dumps, JSONDecodeError=json.JSONDecodeError
        ),
    )
    monkeypatch.setattr(dashboard_audit_middleware, "get_db_manager", lambda: observed.manager)
    monkeypatch.setattr(dashboard_audit_middleware, "emit_dashboard_audit", record_generic)
    return observed


def _mounted(seams, app=None):
    """The production app and owner middleware, wired to the observed DB manager."""
    app = app if app is not None else create_authenticated_domain_app()
    app.dependency_overrides[_get_db_manager] = lambda: seams.manager
    return app


async def _raw_post(
    app, chunks: list[bytes], headers: list[tuple[bytes, bytes]], query_string: bytes = b""
):
    """Drive one POST through the full ASGI stack, counting ``receive`` calls."""
    pending = list(chunks)
    received = 0
    sent: list[dict] = []

    async def receive():
        nonlocal received
        received += 1
        if pending:
            chunk = pending.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(pending)}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": _ROUTE,
        "raw_path": _ROUTE.encode(),
        "query_string": query_string,
        "root_path": "",
        "headers": [(b"host", b"butlers.example.test"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("butlers.example.test", 443),
    }
    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body), received


def _padded_body(spelling: str, size: int) -> bytes:
    """A valid batch spelled plainly or with every string character escaped, padded to size."""

    def escaped(value: str) -> str:
        return "".join(f"\\u{ord(char):04x}" for char in value)

    if spelling == "whitespace":
        body = json.dumps(
            {"mappings": [{"ha_person_id": "person.bound_fixture", "entity_id": str(uuid4())}]}
        )
    else:
        items: list[str] = []
        while len(items) < 50:
            item = (
                f'{{"ha_person_id":"{escaped(f"person.escaped_{len(items):02d}_" + "a" * 60)}",'
                f'"entity_id":"{escaped(str(UUID(int=len(items) + 1)))}"}}'
            )
            if len('{"mappings":[]}') + len(",".join([*items, item])) > size - 16:
                break
            items.append(item)
        body = '{"mappings":[' + ",".join(items) + "]}"
    encoded = body.encode()
    assert len(encoded) <= size
    return encoded + b" " * (size - len(encoded))


async def _await_lock_waiters(pool: asyncpg.Pool, count: int) -> None:
    """Latch until ``count`` backends in this database are blocked on a lock."""
    for _ in range(500):
        waiting = await pool.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
        if waiting >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {count} lock waiters")


@asynccontextmanager
async def _held_advisory_lock(db_url: str, name: str):
    connection = await asyncpg.connect(db_url)
    try:
        await connection.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", name)
        yield lambda: connection.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", name)
    finally:
        await connection.close()


async def _mapped_count(pool: asyncpg.Pool, *ha_ids: str) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM connectors.home_assistant_persons "
        "WHERE ha_entity_id = ANY($1::text[])",
        list(ha_ids),
    )


async def _audits(pool: asyncpg.Pool, receipt: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT metadata FROM public.audit_log WHERE action = $1 "
        "AND metadata->>'receipt' = $2 ORDER BY id",
        _AUDIT_ACTION,
        receipt,
    )
    return [row["metadata"] for row in rows]


async def _tables_containing(pool: asyncpg.Pool, needle: str) -> set[str]:
    """Content-blind scan of every readable user table's row text for one synthetic value."""
    tables = await pool.fetch(
        "SELECT format('%I.%I', n.nspname, c.relname) AS name FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%' "
        "AND has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(c.oid, 'SELECT')"
    )
    hits = set()
    for table in tables:
        found = await pool.fetchval(
            f"SELECT EXISTS (SELECT 1 FROM {table['name']} AS row_value "
            "WHERE strpos(row_value::text, $1) > 0)",
            needle,
        )
        if found:
            hits.add(table["name"])
    return hits


def _batch(*pairs: tuple[str, str]) -> MappingBatch:
    return MappingBatch.model_validate(
        {
            "mappings": [
                {"ha_person_id": ha_id, "entity_id": entity_id} for ha_id, entity_id in pairs
            ]
        }
    )


@pytest.fixture(scope="module")
def mapping_db_url(postgres_container) -> str:
    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@head")
    return db_url


def _replay_init_db(postgres_container, db_url: str) -> None:
    parsed = urlparse(db_url)
    migration_user = parsed.username
    db_name = parsed.path.lstrip("/")
    assert migration_user and db_name
    engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    raw_connection = engine.raw_connection()
    try:
        raw_connection.autocommit = True
        with raw_connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)", (migration_user,)
            )
            cursor.execute(init_db_sql_for_dbapi())
    finally:
        raw_connection.close()
        engine.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def mapping_pool(mapping_db_url: str):
    pool = await asyncpg.create_pool(
        mapping_db_url,
        min_size=1,
        max_size=4,
        init=register_jsonb_codec,
    )
    try:
        yield pool
    finally:
        await pool.close()


async def _person(pool: asyncpg.Pool, name: str, *, metadata: dict | None = None) -> str:
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
        "VALUES ($1, 'person', $2) RETURNING id",
        name,
        metadata or {},
    )
    return str(entity_id)


_DELIVERIES = {
    "absent": [],
    "understated": [(b"content-length", b"1")],
    "overstated": [(b"content-length", b"65536")],
    "chunked": [(b"transfer-encoding", b"chunked")],
}


def _headers(extra: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    return [
        (b"content-type", b"application/json"),
        (b"idempotency-key", _key(b"raw-bound").encode()),
        *extra,
    ]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["whitespace", "escaped"])
@pytest.mark.parametrize("delivery", sorted(_DELIVERIES))
async def test_mounted_body_bound_is_measured_from_received_octets(
    seams, spelling: str, delivery: str
) -> None:
    """32,768 received octets proceed; octet 32,769 stops before decode whatever headers claim."""
    app = _mounted(seams)

    def chunks(body: bytes) -> list[bytes]:
        if delivery == "chunked":
            return [body[offset : offset + 4096] for offset in range(0, len(body), 4096)]
        return [body]

    exact = chunks(_padded_body(spelling, 32_768))
    status, body, _ = await _raw_post(app, exact, _headers(_DELIVERIES[delivery]))
    # Proceeding means decode, actor derivation and one pool request, which is unavailable here.
    assert (status, body["error"]["code"]) == (503, "MAPPING_DATABASE_UNAVAILABLE")
    assert (seams.decoder_calls, seams.actor_calls, seams.manager.calls) == (1, 1, 1)

    oversize = [*chunks(_padded_body(spelling, 32_769)), b'"private-later-chunk"']
    status, body, received = await _raw_post(app, oversize, _headers(_DELIVERIES[delivery]))
    assert (status, body) == (413, _TOO_LARGE)
    assert received < len(oversize)
    assert (seams.decoder_calls, seams.actor_calls, seams.manager.calls) == (1, 1, 1)
    assert seams.generic == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mounted_owner_boundary_refuses_before_body_actor_or_pool(seams) -> None:
    """Owner auth and header ambiguity fail before ASGI receive, actor, pool or any audit."""
    unavailable = _mounted(seams, create_app(api_key="synthetic-owner-key"))
    configured = _mounted(seams, create_app(api_key="synthetic-owner-key"))
    configured.state.owner_auth_service = _DomainOwnerState("synthetic-owner-key")
    body = [_padded_body("whitespace", 512)]
    owner_key = [(b"x-api-key", b"synthetic-owner-key")]
    refusals = [
        (unavailable, owner_key, 503, "AUTH_UNAVAILABLE"),
        (configured, [], 401, "UNAUTHORIZED"),
        (configured, [(b"x-api-key", b"wrong-owner-key")], 401, "UNAUTHORIZED"),
        (
            configured,
            [*owner_key, (b"content-length", b"512"), (b"content-length", b"1")],
            400,
            "INVALID_REQUEST",
        ),
    ]
    for app, extra, expected_status, expected_code in refusals:
        status, response, received = await _raw_post(app, body, _headers(extra))
        assert (status, response["error"]["code"], received) == (
            expected_status,
            expected_code,
            0,
        )
    assert (seams.decoder_calls, seams.actor_calls, seams.manager.calls) == (0, 0, 0)
    assert seams.generic == []

    # Positive control: the same request with the owner credential reaches the pool seam.
    status, response, received = await _raw_post(configured, body, _headers(owner_key))
    assert (status, response["error"]["code"]) == (503, "MAPPING_DATABASE_UNAVAILABLE")
    assert received >= 1
    assert seams.manager.calls == 1


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_dashboard_and_switchboard_authority_survive_bootstrap_replay(
    mapping_pool,
    mapping_db_url: str,
    postgres_container,
) -> None:
    """The dashboard login and Switchboard can insert; another runtime cannot."""
    owner = await mapping_pool.fetchval("SELECT current_user")
    assert owner == await mapping_pool.fetchval("SELECT session_user")
    posture = await mapping_pool.fetch(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = ANY($1::regclass[]) ORDER BY oid",
        [
            "public.ha_person_mapping_receipts",
            "connectors.home_assistant_persons",
        ],
    )
    assert [(row["relrowsecurity"], row["relforcerowsecurity"]) for row in posture] == [
        (True, False),
        (True, False),
    ]
    owner_entity = await _person(mapping_pool, f"mapping-owner-authority-{uuid4()}")
    owner_decision, _ = await _decide_batch(
        mapping_pool,
        _batch(("person.owner_authority_fixture", owner_entity)),
        _key(b"owner-authority"),
        "owner",
    )
    assert owner_decision.outcome == "success"

    async def _switchboard(connection: asyncpg.Connection) -> None:
        await connection.execute('SET ROLE "butler_switchboard_rw"')

    switchboard_pool = await asyncpg.create_pool(
        mapping_db_url,
        min_size=1,
        max_size=2,
        init=register_jsonb_codec,
        setup=_switchboard,
    )
    switchboard_entity = await _person(mapping_pool, f"mapping-switchboard-authority-{uuid4()}")
    try:
        switchboard_decision, _ = await _decide_batch(
            switchboard_pool,
            _batch(("person.switchboard_authority_fixture", switchboard_entity)),
            _key(b"switchboard-authority"),
            "owner",
        )
        assert switchboard_decision.outcome == "success"
    finally:
        await switchboard_pool.close()

    async def _assert_foreign_role_denied() -> None:
        connection = await asyncpg.connect(mapping_db_url)
        try:
            await connection.execute('SET ROLE "butler_general_rw"')
            for relation in (
                "public.ha_person_mapping_receipts",
                "connectors.home_assistant_persons",
            ):
                try:
                    visible = await connection.fetchval(f"SELECT count(*) FROM {relation}")
                except asyncpg.InsufficientPrivilegeError:
                    visible = 0
                assert visible == 0
            with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.CheckViolationError)):
                await connection.execute(
                    "INSERT INTO public.ha_person_mapping_receipts "
                    "(key_digest, request_digest, receipt, complete, received_count, "
                    "created_count, unchanged_count, conflict_count, invalid_reference_count, "
                    "outcome) VALUES ($1, $2, $3, true, 1, 1, 0, 0, 0, 'success')",
                    hashlib.sha256(b"foreign-key").digest(),
                    hashlib.sha256(b"foreign-request").digest(),
                    uuid4(),
                )
            with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.CheckViolationError)):
                await connection.execute(
                    "INSERT INTO connectors.home_assistant_persons (ha_entity_id, entity_id) "
                    "VALUES ('person.foreign_runtime_fixture', $1)",
                    UUID(owner_entity),
                )
        finally:
            await connection.close()

    await _assert_foreign_role_denied()
    _replay_init_db(postgres_container, mapping_db_url)
    await _assert_foreign_role_denied()

    privileges = await mapping_pool.fetchrow(
        "SELECT "
        "has_table_privilege('butler_switchboard_rw', $1, 'SELECT,INSERT') AS switchboard_ok, "
        "has_table_privilege('butler_switchboard_rw', $1, 'UPDATE') AS switchboard_update, "
        "has_table_privilege('butler_switchboard_rw', $1, 'DELETE') AS switchboard_delete",
        "connectors.home_assistant_persons",
    )
    assert privileges["switchboard_ok"] is True
    assert privileges["switchboard_update"] is False
    assert privileges["switchboard_delete"] is False


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
def test_core_246_shared_ddl_replays_and_downgrades_only_with_the_last_schema(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core", schema="general"))
    asyncio.run(run_migrations(db_url, chain="core", schema="switchboard"))
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT to_regclass('public.ha_person_mapping_receipts') IS NOT NULL"
            ).scalar_one()
    finally:
        engine.dispose()

    general = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    switchboard = _build_alembic_config(db_url, chains=["core"], target_schema="switchboard")
    command.downgrade(general, "core_245")
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT to_regclass('public.ha_person_mapping_receipts') IS NOT NULL"
            ).scalar_one()
    finally:
        engine.dispose()

    command.downgrade(switchboard, "core_245")
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            assert not connection.exec_driver_sql(
                "SELECT to_regclass('public.ha_person_mapping_receipts') IS NOT NULL"
            ).scalar_one()
    finally:
        engine.dispose()

    command.upgrade(general, "core@head")
    command.upgrade(switchboard, "core@head")


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_real_postgres_batch_is_atomic_idempotent_and_content_blind(mapping_pool) -> None:
    first = await _person(mapping_pool, f"mapping-fixture-{uuid4()}")
    second = await _person(mapping_pool, f"mapping-fixture-{uuid4()}")
    private_ha = "person.private_absence_sentinel"
    batch = _batch((private_ha, first), ("person.second_fixture", second))
    key = _key(b"first")

    created, replayed = await _decide_batch(mapping_pool, batch, key, "owner")
    replay, replayed_again = await _decide_batch(
        mapping_pool,
        _batch(("person.second_fixture", second), (private_ha, first)),
        key,
        "owner",
    )

    assert created.outcome == "success"
    assert created.receipt.created_count == 2
    assert replayed is False
    assert replayed_again is True
    assert replay == created
    assert (
        await mapping_pool.fetchval(
            "SELECT count(*) FROM connectors.home_assistant_persons WHERE ha_entity_id = ANY($1::text[])",
            [private_ha, "person.second_fixture"],
        )
        == 2
    )
    assert (
        await mapping_pool.fetchval(
            "SELECT count(*) FROM public.ha_person_mapping_receipts WHERE key_digest = $1",
            _key_digest(key),
        )
        == 1
    )

    stored = await mapping_pool.fetchval(
        "SELECT to_jsonb(receipt_row)::text "
        "FROM public.ha_person_mapping_receipts AS receipt_row WHERE key_digest = $1",
        _key_digest(key),
    )
    audits = await mapping_pool.fetchval(
        "SELECT jsonb_agg(metadata)::text FROM public.audit_log "
        "WHERE action = 'home_assistant_person_mapping_batch' "
        "AND metadata->>'receipt' = $1",
        created.receipt.receipt,
    )
    for private_value in (private_ha, first, "person.second_fixture", second, key):
        assert private_value not in stored
        assert private_value not in audits
    audit_metadata = await mapping_pool.fetchval(
        "SELECT metadata FROM public.audit_log "
        "WHERE action = 'home_assistant_person_mapping_batch' "
        "AND metadata->>'receipt' = $1 ORDER BY id LIMIT 1",
        created.receipt.receipt,
    )
    assert set(audit_metadata) == {
        "receipt",
        "complete",
        "received_count",
        "created_count",
        "unchanged_count",
        "conflict_count",
        "invalid_reference_count",
        "outcome",
    }

    fresh_noop, _ = await _decide_batch(mapping_pool, batch, _key(b"fresh-noop"), "owner")
    assert fresh_noop.receipt.created_count == 0
    assert fresh_noop.receipt.unchanged_count == 2

    third = await _person(mapping_pool, f"mapping-fixture-{uuid4()}")
    mixed, _ = await _decide_batch(
        mapping_pool,
        _batch((private_ha, first), ("person.third_fixture", third)),
        _key(b"mixed"),
        "owner",
    )
    assert mixed.receipt.created_count == 1
    assert mixed.receipt.unchanged_count == 1

    fourth = await _person(mapping_pool, f"mapping-fixture-{uuid4()}")
    mapping_conflict, _ = await _decide_batch(
        mapping_pool,
        _batch(("person.conflicts_by_entity", first), (private_ha, fourth)),
        _key(b"mapping-conflict"),
        "owner",
    )
    assert mapping_conflict.failure_category == "mapping_conflict"
    assert mapping_conflict.receipt.conflict_count == 2
    assert not await mapping_pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM connectors.home_assistant_persons "
        "WHERE ha_entity_id = 'person.conflicts_by_entity')"
    )

    wrong_type = await mapping_pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type) "
        "VALUES ($1, 'organization') RETURNING id",
        f"mapping-wrong-type-{uuid4()}",
    )
    tombstoned = await _person(
        mapping_pool,
        f"mapping-tombstoned-{uuid4()}",
        metadata={"deleted_at": "2026-09-22T00:00:00Z"},
    )
    merged = await _person(
        mapping_pool,
        f"mapping-merged-{uuid4()}",
        metadata={"merged_into": first},
    )
    # The contract is SQL `metadata->>'…' IS NULL`, so present-but-falsy JSON
    # values are still lifecycle markers.
    empty_tombstone = await _person(
        mapping_pool, f"mapping-empty-tombstone-{uuid4()}", metadata={"deleted_at": ""}
    )
    false_merge = await _person(
        mapping_pool, f"mapping-false-merge-{uuid4()}", metadata={"merged_into": False}
    )
    invalid, _ = await _decide_batch(
        mapping_pool,
        _batch(
            ("person.missing_fixture", str(uuid4())),
            ("person.wrong_type_fixture", str(wrong_type)),
            ("person.tombstoned_fixture", tombstoned),
            ("person.merged_fixture", merged),
            ("person.empty_tombstone_fixture", empty_tombstone),
            ("person.false_merge_fixture", false_merge),
        ),
        _key(b"invalid-references"),
        "owner",
    )
    assert invalid.failure_category == "reference_invalid"
    assert invalid.receipt.invalid_reference_count == 6

    divergent = _batch(("person.other_fixture", second))
    refused, _ = await _decide_batch(mapping_pool, divergent, key, "owner")
    assert refused.failure_category == "idempotency_conflict"
    assert (
        await mapping_pool.fetchval(
            "SELECT count(*) FROM public.ha_person_mapping_receipts WHERE key_digest = $1",
            _key_digest(key),
        )
        == 1
    )


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_real_postgres_serializes_competing_batches_and_rolls_back(
    mapping_pool, mapping_db_url: str, seams
) -> None:
    shared = await _person(mapping_pool, f"mapping-shared-{uuid4()}")
    first = _batch(("person.concurrent_first", shared))
    second = _batch(("person.concurrent_second", shared))

    async def overlapped(*calls):
        """Park every call on the mapping advisory lock before any receipt lookup runs."""
        async with _held_advisory_lock(mapping_db_url, _LOCK_NAMESPACE) as release:
            tasks = [asyncio.create_task(call) for call in calls]
            await _await_lock_waiters(mapping_pool, len(tasks))
            await release()
        return await asyncio.gather(*tasks)

    decisions = await overlapped(
        _decide_batch(mapping_pool, first, _key(b"concurrent-first"), "owner"),
        _decide_batch(mapping_pool, second, _key(b"concurrent-second"), "owner"),
    )
    assert sorted(decision.outcome for decision, _ in decisions) == ["refused", "success"]
    assert (
        await mapping_pool.fetchval(
            "SELECT count(*) FROM connectors.home_assistant_persons WHERE entity_id = $1",
            UUID(shared),
        )
        == 1
    )

    identical_entity = await _person(mapping_pool, f"mapping-identical-{uuid4()}")
    identical_batch = _batch(("person.concurrent_identical", identical_entity))
    identical_key = _key(b"concurrent-identical")
    identical = await overlapped(
        _decide_batch(mapping_pool, identical_batch, identical_key, "owner"),
        _decide_batch(mapping_pool, identical_batch, identical_key, "owner"),
    )
    assert identical[0][0] == identical[1][0]
    assert sorted(replayed for _, replayed in identical) == [False, True]
    assert (
        await mapping_pool.fetchval(
            "SELECT count(*) FROM public.ha_person_mapping_receipts WHERE key_digest = $1",
            _key_digest(identical_key),
        )
        == 1
    )

    divergent_a = await _person(mapping_pool, f"mapping-divergent-a-{uuid4()}")
    divergent_b = await _person(mapping_pool, f"mapping-divergent-b-{uuid4()}")
    divergent_key = _key(b"concurrent-divergent")
    divergent = await overlapped(
        _decide_batch(
            mapping_pool,
            _batch(("person.concurrent_divergent_a", divergent_a)),
            divergent_key,
            "owner",
        ),
        _decide_batch(
            mapping_pool,
            _batch(("person.concurrent_divergent_b", divergent_b)),
            divergent_key,
            "owner",
        ),
    )
    assert sorted(decision.failure_category or "success" for decision, _ in divergent) == [
        "idempotency_conflict",
        "success",
    ]
    assert (
        await mapping_pool.fetchval(
            "SELECT count(*) FROM public.ha_person_mapping_receipts WHERE key_digest = $1",
            _key_digest(divergent_key),
        )
        == 1
    )
    assert (
        await _mapped_count(
            mapping_pool, "person.concurrent_divergent_a", "person.concurrent_divergent_b"
        )
        == 1
    )

    # A failure after the explicit audit insert rolls back mapping, receipt and audit together,
    # and the mounted route never claims creation.
    seams.manager.serving = mapping_pool
    rollback_entity = await _person(mapping_pool, f"mapping-rollback-{uuid4()}")
    rollback_key = _key(b"rollback")
    audits_before = await mapping_pool.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE action = $1", _AUDIT_ACTION
    )
    await mapping_pool.execute(
        "CREATE FUNCTION public.reject_mapping_audit_fixture() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic audit failure'; END $$"
    )
    await mapping_pool.execute(
        "CREATE TRIGGER reject_mapping_audit_fixture BEFORE INSERT ON public.audit_log "
        f"FOR EACH ROW WHEN (NEW.action = '{_AUDIT_ACTION}') "
        "EXECUTE FUNCTION public.reject_mapping_audit_fixture()"
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=_mounted(seams)), base_url=_ORIGIN
        ) as client:
            response = await client.post(
                _ROUTE,
                json={
                    "mappings": [
                        {"ha_person_id": "person.rollback_fixture", "entity_id": rollback_entity}
                    ]
                },
                headers={"Idempotency-Key": rollback_key},
            )
    finally:
        await mapping_pool.execute("DROP TRIGGER reject_mapping_audit_fixture ON public.audit_log")
        await mapping_pool.execute("DROP FUNCTION public.reject_mapping_audit_fixture()")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "MAPPING_DATABASE_UNAVAILABLE"
    assert (error["details"]["complete"], error["details"]["created_count"]) == (False, 0)
    assert await _mapped_count(mapping_pool, "person.rollback_fixture") == 0
    assert not await mapping_pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM public.ha_person_mapping_receipts WHERE key_digest = $1)",
        _key_digest(rollback_key),
    )
    assert audits_before == await mapping_pool.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE action = $1", _AUDIT_ACTION
    )


_LIFECYCLE_MUTATIONS = {
    "merge": (
        "UPDATE public.entities SET metadata = coalesce(metadata, '{}'::jsonb) "
        "|| jsonb_build_object('merged_into', $2::text) WHERE id = $1"
    ),
    "tombstone": (
        "UPDATE public.entities SET metadata = coalesce(metadata, '{}'::jsonb) "
        "|| jsonb_build_object('deleted_at', '2026-09-29T00:00:00Z') "
        "WHERE id = $1 AND $2::text IS NOT NULL"
    ),
    "delete": "DELETE FROM public.entities WHERE id = $1 AND $2::text IS NOT NULL",
    "retype": (
        "UPDATE public.entities SET entity_type = 'organization' "
        "WHERE id = $1 AND $2::text IS NOT NULL"
    ),
}


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("mutation", sorted(_LIFECYCLE_MUTATIONS))
async def test_real_postgres_entity_lifecycle_races_in_both_orders(
    mapping_pool, mapping_db_url: str, mutation: str
) -> None:
    """Lifecycle-first yields INVALID_REFERENCE; mapping-first makes the mutation wait."""
    survivor = await _person(mapping_pool, f"mapping-survivor-{uuid4()}")

    async def mutate(connection: asyncpg.Connection, entity: str) -> None:
        await connection.execute(_LIFECYCLE_MUTATIONS[mutation], UUID(entity), survivor)

    async def ordered_pair() -> tuple[str, str]:
        pair = [await _person(mapping_pool, f"mapping-lifecycle-{uuid4()}") for _ in range(2)]
        low, high = sorted(pair, key=UUID)
        return low, high

    async def receipt_outcomes(key: str) -> list[str]:
        rows = await mapping_pool.fetch(
            "SELECT outcome FROM public.ha_person_mapping_receipts WHERE key_digest = $1",
            _key_digest(key),
        )
        return [row["outcome"] for row in rows]

    # Lifecycle first: the uncommitted mutation holds the higher UUID. The batch lists it first,
    # yet the mapping locks the lower UUID before waiting, so locks are taken in ascending order.
    low, high = await ordered_pair()
    lifecycle_ids = (f"person.{mutation}_lifecycle_high", f"person.{mutation}_lifecycle_low")
    lifecycle_key = _key(f"{mutation}-lifecycle-first".encode())
    lifecycle = await asyncpg.connect(mapping_db_url)
    try:
        transaction = lifecycle.transaction()
        await transaction.start()
        await mutate(lifecycle, high)
        pending = asyncio.create_task(
            _decide_batch(
                mapping_pool,
                _batch((lifecycle_ids[0], high), (lifecycle_ids[1], low)),
                lifecycle_key,
                "owner",
            )
        )
        await _await_lock_waiters(mapping_pool, 1)
        with pytest.raises(asyncpg.LockNotAvailableError):
            await mapping_pool.execute(
                "SELECT 1 FROM public.entities WHERE id = $1 FOR UPDATE NOWAIT", UUID(low)
            )
        await transaction.commit()
        refused, _ = await pending
    finally:
        await lifecycle.close()
    assert (refused.failure_category, refused.receipt.invalid_reference_count) == (
        "reference_invalid",
        1,
    )
    assert await _mapped_count(mapping_pool, *lifecycle_ids) == 0
    assert await receipt_outcomes(lifecycle_key) == ["refused"]

    # Mapping first: a trigger parks the decision after its row locks and mapping insert,
    # before the receipt; the mutation must wait until mapping, receipt and audit commit.
    low, high = await ordered_pair()
    mapping_ids = (f"person.{mutation}_mapping_high", f"person.{mutation}_mapping_low")
    mapping_key = _key(f"{mutation}-mapping-first".encode())
    latch = "butlers:test:ha-person-mapping-lifecycle-latch"
    await mapping_pool.execute(
        "CREATE FUNCTION public.latch_mapping_receipt_fixture() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        f"PERFORM pg_advisory_xact_lock(hashtextextended('{latch}', 0)); RETURN NEW; END $$"
    )
    await mapping_pool.execute(
        "CREATE TRIGGER latch_mapping_receipt_fixture "
        "BEFORE INSERT ON public.ha_person_mapping_receipts "
        "FOR EACH ROW EXECUTE FUNCTION public.latch_mapping_receipt_fixture()"
    )
    lifecycle = await asyncpg.connect(mapping_db_url)
    try:
        async with _held_advisory_lock(mapping_db_url, latch) as release:
            pending = asyncio.create_task(
                _decide_batch(
                    mapping_pool,
                    _batch((mapping_ids[0], high), (mapping_ids[1], low)),
                    mapping_key,
                    "owner",
                )
            )
            await _await_lock_waiters(mapping_pool, 1)
            mutating = asyncio.create_task(mutate(lifecycle, high))
            await _await_lock_waiters(mapping_pool, 2)
            assert not mutating.done()
            await release()
            created, _ = await pending
            await mutating
    finally:
        await lifecycle.close()
        await mapping_pool.execute(
            "DROP TRIGGER latch_mapping_receipt_fixture ON public.ha_person_mapping_receipts"
        )
        await mapping_pool.execute("DROP FUNCTION public.latch_mapping_receipt_fixture()")
    assert (created.outcome, created.receipt.created_count) == ("success", 2)
    assert await _mapped_count(mapping_pool, *mapping_ids) == 2
    assert await receipt_outcomes(mapping_key) == ["success"]
    assert [audit["outcome"] for audit in await _audits(mapping_pool, created.receipt.receipt)] == [
        "success"
    ]


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_mounted_duplicates_and_legacy_null_target_refuse_without_writes(
    mapping_pool, seams
) -> None:
    seams.manager.serving = mapping_pool
    first = await _person(mapping_pool, f"mapping-duplicate-{uuid4()}")
    second = await _person(mapping_pool, f"mapping-duplicate-{uuid4()}")
    await mapping_pool.execute(
        "INSERT INTO connectors.home_assistant_persons (ha_entity_id, entity_id) "
        "VALUES ('person.legacy_null_fixture', NULL)"
    )
    cases = {
        "duplicate-ha": (
            [("person.duplicate_ha_fixture", first), ("person.duplicate_ha_fixture", second)],
            422,
            "INVALID_REQUEST",
            "request_invalid",
        ),
        "duplicate-entity": (
            [("person.duplicate_entity_a", first), ("person.duplicate_entity_b", first)],
            422,
            "INVALID_REQUEST",
            "request_invalid",
        ),
        "legacy-null": (
            [("person.legacy_null_fixture", first), ("person.legacy_null_peer", second)],
            409,
            "MAPPING_CONFLICT",
            "mapping_conflict",
        ),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_mounted(seams)), base_url=_ORIGIN
    ) as client:
        for name, (pairs, status, code, category) in cases.items():
            key = _key(name.encode())
            response = await client.post(
                _ROUTE,
                json={"mappings": [{"ha_person_id": h, "entity_id": e} for h, e in pairs]},
                headers={"Idempotency-Key": key},
            )
            assert (response.status_code, response.json()["error"]["code"]) == (status, code)
            details = response.json()["error"]["details"]
            assert (details["complete"], details["created_count"]) == (False, 0)
            assert [
                (audit["outcome"], audit["failure_category"])
                for audit in await _audits(mapping_pool, details["receipt"])
            ] == [("refused", category)]
            assert not await mapping_pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM public.ha_person_mapping_receipts "
                "WHERE key_digest = $1 AND outcome = 'success')",
                _key_digest(key),
            )
    assert (
        await _mapped_count(mapping_pool, *(h for pairs, *_ in cases.values() for h, _ in pairs))
        == 1
    )
    assert (
        await mapping_pool.fetchval(
            "SELECT entity_id FROM connectors.home_assistant_persons "
            "WHERE ha_entity_id = 'person.legacy_null_fixture'"
        )
        is None
    )


def _body(value) -> bytes:
    return json.dumps(value).encode()


def _pairs(*pairs: tuple[str, str]) -> dict:
    return {"mappings": [{"ha_person_id": h, "entity_id": e} for h, e in pairs]}


def _decoded_mappings(body: bytes) -> list[dict]:
    try:
        value = json.loads(body)
    except ValueError:
        return []
    mappings = value.get("mappings") if isinstance(value, dict) else value
    if isinstance(mappings, dict):
        mappings = [mappings]
    return [m for m in mappings or [] if isinstance(m, dict)]


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_mounted_structural_refusals_are_fixed_422_without_writes(
    mapping_pool, seams
) -> None:
    """Every count, shape, identifier, key and query refusal is one fixed audited 422."""
    seams.manager.serving = mapping_pool
    entity = str(uuid4())
    one = _body(_pairs(("person.structural_valid", entity)))
    bodies: dict[str, bytes] = {
        "count-empty": _body({"mappings": []}),
        "count-51": _body(_pairs(*((f"person.count_{i:02d}", str(uuid4())) for i in range(51)))),
        "shape-top-list": _body([{"ha_person_id": "person.shape_list", "entity_id": entity}]),
        "shape-missing-mappings": _body({}),
        "shape-extra-top-field": _body({**_pairs(("person.shape_extra", entity)), "force": True}),
        "shape-mappings-not-list": _body(
            {"mappings": {"ha_person_id": "person.shape_obj", "entity_id": entity}}
        ),
        "shape-member-missing-entity": _body({"mappings": [{"ha_person_id": "person.shape_m"}]}),
        "shape-member-extra-field": _body(
            {"mappings": [{"ha_person_id": "person.shape_x", "entity_id": entity, "note": "x"}]}
        ),
        "shape-member-int": _body({"mappings": [{"ha_person_id": 1, "entity_id": entity}]}),
        "shape-invalid-utf8": b'{"mappings":[{"ha_person_id":"person.\xff","entity_id":"x"}]}',
        "shape-malformed-json": b'{"mappings":[',
        **{
            f"ha-{name}": _body(_pairs((ha_id, entity)))
            for name, ha_id in {
                "uppercase": "Person.a",
                "empty-suffix": "person.",
                "hyphen": "person.a-b",
                "leading-space": " person.a",
                "trailing-newline": "person.a\n",
                "other-domain": "sensor.a",
                "non-ascii": "person.é",
                "256-bytes": "person." + "a" * 249,
            }.items()
        },
        **{
            f"entity-{name}": _body(_pairs(("person.entity_spelling", value)))
            for name, value in {
                "uppercase": entity.upper(),
                "braced": "{" + entity + "}",
                "hex32": UUID(entity).hex,
                "urn": UUID(entity).urn,
                "not-a-uuid": "not-a-uuid",
            }.items()
        },
    }
    valid_key = _key(b"structural-valid")
    cases: dict[str, tuple[bytes, list[tuple[bytes, bytes]], bytes, int]] = {
        name: (body, [(b"idempotency-key", _key(name.encode()).encode())], b"", 0)
        for name, body in bodies.items()
    }
    for name, raw_key in {
        "key-absent": None,
        "key-42": valid_key[:42],
        "key-44": valid_key + "A",
        "key-plus": "+" + valid_key[1:],
        "key-slash": "/" + valid_key[1:],
        "key-padded": valid_key + "=",
    }.items():
        key_headers = [] if raw_key is None else [(b"idempotency-key", raw_key.encode())]
        cases[name] = (one, key_headers, b"", 1)
    cases["query"] = (one, [(b"idempotency-key", valid_key.encode())], b"probe=1", 0)

    app = _mounted(seams)
    for name, (body, key_headers, query, received_count) in cases.items():
        decoder_calls = seams.decoder_calls
        status, payload, receives = await _raw_post(
            app,
            [body],
            [(b"content-type", b"application/json"), *key_headers],
            query_string=query,
        )
        assert (status, payload["error"]["code"]) == (422, "INVALID_REQUEST"), name
        assert set(payload) == {"error"} and set(payload["error"]) == {
            "code",
            "message",
            "butler",
            "details",
        }, name
        details = payload["error"]["details"]
        assert set(details) == _RECEIPT_FIELDS, name
        assert details["complete"] is False, name
        assert details["received_count"] == received_count, name
        assert (
            details["created_count"],
            details["unchanged_count"],
            details["conflict_count"],
            details["invalid_reference_count"],
        ) == (0, 0, 0, 0), name
        assert [
            (audit["outcome"], audit["failure_category"])
            for audit in await _audits(mapping_pool, details["receipt"])
        ] == [("refused", "request_invalid")], name
        for header, value in key_headers:
            assert not await mapping_pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM public.ha_person_mapping_receipts "
                "WHERE key_digest = $1 AND outcome = 'success')",
                _key_digest(value.decode()),
            ), name
        if name == "query":
            assert (receives, seams.decoder_calls) == (0, decoder_calls), name
    assert seams.generic == []
    ha_ids = {
        mapping["ha_person_id"]
        for body, *_ in cases.values()
        for mapping in _decoded_mappings(body)
        if isinstance(mapping.get("ha_person_id"), str)
    }
    assert ha_ids and await _mapped_count(mapping_pool, *ha_ids) == 0


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_mounted_count_and_length_edges_pass_structural_validation(
    mapping_pool, seams
) -> None:
    """Exactly 50 members and a 255-byte identifier clear validation and reach references."""
    seams.manager.serving = mapping_pool
    cases = {
        "edge-50": [(f"person.edge_{i:02d}", str(uuid4())) for i in range(50)],
        "edge-255-bytes": [("person." + "a" * 248, str(uuid4()))],
    }
    assert len(cases["edge-255-bytes"][0][0].encode()) == 255
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_mounted(seams)), base_url=_ORIGIN
    ) as client:
        for name, pairs in cases.items():
            response = await client.post(
                _ROUTE, json=_pairs(*pairs), headers={"Idempotency-Key": _key(name.encode())}
            )
            assert (response.status_code, response.json()["error"]["code"]) == (
                422,
                "INVALID_REFERENCE",
            ), name
            details = response.json()["error"]["details"]
            assert (
                details["complete"],
                details["received_count"],
                details["invalid_reference_count"],
                details["created_count"],
            ) == (False, len(pairs), len(pairs), 0), name
            assert [
                (audit["outcome"], audit["failure_category"])
                for audit in await _audits(mapping_pool, details["receipt"])
            ] == [("refused", "reference_invalid")], name
    assert (
        await _mapped_count(mapping_pool, *(h for pairs in cases.values() for h, _ in pairs)) == 0
    )


def _metric_samples() -> list[tuple[str, dict[str, str], float]]:
    return [
        (sample.name, dict(sample.labels), sample.value)
        for metric in REGISTRY.collect()
        for sample in metric.samples
    ]


@pytest.mark.integration
@pytest.mark.skipif(not _DOCKER_AVAILABLE, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_mounted_outcomes_are_aggregate_replayable_and_sentinel_free(
    mapping_pool, seams, caplog
) -> None:
    """Identifiers never leave the mapping row, across every terminal outcome and capture path.

    The whole-database scan covers receipts, explicit audit, and session/prompt stores; the
    other capture paths are the response, URL, logs, rendered exceptions, metric labels,
    spans, baggage (at span start and at mapping-router log emission), and generic audit.
    Each path is proven live before absence counts.
    """
    from opentelemetry import baggage, context
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.metrics import NoOpMeterProvider
    from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    span_baggage: list[dict[str, object]] = []
    log_baggage: list[dict[str, object]] = []

    class _BaggageAtSpanStart(SpanProcessor):
        def on_start(self, span, parent_context=None) -> None:
            span_baggage.append(dict(baggage.get_all(parent_context or context.get_current())))

    class _BaggageAtLog(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            log_baggage.append(dict(baggage.get_all()))
            return True

    caplog.set_level(logging.DEBUG)
    seams.manager.serving = mapping_pool
    app = _mounted(seams)
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer_provider.add_span_processor(_BaggageAtSpanStart())
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=tracer_provider, meter_provider=NoOpMeterProvider()
    )
    mapping_logger = logging.getLogger(mapping_router.__name__)
    log_filter = _BaggageAtLog()

    token = uuid4().hex[:12]
    probe = {"mapping_probe": f"live-{token}"}
    probe_header = {"baggage": f"mapping_probe=live-{token}"}
    ha_sentinel = f"person.sentinel_{token}"
    entity_sentinel = await _person(mapping_pool, f"mapping-sentinel-{token}")
    other_entity = await _person(mapping_pool, f"mapping-sentinel-peer-{token}")
    refused_ha = f"person.sentinel_refused_{token}"
    refused_entity = str(uuid4())
    key = _key(token.encode())

    def batch(ha_id: str, entity_id: str) -> bytes:
        return json.dumps({"mappings": [{"ha_person_id": ha_id, "entity_id": entity_id}]}).encode()

    mapping_logger.addFilter(log_filter)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=_ORIGIN
        ) as client:

            async def post(body: bytes, idempotency_key: str) -> httpx.Response:
                return await client.post(
                    _ROUTE,
                    content=body,
                    headers={
                        "Idempotency-Key": idempotency_key,
                        "Content-Type": "application/json",
                        **probe_header,
                    },
                )

            created = await post(batch(ha_sentinel, entity_sentinel), key)
            replayed = await post(batch(ha_sentinel, entity_sentinel), key)
            refusals = {
                "IDEMPOTENCY_CONFLICT": await post(batch(refused_ha, other_entity), key),
                "INVALID_REFERENCE": await post(batch(refused_ha, refused_entity), _key(b"s-ref")),
                "MAPPING_CONFLICT": await post(
                    batch(ha_sentinel, other_entity), _key(b"s-conflict")
                ),
            }
            seams.manager.serving = None
            refusals["MAPPING_DATABASE_UNAVAILABLE"] = await post(
                batch(refused_ha, other_entity), _key(b"s-unavailable")
            )
            audit_control = await client.post(
                "/api/home/person-mappings-generic-audit-control", headers=probe_header
            )
    finally:
        mapping_logger.removeFilter(log_filter)
    responses = [created, replayed, *refusals.values()]

    assert created.status_code == 200
    assert set(created.json()) == {"data", "meta"}
    assert set(created.json()["data"]) == _RECEIPT_FIELDS
    assert (created.json()["data"]["complete"], created.json()["data"]["created_count"]) == (
        True,
        1,
    )
    assert replayed.content == created.content
    assert await _mapped_count(mapping_pool, ha_sentinel, refused_ha) == 1
    assert (
        await mapping_pool.fetchval(
            "SELECT count(*) FROM public.ha_person_mapping_receipts WHERE key_digest = $1",
            _key_digest(key),
        )
        == 1
    )
    expected_status = {
        "IDEMPOTENCY_CONFLICT": 409,
        "INVALID_REFERENCE": 422,
        "MAPPING_CONFLICT": 409,
        "MAPPING_DATABASE_UNAVAILABLE": 503,
    }
    for code, response in refusals.items():
        envelope = response.json()
        assert (response.status_code, envelope["error"]["code"]) == (expected_status[code], code)
        assert set(envelope) == {"error"}
        assert set(envelope["error"]) == {"code", "message", "butler", "details"}
        assert set(envelope["error"]["details"]) == _RECEIPT_FIELDS
        assert envelope["error"]["details"]["complete"] is False

    audit_fields = _RECEIPT_FIELDS | {"outcome"}
    created_audits = await _audits(mapping_pool, created.json()["data"]["receipt"])
    assert [set(audit) for audit in created_audits] == [audit_fields, audit_fields]
    for code in ("IDEMPOTENCY_CONFLICT", "INVALID_REFERENCE", "MAPPING_CONFLICT"):
        receipt = refusals[code].json()["error"]["details"]["receipt"]
        assert [set(audit) for audit in await _audits(mapping_pool, receipt)] == [
            audit_fields | {"failure_category"}
        ]

    # Every capture path is live: without these, an empty capture would pass absence checks.
    spans = exporter.get_finished_spans()
    assert any((span.attributes or {}).get("http.route") == _ROUTE for span in spans), [
        dict(span.attributes or {}) for span in spans
    ]
    assert "HA person mapping batch outcome=success" in caplog.text
    # Baggage is context-propagated, not exported on spans: the inbound probe proves both
    # captures see the request's baggage, and the route adds no entry of its own.
    assert span_baggage and log_baggage
    assert probe in span_baggage and probe in log_baggage
    assert all(captured_baggage == probe for captured_baggage in span_baggage + log_baggage), (
        span_baggage,
        log_baggage,
    )
    assert all("baggage" not in response.headers for response in [*responses, audit_control])
    mapping_labels = [
        labels
        for name, labels, _ in _metric_samples()
        if name == "dashboard_ha_person_mapping_batch_total"
    ]
    assert {"outcome": "success", "failure_category": "none"} in mapping_labels
    assert all(set(labels) == {"outcome", "failure_category"} for labels in mapping_labels)
    assert [audit["path"] for audit in seams.generic] == [audit_control.request.url.path]
    assert await _tables_containing(mapping_pool, ha_sentinel) == {
        "connectors.home_assistant_persons"
    }
    assert await _tables_containing(mapping_pool, entity_sentinel) == {
        "connectors.home_assistant_persons",
        "public.entities",
    }

    captured = [
        *(response.text for response in responses),
        *(repr(sorted(response.headers.items())) for response in responses),
        *(str(response.request.url) for response in responses),
        caplog.text,
        *(record.exc_text or "" for record in caplog.records),
        repr([(span.name, dict(span.attributes or {}), span.events) for span in spans]),
        repr(span_baggage),
        repr(log_baggage),
        repr(_metric_samples()),
        repr(seams.generic),
    ]
    for sentinel in (ha_sentinel, entity_sentinel, refused_ha, refused_entity):
        assert not [text for text in captured if sentinel in text], sentinel
    for sentinel in (refused_ha, refused_entity):
        assert await _tables_containing(mapping_pool, sentinel) == set()
