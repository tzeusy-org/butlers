"""Disposable-PG controls for protected receiver recording.

The exact historical writer is safe public repository source, compiled outside
production source identity. Planted chronology and short elapsed receipts are
separate species; this helper never manufactures a three-hour recording claim.
"""

from __future__ import annotations

import asyncio
import hashlib
import types
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg

from butlers.db import register_jsonb_codec
from butlers.tools.switchboard.connector.heartbeat import heartbeat

HISTORICAL_BODY_SHA256 = "d4e6fa6dfcdb8775a83c7ec26bfcb441b2d4f3102d5e704b30535a3ff10798a9"
HISTORICAL_GIT_SHA = "6cfc4eeac9003c0321a892d02b9865113cdccb72"


def _payload(endpoint: str, *, instance: str | None = None) -> dict:
    return {
        "schema_version": "connector.heartbeat.v1",
        "connector": {
            "connector_type": "telegram_bot",
            "endpoint_identity": endpoint,
            "instance_id": instance or str(uuid4()),
            "version": "coverage-control",
        },
        "status": {"state": "healthy", "uptime_s": 1},
        "counters": {
            "messages_ingested": 2,
            "messages_failed": 1,
            "source_api_calls": 3,
            "checkpoint_saves": 0,
            "dedupe_accepted": 0,
        },
        "sent_at": "2050-01-01T00:00:00Z",
    }


async def _setup_runtime(connection):
    # Pool.release() resets session settings. Codec initialization belongs to
    # the physical connection; identity and namespace belong to each checkout.
    await connection.execute('SET ROLE "butler_switchboard_rw"')
    await connection.execute("SET search_path TO switchboard, public")
    await _assert_checkout_session(connection, runtime=True)


async def _setup_admin(connection):
    await connection.execute("RESET ROLE")
    await connection.execute("SET search_path TO switchboard, public")
    await _assert_checkout_session(connection, runtime=False)


async def _assert_checkout_session(connection, *, runtime: bool) -> None:
    principal = (
        "current_user = 'butler_switchboard_rw' AND NOT "
        "(SELECT rolcreaterole FROM pg_roles WHERE rolname=current_user)"
        if runtime
        else "current_user = session_user"
    )
    # Boolean witnesses keep fixture login identity out of diagnostic output.
    assert await connection.fetchval(
        f"SELECT ({principal}) AND "
        "current_schemas(false) = ARRAY['switchboard','public']::name[] AND "
        "to_regclass('connector_registry') = to_regclass('switchboard.connector_registry') AND "
        "to_regclass('connector_heartbeat_log') = "
        "to_regclass('switchboard.connector_heartbeat_log')"
    )
    assert await connection.fetchval("SELECT $1::jsonb", {"checkout_codec": True}) == {
        "checkout_codec": True
    }


async def _exercise_checkout_reuse(pool, *, runtime: bool) -> None:
    """Prove reuse, peer creation and savepoint rollback before business writes."""
    async with pool.acquire() as first:
        first_pid = await first.fetchval("SELECT pg_backend_pid()")
        await first.execute("SET search_path TO pg_catalog")
        await first.execute("RESET ROLE" if runtime else 'SET ROLE "butler_switchboard_rw"')
    async with pool.acquire() as reused:
        assert await reused.fetchval("SELECT pg_backend_pid()") == first_pid
        await _assert_checkout_session(reused, runtime=runtime)
        async with reused.transaction():
            try:
                async with reused.transaction():
                    await reused.execute("SET LOCAL search_path TO pg_catalog")
                    raise RuntimeError("checkout savepoint control")
            except RuntimeError as exc:
                assert str(exc) == "checkout savepoint control"
            await _assert_checkout_session(reused, runtime=runtime)
        # A held checkout forces another physical connection. Its codec and
        # role/path must be initialized without borrowing the first session.
        async with pool.acquire() as peer:
            assert await peer.fetchval("SELECT pg_backend_pid()") != first_pid
            await _assert_checkout_session(peer, runtime=runtime)
            await peer.execute("SET search_path TO pg_catalog")
            await peer.execute("RESET ROLE" if runtime else 'SET ROLE "butler_switchboard_rw"')


