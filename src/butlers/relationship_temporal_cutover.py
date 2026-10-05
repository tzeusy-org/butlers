"""Dormant catalog/consumption candidate for Relationship temporal admission.

This module is a feasibility protocol, not G, a collector, or a production
bootstrap hook. Its privileged issuer accepts complete conformance inputs;
their origin must be established separately by the future protected collector.
No ordinary caller can issue authority through this interface. Existing
bootstrap identities remain trusted, including their ability to erase history.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

_TABLE = "relationship.temporal_cutover_consumptions"
_FUNCTION = "relationship.temporal_cutover_consume_guard"
_ROLE = "butler_relationship_rw"
_SCHEMA_TAG = "butlers.relationship-temporal-candidate/v1"
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PROOF_KEYS = {
    "target_sha",
    "target_image_id",
    "roster_tree_id",
    "compose_invocation",
    "compose_config_digest",
    "instances",
    "credentialed_services",
    "mutator_inventory",
    "mutator_inventory_digest",
    "test_receipt",
    "test_receipt_digest",
    "fence",
    "isolated_test_runtime",
    "never_released",
}


class CandidateRefusal(RuntimeError):
    """Expose a stable content-blind code, never SQL, claims or driver errors."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_candidate_proof(proof: dict[str, Any]) -> None:
    """Validate complete supplied inputs; this does not establish collection."""
    if not isinstance(proof, dict) or set(proof) != _PROOF_KEYS:
        raise CandidateRefusal("candidate_proof_incomplete")
    for key in ["target_sha", "roster_tree_id"]:
        if not isinstance(proof[key], str) or not _HEX40.fullmatch(proof[key]):
            raise CandidateRefusal("candidate_proof_invalid")
    for key in ["compose_config_digest", "mutator_inventory_digest", "test_receipt_digest"]:
        if not isinstance(proof[key], str) or not _HEX64.fullmatch(proof[key]):
            raise CandidateRefusal("candidate_proof_invalid")
    if not isinstance(proof["target_image_id"], str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", proof["target_image_id"]
    ):
        raise CandidateRefusal("candidate_proof_invalid")
    invocation = proof["compose_invocation"]
    if not isinstance(invocation, dict) or set(invocation) != {
        "row",
        "files",
        "project",
        "env_file",
        "profiles",
    }:
        raise CandidateRefusal("candidate_invocation_invalid")
    if invocation["row"] not in {"prod-deploy", "prod-launcher", "dev-launcher"}:
        raise CandidateRefusal("candidate_invocation_invalid")
    if not invocation["files"] or not invocation["project"] or not invocation["env_file"]:
        raise CandidateRefusal("candidate_invocation_invalid")
    if not isinstance(invocation["profiles"], list) or "hotreload" in invocation["profiles"]:
        raise CandidateRefusal("candidate_invocation_invalid")
    if proof["never_released"] is not True:
        raise CandidateRefusal("candidate_birth_released")
    if not isinstance(proof["credentialed_services"], list) or not proof["credentialed_services"]:
        raise CandidateRefusal("candidate_cohort_incomplete")
    if not isinstance(proof["instances"], list):
        raise CandidateRefusal("candidate_cohort_incomplete")
    inventory = proof["mutator_inventory"]
    if not isinstance(inventory, list) or not inventory:
        raise CandidateRefusal("candidate_inventory_incomplete")
    for entry in inventory:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "qualname", "classification", "file_sha256"}
            or not isinstance(entry["file_sha256"], str)
            or not _HEX64.fullmatch(entry["file_sha256"])
        ):
            raise CandidateRefusal("candidate_inventory_incomplete")
    if hashlib.sha256(_json(inventory).encode()).hexdigest() != proof["mutator_inventory_digest"]:
        raise CandidateRefusal("candidate_inventory_mismatch")
    receipt = proof["test_receipt"]
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {
            "schema",
            "target_sha",
            "node_ids_digest",
            "log_digest",
            "verdict",
            "authorization_id",
            "fence_id",
        }
        or receipt["schema"] != "butlers.relationship-temporal-cutover-tests/v1"
    ):
        raise CandidateRefusal("candidate_tests_incomplete")
    if receipt["verdict"] != "PASS" or receipt["target_sha"] != proof["target_sha"]:
        raise CandidateRefusal("candidate_tests_incomplete")
    for key in ["node_ids_digest", "log_digest"]:
        if not isinstance(receipt[key], str) or not _HEX64.fullmatch(receipt[key]):
            raise CandidateRefusal("candidate_tests_incomplete")
    if hashlib.sha256(_json(receipt).encode()).hexdigest() != proof["test_receipt_digest"]:
        raise CandidateRefusal("candidate_tests_mismatch")
    fence = proof["fence"]
    if (
        not isinstance(fence, dict)
        or set(fence)
        != {
            "fence_id",
            "authorization_id",
            "project",
            "target_sha",
            "target_image_id",
            "generation",
            "phase",
            "set_time",
        }
        or fence["phase"] != "quiesced"
    ):
        raise CandidateRefusal("candidate_fence_invalid")
    if any(fence[k] != proof[k] for k in ["target_sha", "target_image_id"]):
        raise CandidateRefusal("candidate_fence_invalid")
    if fence["project"] != invocation["project"] or any(
        receipt[k] != fence[k] for k in ["authorization_id", "fence_id"]
    ):
        raise CandidateRefusal("candidate_fence_invalid")
    isolation = proof["isolated_test_runtime"]
    if (
        not isinstance(isolation, dict)
        or set(isolation)
        != {
            "source_tree_id",
            "test_image_digest",
            "dependencies_digest",
            "own_runtime",
            "host_daemon_denied",
            "key_denied",
            "log_write_denied",
            "target_tcp_denied",
            "target_unix_denied",
            "offline",
            "cleaned",
        }
        or not all(
            isolation[k] is True
            for k in [
                "own_runtime",
                "host_daemon_denied",
                "key_denied",
                "log_write_denied",
                "target_tcp_denied",
                "target_unix_denied",
                "offline",
                "cleaned",
            ]
        )
    ):
        raise CandidateRefusal("candidate_isolation_incomplete")


