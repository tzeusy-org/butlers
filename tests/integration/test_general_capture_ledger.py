"""Migrated capture authority/admission proof: RFC0037, REQ-general-capture-001/-002."""

from __future__ import annotations

import asyncio
import subprocess
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
    load_epoch,
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
        revisions={"core": "core_287"},
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
    refs = [{"source_id": str(uuid.uuid4()), "attachment_id": str(uuid.uuid4())}]
    reference_auth = VerifiedAuthority(
        auth.principal_id,
        uuid.uuid4(),
        datetime.now(UTC),
        epoch.generation,
        canonical_intake("Synthetic capture", refs)[1],
    )
    referenced = await svc.admit(reference_auth, "Synthetic capture", references=refs)
    assert referenced.disposition == "held"
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
    insertion = await capture_pool.fetchval(
        "SELECT to_jsonb(c) FROM public.captures AS c WHERE id=$1", receipt.capture_id
    )
    insertion.update(id=str(uuid.uuid4()), source_occurrence=str(uuid.uuid4()))
    await asyncio.to_thread(
        _bootstrap_migration_prerequisites,
        migration_bootstrap_db_url(postgres_container, parsed.path.lstrip("/")),
        parsed.username,
    )
    # Role membership alone is not effective current_user. Table owner gets zero
    # rows, and inherited memberships do not turn another runtime into General.
    conn = await asyncpg.connect(capture_db_url)
    await register_jsonb_codec(conn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM public.captures") == 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO public.captures SELECT * FROM "
                "jsonb_populate_record(NULL::public.captures,$1)",
                insertion,
            )
        for role in await conn.fetch(
            "SELECT rolname FROM pg_roles WHERE rolname ~ '^butler_.+_rw$'"
        ):
            if role["rolname"] == "butler_general_rw":
                continue
            await conn.execute(f'SET ROLE "{role["rolname"]}"')
            try:
                assert await conn.fetchval("SELECT count(*) FROM public.captures") == 0
                assert (
                    await conn.execute(
                        "UPDATE public.captures SET category='classification_failed'"
                    )
                    == "UPDATE 0"
                )
            except asyncpg.InsufficientPrivilegeError:
                pass
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "INSERT INTO public.captures SELECT * FROM "
                    "jsonb_populate_record(NULL::public.captures,$1)",
                    insertion,
                )
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

    from butlers.tools.general.items import item_create_versioned
    from butlers.tools.general.vocabulary import collection_declare

    await collection_declare(capture_pool, "notes", "Ordinary notes")
    positive_text = 'Synthetic é capture "\\\n'
    positive = await svc.admit(authority(epoch, positive_text), positive_text)
    positive = await svc.process_one(positive.capture_id, owner="general", kind="note")
    genuine = await svc.verify(positive.operation_id)
    assert genuine is not None and genuine["item_id"] == str(positive.operation_id)
    # A real pg_dump restore leaves row_security=off in the session. The
    # invoker importer still applies General FORCE RLS rather than aborting or
    # bypassing it, even when called independently of the scripted transaction.
    async with capture_pool.acquire() as conn, conn.transaction():
        restored_rows = [
            (
                table,
                await conn.fetchval(
                    f"SELECT to_jsonb(t) FROM public.{table} AS t WHERE id=$1", key
                ),
            )
            for table, key in (
                ("captures", positive.capture_id),
                ("capture_operations", positive.operation_id),
            )
        ]
        await conn.execute("SET LOCAL row_security=off")
        for table, row in restored_rows:
            await conn.execute("SELECT public.capture_restore_row($1,$2)", table, row)
    claimed = await svc.claim(receipt.capture_id, owner="general", kind="note")

    # A non-null shape, another operation's genuine locator, stale generation,
    # edited digest or mismatched original content cannot establish owned proof.
    # The genuine service target above makes these refusals non-vacuous.
    for variant in (
        "missing_target",
        "other_operation",
        "generation",
        "digest",
        "content",
        "source_digest",
    ):
        forged = {key: value for key, value in genuine.items() if key != "content"}
        forged.update(operation_id=str(claimed.operation_id), item_id=str(claimed.operation_id))
        with pytest.raises(asyncpg.PostgresError):
            async with capture_pool.acquire() as conn, conn.transaction():
                if variant == "source_digest":
                    parent = await conn.fetchrow(
                        "SELECT id,eligibility_generation FROM collections WHERE name='notes'"
                    )
                    data = {"text": "Synthetic capture", "references": []}
                    await conn.execute(
                        "INSERT INTO collection_items(id,collection_id,data,tags) VALUES ($1,$2,$3,$4)",
                        claimed.operation_id,
                        parent["id"],
                        data,
                        [],
                    )
                    await conn.execute(
                        "INSERT INTO source_versions(item_id,version,collection_id,"
                        "eligibility_generation,operation,digest,content) "
                        "VALUES ($1,1,$2,$3,'create',$4,$5)",
                        claimed.operation_id,
                        parent["id"],
                        parent["eligibility_generation"],
                        "0" * 64,
                        {"collection_id": str(parent["id"]), "data": data, "tags": []},
                    )
                    forged.update(digest="0" * 64, generation=parent["eligibility_generation"])
                elif variant not in {"missing_target", "other_operation"}:
                    _, version = await item_create_versioned(
                        conn,
                        "notes",
                        {
                            "text": "Wrong synthetic content"
                            if variant == "content"
                            else "Synthetic capture",
                            "references": [],
                        },
                        capture_operation_id=claimed.operation_id,
                    )
                    forged.update(digest=version.digest, generation=version.eligibility_generation)
                if variant == "other_operation":
                    forged["item_id"] = genuine["item_id"]
                elif variant == "generation":
                    forged["generation"] += 1
                elif variant == "digest":
                    forged["digest"] = "0" * 64
                await conn.execute(
                    "UPDATE public.capture_operations SET stage='routed',receipt=$2 WHERE id=$1",
                    claimed.operation_id,
                    forged,
                )
                await conn.execute(
                    "UPDATE public.captures SET disposition='routed',category='routed',receipt=$2 "
                    "WHERE id=$1",
                    receipt.capture_id,
                    forged,
                )
        assert await svc.verify(claimed.operation_id) is None
        assert (await svc.verify(positive.operation_id)) is not None
        assert (
            await capture_pool.fetchval(
                "SELECT stage FROM public.capture_operations WHERE id=$1", claimed.operation_id
            )
            == "claimed"
        )
        assert (
            await capture_pool.fetchval(
                "SELECT count(*) FROM collection_items WHERE id=$1", claimed.operation_id
            )
            == 0
        )

    # Import accepts captures before operations within a complete transaction,
    # but null or absent operation proof must fail before commit.
    template = await capture_pool.fetchval(
        "SELECT to_jsonb(c) FROM public.captures AS c WHERE id=$1", receipt.capture_id
    )
    for detached in (None, str(uuid.uuid4())):
        incoming = {
            **template,
            "id": str(uuid.uuid4()),
            "mutation_key": None,
            "disposition": "routed",
            "category": "routed",
            "operation_id": detached,
            "receipt": forged,
        }
        with pytest.raises(asyncpg.PostgresError):
            async with capture_pool.acquire() as conn, conn.transaction():
                await conn.execute("SELECT public.capture_restore_row('captures',$1)", incoming)


