from __future__ import annotations

import asyncio
import base64
import hashlib
import shutil
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alembic import command
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.api.routers.home_person_mappings import (
    MappingBatch,
    _bounded_body,
    _decide_batch,
    _get_db_manager,
    _key_digest,
    router,
)
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

_DOCKER_AVAILABLE = shutil.which("docker") is not None


def _key(seed: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest()).decode().rstrip("=")


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


@pytest_asyncio.fixture(loop_scope="session")
async def mapping_pool(mapping_db_url: str):
    pool = await asyncpg.create_pool(
        mapping_db_url,
        min_size=1,
        max_size=4,
        init=register_jsonb_codec,
    )
    try:
        async with pool.acquire() as connection:
            await connection.execute("TRUNCATE public.ha_person_mapping_receipts")
            await connection.execute("TRUNCATE connectors.home_assistant_persons")
            await connection.execute(
                "DELETE FROM public.audit_log WHERE action = 'home_assistant_person_mapping_batch'"
            )
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


class _ChunkedRequest:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.read_count = 0

    async def stream(self):
        for chunk in self._chunks:
            self.read_count += 1
            yield chunk


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bounded_reader_measures_streamed_octets_and_stops_at_the_limit() -> None:
    accepted = _ChunkedRequest([b"a" * 16_384, b"b" * 16_384])
    assert len(await _bounded_body(accepted)) == 32_768

    oversized = _ChunkedRequest([b"a" * 32_768, b"b", b"private-later-chunk"])
    assert await _bounded_body(oversized) is None
    assert oversized.read_count == 2

    entity_a = str(uuid4())
    entity_b = str(uuid4())
    with pytest.raises(ValueError):
        _batch(("person.duplicate", entity_a), ("person.duplicate", entity_b))
    with pytest.raises(ValueError):
        _batch(("person.first", entity_a), ("person.second", entity_a))


@pytest.mark.unit
def test_api_enforces_raw_body_boundary_without_pool_or_private_echo() -> None:
    class PoolSpy:
        calls = 0

        def pool(self, _name: str):
            self.calls += 1
            raise RuntimeError("synthetic unavailable")

    manager = PoolSpy()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_dashboard_owner_control] = lambda: "owner"
    app.dependency_overrides[_get_db_manager] = lambda: manager
    client = TestClient(app)
    private_sentinel = "person.private_oversize_sentinel"
    body = (
        '{"mappings":[{"ha_person_id":"'
        + private_sentinel
        + '","entity_id":"00000000-0000-4000-8000-000000000001"}]}'
    ).encode()
    body = body + b" " * (32_769 - len(body))

    response = client.post(
        "/api/home/person-mappings",
        content=body,
        headers={"Content-Length": "1", "Idempotency-Key": _key(b"oversize")},
    )

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "REQUEST_BODY_TOO_LARGE",
            "message": "Request body exceeds 32 KiB.",
            "butler": None,
            "details": None,
        }
    }
    assert manager.calls == 0
    assert private_sentinel not in response.text


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
        "SELECT jsonb_agg(to_jsonb(receipt_row))::text "
        "FROM public.ha_person_mapping_receipts AS receipt_row"
    )
    audits = await mapping_pool.fetchval(
        "SELECT jsonb_agg(metadata)::text FROM public.audit_log "
        "WHERE action = 'home_assistant_person_mapping_batch'"
    )
    for private_value in (private_ha, first, "person.second_fixture", second, key):
        assert private_value not in stored
        assert private_value not in audits
    audit_metadata = await mapping_pool.fetchval(
        "SELECT metadata FROM public.audit_log "
        "WHERE action = 'home_assistant_person_mapping_batch' ORDER BY id LIMIT 1"
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
    invalid, _ = await _decide_batch(
        mapping_pool,
        _batch(
            ("person.missing_fixture", str(uuid4())),
            ("person.wrong_type_fixture", str(wrong_type)),
            ("person.tombstoned_fixture", tombstoned),
            ("person.merged_fixture", merged),
        ),
        _key(b"invalid-references"),
        "owner",
    )
    assert invalid.failure_category == "reference_invalid"
    assert invalid.receipt.invalid_reference_count == 4

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
async def test_real_postgres_serializes_competing_batches_and_rolls_back(mapping_pool) -> None:
    shared = await _person(mapping_pool, f"mapping-shared-{uuid4()}")
    first = _batch(("person.concurrent_first", shared))
    second = _batch(("person.concurrent_second", shared))

    decisions = await asyncio.gather(
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
    identical = await asyncio.gather(
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
    divergent = await asyncio.gather(
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

    rollback_entity = await _person(mapping_pool, f"mapping-rollback-{uuid4()}")
    await mapping_pool.execute(
        "CREATE FUNCTION public.reject_mapping_receipt_fixture() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic receipt failure'; END $$"
    )
    await mapping_pool.execute(
        "CREATE TRIGGER reject_mapping_receipt_fixture "
        "BEFORE INSERT ON public.ha_person_mapping_receipts "
        "FOR EACH ROW EXECUTE FUNCTION public.reject_mapping_receipt_fixture()"
    )
    try:
        with pytest.raises(Exception, match="synthetic receipt failure"):
            await _decide_batch(
                mapping_pool,
                _batch(("person.rollback_fixture", rollback_entity)),
                _key(b"rollback"),
                "owner",
            )
    finally:
        await mapping_pool.execute(
            "DROP TRIGGER reject_mapping_receipt_fixture ON public.ha_person_mapping_receipts"
        )
        await mapping_pool.execute("DROP FUNCTION public.reject_mapping_receipt_fixture()")
    assert not await mapping_pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM connectors.home_assistant_persons "
        "WHERE ha_entity_id = 'person.rollback_fixture')"
    )