_GUARD_BODY = r"""
DECLARE
    v_birth jsonb;
    v_binding jsonb;
    v_text text;
    v_owner oid;
    v_function oid;
    v_database oid;
    v_migration oid;
    v_system text;
    v_backend timestamptz;
    v_now bigint;
BEGIN
    SELECT c.relowner, d.oid, d.datdba INTO v_owner, v_database, v_migration
      FROM pg_class c, pg_database d
     WHERE c.oid = TG_RELID AND d.datname = current_database();
    SELECT t.tgfoid INTO v_function FROM pg_trigger t
     WHERE t.tgrelid = TG_RELID AND NOT t.tgisinternal
       AND t.tgname = 'temporal_cutover_consume_guard';
    IF TG_RELID <> to_regclass('relationship.temporal_cutover_consumptions')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE oid = v_owner AND rolsuper)
       OR NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                       WHERE p.oid=v_function AND p.proowner=v_owner AND NOT p.prosecdef
                         AND n.nspname='relationship' AND n.nspowner=v_owner
                         AND p.proname='temporal_cutover_consume_guard'
                         AND p.proconfig=ARRAY['search_path=pg_catalog']) THEN
        RAISE EXCEPTION 'candidate_object_invalid';
    END IF;
    v_text := obj_description(TG_RELID, 'pg_class');
    IF v_text IS NULL OR NOT (v_text IS JSON OBJECT WITH UNIQUE KEYS) THEN
        RAISE EXCEPTION 'candidate_birth_invalid';
    END IF;
    v_birth := v_text::jsonb;
    IF NOT (v_birth ?& ARRAY['schema','birth_id','state','guard_sha256',
        'database_oid','database_name','migration_oid','owner_oid','system_identifier'])
       OR (SELECT count(*) FROM jsonb_object_keys(v_birth)) <> 9
       OR EXISTS (SELECT 1 FROM jsonb_each(v_birth) WHERE value='null'::jsonb) THEN
        RAISE EXCEPTION 'candidate_birth_invalid';
    END IF;
    v_text := obj_description(v_function, 'pg_proc');
    IF v_text IS NULL OR NOT (v_text IS JSON OBJECT WITH UNIQUE KEYS) THEN
        RAISE EXCEPTION 'candidate_binding_invalid';
    END IF;
    v_binding := v_text::jsonb;
    IF NOT (v_binding ?& ARRAY['birth_id','schema_name','purpose','authorization_id',
        'fence_id','issued_at','expires_at','proof','proof_digest',
        'backend_pid','backend_start','full_xid','revision'])
       OR (SELECT count(*) FROM jsonb_object_keys(v_binding)) <> 13
       OR EXISTS (SELECT 1 FROM jsonb_each(v_binding) WHERE value='null'::jsonb) THEN
        RAISE EXCEPTION 'candidate_binding_invalid';
    END IF;
    SELECT system_identifier::text INTO v_system FROM pg_control_system();
    SELECT backend_start INTO v_backend FROM pg_stat_activity WHERE pid=pg_backend_pid();
    v_now := floor(extract(epoch FROM clock_timestamp()))::bigint;
    IF v_birth->>'schema' <> 'butlers.relationship-temporal-candidate/v1'
       OR (v_birth->>'database_oid')::oid <> v_database
       OR v_birth->>'database_name' <> current_database()
       OR v_birth->>'system_identifier' <> v_system
       OR (v_birth->>'owner_oid')::oid <> v_owner
       OR (v_birth->>'migration_oid')::oid <> v_migration
       OR v_birth->>'guard_sha256' <> (
           SELECT encode(sha256(convert_to(prosrc, 'UTF8')), 'hex')
             FROM pg_proc WHERE oid=v_function)
       OR v_birth->>'state' <> 'never_released' THEN
        RAISE EXCEPTION 'candidate_birth_invalid';
    END IF;
    IF v_binding->>'birth_id' <> v_birth->>'birth_id'
       OR v_binding->>'authorization_id' <> NEW.authorization_id::text
       OR v_binding->>'purpose' <> 'cutover'
       OR v_binding->>'schema_name' <> 'relationship'
       OR v_binding->>'fence_id' <> NEW.fence_id::text
       OR v_binding->>'proof_digest' <> NEW.proof_digest
       OR v_binding->'proof' IS DISTINCT FROM NEW.proof
       OR v_binding->'proof'->'fence'->>'phase' <> 'quiesced'
       OR v_binding->'proof'->'fence'->>'fence_id' <> NEW.fence_id::text
       OR v_binding->'proof'->'fence'->>'authorization_id' <> NEW.authorization_id::text THEN
        RAISE EXCEPTION 'candidate_binding_mismatch';
    END IF;
    IF NOT (v_binding->'proof' ?& ARRAY['target_sha','target_image_id','roster_tree_id',
        'compose_invocation','compose_config_digest','instances','credentialed_services',
        'mutator_inventory','mutator_inventory_digest','test_receipt','test_receipt_digest',
        'fence','isolated_test_runtime','never_released'])
       OR (SELECT count(*) FROM jsonb_object_keys(v_binding->'proof')) <> 14 THEN
        RAISE EXCEPTION 'candidate_proof_incomplete';
    END IF;
    IF v_binding->>'revision' IS DISTINCT FROM
       (SELECT version_num FROM relationship.alembic_version) THEN
        RAISE EXCEPTION 'candidate_revision_mismatch';
    END IF;
    IF (v_binding->>'expires_at')::bigint <= v_now
       OR (v_binding->>'issued_at')::bigint > v_now
       OR (v_binding->>'expires_at')::bigint - (v_binding->>'issued_at')::bigint > 900 THEN
        RAISE EXCEPTION 'candidate_binding_expired';
    END IF;
    IF current_user <> 'butler_relationship_rw'
       OR (SELECT oid FROM pg_roles WHERE rolname=session_user)
          <> (v_birth->>'migration_oid')::oid
       OR (v_binding->>'backend_pid')::integer <> pg_backend_pid()
       OR (v_binding->>'backend_start')::timestamptz <> v_backend
       OR v_binding->>'full_xid' <> pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'candidate_session_mismatch';
    END IF;
    LOCK TABLE relationship.entity_facts IN ACCESS EXCLUSIVE MODE NOWAIT;
    -- The challenge may have read activity before another backend connected.
    -- Refresh PostgreSQL's transaction-local statistics snapshot, not authority.
    PERFORM pg_stat_clear_snapshot();
    IF EXISTS (SELECT 1 FROM pg_stat_activity
                WHERE datid=v_database AND pid<>pg_backend_pid()
                  AND backend_type='client backend') THEN
        RAISE EXCEPTION 'candidate_other_session';
    END IF;
    NEW.birth_id := (v_birth->>'birth_id')::uuid;
    NEW.consumed_at := clock_timestamp();
    RETURN NEW;
END;
"""


