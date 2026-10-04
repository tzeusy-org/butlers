"""Migrated capture authority/admission proof: RFC0037, REQ-general-capture-001/-002."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest

from butlers.core.capture import (
    CaptureUnavailable,
    VerifiedAuthority,
    canonical_intake,
    rotate_epoch,
)
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.general.capture_service import CaptureService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def capture_db_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "general"],
        schemas={"general": "general"},
        revisions={"core": "core_259"},
    )


@pytest.fixture
async def capture_pool(capture_db_url):
    async def init(conn):
        await register_jsonb_codec(conn)

    async def setup(conn):
        await conn.execute("SET ROLE butler_general_rw")
        await conn.execute("SET search_path=general,public")

    pool = await asyncpg.create_pool(capture_db_url, min_size=1, max_size=8, init=init, setup=setup)
    yield pool
    await pool.close()


@pytest.fixture
async def service(capture_pool, tmp_path):
    path = tmp_path / "outside-db-epoch.json"
    epoch = rotate_epoch(path)
    await capture_pool.execute(
        """INSERT INTO public.capture_service_control
           (admitted_epoch,admission_enabled,dispatch_enabled,recovery_required)
           VALUES ($1,true,true,false) ON CONFLICT (singleton) DO UPDATE
           SET admitted_epoch=$1,admission_enabled=true,dispatch_enabled=true,recovery_required=false""",
        epoch.generation,
    )
    return CaptureService(capture_pool, path), epoch


def authority(epoch, text="Synthetic capture", source=None, principal=None):
    return VerifiedAuthority(
        principal or uuid.uuid4(),
        source or uuid.uuid4(),
        datetime.now(UTC),
        epoch.generation,
        canonical_intake(text)[1],
    )


async def test_held_admission_races_and_external_epoch_refuse_old_occurrences(
    service, capture_pool
):
    svc, epoch = service
    auth = authority(epoch)
    first, second = await asyncio.gather(
        svc.admit(auth, "Synthetic capture", mutation_key="same"),
        svc.admit(auth, "Synthetic capture", mutation_key="same"),
    )
    assert first == second and first.disposition == "held"
    # A new service process reads the committed receipt; no target write was called.
    restart = CaptureService(capture_pool, svc.epoch_path)
    assert await restart.admit(auth, "Synthetic capture", mutation_key="same") == first
    changed = VerifiedAuthority(
        auth.principal_id,
        auth.source_occurrence,
        auth.source_occurred_at,
        epoch.generation,
        canonical_intake("Changed")[1],
    )
    with pytest.raises(CaptureUnavailable, match="Capture service unavailable"):
        await restart.admit(changed, "Changed", mutation_key="same")
    distinct = await svc.admit(authority(epoch), "Synthetic capture", mutation_key="same")
    assert distinct.capture_id != first.capture_id
    assert (
        await capture_pool.fetchval(
            "SELECT count(*) FROM public.capture_operations WHERE capture_id=$1", first.capture_id
        )
        == 0
    )
    new_epoch = rotate_epoch(svc.epoch_path)
    with pytest.raises(CaptureUnavailable):
        await restart.admit(auth, "Synthetic capture", mutation_key="same")
    # Even after explicit operator recovery enables the new epoch, an old source
    # occurrence cannot be reminted as a fresh authority after a missing receipt.
    await capture_pool.execute(
        "UPDATE public.capture_service_control SET admitted_epoch=$1", new_epoch.generation
    )
    reminted = VerifiedAuthority(
        auth.principal_id,
        uuid.uuid4(),
        auth.source_occurred_at,
        new_epoch.generation,
        auth.payload_digest,
    )
    with pytest.raises(CaptureUnavailable):
        await restart.admit(reminted, "Synthetic capture", mutation_key="absent-old-snapshot")
    positive = await restart.admit(authority(new_epoch), "Synthetic capture")
    assert positive.disposition == "held"


async def test_effective_role_and_durable_receipt_guards(
    service, capture_pool, capture_db_url, postgres_container
):
    svc, epoch = service
    receipt = await svc.admit(authority(epoch), "Synthetic capture")
    from butlers.testing.migration import (
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url,
    )

    parsed = urlparse(capture_db_url)
    await asyncio.to_thread(
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url(postgres_container, parsed.path.lstrip("/")),
        parsed.username,
    )
    # Role membership alone is not effective current_user. Table owner gets zero
    # rows, and inherited memberships do not turn another runtime into General.
    conn = await asyncpg.connect(capture_db_url)
    try:
        assert await conn.fetchval("SELECT count(*) FROM public.captures") == 0
        for role in await conn.fetch(
            "SELECT rolname FROM pg_roles WHERE rolname ~ '^butler_.+_rw$'"
        ):
            if role["rolname"] == "butler_general_rw":
                continue
            await conn.execute(f'SET ROLE "{role["rolname"]}"')
            try:
                assert await conn.fetchval("SELECT count(*) FROM public.captures") == 0
            except asyncpg.InsufficientPrivilegeError:
                pass
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute("TRUNCATE public.captures CASCADE")
            await conn.execute("RESET ROLE")
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("TRUNCATE public.captures CASCADE")
    finally:
        await conn.close()
    async with capture_pool.acquire() as conn:
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                "UPDATE public.captures SET principal_id=$2 WHERE id=$1",
                receipt.capture_id,
                uuid.uuid4(),
            )
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("DELETE FROM public.captures WHERE id=$1", receipt.capture_id)
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("SELECT public.capture_restore_row('foreign', '{}'::jsonb)")


async def seed_capture_snapshot(db_url: str, epoch_path: Path) -> None:
    """Seed through real internal producers for canonical backup/restore tests."""
    from butlers.migrations import run_migrations
    from butlers.tools.general.vocabulary import collection_declare

    await run_migrations(db_url, chain="general", schema="general")

    async def init(conn):
        await register_jsonb_codec(conn)

    async def setup(conn):
        await conn.execute("SET ROLE butler_general_rw")
        await conn.execute("SET search_path=general,public")

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2, init=init, setup=setup)
    try:
        epoch = rotate_epoch(epoch_path)
        await pool.execute(
            "INSERT INTO public.capture_service_control "
            "(admitted_epoch,admission_enabled,dispatch_enabled,recovery_required) "
            "VALUES ($1,true,true,false) ON CONFLICT(singleton) DO UPDATE SET "
            "admitted_epoch=$1,admission_enabled=true,dispatch_enabled=true,recovery_required=false",
            epoch.generation,
        )
        svc = CaptureService(pool, epoch_path)
        await collection_declare(pool, "notes", "Ordinary notes")
        routed = await svc.admit(authority(epoch), "Synthetic capture")
        await svc.process_one(routed.capture_id, owner="general", kind="note")
        held = await svc.admit(authority(epoch), "Synthetic capture")
        claimed = await svc.claim(held.capture_id, owner="general", kind="note")
        await svc.mark_unknown(claimed.operation_id)
    finally:
        await pool.close()


async def test_empty_rollback_and_nonempty_receipt_replay_refusal(postgres_container):
    from sqlalchemy.exc import DBAPIError

    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core"],
        revisions={"core": "core_259"},
    )
    config = _build_alembic_config(db_url, chains=["core"])
    await asyncio.to_thread(command.downgrade, config, "core_258")
    await asyncio.to_thread(command.upgrade, config, "core_259")
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("SET ROLE butler_general_rw")
        await conn.execute(
            "UPDATE public.capture_service_control SET admitted_epoch=gen_random_uuid()"
        )
    finally:
        await conn.close()
    with pytest.raises(DBAPIError, match="capture evidence retained"):
        await asyncio.to_thread(command.downgrade, config, "core_258")
    # A new schema's historical chain must not remove/reset the shared control.
    await asyncio.to_thread(
        command.upgrade,
        _build_alembic_config(db_url, chains=["core"], schema="capture_replay"),
        "core_259",
    )
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("SET ROLE butler_general_rw")
        assert await conn.fetchval("SELECT count(*) FROM public.capture_service_control") == 1
        assert not await conn.fetchval(
            "SELECT admission_enabled FROM public.capture_service_control"
        )
    finally:
        await conn.close()
