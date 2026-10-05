"""Real PostgreSQL candidate conformance, not collected-origin or G proof.

Complete supplied cohort/isolation claims are synthetic trusted inputs. The
unchanged bootstrap is replayed plus narrowing in an explicit harness-only
transaction; no actual production bootstrap hook/default-head wiring is added.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from butlers.migrations import run_migrations
from butlers.relationship_temporal_cutover import (
    _GUARD_BODY,
    CandidateRefusal,
    candidate_session_challenge,
    consume_candidate_binding,
    create_candidate_database,
    issue_candidate_binding,
    narrow_candidate_privileges,
    validate_candidate_proof,
)
from butlers.testing.migration import (
    _create_test_migration_role,
    _upgrade_chain_to_revision,
    bootstrap_extensions,
    create_migrated_test_db,
    init_db_sql_for_dbapi,
    migration_db_name,
)

pytestmark = pytest.mark.integration
_TABLE = "relationship.temporal_cutover_consumptions"
_FUNCTION = "relationship.temporal_cutover_consume_guard"


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_encoded(value).encode()).hexdigest()


def _proof():
    """Full input shape; all proof/isolation/cohort claims are synthetic."""
    authorization, fence = str(uuid.uuid4()), str(uuid.uuid4())
    receipt = {
        "schema": "butlers.relationship-temporal-cutover-tests/v1",
        "target_sha": "a" * 40,
        "node_ids_digest": "b" * 64,
        "log_digest": "c" * 64,
        "verdict": "PASS",
        "authorization_id": authorization,
        "fence_id": fence,
    }
    inventory = [
        {
            "path": "src/butlers/synthetic.py",
            "qualname": "synthetic",
            "classification": "synthetic conformance input",
            "file_sha256": "d" * 64,
        }
    ]
    return {
        "target_sha": "a" * 40,
        "target_image_id": "sha256:" + "e" * 64,
        "roster_tree_id": "f" * 40,
        "compose_invocation": {
            "row": "dev-launcher",
            "files": ["docker-compose.yml"],
            "project": "butlers-dev",
            "env_file": ".env.dev",
            "profiles": ["dev"],
        },
        "compose_config_digest": "1" * 64,
        "instances": [],
        "credentialed_services": ["migrations", "butlers-up"],
        "mutator_inventory": inventory,
        "mutator_inventory_digest": _digest(inventory),
        "test_receipt": receipt,
        "test_receipt_digest": _digest(receipt),
        "fence": {
            "fence_id": fence,
            "authorization_id": authorization,
            "project": "butlers-dev",
            "target_sha": "a" * 40,
            "target_image_id": "sha256:" + "e" * 64,
            "generation": 1,
            "phase": "quiesced",
            "set_time": 1,
        },
        "isolated_test_runtime": {
            "source_tree_id": "2" * 40,
            "test_image_digest": "3" * 64,
            "dependencies_digest": "4" * 64,
            "own_runtime": True,
            "host_daemon_denied": True,
            "key_denied": True,
            "log_write_denied": True,
            "target_tcp_denied": True,
            "target_unix_denied": True,
            "offline": True,
            "cleaned": True,
        },
        "never_released": True,
    }


@dataclass(repr=False)
class CandidateDB:
    name: str
    migration_role: str
    migration: Engine
    bootstrap: Engine
    control: Engine
    birth: dict


def _execute_bootstrap_sql(conn):
    # The existing migration bootstrap also uses the raw DBAPI cursor. Omitting
    # the parameter argument preserves PostgreSQL format('%I', ...) literally;
    # SQLAlchemy's empty parameter mapping makes psycopg2 reinterpret it.
    with conn.connection.cursor() as cursor:
        cursor.execute(init_db_sql_for_dbapi())


def _bootstrap(db, *, narrow=True, after_grants=None):
    """Actual init-db SQL and explicit candidate hook, one transaction."""
    with db.bootstrap.begin() as conn:
        conn.execute(
            text("SELECT set_config('butlers.connecting_user', :role, true)"),
            {"role": db.migration_role},
        )
        _execute_bootstrap_sql(conn)
        if after_grants:
            after_grants(conn)
        if narrow:
            narrow_candidate_privileges(conn, db.migration_role)


def _new_candidate(postgres_container):
    admin_url = postgres_container.get_connection_url()
    name = migration_db_name()
    role, password = f"migration_{name}", uuid.uuid4().hex
    _create_test_migration_role(admin_url, role, password)
    control = create_engine(admin_url, poolclass=NullPool)
    birth = create_candidate_database(control, name, role)
    bootstrap_url = control.url.set(database=name)
    bootstrap_extensions(bootstrap_url.render_as_string(hide_password=False))
    migration_url = URL.create(
        "postgresql",
        username=role,
        password=password,
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        database=name,
    )
    return CandidateDB(
        name,
        role,
        create_engine(migration_url, poolclass=NullPool),
        create_engine(bootstrap_url, poolclass=NullPool),
        control,
        birth,
    )


def _drop(db):
    db.migration.dispose()
    db.bootstrap.dispose()
    quote = db.control.dialect.identifier_preparer.quote_identifier
    with db.control.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {quote(db.name)} WITH (FORCE)")
        conn.exec_driver_sql(f"DROP ROLE IF EXISTS {quote(db.migration_role)}")
    db.control.dispose()


def _migrate(db, *, partial=False):
    url = db.migration.url.render_as_string(hide_password=False)
    asyncio.run(run_migrations(url, chain="core"))
    if partial:
        _upgrade_chain_to_revision(
            url, chain="relationship", schema="relationship", revision="rel_034"
        )
    else:
        asyncio.run(run_migrations(url, chain="relationship", schema="relationship"))


@pytest.fixture(scope="module")
def candidate_db(postgres_container):
    # Existing normal-login factory, actual bootstrap, actual full chains.
    db = _new_candidate(postgres_container)
    try:
        _bootstrap(db)
        _migrate(db)
        # Genuine normal chain cannot silently re-widen a protected object.
        with db.migration.connect() as conn:
            assert not conn.execute(
                text(f"SELECT has_table_privilege('butler_relationship_rw', '{_TABLE}', 'UPDATE')")
            ).scalar_one()
        yield db
    finally:
        _drop(db)


@contextmanager
def _bound(db, proof=None):
    proof = proof or _proof()
    with db.migration.begin() as conn:
        challenge = candidate_session_challenge(conn)
        with db.bootstrap.begin() as issuer:
            binding = issue_candidate_binding(issuer, db.birth, challenge, proof)
        # NullPool actually closes the issuer before ordinary consumption.
        yield conn, binding, proof


def _read(db, statement, params=None):
    with db.migration.connect() as conn:
        return conn.execute(text(statement), params or {}).scalar_one()


def _consumed(db, binding):
    return _read(
        db,
        f"SELECT count(*) FROM {_TABLE} WHERE authorization_id=:id",
        {"id": binding["authorization_id"]},
    )


def _comment(db, value, *, birth=False):
    with db.bootstrap.begin() as conn:
        target = f"TABLE {_TABLE}" if birth else f"FUNCTION {_FUNCTION}()"
        conn.execute(text(f"COMMENT ON {target} IS :comment"), {"comment": _encoded(value)})


def _assert_source_creation_and_actual_normal_role_catalog_visibility(candidate_db):
    db = candidate_db
    with pytest.raises(CandidateRefusal, match="^candidate_creation_refused$"):
        create_candidate_database(db.control, db.name, db.migration_role)
    with pytest.raises(CandidateRefusal, match="^candidate_issuer_untrusted$"):
        create_candidate_database(db.migration, migration_db_name(), db.migration_role)
    with db.migration.begin() as conn:
        owner, superuser, createdb = conn.execute(
            text(
                "SELECT d.datdba=r.oid, r.rolsuper, r.rolcreatedb "
                "FROM pg_database d, pg_roles r "
                "WHERE d.datname=current_database() AND r.rolname=current_user"
            )
        ).one()
        assert owner and not superuser and not createdb
        for role in [db.migration_role, "butler_relationship_rw"]:
            conn.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
            assert (
                conn.execute(
                    text("SELECT system_identifier::text FROM pg_control_system()")
                ).scalar_one()
                == db.birth["system_identifier"]
            )
            assert conn.execute(
                text(f"SELECT obj_description('{_TABLE}'::regclass, 'pg_class')")
            ).scalar_one() == _encoded(db.birth)


def _assert_actual_migration_session_consumes_once_and_replay_is_atomic(candidate_db):
    with _bound(candidate_db) as (conn, binding, proof):
        consume_candidate_binding(conn, binding, proof)
        with pytest.raises(CandidateRefusal, match="^candidate_replay_refused$"):
            with conn.begin_nested():
                consume_candidate_binding(conn, binding, proof)
    assert _consumed(candidate_db, binding) == 1


def test_consume_index_and_synthetic_stamp_share_real_rollback(candidate_db):
    db = candidate_db
    old_stamp = _read(db, "SELECT version_num FROM relationship.alembic_version")
    index = _read(
        db,
        "SELECT indexdef FROM pg_indexes WHERE schemaname='relationship' "
        "AND indexname='uq_ef_spo_active'",
    )
    # Plant actual migrated all-validity rows and evidence: an empty relation
    # could not demonstrate preservation. This is SQL protocol evidence only,
    # not a central-writer or approval-replay test.
    subject = str(uuid.uuid4())
    with db.migration.begin() as conn:
        conn.execute(
            text("INSERT INTO public.entities (id,canonical_name) VALUES (:id,:name)"),
            {"id": subject, "name": "synthetic candidate " + subject},
        )
        for validity in ["active", "retracted", "superseded"]:
            fact_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO relationship.entity_facts "
                    "(id,subject,predicate,object,object_kind,src,validity) "
                    "VALUES (:id,:subject,'candidate-protocol',:object,'literal',"
                    "'synthetic-conformance',:validity)"
                ),
                {"id": fact_id, "subject": subject, "object": validity, "validity": validity},
            )
            conn.execute(
                text(
                    "INSERT INTO relationship.fact_evidence "
                    "(fact_id,seq,kind,ref,note,src,origin) "
                    "VALUES (:id,1,'text','synthetic','protocol fixture',"
                    "'synthetic-conformance','direct')"
                ),
                {"id": fact_id},
            )

    def history_snapshot():
        return _read(
            db,
            "SELECT jsonb_agg(to_jsonb(f) ORDER BY f.id) "
            "FROM relationship.entity_facts f WHERE subject=:id",
            {"id": subject},
        ), _read(
            db,
            "SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id) "
            "FROM relationship.fact_evidence e JOIN relationship.entity_facts f "
            "ON f.id=e.fact_id WHERE f.subject=:id",
            {"id": subject},
        )

    history = history_snapshot()
    assert len(history[0]) == len(history[1]) == 3
    # Harness marker in real stamp table, never a reserved/executed G.
    with pytest.raises(RuntimeError, match="^synthetic_after_stamp$"):
        with _bound(db) as (conn, binding, proof):
            consume_candidate_binding(conn, binding, proof)
            conn.exec_driver_sql("DROP INDEX relationship.uq_ef_spo_active")
            conn.execute(
                text("UPDATE relationship.alembic_version SET version_num=:stamp"),
                {"stamp": "candidate_feasibility"},
            )
            raise RuntimeError("synthetic_after_stamp")
    assert _consumed(db, binding) == 0
    assert _read(db, "SELECT version_num FROM relationship.alembic_version") == old_stamp
    assert history_snapshot() == history
    assert _read(db, "SELECT to_regclass('relationship.uq_ef_spo_active') IS NOT NULL")
    with _bound(db) as (conn, binding, proof):
        consume_candidate_binding(conn, binding, proof)
        conn.exec_driver_sql("DROP INDEX relationship.uq_ef_spo_active")
        conn.execute(
            text("UPDATE relationship.alembic_version SET version_num=:stamp"),
            {"stamp": "candidate_feasibility"},
        )
    assert _consumed(db, binding) == 1
    assert (
        _read(db, "SELECT version_num FROM relationship.alembic_version") == "candidate_feasibility"
    )
    assert not _read(db, "SELECT to_regclass('relationship.uq_ef_spo_active') IS NOT NULL")
    assert history_snapshot() == history
    # Only disposable state is restored; committed consumption stays intact.
    with db.migration.begin() as conn:
        conn.exec_driver_sql(index)
        conn.execute(
            text("UPDATE relationship.alembic_version SET version_num=:stamp"), {"stamp": old_stamp}
        )
        # Existing migration/database-owner authority can erase its own fact
        # history and the real evidence cascade. The protected ledger does not
        # claim to detect this trusted historical-erasure residual.
        deleted = conn.execute(
            text(
                "DELETE FROM relationship.entity_facts WHERE subject=:id AND validity='retracted'"
            ),
            {"id": subject},
        ).rowcount
        assert deleted == 1
    remaining = history_snapshot()
    assert len(remaining[0]) == len(remaining[1]) == 2
    assert _consumed(db, binding) == 1


@pytest.mark.parametrize("role", [None, "butler_relationship_rw"])
def test_normal_database_owner_and_own_role_cannot_forge_protected_objects(candidate_db, role):
    db = candidate_db
    attacks = [
        f"COMMENT ON TABLE {_TABLE} IS '{{}}'",
        f"COMMENT ON FUNCTION {_FUNCTION}() IS '{{}}'",
        f"ALTER TABLE {_TABLE} OWNER TO {db.migration_role}",
        f"ALTER TABLE {_TABLE} DISABLE TRIGGER ALL",
        f"ALTER TABLE {_TABLE} DROP CONSTRAINT temporal_cutover_consumptions_pkey",
        f"DROP TABLE {_TABLE}",
        f"DROP FUNCTION {_FUNCTION}() CASCADE",
        "DROP SCHEMA relationship CASCADE",
        f"TRUNCATE {_TABLE}",
        f"UPDATE {_TABLE} SET proof_digest=repeat('0',64)",
        f"DELETE FROM {_TABLE}",
        f"CREATE TRIGGER attacker BEFORE INSERT ON {_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()",
        f"CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger "
        "LANGUAGE plpgsql AS $$BEGIN RETURN NEW; END$$",
        "SET LOCAL session_replication_role=replica",
    ]
    with db.migration.begin() as conn:
        if role:
            conn.exec_driver_sql(f"SET LOCAL ROLE {role}")
        # CREATE/DML positive witnesses the actor's genuine local authority.
        conn.exec_driver_sql("CREATE TABLE relationship.candidate_actor_positive (value integer)")
        conn.exec_driver_sql("INSERT INTO relationship.candidate_actor_positive VALUES (1)")
        assert (
            conn.exec_driver_sql(
                "SELECT value FROM relationship.candidate_actor_positive"
            ).scalar_one()
            == 1
        )
        conn.exec_driver_sql("DROP TABLE relationship.candidate_actor_positive")
        for attack in attacks:
            with pytest.raises(DBAPIError) as refused:
                with conn.begin_nested():
                    conn.exec_driver_sql(attack)
            assert refused.value.orig.pgcode == "42501", attack.split()[0]
    assert _read(db, f"SELECT obj_description('{_TABLE}'::regclass,'pg_class')") == _encoded(
        db.birth
    )


def _assert_every_missing_claim_and_wrong_expected_binding_refuses(candidate_db):
    proof = _proof()
    for key in proof:
        incomplete = {k: v for k, v in proof.items() if k != key}
        with pytest.raises(CandidateRefusal, match="^candidate_proof_incomplete$"):
            validate_candidate_proof(incomplete)
    with _bound(candidate_db, proof) as (conn, binding, _):
        # Bypass the Python comparison as an adversarial ordinary SQL writer.
        # Every supplied claim remains coupled by the real protected trigger;
        # these synthetic values receive no collected-origin credit.
        for key in proof:
            mismatch = copy.deepcopy(proof) | {key: None}
            with pytest.raises(DBAPIError) as refused:
                with conn.begin_nested():
                    conn.exec_driver_sql("SET LOCAL ROLE butler_relationship_rw")
                    conn.execute(
                        text(
                            f"INSERT INTO {_TABLE} "
                            "(authorization_id,birth_id,fence_id,proof_digest,proof) "
                            "VALUES (:authorization_id,:birth_id,:fence_id,"
                            ":proof_digest,CAST(:proof AS jsonb))"
                        ),
                        binding | {"proof": _encoded(mismatch)},
                    )
            assert refused.value.orig.diag.message_primary == "candidate_binding_mismatch"
        for key, value in [
            ("target_sha", "b" * 40),
            ("roster_tree_id", "e" * 40),
            ("compose_config_digest", "2" * 64),
            ("target_image_id", "sha256:" + "f" * 64),
        ]:
            mismatch = copy.deepcopy(proof)
            mismatch[key] = value
            # Keep the supplied shape internally valid so this control reaches
            # the exact protected-binding comparison, not a parser rejection.
            if key in {"target_sha", "target_image_id"}:
                mismatch["fence"][key] = value
            if key == "target_sha":
                mismatch["test_receipt"][key] = value
                mismatch["test_receipt_digest"] = _digest(mismatch["test_receipt"])
            with pytest.raises(CandidateRefusal, match="^candidate_proof_mismatch$"):
                consume_candidate_binding(conn, binding, mismatch)
        consume_candidate_binding(conn, binding, proof)
    assert _consumed(candidate_db, binding) == 1


def test_actual_binding_revision_fence_expiry_and_full_xid_controls(candidate_db):
    _assert_every_missing_claim_and_wrong_expected_binding_refuses(candidate_db)
    db = candidate_db
    mutations = [
        ("purpose", "rollback_before_first_temporal_write", "candidate_binding_mismatch"),
        ("schema_name", "public", "candidate_binding_mismatch"),
        ("birth_id", str(uuid.uuid4()), "candidate_binding_mismatch"),
        ("fence_id", str(uuid.uuid4()), "candidate_binding_mismatch"),
        ("revision", "rel_000", "candidate_revision_mismatch"),
        ("backend_pid", -1, "candidate_session_mismatch"),
        ("backend_start", "2000-01-01 00:00:00+00", "candidate_session_mismatch"),
        ("expires_at", 0, "candidate_binding_expired"),
        ("issued_at", 9223372036854775000, "candidate_binding_expired"),
    ]
    with _bound(db) as (conn, binding, proof):
        mutations.append(
            ("full_xid", str(int(binding["full_xid"]) + 2**32), "candidate_session_mismatch")
        )
        for key, value, code in mutations:
            _comment(db, dict(binding) | {key: value})
            with pytest.raises(CandidateRefusal, match=f"^{code}$"):
                with conn.begin_nested():
                    consume_candidate_binding(conn, binding, proof)
                    # Observe only after the invalid consume has returned, so
                    # this read cannot make the guard's negative control pass.
                    visible = conn.execute(
                        text(
                            "SELECT (description::jsonb ->> :key) "
                            "IS NOT DISTINCT FROM CAST(:value AS text) "
                            "FROM pg_description WHERE objoid="
                            "'relationship.temporal_cutover_consume_guard()'::regprocedure "
                            "AND classoid='pg_proc'::regclass AND objsubid=0"
                        ),
                        {"key": key, "value": str(value)},
                    ).scalar_one()
                    rows = conn.execute(
                        text(f"SELECT count(*) FROM {_TABLE} WHERE authorization_id=:id"),
                        {"id": binding["authorization_id"]},
                    ).scalar_one()
                    pytest.fail(
                        f"protected binding mutation was accepted: {key}; "
                        f"visible_mutation={visible}; nonce_rows={rows}"
                    )
        _comment(db, binding)
        consume_candidate_binding(conn, binding, proof)
    assert _consumed(db, binding) == 1


def test_binding_cannot_cross_connections_epochs_or_foreign_runtime_roles(candidate_db):
    _assert_actual_migration_session_consumes_once_and_replay_is_atomic(candidate_db)
    db = candidate_db
    with db.migration.begin() as original:
        challenge = candidate_session_challenge(original)
        proof = _proof()
        with db.bootstrap.begin() as issuer:
            spoof = dict(challenge) | {"full_xid": str(int(challenge["full_xid"]) + 2**32)}
            with pytest.raises(CandidateRefusal, match="^candidate_session_mismatch$"):
                issue_candidate_binding(issuer, db.birth, spoof, proof)
            binding = issue_candidate_binding(issuer, db.birth, challenge, proof)
        consume_candidate_binding(original, binding, proof)
    assert _consumed(db, binding) == 1
    with db.migration.begin() as conn:
        with pytest.raises(CandidateRefusal, match="^candidate_session_mismatch$"):
            with conn.begin_nested():
                consume_candidate_binding(conn, binding, proof)
        conn.exec_driver_sql("SET LOCAL ROLE butler_general_rw")
        with pytest.raises(DBAPIError) as refused:
            with conn.begin_nested():
                conn.exec_driver_sql(f"SELECT count(*) FROM {_TABLE}")
        assert refused.value.orig.pgcode == "42501"
    assert _consumed(db, binding) == 1
    with _bound(db) as (conn, binding, proof):
        assert conn.execute(
            text("SELECT pg_has_role(current_user,'butler_relationship_rw','USAGE')")
        ).scalar_one()
        assert conn.execute(
            text(
                "SELECT backend_start IS NOT NULL FROM pg_stat_activity WHERE pid=pg_backend_pid()"
            )
        ).scalar_one()
        with conn.begin_nested():
            conn.exec_driver_sql("SET LOCAL ROLE butler_relationship_rw")
            # Existing membership points from migration login to managed role,
            # not back to the login. The original downshift really hides this
            # same physical session's fields; NULL must never equal authority.
            assert conn.execute(
                text(
                    "SELECT backend_start IS NULL FROM pg_stat_activity WHERE pid=pg_backend_pid()"
                )
            ).scalar_one()
            with pytest.raises(CandidateRefusal, match="^candidate_session_mismatch$"):
                with conn.begin_nested():
                    consume_candidate_binding(conn, binding, proof)
            conn.exec_driver_sql("RESET ROLE")
        with conn.begin_nested():
            conn.exec_driver_sql("SET LOCAL ROLE butler_general_rw")
            with pytest.raises(CandidateRefusal, match="^candidate_sql_refused$"):
                with conn.begin_nested():
                    consume_candidate_binding(conn, binding, proof)
            conn.exec_driver_sql("RESET ROLE")
        assert (
            conn.execute(
                text(f"SELECT count(*) FROM {_TABLE} WHERE authorization_id=:id"),
                {"id": binding["authorization_id"]},
            ).scalar_one()
            == 0
        )
        consume_candidate_binding(conn, binding, proof)
    assert _consumed(db, binding) == 1


def test_grant_replay_narrows_atomically_and_omitting_hook_is_a_real_unsafe_control(candidate_db):
    db = candidate_db
    with _bound(db) as (conn, binding, proof):
        consume_candidate_binding(conn, binding, proof)

    def ordinary_update():
        with db.migration.begin() as actor:
            actor.exec_driver_sql("SET LOCAL ROLE butler_relationship_rw")
            actor.exec_driver_sql("SET LOCAL statement_timeout='2s'")
            try:
                actor.execute(
                    text(f"UPDATE {_TABLE} SET proof_digest=:digest WHERE authorization_id=:id"),
                    {"digest": "0" * 64, "id": binding["authorization_id"]},
                )
            except DBAPIError as exc:
                return exc.orig.pgcode
        return "wrote"

    def while_grants_uncommitted(conn):
        assert conn.execute(
            text(f"SELECT has_table_privilege('butler_relationship_rw','{_TABLE}','UPDATE')")
        ).scalar_one()
        with ThreadPoolExecutor(max_workers=1) as pool:
            # PostgreSQL may deny the old ACL immediately or block on the
            # uncommitted grant's lock. Neither path may write the planted row.
            assert pool.submit(ordinary_update).result(timeout=5) in {"42501", "57014"}

    _bootstrap(db, after_grants=while_grants_uncommitted)
    assert ordinary_update() == "42501"
    _bootstrap(db, narrow=False)  # Explicit disposable causal control.
    try:
        assert ordinary_update() == "wrote"
        assert (
            _read(
                db,
                f"SELECT proof_digest FROM {_TABLE} WHERE authorization_id=:id",
                {"id": binding["authorization_id"]},
            )
            == "0" * 64
        )
    finally:
        _bootstrap(db)
    assert ordinary_update() == "42501"


def test_other_actual_session_and_nowait_lock_refuse_before_consumption(candidate_db):
    db = candidate_db
    with _bound(db) as (conn, binding, proof):
        with db.migration.begin() as competing:
            competing.exec_driver_sql("SELECT 1")
            with pytest.raises(CandidateRefusal, match="^candidate_other_session$"):
                with conn.begin_nested():
                    consume_candidate_binding(conn, binding, proof)
            competing.exec_driver_sql("LOCK TABLE relationship.entity_facts IN ROW EXCLUSIVE MODE")
            with pytest.raises(CandidateRefusal, match="^candidate_lock_busy$"):
                with conn.begin_nested():
                    consume_candidate_binding(conn, binding, proof)
        with db.bootstrap.begin() as hidden_competing:
            hidden_pid = hidden_competing.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            conn.exec_driver_sql("SELECT pg_stat_clear_snapshot()")
            assert conn.execute(
                text("SELECT backend_type IS NULL FROM pg_stat_activity WHERE pid=:pid"),
                {"pid": hidden_pid},
            ).scalar_one()
            # The client exists even when ordinary activity access cannot tell
            # its kind. A hidden field must fail closed, without pg_monitor.
            with pytest.raises(CandidateRefusal, match="^candidate_other_session$"):
                with conn.begin_nested():
                    consume_candidate_binding(conn, binding, proof)
        consume_candidate_binding(conn, binding, proof)
    assert _consumed(db, binding) == 1


def test_restored_birth_and_writable_lookalike_do_not_qualify(candidate_db, postgres_container):
    db = candidate_db
    with _bound(db) as (conn, binding, proof):
        for key, value in [
            ("database_oid", 0),
            ("system_identifier", "0"),
            ("owner_oid", db.birth["migration_oid"]),
            ("state", "released"),
            ("guard_sha256", "0" * 64),
        ]:
            _comment(db, dict(db.birth) | {key: value}, birth=True)
            with pytest.raises(CandidateRefusal, match="^candidate_birth_invalid$"):
                with conn.begin_nested():
                    consume_candidate_binding(conn, binding, proof)
        _comment(db, db.birth, birth=True)
        consume_candidate_binding(conn, binding, proof)
    # Trusted-bootstrap historical erasure is an explicit residual. Prove that
    # it can erase a committed nonce and mint fresh session authority for the
    # same authorization; never claim protection against this existing actor.
    with _bound(db, proof) as (conn, fresh_binding, _):
        with pytest.raises(CandidateRefusal, match="^candidate_replay_refused$"):
            with conn.begin_nested():
                consume_candidate_binding(conn, fresh_binding, proof)
        with db.bootstrap.begin() as trusted:
            trusted.execute(
                text(f"DELETE FROM {_TABLE} WHERE authorization_id=:id"),
                {"id": fresh_binding["authorization_id"]},
            )
        consume_candidate_binding(conn, fresh_binding, proof)
    assert _consumed(db, fresh_binding) == 1
    # Real independent generic target uses unchanged requested current head.
    # A writable catalog/body copy cannot manufacture the trusted object owner.
    name = migration_db_name()
    url = create_migrated_test_db(
        postgres_container,
        name,
        chains=["core", "relationship"],
        schemas={"relationship": "relationship"},
    )
    clone = create_engine(url, poolclass=NullPool)
    try:
        with clone.begin() as conn:
            # schema-standin-exempt: intentional adversarial writable lookalike,
            # not a query fixture or protected migrated-schema proof.
            conn.exec_driver_sql(
                f"CREATE TABLE {_TABLE} (authorization_id uuid, birth_id uuid, "
                "fence_id uuid, proof_digest text, proof jsonb)"
            )
            conn.exec_driver_sql(
                f"CREATE FUNCTION {_FUNCTION}() RETURNS trigger "
                "LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog "
                "AS $clone$" + _GUARD_BODY + "$clone$"
            )
            conn.exec_driver_sql(
                f"CREATE TRIGGER temporal_cutover_consume_guard "
                f"BEFORE INSERT ON {_TABLE} FOR EACH ROW "
                f"EXECUTE FUNCTION {_FUNCTION}()"
            )
            conn.execute(
                text(f"COMMENT ON TABLE {_TABLE} IS :value"), {"value": _encoded(db.birth)}
            )
            conn.execute(
                text(f"COMMENT ON FUNCTION {_FUNCTION}() IS :value"), {"value": _encoded(binding)}
            )
            with pytest.raises(DBAPIError) as refused:
                with conn.begin_nested():
                    conn.execute(
                        text(f"INSERT INTO {_TABLE} VALUES (:a,:b,:f,:d, '{{}}'::jsonb)"),
                        {
                            "a": str(uuid.uuid4()),
                            "b": db.birth["birth_id"],
                            "f": binding["fence_id"],
                            "d": "0" * 64,
                        },
                    )
            assert refused.value.orig.diag.message_primary == "candidate_object_invalid"
    finally:
        clone.dispose()
        with db.control.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
            conn.exec_driver_sql(f'DROP ROLE "migration_{name}"')


def test_durable_birth_survives_interrupted_bootstrap_and_committed_partial_resume(
    postgres_container,
    candidate_db,
):
    # Real new target: committed birth, actual bootstrap rollback, committed
    # partial rel034, current rel035/036, then genuine new-session rebinding.
    # Inner protocol only; no actual outer collector/unsigned G claim.
    db = _new_candidate(postgres_container)
    try:
        with pytest.raises(RuntimeError, match="^synthetic_bootstrap_interruption$"):
            with db.bootstrap.begin() as conn:
                conn.execute(
                    text("SELECT set_config('butlers.connecting_user', :role, true)"),
                    {"role": db.migration_role},
                )
                _execute_bootstrap_sql(conn)
                raise RuntimeError("synthetic_bootstrap_interruption")
        with db.bootstrap.connect() as conn:
            assert conn.execute(
                text(f"SELECT obj_description('{_TABLE}'::regclass,'pg_class')")
            ).scalar_one() == _encoded(db.birth)
        _bootstrap(db)
        _migrate(db, partial=True)
        assert _read(db, "SELECT version_num FROM relationship.alembic_version") == "rel_034"
        asyncio.run(
            run_migrations(
                db.migration.url.render_as_string(hide_password=False),
                chain="relationship",
                schema="relationship",
            )
        )
        assert _read(db, "SELECT version_num FROM relationship.alembic_version") == "rel_036"
        # A genuine protected birth copied from another actual migrated target
        # must fail despite the correct protected object owner and body.
        _assert_source_creation_and_actual_normal_role_catalog_visibility(db)
        _comment(db, candidate_db.birth, birth=True)
        with _bound(db) as (conn, binding, proof):
            with pytest.raises(CandidateRefusal, match="^candidate_birth_invalid$"):
                with conn.begin_nested():
                    consume_candidate_binding(conn, binding, proof)
        assert _consumed(db, binding) == 0
        _comment(db, db.birth, birth=True)
        with _bound(db) as (conn, binding, proof):
            consume_candidate_binding(conn, binding, proof)
        assert _consumed(db, binding) == 1
        assert _read(db, f"SELECT obj_description('{_TABLE}'::regclass,'pg_class')") == _encoded(
            db.birth
        )
        with _bound(db) as (conn, binding, proof):
            with db.bootstrap.begin() as trusted:
                trusted.exec_driver_sql(f"DROP TABLE {_TABLE}")
            with pytest.raises(CandidateRefusal, match="^candidate_object_missing$"):
                with conn.begin_nested():
                    consume_candidate_binding(conn, binding, proof)
        assert _read(db, "SELECT version_num FROM relationship.alembic_version") == "rel_036"
        assert _read(db, "SELECT to_regclass('relationship.uq_ef_spo_active') IS NOT NULL")
    finally:
        _drop(db)