def _bootstrap_only(conn: Connection) -> None:
    if not conn.execute(
        text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    ).scalar_one():
        raise CandidateRefusal("candidate_issuer_untrusted")


def create_candidate_database(
    bootstrap_engine: Engine, name: str, migration_role: str
) -> dict[str, Any]:
    """Create and commit birth, never discover/relabel an existing target.

    Roles are provisioned by the unchanged existing test/bootstrap contract.
    This controller creates no authority role, grant or new namespace. It
    installs durable birth in the existing managed Relationship namespace
    before the ordinary bootstrap starts. There is no post-hoc install verb.
    """
    if not re.fullmatch(r"test_[0-9a-f]+", name):
        raise CandidateRefusal("candidate_target_invalid")
    with bootstrap_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _bootstrap_only(conn)
        quote = conn.dialect.identifier_preparer.quote_identifier
        try:
            conn.exec_driver_sql(f"CREATE DATABASE {quote(name)} OWNER {quote(migration_role)}")
        except DBAPIError:
            raise CandidateRefusal("candidate_creation_refused") from None
    target = create_engine(bootstrap_engine.url.set(database=name))
    try:
        with target.begin() as conn:
            return _install_candidate_birth(conn, migration_role)
    finally:
        target.dispose()


def _install_candidate_birth(conn: Connection, migration_role: str) -> dict[str, Any]:
    """Install the dormant object in the existing managed Relationship schema.

    Private DDL called only after the controller's successful CREATE DATABASE;
    it is neither an ordinary-caller nor a standalone freshness API.
    """
    _bootstrap_only(conn)
    conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS relationship")
    conn.exec_driver_sql(f"""
        CREATE TABLE {_TABLE} (
            authorization_id uuid PRIMARY KEY,
            birth_id uuid NOT NULL,
            fence_id uuid NOT NULL,
            proof_digest text NOT NULL CHECK (proof_digest ~ '^[0-9a-f]{{64}}$'),
            proof jsonb NOT NULL CHECK (jsonb_typeof(proof)='object'),
            consumed_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    conn.exec_driver_sql(f"""
        CREATE FUNCTION {_FUNCTION}() RETURNS trigger
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
        AS $candidate${_GUARD_BODY}$candidate$
    """)
    conn.exec_driver_sql(f"""
        CREATE TRIGGER temporal_cutover_consume_guard BEFORE INSERT ON {_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()
    """)
    values = (
        conn.execute(
            text("""
        SELECT d.oid AS database_oid, d.datname AS database_name,
               d.datdba AS migration_oid, r.oid AS owner_oid,
               s.system_identifier::text AS system_identifier
        FROM pg_database d, pg_roles r, pg_control_system() s
        WHERE d.datname=current_database() AND r.rolname=current_user
    """)
        )
        .mappings()
        .one()
    )
    actual_role = conn.execute(
        text("SELECT oid FROM pg_roles WHERE rolname=:role"), {"role": migration_role}
    ).scalar_one()
    if values["migration_oid"] != actual_role:
        raise CandidateRefusal("candidate_migration_owner_mismatch")
    birth = dict(values) | {
        "schema": _SCHEMA_TAG,
        "birth_id": str(uuid.uuid4()),
        "state": "never_released",
        "guard_sha256": hashlib.sha256(_GUARD_BODY.encode()).hexdigest(),
    }
    conn.execute(text(f"COMMENT ON TABLE {_TABLE} IS :comment"), {"comment": _json(birth)})
    return birth


def narrow_candidate_privileges(conn: Connection, migration_role: str) -> None:
    """REVOKE-only hook to test after real grants in the SAME transaction.

    This hook is not wired into init-db. Replay without the hook is an explicit
    negative control, not a safe production installation or advancement path.
    """
    _bootstrap_only(conn)
    quote = conn.dialect.identifier_preparer.quote_identifier
    conn.exec_driver_sql(
        f"REVOKE UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES ON {_TABLE} FROM {_ROLE}"
    )
    conn.exec_driver_sql(f"REVOKE ALL ON {_TABLE} FROM PUBLIC, {quote(migration_role)}")


def candidate_session_challenge(conn: Connection) -> dict[str, Any]:
    """Read the actual online backend and full xid; no Config/caller label."""
    return dict(
        conn.execute(
            text("""
        SELECT pg_backend_pid() AS backend_pid, backend_start::text AS backend_start,
               pg_current_xact_id()::text AS full_xid,
               (SELECT oid FROM pg_roles WHERE rolname=session_user) AS session_oid,
               (SELECT version_num FROM relationship.alembic_version) AS revision
        FROM pg_stat_activity WHERE pid=pg_backend_pid()
    """)
        )
        .mappings()
        .one()
    )


def issue_candidate_binding(
    conn: Connection,
    birth: dict[str, Any],
    challenge: dict[str, Any],
    proof: dict[str, Any],
    *,
    lifetime: int = 300,
) -> dict[str, Any]:
    """Trusted issuer's inner conformance channel, not collected-origin proof.

    A future collector must derive all claims itself; there is no CLI or normal
    caller issuer. This bounded phase establishes only catalog/session/SQL
    protocol feasibility for complete supplied synthetic inputs.
    """
    _bootstrap_only(conn)
    validate_candidate_proof(proof)
    if type(lifetime) is not int or not 0 < lifetime <= 900:
        raise CandidateRefusal("candidate_binding_expired")
    backend = (
        conn.execute(
            text("""
        SELECT datid, usesysid, backend_start::text AS backend_start,
               backend_xid::text AS backend_xid
        FROM pg_stat_activity WHERE pid=:pid AND backend_type='client backend'
    """),
            {"pid": challenge["backend_pid"]},
        )
        .mappings()
        .one_or_none()
    )
    if (
        backend is None
        or backend["datid"] != birth["database_oid"]
        or (
            backend["usesysid"] != birth["migration_oid"]
            or challenge["session_oid"] != birth["migration_oid"]
            or backend["backend_start"] != challenge["backend_start"]
            or backend["backend_xid"] != str(int(challenge["full_xid"]) % (2**32))
        )
    ):
        raise CandidateRefusal("candidate_session_mismatch")
    latest_xid = int(conn.execute(text("SELECT pg_current_xact_id()::text")).scalar_one())
    backend_xid = int(backend["backend_xid"])
    epoch = latest_xid // (2**32) - (backend_xid > latest_xid % (2**32))
    if int(challenge["full_xid"]) != epoch * (2**32) + backend_xid:
        raise CandidateRefusal("candidate_session_mismatch")
    revision = conn.execute(text("SELECT version_num FROM relationship.alembic_version"))
    if revision.scalar_one() != challenge["revision"]:
        raise CandidateRefusal("candidate_revision_mismatch")
    now = conn.execute(text("SELECT floor(extract(epoch FROM clock_timestamp()))::bigint"))
    issued = now.scalar_one()
    binding = {
        "birth_id": birth["birth_id"],
        "schema_name": "relationship",
        "purpose": "cutover",
        "authorization_id": proof["fence"]["authorization_id"],
        "fence_id": proof["fence"]["fence_id"],
        "issued_at": issued,
        "expires_at": issued + lifetime,
        "proof": proof,
        "proof_digest": hashlib.sha256(_json(proof).encode()).hexdigest(),
    } | {k: challenge[k] for k in ["backend_pid", "backend_start", "full_xid", "revision"]}
    conn.execute(
        text(f"COMMENT ON FUNCTION {_FUNCTION}() IS :comment"), {"comment": _json(binding)}
    )
    return binding


def consume_candidate_binding(
    conn: Connection,
    binding: dict[str, Any],
    expected_proof: dict[str, Any],
) -> None:
    """Consume on the caller's real transaction, without index/stamp wiring.

    No commit/pool acquisition occurs here. The dormant guard rechecks actual
    owner/catalog/backend/full-xid/session/exclusion at INSERT. Caller-supplied
    expected claims are comparison inputs, never an issuer or freshness bypass.
    """
    validate_candidate_proof(expected_proof)
    if binding["proof"] != expected_proof:
        raise CandidateRefusal("candidate_proof_mismatch")
    if binding["proof_digest"] != hashlib.sha256(_json(expected_proof).encode()).hexdigest():
        raise CandidateRefusal("candidate_proof_mismatch")
    try:
        conn.exec_driver_sql(f"SET LOCAL ROLE {_ROLE}")
        conn.execute(
            text(f"""
        INSERT INTO {_TABLE} (authorization_id, birth_id, fence_id, proof_digest, proof)
        VALUES (:authorization_id, :birth_id, :fence_id, :proof_digest, CAST(:proof AS jsonb))
    """),
            {k: binding[k] for k in ["authorization_id", "birth_id", "fence_id", "proof_digest"]}
            | {"proof": _json(expected_proof)},
        )
        conn.exec_driver_sql("RESET ROLE")
    except DBAPIError as exc:
        # The real transaction remains aborted; only its owner may roll back.
        # Suppress driver text/parameters when exposing the refusal boundary.
        original = exc.orig
        primary = getattr(getattr(original, "diag", None), "message_primary", "")
        sqlstate = getattr(original, "pgcode", None)
        if re.fullmatch(r"candidate_[a-z_]+", primary):
            code = primary
        elif sqlstate == "23505":
            code = "candidate_replay_refused"
        elif sqlstate == "55P03":
            code = "candidate_lock_busy"
        elif sqlstate == "42P01":
            code = "candidate_object_missing"
        else:
            code = "candidate_sql_refused"
        raise CandidateRefusal(code) from None