async def seed_capture_snapshot(db_url: str, epoch_path: Path, *, historical: bool = False) -> None:
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
        if historical:
            from butlers.tools.general.items import item_delete

            deleted = await svc.admit(authority(epoch), "Synthetic capture")
            deleted = await svc.process_one(deleted.capture_id, owner="general", kind="note")
            await item_delete(pool, deleted.operation_id)
            await collection_declare(pool, "preferences", "Ordinary preferences")
            private = await svc.admit(authority(epoch), "Synthetic capture")
            private = await svc.process_one(private.capture_id, owner="general", kind="preference")
            assert await svc.verify(private.operation_id) is not None
            await pool.execute(
                "UPDATE collections SET custody_private=true WHERE name='preferences'"
            )
            assert await svc.verify(private.operation_id) is None
            assert await svc.verify(deleted.operation_id) is None
        held = await svc.admit(authority(epoch), "Synthetic capture")
        claimed = await svc.claim(held.capture_id, owner="general", kind="note")
        await svc.mark_unknown(claimed.operation_id)
    finally:
        await pool.close()


async def test_fixed_general_snapshot_imports_into_a_new_target(postgres_container, tmp_path):
    """Schema transport is setup; row transport exercises the real General importer.

    A migrated target seeds its own NULL-epoch control row. A schema-only restore,
    as used by the backup data exclusions, instead starts with no control data.
    This carrier creates that genuinely empty target, without deleting evidence,
    copying source rows as an administrator, or replaying into existing rows.
    The full checked-in dump/restore script positives remain separate carriers.
    """
    from butlers.testing.migration import migration_bootstrap_db_url

    source_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core", "general"],
        schemas={"general": "general"},
        revisions={"core": "core_287"},
    )
    epoch_path = tmp_path / "source-epoch.json"
    await seed_capture_snapshot(source_url, epoch_path)
    source_name = urlparse(source_url).path.lstrip("/")
    target_name = migration_db_name()
    container_id = postgres_container.get_wrapped_container().id
    bootstrap = urlparse(migration_bootstrap_db_url(postgres_container, "postgres"))
    admin_url = bootstrap._replace(scheme="postgresql").geturl()
    admin = await asyncpg.connect(admin_url)
    try:
        owner = urlparse(source_url).username
        # Both identifiers come from migration_db_name/create_migrated_test_db.
        await admin.execute(f'CREATE DATABASE "{target_name}" OWNER "{owner}"')
        schema = await asyncio.to_thread(
            subprocess.run,
            [
                "docker",
                "exec",
                container_id,
                "pg_dump",
                "-U",
                bootstrap.username,
                "-d",
                source_name,
                "--schema-only",
                "--clean",
                "--if-exists",
            ],
            capture_output=True,
            timeout=120,
        )
        assert schema.returncode == 0, "fixed source schema export failed"
        installed = await asyncio.to_thread(
            subprocess.run,
            [
                "docker",
                "exec",
                "-i",
                container_id,
                "psql",
                "-U",
                bootstrap.username,
                "-d",
                target_name,
                "-X",
                "-q",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            input=schema.stdout,
            capture_output=True,
            timeout=120,
        )
        assert installed.returncode == 0, "new target schema installation failed"

        source = await asyncpg.connect(source_url)
        target = await asyncpg.connect(
            urlparse(source_url)._replace(path=f"/{target_name}").geturl()
        )
        try:
            for conn in (source, target):
                await register_jsonb_codec(conn)
                await conn.execute("SET ROLE butler_general_rw")
                await conn.execute("SET search_path=general,public")
            assert await target.fetchval("SELECT current_user") == "butler_general_rw"
            assert await target.fetchval("SELECT count(*) FROM public.captures") == 0
            assert await target.fetchval("SELECT count(*) FROM public.capture_operations") == 0
            assert await target.fetchval("SELECT count(*) FROM public.capture_service_control") == 0
            ordinary = (
                "general.collections",
                "general.collection_vocabulary",
                "general.collection_vocabulary_keys",
                "general.collection_items",
                "general.source_versions",
            )
            ledger = (
                "public.captures",
                "public.capture_operations",
                "public.capture_service_control",
            )
            exported = {}
            async with source.transaction(isolation="repeatable_read", readonly=True):
                snapshot = await source.fetchval("SELECT pg_export_snapshot()")
                assert snapshot
                for table in (*ordinary, *ledger):
                    exported[table] = [
                        row["payload"]
                        for row in await source.fetch(
                            f"SELECT to_jsonb(t) AS payload FROM {table} t ORDER BY to_jsonb(t)::text"
                        )
                    ]
                async with target.transaction():
                    await target.execute("SET LOCAL row_security=on")
                    for table in ordinary:
                        # Generated vocabulary keys must be computed on the target.
                        columns = await source.fetchval(
                            "SELECT string_agg(quote_ident(attname),',' ORDER BY attnum) "
                            "FROM pg_attribute WHERE attrelid=$1::regclass AND attnum>0 "
                            "AND NOT attisdropped AND attgenerated=''",
                            table,
                        )
                        for payload in exported[table]:
                            await target.execute(
                                f"INSERT INTO {table} ({columns}) SELECT {columns} FROM "
                                f"jsonb_populate_record(NULL::{table},$1)",
                                payload,
                            )
                    for table in ledger:
                        for payload in exported[table]:
                            await target.execute(
                                "SELECT public.capture_restore_row($1,$2)",
                                table.removeprefix("public."),
                                payload,
                            )
                for table in (*ordinary, *ledger[:-1]):
                    assert [
                        row["payload"]
                        for row in await target.fetch(
                            f"SELECT to_jsonb(t) AS payload FROM {table} t ORDER BY to_jsonb(t)::text"
                        )
                    ] == exported[table]
            assert exported["public.captures"] and exported["public.capture_operations"]
            control = await target.fetchval(
                "SELECT to_jsonb(t) FROM public.capture_service_control t"
            )
            assert control == {
                **exported["public.capture_service_control"][0],
                "admission_enabled": False,
                "dispatch_enabled": False,
                "recovery_required": True,
            }
            assert await target.fetchval(
                "SELECT bool_and(relrowsecurity AND relforcerowsecurity) FROM pg_class "
                "WHERE oid=ANY($1::regclass[])",
                list(ledger),
            )
            routed = next(
                row for row in exported["public.captures"] if row["disposition"] == "routed"
            )

            async def setup(conn):
                await conn.execute("SET ROLE butler_general_rw")
                await conn.execute("SET search_path=general,public")

            runtime = await asyncpg.create_pool(
                urlparse(source_url)._replace(path=f"/{target_name}").geturl(),
                min_size=1,
                max_size=1,
                init=register_jsonb_codec,
                setup=setup,
            )
            try:
                target_service = CaptureService(runtime, epoch_path)
                assert await target_service.verify(uuid.UUID(routed["operation_id"])) is not None
                # Restored disablement is real, even with the original external epoch.
                with pytest.raises(CaptureUnavailable):
                    await target_service.admit(
                        authority(load_epoch(epoch_path)), "Synthetic capture"
                    )
            finally:
                await runtime.close()
            # A conflicting replay rolls back rather than replacing original evidence.
            changed = {**routed, "payload_digest": "0" * 64}
            with pytest.raises(asyncpg.PostgresError):
                async with target.transaction():
                    await target.execute(
                        "SELECT public.capture_restore_row('captures',$1)", changed
                    )
            assert (
                await target.fetchval(
                    "SELECT payload_digest FROM public.captures WHERE id=$1",
                    uuid.UUID(routed["id"]),
                )
                == routed["payload_digest"]
            )
        finally:
            await source.close()
            await target.close()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{target_name}" WITH (FORCE)')
        await admin.close()


async def test_empty_rollback_and_nonempty_receipt_replay_refusal(postgres_container):
    from sqlalchemy.exc import DBAPIError

    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core"],
        revisions={"core": "core_287"},
    )
    config = _build_alembic_config(db_url, chains=["core"])
    await asyncio.to_thread(command.downgrade, config, "core_282")
    await asyncio.to_thread(command.upgrade, config, "core_287")
    sibling = _build_alembic_config(db_url, chains=["core"], target_schema="capture_replay")
    await asyncio.to_thread(command.upgrade, sibling, "core_287")
    await asyncio.to_thread(command.downgrade, config, "core_282")
    await asyncio.to_thread(command.downgrade, sibling, "core_282")
    await asyncio.to_thread(command.upgrade, config, "core_287")
    # A missing sibling table cannot erase the still-present control record.
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("DROP TABLE public.capture_operations")
    finally:
        await conn.close()
    with pytest.raises(DBAPIError, match="partial ledger"):
        await asyncio.to_thread(command.downgrade, config, "core_282")
    # Reinstall the empty missing table through the real installer, preserving
    # the existing control. A restrictive policy must not hide evidence either.
    await asyncio.to_thread(command.upgrade, sibling, "core_287")
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            "CREATE POLICY capture_hidden ON public.capture_service_control AS RESTRICTIVE "
            "FOR SELECT USING (false)"
        )
    finally:
        await conn.close()
    with pytest.raises(DBAPIError, match="General policy proof invalid"):
        await asyncio.to_thread(command.downgrade, config, "core_282")
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("DROP POLICY capture_hidden ON public.capture_service_control")
        await conn.execute("SET ROLE butler_general_rw")
        await conn.execute(
            "UPDATE public.capture_service_control SET admitted_epoch=gen_random_uuid()"
        )
    finally:
        await conn.close()
    with pytest.raises(DBAPIError, match="capture evidence retained"):
        await asyncio.to_thread(command.downgrade, config, "core_282")
    # A new schema's historical chain must not remove/reset the shared control.
    await asyncio.to_thread(
        command.upgrade,
        _build_alembic_config(db_url, chains=["core"], target_schema="capture_evidence_replay"),
        "core_287",
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