def assert_positive_catalog(catalog: list[dict]) -> None:
    """Validate decoded numeric receipts without coercing corrupt JSON leaves."""
    assert catalog and all(type(receipt["oid"]) is int for receipt in catalog)
    assert catalog and all(receipt["oid"] > 0 for receipt in catalog)


async def exercise_recording_boundary(db_url: str, tmp_path: Path) -> None:
    """Keep every causal role/catalog/commit witness inside an existing PG node."""
    from sqlalchemy.engine import make_url

    # DSN moves between processes/libraries without entering receipts or output.
    dsn = make_url(db_url).set(drivername="postgresql").render_as_string(hide_password=False)
    runtime = await asyncpg.create_pool(
        dsn, min_size=1, max_size=3, init=register_jsonb_codec, setup=_setup_runtime
    )
    admin = await asyncpg.create_pool(
        dsn, min_size=1, max_size=3, init=register_jsonb_codec, setup=_setup_admin
    )
    try:
        assert await runtime.fetchval("SELECT current_user") == "butler_switchboard_rw"
        assert await runtime.fetchval(
            "SELECT has_table_privilege(current_user, "
            "'switchboard.connector_heartbeat_log', 'TRIGGER')"
        )
        await runtime.execute(
            "SELECT switchboard.switchboard_connector_heartbeat_log_ensure_partition(clock_timestamp())"
        )
        catalog = await runtime.fetchval("SELECT switchboard.heartbeat_recording_catalog()")
        assert_positive_catalog(catalog)
        await _exercise_checkout_reuse(runtime, runtime=True)
        await _exercise_checkout_reuse(admin, runtime=False)

        payload = _payload("paired-positive")
        ack = await heartbeat(runtime, payload)
        # Separate acquisition: ACK follows durable BOTH-writes readback.
        row = await admin.fetchrow(
            "SELECT last_heartbeat_at, heartbeat_history_coverage "
            "FROM connector_registry WHERE endpoint_identity=$1",
            "paired-positive",
        )
        log = await admin.fetchrow(
            "SELECT received_at, sent_at, recording_xid::text AS xid "
            "FROM connector_heartbeat_log WHERE endpoint_identity=$1",
            "paired-positive",
        )
        assert ack.status == "accepted"
        assert (
            ack.server_time
            == row["last_heartbeat_at"].isoformat()
            == log["received_at"].isoformat()
        )
        assert log["sent_at"].year == 2050 and log["received_at"].year != 2050
        assert log["xid"] and row["heartbeat_history_coverage"]["version"] == 1
        first_start = row["heartbeat_history_coverage"]["coverage_start"]
        await heartbeat(runtime, payload)
        assert (
            await admin.fetchval(
                "SELECT heartbeat_history_coverage->>'coverage_start' "
                "FROM connector_registry WHERE endpoint_identity='paired-positive'"
            )
            == first_start
        )

        # A direct unpaired refresh is compatible but invalidates recording.
        await runtime.execute(
            "UPDATE connector_registry SET last_heartbeat_at=clock_timestamp(), "
            "heartbeat_history_coverage=$1 WHERE endpoint_identity='paired-positive'",
            {"version": 1, "coverage_start": "1900-01-01T00:00:00Z", "partitions": catalog},
        )
        assert (
            await admin.fetchval(
                "SELECT heartbeat_history_coverage FROM connector_registry "
                "WHERE endpoint_identity='paired-positive'"
            )
            is None
        )
        await heartbeat(runtime, payload)
        restart = await admin.fetchval(
            "SELECT heartbeat_history_coverage->>'coverage_start' "
            "FROM connector_registry WHERE endpoint_identity='paired-positive'"
        )
        assert restart > first_start

        # Caller marker-only forgery cannot change a valid boundary.
        await runtime.execute(
            "UPDATE connector_registry SET heartbeat_history_coverage=$1 "
            "WHERE endpoint_identity='paired-positive'",
            {"version": 1, "coverage_start": "1900-01-01T00:00:00Z", "partitions": catalog},
        )
        assert (
            await admin.fetchval(
                "SELECT heartbeat_history_coverage->>'coverage_start' "
                "FROM connector_registry WHERE endpoint_identity='paired-positive'"
            )
            == restart
        )

        # Settings/cursor edits preserve an unchanged heartbeat boundary. A
        # stale matching committed row cannot recertify a later direct refresh.
        await runtime.execute(
            "UPDATE connector_registry SET settings=$1, "
            "checkpoint_cursor='cursor-safe' WHERE endpoint_identity='paired-positive'",
            {"sampling": "unchanged"},
        )
        assert (
            await admin.fetchval(
                "SELECT heartbeat_history_coverage->>'coverage_start' "
                "FROM connector_registry WHERE endpoint_identity='paired-positive'"
            )
            == restart
        )
        stale_at = await admin.fetchval(
            "SELECT min(received_at) FROM connector_heartbeat_log "
            "WHERE endpoint_identity='paired-positive'"
        )
        await runtime.execute(
            "UPDATE connector_registry SET last_heartbeat_at=$1 "
            "WHERE endpoint_identity='paired-positive'",
            stale_at,
        )
        assert (
            await admin.fetchval(
                "SELECT heartbeat_history_coverage FROM connector_registry "
                "WHERE endpoint_identity='paired-positive'"
            )
            is None
        )
        await heartbeat(runtime, payload)
        before_restart = await admin.fetchval(
            "SELECT heartbeat_history_coverage->>'coverage_start' "
            "FROM connector_registry WHERE endpoint_identity='paired-positive'"
        )
        await heartbeat(runtime, _payload("paired-positive"))
        assert (
            await admin.fetchval(
                "SELECT heartbeat_history_coverage->>'coverage_start' "
                "FROM connector_registry WHERE endpoint_identity='paired-positive'"
            )
            > before_restart
        )

        # Direct paired SQL follows log -> registry in the same real xid.
        # Registry-first/history-later work must REFUSE and roll back both
        # writes, including across successful savepoints and session unlocks.
        direct_insert = """INSERT INTO connector_heartbeat_log (
            connector_type,endpoint_identity,instance_id,state,error_message,uptime_s,
            counter_messages_ingested,counter_messages_failed,counter_source_api_calls,
            counter_checkpoint_saves,counter_dedupe_accepted,received_at)
            SELECT connector_type,endpoint_identity,instance_id,state,error_message,uptime_s,
            counter_messages_ingested,counter_messages_failed,counter_source_api_calls,
            counter_checkpoint_saves,counter_dedupe_accepted,clock_timestamp()
            FROM connector_registry WHERE endpoint_identity='paired-positive'
            RETURNING received_at"""
        reversed_key = "hashtextextended('heartbeat-registry-first:telegram_bot:paired-positive',0)"
        own_reversed_marker = (
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
            "AND pid=pg_backend_pid() AND granted AND mode='ExclusiveLock' "
            f"AND objsubid=1 AND ((classid::bigint << 32) | objid::bigint)={reversed_key})"
        )

        async def committed_pair_state():
            # Independent pool/acquisition: no transaction-local readback can
            # masquerade as successful rollback or durable paired acceptance.
            return (
                await admin.fetchval(
                    "SELECT to_jsonb(r) FROM connector_registry r "
                    "WHERE endpoint_identity='paired-positive'"
                ),
                await admin.fetchval(
                    "SELECT count(*) FROM connector_heartbeat_log "
                    "WHERE endpoint_identity='paired-positive'"
                ),
            )

        for reversal in ("ordinary", "session-unlock", "successful-savepoint"):
            before_pair = await committed_pair_state()
            try:
                async with runtime.acquire() as connection:
                    async with connection.transaction():
                        if reversal == "successful-savepoint":
                            async with connection.transaction():
                                await connection.execute(
                                    "UPDATE connector_registry "
                                    "SET last_heartbeat_at=clock_timestamp() "
                                    "WHERE endpoint_identity='paired-positive'"
                                )
                        else:
                            await connection.execute(
                                "UPDATE connector_registry SET last_heartbeat_at=clock_timestamp() "
                                "WHERE endpoint_identity='paired-positive'"
                            )
                        assert await connection.fetchval(own_reversed_marker)
                        if reversal == "session-unlock":
                            await connection.execute("SELECT pg_advisory_unlock_all()")
                            assert await connection.fetchval(own_reversed_marker)
                        await connection.fetchval(direct_insert)
            except asyncpg.ObjectNotInPrerequisiteStateError as exc:
                assert exc.sqlstate == "55000", reversal
                assert exc.message == "heartbeat paired write order invalid", reversal
            else:
                raise AssertionError(f"{reversal}: reversed paired writes committed")
            assert await committed_pair_state() == before_pair, reversal

        # A standalone legacy refresh still commits, clears coverage, and has
        # no transaction marker in a later correctly ordered transaction.
        await runtime.execute(
            "UPDATE connector_registry SET last_heartbeat_at=clock_timestamp() "
            "WHERE endpoint_identity='paired-positive'"
        )
        assert (await committed_pair_state())[0]["heartbeat_history_coverage"] is None
        async with runtime.acquire() as connection:
            async with connection.transaction():
                await connection.fetchval("SELECT switchboard.heartbeat_recording_catalog()")
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended("
                    "'connector-classification:telegram_bot:paired-positive',0))"
                )
                stamp = await connection.fetchval(direct_insert)
                await connection.execute(
                    "UPDATE connector_registry SET last_heartbeat_at=$1 "
                    "WHERE endpoint_identity='paired-positive'",
                    stamp,
                )
        assert (
            await admin.fetchval(
                "SELECT heartbeat_history_coverage->>'version' "
                "FROM connector_registry WHERE endpoint_identity='paired-positive'"
            )
            == "1"
        )

        # A rolled-back savepoint releases the negative-only witness along
        # with its registry mutation. The same outer transaction can then pair
        # correctly; a surviving marker would make this positive fail.
        async with runtime.acquire() as connection:
            async with connection.transaction():
                try:
                    async with connection.transaction():
                        await connection.execute(
                            "UPDATE connector_registry SET last_heartbeat_at=clock_timestamp() "
                            "WHERE endpoint_identity='paired-positive'"
                        )
                        assert await connection.fetchval(own_reversed_marker)
                        raise ValueError("rollback only this test savepoint")
                except ValueError:
                    pass
                assert not await connection.fetchval(own_reversed_marker)
                await connection.fetchval("SELECT switchboard.heartbeat_recording_catalog()")
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended("
                    "'connector-classification:telegram_bot:paired-positive',0))"
                )
                stamp = await connection.fetchval(direct_insert)
                await connection.execute(
                    "UPDATE connector_registry SET last_heartbeat_at=$1 "
                    "WHERE endpoint_identity='paired-positive'",
                    stamp,
                )
        assert (await committed_pair_state())[0]["heartbeat_history_coverage"]["version"] == 1

        # Caller-acquired locks can only deny their own append, never mint
        # coverage. No role/grant change is needed to exercise this negative.
        before_pair = await committed_pair_state()
        try:
            async with runtime.acquire() as connection:
                async with connection.transaction():
                    await connection.execute(f"SELECT pg_advisory_xact_lock({reversed_key})")
                    await connection.fetchval(direct_insert)
        except asyncpg.ObjectNotInPrerequisiteStateError as exc:
            assert exc.sqlstate == "55000"
            assert exc.message == "heartbeat paired write order invalid"
        else:
            raise AssertionError("caller-acquired negative marker minted history")
        assert await committed_pair_state() == before_pair

        # A peer-held negative key cannot make an unpaired update silently
        # omit its witness. Try-lock failure refuses immediately and preserves
        # the committed row; no row -> endpoint blocking wait is introduced.
        before_pair = await committed_pair_state()
        async with admin.acquire() as blocker:
            async with blocker.transaction():
                await blocker.execute(f"SELECT pg_advisory_xact_lock({reversed_key})")
                try:
                    await asyncio.wait_for(
                        runtime.execute(
                            "UPDATE connector_registry SET last_heartbeat_at=clock_timestamp() "
                            "WHERE endpoint_identity='paired-positive'"
                        ),
                        3,
                    )
                except asyncpg.ObjectNotInPrerequisiteStateError as exc:
                    assert exc.sqlstate == "55000"
                    assert exc.message == "heartbeat paired write order unavailable"
                else:
                    raise AssertionError("peer interference omitted the negative witness")
        assert await committed_pair_state() == before_pair

        # A registry-only endpoint does not block or refuse another exact pair
        # in the SAME transaction. Settings/cursor-only edits on the paired
        # endpoint also preserve the positive without making a false marker.
        other_payload = _payload("registry-only-independent")
        await heartbeat(runtime, other_payload)
        async with runtime.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "UPDATE connector_registry SET last_heartbeat_at=clock_timestamp() "
                    "WHERE endpoint_identity='registry-only-independent'"
                )
                await connection.execute(
                    "UPDATE connector_registry SET checkpoint_cursor='pair-after-settings' "
                    "WHERE endpoint_identity='paired-positive'"
                )
                assert not await connection.fetchval(own_reversed_marker)
                await connection.fetchval("SELECT switchboard.heartbeat_recording_catalog()")
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended("
                    "'connector-classification:telegram_bot:paired-positive',0))"
                )
                stamp = await connection.fetchval(direct_insert)
                await connection.execute(
                    "UPDATE connector_registry SET last_heartbeat_at=$1 "
                    "WHERE endpoint_identity='paired-positive'",
                    stamp,
                )
        assert (await committed_pair_state())[0]["heartbeat_history_coverage"]["version"] == 1
        assert (
            await admin.fetchval(
                "SELECT heartbeat_history_coverage FROM connector_registry "
                "WHERE endpoint_identity='registry-only-independent'"
            )
            is None
        )

        # Required registry failure rolls back a successfully attempted append.
        await admin.execute(
            "ALTER TABLE connector_registry ADD CONSTRAINT "
            "coverage_registry_failure CHECK(endpoint_identity <> 'registry-failure')"
        )
        try:
            try:
                await heartbeat(runtime, _payload("registry-failure"))
            except RuntimeError:
                pass
            else:
                raise AssertionError("registry failure returned an ACK")
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM connector_heartbeat_log "
                    "WHERE endpoint_identity='registry-failure'"
                )
                == 0
            )
        finally:
            await admin.execute(
                "ALTER TABLE connector_registry DROP CONSTRAINT coverage_registry_failure"
            )
        await heartbeat(runtime, _payload("registry-failure"))
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM connector_heartbeat_log "
                "WHERE endpoint_identity='registry-failure'"
            )
            == 1
        )

        # Exact source baseline: no shallow-checkout git dependency and no fake
        # production filename (historical source stays outside current src).
        source = Path(__file__).with_name("fixtures") / "heartbeat_pre_atomic_6cfc.py.txt"
        body = source.read_bytes()
        assert hashlib.sha256(body).hexdigest() == HISTORICAL_BODY_SHA256
        materialized = tmp_path / "historical-heartbeat-6cfc.py"
        materialized.write_bytes(body)
        historical = types.ModuleType("historical_heartbeat_control")
        historical.__file__ = str(materialized)
        import sys

        sys.modules[historical.__name__] = historical
        try:
            exec(compile(body, str(materialized), "exec"), historical.__dict__)
            await admin.execute(
                "ALTER TABLE connector_heartbeat_log ADD CONSTRAINT "
                "coverage_failure_control CHECK (endpoint_identity <> 'forced-history-failure')"
            )
            old_ack = await historical.heartbeat(runtime, _payload("forced-history-failure"))
            assert old_ack.status == "accepted"
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM connector_registry "
                    "WHERE endpoint_identity='forced-history-failure'"
                )
                == 1
            )
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM connector_heartbeat_log "
                    "WHERE endpoint_identity='forced-history-failure'"
                )
                == 0
            )
            before = await admin.fetchval(
                "SELECT last_heartbeat_at FROM connector_registry "
                "WHERE endpoint_identity='forced-history-failure'"
            )
            try:
                await heartbeat(runtime, _payload("forced-history-failure"))
            except RuntimeError as exc:
                assert str(exc) == "Failed to persist connector heartbeat"
            else:
                raise AssertionError("failed history transaction returned an ACK")
            assert (
                await admin.fetchval(
                    "SELECT last_heartbeat_at FROM connector_registry "
                    "WHERE endpoint_identity='forced-history-failure'"
                )
                == before
            )
        finally:
            await admin.execute(
                "ALTER TABLE connector_heartbeat_log DROP CONSTRAINT coverage_failure_control"
            )
            sys.modules.pop(historical.__name__, None)
        await heartbeat(runtime, _payload("forced-history-failure"))
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM connector_heartbeat_log "
                "WHERE endpoint_identity='forced-history-failure'"
            )
            == 1
        )

        # Runtime schema CREATE and TRIGGER are REAL allowed privileges. Extra
        # BEFORE, deferred AFTER, disabled and lookalike chains are not trusted.
        await runtime.execute(
            "CREATE FUNCTION switchboard.coverage_hostile_trigger() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN NEW.received_at='1900-01-01'; RETURN NEW; END $$"
        )
        leaf = await admin.fetchval(
            "SELECT tableoid::regclass::text FROM connector_heartbeat_log "
            "WHERE endpoint_identity='paired-positive' LIMIT 1"
        )
        for ddl, undo in (
            (
                "CREATE TRIGGER coverage_hostile BEFORE INSERT ON connector_heartbeat_log "
                "FOR EACH ROW EXECUTE FUNCTION switchboard.coverage_hostile_trigger()",
                "DROP TRIGGER coverage_hostile ON connector_heartbeat_log",
            ),
            (
                f"CREATE CONSTRAINT TRIGGER coverage_hostile AFTER INSERT ON {leaf} "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION switchboard.coverage_hostile_trigger()",
                f"DROP TRIGGER coverage_hostile ON {leaf}",
            ),
        ):
            relation = undo.split(" ON ", 1)[1]
            owner_membership = (
                "SELECT pg_has_role(current_user, "
                "(SELECT relowner FROM pg_class WHERE oid=$1::text::regclass),'MEMBER')"
            )
            trigger_present = (
                "SELECT EXISTS(SELECT 1 FROM pg_trigger "
                "WHERE tgrelid=$1::text::regclass AND tgname='coverage_hostile')"
            )
            # TRIGGER permits creation; removal requires the table owner.
            # The existing fixture login owns these migrated parent/leaf tables.
            assert not await runtime.fetchval(owner_membership, relation)
            assert await admin.fetchval(owner_membership, relation)
            await runtime.execute(ddl)
            try:
                try:
                    await heartbeat(runtime, _payload("interference-refused"))
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("interfering trigger admitted a heartbeat")
                assert (
                    await admin.fetchval(
                        "SELECT count(*) FROM connector_registry "
                        "WHERE endpoint_identity='interference-refused'"
                    )
                    == 0
                )
                try:
                    await runtime.execute(undo)
                except asyncpg.InsufficientPrivilegeError as denied:
                    assert denied.sqlstate == "42501"
                else:
                    raise AssertionError("runtime removed a table-owned hostile trigger")
                assert await admin.fetchval(trigger_present, relation)
            finally:
                # An unexpected successful runtime DROP must leave its own
                # assertion visible, rather than be masked by absent-trigger
                # cleanup. Genuine remaining hostile triggers use owner cleanup.
                if await admin.fetchval(trigger_present, relation):
                    await admin.execute(undo)
            # Independent committed catalog and registered-writer witnesses
            # prove cleanup restored the canonical chain for each species.
            assert not await admin.fetchval(trigger_present, relation)
            restored_endpoint = "restored-hostile-" + (
                "before" if "BEFORE INSERT" in ddl else "deferred"
            )
            restored_ack = await heartbeat(runtime, _payload(restored_endpoint))
            restored_row = await admin.fetchrow(
                "SELECT r.last_heartbeat_at,r.heartbeat_history_coverage,l.received_at "
                "FROM connector_registry r JOIN connector_heartbeat_log l "
                "USING(connector_type,endpoint_identity) WHERE r.endpoint_identity=$1",
                restored_endpoint,
            )
            assert restored_ack.status == "accepted"
            assert restored_row["heartbeat_history_coverage"]["version"] == 1
            assert (
                restored_ack.server_time
                == restored_row["last_heartbeat_at"].isoformat()
                == restored_row["received_at"].isoformat()
            )
        await runtime.execute("DROP FUNCTION switchboard.coverage_hostile_trigger()")
        await admin.execute(
            "ALTER TABLE connector_heartbeat_log DISABLE TRIGGER heartbeat_history_stamp"
        )
        try:
            try:
                await heartbeat(runtime, _payload("disabled-refused"))
            except RuntimeError:
                pass
            else:
                raise AssertionError("disabled chain admitted a heartbeat")
        finally:
            await admin.execute(
                "ALTER TABLE connector_heartbeat_log ENABLE TRIGGER heartbeat_history_stamp"
            )
        await heartbeat(runtime, _payload("restored-positive"))

        # Runtime cannot create TEMP material under the canonical database
        # grants. The existing fixture owner prepares ONE same-session object;
        # its narrow relation ACL is disposable setup, never a runtime grant.
        # Copied trigger functions still cannot mint canonical server evidence.
        async with runtime.acquire() as connection:
            backend_pid = await connection.fetchval("SELECT pg_backend_pid()")
            assert not await connection.fetchval(
                "SELECT has_database_privilege(current_user,current_database(),'TEMP')"
            )
            try:
                await connection.execute(
                    "CREATE TEMP TABLE coverage_lookalike "
                    "(LIKE switchboard.connector_heartbeat_log INCLUDING DEFAULTS)"
                )
            except asyncpg.InsufficientPrivilegeError as denied:
                assert denied.sqlstate == "42501"
            else:
                raise AssertionError("runtime created TEMP material without database permission")
            assert (
                await connection.fetchval("SELECT to_regclass('pg_temp.coverage_lookalike')")
                is None
            )
            await _setup_admin(connection)
            try:
                await connection.execute(
                    "CREATE TEMP TABLE coverage_lookalike "
                    "(LIKE switchboard.connector_heartbeat_log INCLUDING DEFAULTS)"
                )
                assert await connection.fetchval(
                    "SELECT relpersistence='t' AND relnamespace=pg_my_temp_schema() "
                    "AND relowner=(SELECT oid FROM pg_roles WHERE rolname=session_user) "
                    "FROM pg_class WHERE oid='pg_temp.coverage_lookalike'::regclass"
                )
                await connection.execute(
                    "GRANT SELECT, INSERT, TRIGGER ON pg_temp.coverage_lookalike "
                    'TO "butler_switchboard_rw"'
                )
                await _setup_runtime(connection)
                assert await connection.fetchval("SELECT pg_backend_pid()") == backend_pid
                assert not await connection.fetchval(
                    "SELECT has_database_privilege(current_user,current_database(),'TEMP')"
                )
                assert await connection.fetchval(
                    "SELECT has_table_privilege(current_user,'pg_temp.coverage_lookalike', "
                    "'SELECT') AND has_table_privilege(current_user, "
                    "'pg_temp.coverage_lookalike','INSERT') AND has_table_privilege(current_user, "
                    "'pg_temp.coverage_lookalike','TRIGGER') AND NOT has_table_privilege("
                    "current_user,'pg_temp.coverage_lookalike','UPDATE,DELETE,TRUNCATE,REFERENCES') "
                    "AND NOT pg_has_role(current_user, "
                    "(SELECT relowner FROM pg_class "
                    "WHERE oid='pg_temp.coverage_lookalike'::regclass),'MEMBER')"
                )
                await connection.execute(
                    "CREATE TRIGGER lookalike BEFORE INSERT ON coverage_lookalike "
                    "FOR EACH ROW EXECUTE FUNCTION switchboard.stamp_heartbeat_history_row()"
                )
                try:
                    await connection.execute(
                        "INSERT INTO coverage_lookalike (connector_type, "
                        "endpoint_identity,state,received_at) VALUES ('telegram_bot','lookalike','healthy',clock_timestamp())"
                    )
                except asyncpg.InsufficientPrivilegeError as refused:
                    assert refused.sqlstate == "42501"
                else:
                    raise AssertionError("lookalike trigger context minted a receipt")
                assert await connection.fetchval("SELECT count(*) FROM coverage_lookalike") == 0
            finally:
                await _setup_admin(connection)
                try:
                    if await connection.fetchval(
                        "SELECT to_regclass('pg_temp.coverage_lookalike')"
                    ):
                        await connection.execute("DROP TABLE coverage_lookalike")
                finally:
                    await _setup_runtime(connection)
            assert await connection.fetchval("SELECT pg_backend_pid()") == backend_pid
            assert (
                await connection.fetchval("SELECT to_regclass('pg_temp.coverage_lookalike')")
                is None
            )
        lookalike_ack = await heartbeat(runtime, _payload("lookalike-restored-positive"))
        lookalike_row = await admin.fetchrow(
            "SELECT r.last_heartbeat_at,r.heartbeat_history_coverage,l.received_at "
            "FROM connector_registry r JOIN connector_heartbeat_log l "
            "USING(connector_type,endpoint_identity) "
            "WHERE r.endpoint_identity='lookalike-restored-positive'"
        )
        assert lookalike_ack.status == "accepted"
        assert lookalike_row["heartbeat_history_coverage"]["version"] == 1
        assert (
            lookalike_ack.server_time
            == lookalike_row["last_heartbeat_at"].isoformat()
            == lookalike_row["received_at"].isoformat()
        )
        assert not await runtime.fetchval(
            "SELECT has_table_privilege(current_user, "
            "'switchboard.connector_heartbeat_log', 'TRUNCATE')"
        )
        assert not await runtime.fetchval(
            "SELECT pg_has_role(current_user, "
            "(SELECT relowner FROM pg_class WHERE oid='switchboard.connector_heartbeat_log'::regclass),'MEMBER')"
        )

        # A queued writer stamps AFTER endpoint serialization; another endpoint
        # remains usable. These short elapsed receipts are not three-hour proof.
        async with admin.acquire() as blocker:
            async with blocker.transaction():
                await blocker.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended("
                    "'connector-classification:telegram_bot:post-wait',0))"
                )
                queued = asyncio.create_task(heartbeat(runtime, _payload("post-wait")))
                try:
                    await asyncio.sleep(0.05)
                    assert not queued.done()
                    await asyncio.wait_for(heartbeat(runtime, _payload("independent-endpoint")), 3)
                    released_at = await blocker.fetchval("SELECT clock_timestamp()")
                except BaseException:
                    queued.cancel()
                    await asyncio.gather(queued, return_exceptions=True)
                    raise
            queued_ack = await asyncio.wait_for(queued, 3)
        stamped = await admin.fetchval(
            "SELECT received_at FROM connector_heartbeat_log WHERE endpoint_identity='post-wait'"
        )
        assert stamped >= released_at and queued_ack.server_time == stamped.isoformat()

        # Direct child COPY is stamped and never activates a registry row by
        # itself. Runtime UPDATE/DELETE/backdating cannot rewrite old history.
        leaf_name = leaf.split(".")[-1].strip('"')
        await runtime.copy_records_to_table(
            leaf_name,
            schema_name="switchboard",
            columns=["connector_type", "endpoint_identity", "instance_id", "state", "received_at"],
            records=[("telegram_bot", "copy-positive", uuid4(), "error", datetime.now(UTC))],
        )
        assert await admin.fetchval(
            "SELECT recording_xid IS NOT NULL FROM connector_heartbeat_log "
            "WHERE endpoint_identity='copy-positive'"
        )
        for statement in (
            "UPDATE connector_heartbeat_log SET received_at='1900-01-01' WHERE endpoint_identity='copy-positive'",
            "DELETE FROM connector_heartbeat_log WHERE endpoint_identity='copy-positive'",
        ):
            try:
                await runtime.execute(statement)
            except asyncpg.InsufficientPrivilegeError:
                pass
            else:
                raise AssertionError("runtime changed protected history")
        assert (
            await admin.fetchval(
                "SELECT count(*) FROM connector_heartbeat_log "
                "WHERE endpoint_identity='copy-positive'"
            )
            == 1
        )
    finally:
        await runtime.close()
        await admin.close()
