"""Real source-deployment ACL and dedicated-serving identity integration controls.

Issue: butlers-1003.6. Object authority comes exclusively from the unmodified
source bootstrap, selected migration chains and finalizer. The only positive
fixture provisioning is a synthetic low-privilege LOGIN and its one declared
NOLOGIN capability membership. Administrative ACL injection is negative-only.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from contextlib import contextmanager
from urllib.parse import unquote, urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from butlers.db import Database
from butlers.db_authority.grants import deployment_function_plans
from butlers.db_authority.roles import purpose_profile, runtime_profiles
from butlers.migrations import (
    finalize_authority,
    get_all_chains,
    migration_session,
    run_migrations,
)
from butlers.testing.migration import (
    _upgrade_chain_to_revision,
    create_migration_db,
    migration_bootstrap_db_url,
)

# Retain the existing availability mark; missing source roles are failures.
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_BUTLER_SCHEMAS = ("general", "health", "messenger", "relationship", "switchboard")
_RUNTIME_ROLES = {schema: f"butler_{schema}_rw" for schema in _BUTLER_SCHEMAS}
_RESTORE_DRILL_DENIED_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)


@pytest.fixture(scope="module")
def postgres_container():
    """Own an isolated server: the serving source identity is literally butlers.

    This cannot borrow the session-wide random-name database or remap roster
    profiles. Preserve root faketime/shared-memory container configuration.
    """
    from testcontainers.postgres import PostgresContainer

    from conftest import _configure_postgres_container

    container = _configure_postgres_container(
        PostgresContainer("pgvector/pgvector:pg17"), os.environ
    )
    with container as postgres:
        yield postgres


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _unique_db_name() -> str:
    # A data-probe nonce only. The actual database remains the source identity.
    return f"test_{uuid.uuid4().hex[:12]}"


def _create_db(postgres_container, db_name: str) -> str:
    """Install the full actual roster once on this test-owned isolated server.

    The supplied historical per-case name is only a nonce, never a serving
    profile alias. The production chain registry excludes modules with no
    migration chain; every actual declared core/butler/module/private chain is
    selected, including core114 staging before shared contact retirement.
    """
    if not db_name.startswith("test_"):
        raise ValueError("Expected a synthetic case nonce")
    existing = getattr(postgres_container, "_schema_acl_deployment", None)
    if existing is not None:
        return existing[0]
    migration_url = create_migration_db(postgres_container, "butlers")
    control_url = migration_bootstrap_db_url(postgres_container, "butlers")
    profiles = runtime_profiles("butlers")
    available = tuple(get_all_chains())
    chain_order = {chain: index for index, chain in enumerate(available)}
    plans = tuple(
        sorted(
            (row for row in deployment_function_plans(profiles) if row[0] in chain_order),
            key=lambda row: (chain_order[row[0]], row[1] or ""),
        )
    )
    # No alternate compiler or hand-built object/role grants. A non-migration
    # module label has no Alembic chain; all real source chains are included.
    core = tuple(row for row in plans if row[0] == "core")
    others = tuple(row for row in plans if row[0] != "core")
    with migration_session(control_url, source_database="butlers") as connection:
        for _, schema, _ in core:
            _upgrade_chain_to_revision(
                migration_url,
                chain="core",
                schema=schema,
                revision="core_114",
                connection=connection,
            )
        for chain, schema, _ in (*core, *others):
            asyncio.run(
                run_migrations(
                    migration_url,
                    chain=chain,
                    schema=schema,
                    connection=connection,
                )
            )
        finalize_authority(connection, source_database="butlers", plans=plans)
    postgres_container._schema_acl_deployment = (migration_url, control_url, plans)
    return migration_url


def _control_url(postgres_container) -> str:
    return migration_bootstrap_db_url(postgres_container, "butlers")


@contextmanager
def _engine_connection(db_url: str):
    engine = create_engine(
        db_url,
        isolation_level="AUTOCOMMIT",
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=30000 -c lock_timeout=5000",
        },
    )
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def _scalar(db_url: str, sql: str, parameters=None):
    with _engine_connection(db_url) as connection:
        return connection.execute(text(sql), parameters or {}).scalar_one()


def _role_exists(db_url: str, role_name: str) -> bool:
    return bool(
        _scalar(db_url, "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:r)", {"r": role_name})
    )


def _require_runtime_acl(db_url: str) -> None:
    missing = [role for role in _RUNTIME_ROLES.values() if not _role_exists(db_url, role)]
    if missing:
        pytest.fail("Source deployment did not install every required runtime capability")


def _execute_as_role(db_url: str, role_name: str, sql: str, *, scalar: bool = False):
    """Administrative ACL control only; native Database cases use real logins."""
    with _engine_connection(db_url) as connection:
        connection.execute(text(f"SET ROLE {_quote_ident(role_name)}"))
        try:
            result = connection.execute(text(sql))
            return result.scalar() if scalar else result.rowcount
        finally:
            connection.execute(text("RESET ROLE"))


def _execute_as_role_via_session_auth(
    db_url: str,
    session_role: str,
    role_name: str,
    sql: str,
    *,
    scalar: bool = False,
):
    """Administrative test transport with an actual low-privilege session user."""
    with _engine_connection(db_url) as connection:
        connection.execute(text(f"SET SESSION AUTHORIZATION {_quote_ident(session_role)}"))
        try:
            connection.execute(text(f"SET ROLE {_quote_ident(role_name)}"))
            try:
                result = connection.execute(text(sql))
                return result.scalar() if scalar else result.rowcount
            finally:
                connection.execute(text("RESET ROLE"))
        finally:
            connection.execute(text("RESET SESSION AUTHORIZATION"))


@contextmanager
def _serving_login(control_url: str, role: str):
    """Synthetic operator setup: only an existing declared capability membership.

    No runtime table/function privilege, profile substitution or administrative
    serving login is introduced. CREATE acknowledgment owns DROP; an ambiguous
    create failure never authorizes deleting a preexisting identity. The owned
    isolated server remains the final cleanup authority in that failure case.
    """
    declared = {profile.role for profile in runtime_profiles("butlers")}
    declared.update(
        purpose_profile("butlers", purpose).role
        for purpose in ("connector", "credential", "dashboard")
    )
    if role not in declared:
        raise ValueError("Not an existing source-selected capability")
    login = f"acl_serving_{uuid.uuid4().hex}"
    password = uuid.uuid4().hex  # synthetic test data, never external credentials
    created = False
    primary = None
    try:
        with _engine_connection(control_url) as connection:
            if connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:r)"), {"r": login}
            ).scalar_one():
                raise RuntimeError("Synthetic identity already exists")
            capability = connection.execute(
                text(
                    "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
                    "rolreplication,rolbypassrls "
                    "FROM pg_roles WHERE rolname=:role"
                ),
                {"role": role},
            ).one()
            assert capability == (False, False, False, False, False, False)
            connection.execute(
                text(
                    f"CREATE ROLE {_quote_ident(login)} LOGIN NOINHERIT NOSUPERUSER "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :password"
                ),
                {"password": password},
            )
            created = True
            connection.execute(
                text(f"GRANT {_quote_ident(role)} TO {_quote_ident(login)} WITH SET TRUE")
            )
        yield login, password
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if created:
            try:
                with _engine_connection(control_url) as connection:
                    connection.execute(text(f"DROP ROLE {_quote_ident(login)}"))
            except BaseException:
                if primary is None:
                    raise
                primary.add_note("Owned synthetic serving login cleanup was incomplete")


def _database(db_url: str, *, user: str, password: str, role: str) -> Database:
    parsed = urlparse(db_url)
    return Database(
        db_name="butlers",
        schema="general",
        role=role,
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=user,
        password=password,
        min_pool_size=1,
        max_pool_size=1,
    )


def _connector_role_exists(db_url: str) -> bool:
    return _role_exists(db_url, "connector_writer")


def _require_connector_writer(db_url: str) -> None:
    if not _connector_role_exists(db_url):
        pytest.fail("Source deployment did not install the connector capability")


def _rerun_init_db_as_control(control_db_url: str, migration_user: str, plans: tuple) -> None:
    """Executing successor: converge with the real held finalizer, no old GUC."""
    with migration_session(control_db_url, source_database="butlers") as connection:
        if (
            connection.exec_driver_sql(
                "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
            ).scalar_one()
            != migration_user
        ):
            raise RuntimeError("Normal migration owner changed")
        finalize_authority(connection, source_database="butlers", plans=plans)


def _restore_drill_ledger_effective_privileges(db_url: str, role_name: str) -> dict[str, bool]:
    """Return protected-schema/table privileges through an explicit administrative ACL control."""
    quoted_role = _quote_ident(role_name)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET ROLE {quoted_role}"))
            try:
                return dict(
                    conn.execute(
                        text(
                            """
                            SELECT
                                has_schema_privilege(
                                    current_user,
                                    'restore_drill_executor',
                                    'USAGE'
                                ) AS schema_usage,
                                has_table_privilege(
                                    current_user,
                                    (
                                        SELECT ledger.oid
                                        FROM pg_catalog.pg_class AS ledger
                                        JOIN pg_catalog.pg_namespace AS result_schema
                                            ON result_schema.oid = ledger.relnamespace
                                        WHERE result_schema.nspname = 'restore_drill_executor'
                                          AND ledger.relname = 'restore_drill_results'
                                          AND ledger.relkind = 'r'
                                    ),
                                    'SELECT'
                                ) AS ledger_select,
                                has_table_privilege(
                                    current_user,
                                    (
                                        SELECT ledger.oid
                                        FROM pg_catalog.pg_class AS ledger
                                        JOIN pg_catalog.pg_namespace AS result_schema
                                            ON result_schema.oid = ledger.relnamespace
                                        WHERE result_schema.nspname = 'restore_drill_executor'
                                          AND ledger.relname = 'restore_drill_results'
                                          AND ledger.relkind = 'r'
                                    ),
                                    'INSERT'
                                ) AS ledger_insert
                            """
                        )
                    )
                    .mappings()
                    .one()
                )
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        engine.dispose()


_PUBLIC_WRITE_MATRIX_INSERTS: list[tuple[str, str]] = [
    # (table_name, minimal_INSERT_statement)
    (
        "entities",
        "INSERT INTO public.entities (canonical_name, entity_type)"
        " VALUES ('acl-probe-entity', 'other')",
    ),
    # public.contacts was dropped by core_134 (contacts-schema retirement); it is no
    # longer a writable public table, so it is excluded from this write matrix.
    (
        "entity_info",
        "INSERT INTO public.entity_info (entity_id, type, value)"
        " SELECT id, 'acl-probe-key', 'acl-probe-val' FROM public.entities"
        " WHERE canonical_name = 'acl-probe-entity' LIMIT 1",
    ),
    (
        "google_accounts",
        "INSERT INTO public.google_accounts (entity_id, email)"
        " SELECT id, 'acl-probe-google@example.com' FROM public.entities"
        " WHERE canonical_name = 'acl-probe-entity' LIMIT 1",
    ),
    (
        "steam_accounts",
        "INSERT INTO public.steam_accounts (entity_id, steam_id, display_name)"
        " SELECT id, 76561198000000001, 'acl-probe-steam' FROM public.entities"
        " WHERE canonical_name = 'acl-probe-entity' LIMIT 1",
    ),
    (
        "user_context",
        "INSERT INTO public.user_context (signal_type, set_by_butler, expires_at)"
        " VALUES ('acl-probe-signal', 'general', now() + interval '1 hour')",
    ),
    (
        "model_round_robin_counters",
        "INSERT INTO public.model_round_robin_counters (butler_name, complexity_tier, counter)"
        " VALUES ('acl-probe-butler', 'workhorse', 0)"
        " ON CONFLICT (butler_name, complexity_tier) DO NOTHING",
    ),
    (
        "token_usage_ledger",
        "INSERT INTO public.token_usage_ledger"
        " (catalog_entry_id, session_id, butler_name, input_tokens, output_tokens)"
        " SELECT id, gen_random_uuid(), 'general', 10, 10"
        " FROM public.model_catalog"
        " ORDER BY created_at"
        " LIMIT 1",
    ),
    (
        "ingestion_events",
        "INSERT INTO public.ingestion_events"
        " (id, source_channel, source_provider, source_endpoint_identity,"
        "  external_event_id, dedupe_key, dedupe_strategy, ingestion_tier, policy_tier)"
        " VALUES (gen_random_uuid(), 'acl', 'probe', 'ep-1',"
        "  'ext-1', 'dk-acl-probe-1', 'hash', 'full', 'standard')",
    ),
    (
        "healing_attempts",
        "INSERT INTO public.healing_attempts"
        " (fingerprint, butler_name, severity, exception_type, call_site)"
        " VALUES ('acl-probe-fp', 'general', 3, 'AclProbeError', 'probe.py:1')",
    ),
    (
        "qa_dismissals",
        "INSERT INTO public.qa_dismissals (fingerprint, dismissed_until, dismissed_by)"
        " VALUES ('acl-probe-fp', now() + interval '1 day', 'general')",
    ),
    (
        "qa_findings",
        "WITH patrol AS ("
        "  INSERT INTO public.qa_patrols (status) VALUES ('running') RETURNING id"
        ")"
        " INSERT INTO public.qa_findings"
        " (patrol_id, fingerprint, source_type, source_butler, severity,"
        "  exception_type, event_summary, call_site, first_seen, last_seen)"
        " SELECT id, 'acl-probe-fp', 'log_scanner', 'general', 3,"
        "  'AclProbeError', 'probe', 'probe.py:1', now(), now()"
        " FROM patrol",
    ),
    (
        "qa_repo_config",
        # qa_repo_config is only UPDATE-granted (not INSERT); the row is pre-seeded
        # by test_set_role_allows_public_table_writes via admin before the role loop.
        "UPDATE public.qa_repo_config"
        " SET repo_url = 'https://example.com/acl-probe-updated.git'"
        " WHERE singleton = true",
    ),
    (
        "qa_patrols",
        "INSERT INTO public.qa_patrols (status) VALUES ('running')",
    ),
    (
        "memory_catalog",
        "INSERT INTO public.memory_catalog"
        " (source_schema, source_table, source_id)"
        " SELECT 'general', 'facts', id FROM general.facts WHERE subject = 'acl-probe-memory'",
    ),
    (
        "insight_candidates",
        "INSERT INTO public.insight_candidates"
        " (origin_butler, priority, category, dedup_key, expires_at, message)"
        " VALUES ('general', 1, 'acl', 'acl-probe-dk', now() + interval '1 day', 'probe')",
    ),
    (
        "insight_cooldowns",
        "INSERT INTO public.insight_cooldowns (dedup_key, cooldown_until)"
        " VALUES ('acl-cooldown-probe', now() + interval '1 day')"
        " ON CONFLICT (dedup_key) DO NOTHING",
    ),
    (
        "insight_engagement",
        "INSERT INTO public.insight_engagement"
        " (insight_id, engaged)"
        " SELECT id, true FROM public.insight_candidates"
        " WHERE dedup_key = 'acl-probe-dk' LIMIT 1",
    ),
    (
        "insight_settings",
        "UPDATE public.insight_settings SET verbosity = 'normal' WHERE id = 1",
    ),
    (
        "expected_signals",
        "INSERT INTO public.expected_signals "
        "(signal_key, producer, expected_cadence_seconds, last_observed_at, "
        "measurability, evaluated_at) "
        "VALUES ('general:acl-probe', 'owner', 3600, now(), 'present', now())",
    ),
]


def test_runtime_roles_are_limited_to_own_schema_and_shared(postgres_container):
    """Own state/shared non-DND producer positive; foreign/CREATE refusal."""
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)
    control_url = _control_url(postgres_container)
    shared_key = f"acl-shared-{uuid.uuid4().hex}"
    with _engine_connection(control_url) as connection:
        connection.execute(
            text(
                "INSERT INTO public.user_context(signal_type,set_by_butler,expires_at) "
                "VALUES (:key,'shared-ok',now()+interval '1 hour')"
            ),
            {"key": shared_key},
        )
        # Deliberately undeclared negative target: no runtime gets a grant.
        connection.execute(text("CREATE TABLE public.acl_probe_shared(id integer)"))
    try:
        for probe_id, (owned_schema, runtime_role) in enumerate(_RUNTIME_ROLES.items(), start=2):
            key = f"acl-own-{probe_id}"
            _execute_as_role(
                control_url,
                runtime_role,
                f"INSERT INTO {owned_schema}.state(key,value) "
                f"VALUES ('{key}','\"{owned_schema}-ok\"'::jsonb)",
            )
            own_note = _execute_as_role(
                control_url,
                runtime_role,
                f"SELECT value FROM {owned_schema}.state WHERE key='{key}'",
                scalar=True,
            )
            assert own_note == f"{owned_schema}-ok"
            shared_note = _execute_as_role(
                control_url,
                runtime_role,
                f"SELECT set_by_butler FROM public.user_context WHERE signal_type='{shared_key}'",
                scalar=True,
            )
            assert shared_note == "shared-ok"
            signal = f"acl-shared-write-{probe_id}"
            _execute_as_role(
                control_url,
                runtime_role,
                "INSERT INTO public.user_context(signal_type,set_by_butler,expires_at) "
                f"VALUES ('{signal}','shared-write-ok',now()+interval '1 hour')",
            )
            shared_write_note = _execute_as_role(
                control_url,
                runtime_role,
                f"SELECT set_by_butler FROM public.user_context WHERE signal_type='{signal}'",
                scalar=True,
            )
            assert shared_write_note == "shared-write-ok"
            for blocked_schema in _BUTLER_SCHEMAS:
                if blocked_schema != owned_schema:
                    with pytest.raises(ProgrammingError, match="permission denied"):
                        _execute_as_role(
                            control_url,
                            runtime_role,
                            f"SELECT key FROM {blocked_schema}.state LIMIT 1",
                        )
            for sql in (
                f"CREATE TABLE {owned_schema}.acl_runtime_create(id int)",
                "SELECT * FROM public.acl_probe_shared",
            ):
                with pytest.raises(ProgrammingError, match="permission denied"):
                    _execute_as_role(control_url, runtime_role, sql)
    finally:
        with _engine_connection(control_url) as connection:
            connection.execute(text("DROP TABLE public.acl_probe_shared"))


def test_privileged_cross_schema_aggregate_reads_are_allowed(postgres_container):
    """Only the explicit administrative observer can intentionally fan out."""
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)
    control_url = _control_url(postgres_container)
    for schema, keys in (
        ("general", ("acl-fanout-1", "acl-fanout-2")),
        ("health", ("acl-fanout-3",)),
    ):
        for key in keys:
            _execute_as_role(
                control_url,
                _RUNTIME_ROLES[schema],
                f"INSERT INTO {schema}.state(key,value) VALUES ('{key}','true'::jsonb)",
            )
    fanout = (
        "SELECT (SELECT count(*) FROM general.state WHERE key LIKE 'acl-fanout-%') "
        "+ (SELECT count(*) FROM health.state WHERE key LIKE 'acl-fanout-%')"
    )
    total = _scalar(control_url, fanout)
    assert total == 3
    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(control_url, _RUNTIME_ROLES["general"], fanout, scalar=True)


def test_set_role_enforces_own_schema_write(postgres_container):
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)
    control_url = _control_url(postgres_container)
    _execute_as_role(
        control_url,
        _RUNTIME_ROLES["general"],
        "INSERT INTO general.state(key,value) VALUES ('acl-probe-own','\"ok\"'::jsonb)",
    )
    note = _execute_as_role(
        control_url,
        _RUNTIME_ROLES["general"],
        "SELECT value FROM general.state WHERE key='acl-probe-own'",
        scalar=True,
    )
    assert note == "ok"


def test_set_role_blocks_cross_schema_write(postgres_container):
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)
    with pytest.raises(ProgrammingError, match="permission denied"):
        _execute_as_role(
            _control_url(postgres_container),
            _RUNTIME_ROLES["general"],
            "INSERT INTO health.state(key,value) VALUES ('acl-probe-cross','\"blocked\"'::jsonb)",
        )


# Fixed adopted producer roles, not role authority inferred from success.
_PUBLIC_WRITERS = {
    "google_accounts": "connector_writer",
    "steam_accounts": "connector_writer",
    "ingestion_events": "connector_writer",
    "qa_dismissals": "butler_qa_rw",
    "qa_findings": "butler_qa_rw",
    "qa_repo_config": "butler_qa_rw",
    "qa_patrols": "butler_qa_rw",
    "insight_cooldowns": "butler_switchboard_rw",
    "insight_engagement": "butler_switchboard_rw",
    "insight_settings": "butler_switchboard_rw",
}
# Independent committed readback includes every original payload/FK-dependent row.
_PUBLIC_WITNESSES = {
    "entities": "canonical_name='acl-probe-entity' AND entity_type='other'",
    ("entity_info"): (
        "type='acl-probe-key' AND value='acl-probe-val' AND entity_id IN "
        "(SELECT id FROM public.entities WHERE "
        "canonical_name='acl-probe-entity')"
    ),
    ("google_accounts"): (
        "email='acl-probe-google@example.com' AND entity_id IN (SELECT id "
        "FROM public.entities WHERE canonical_name='acl-probe-entity')"
    ),
    ("steam_accounts"): (
        "steam_id=76561198000000001 AND display_name='acl-probe-steam' "
        "AND entity_id IN (SELECT id FROM public.entities WHERE "
        "canonical_name='acl-probe-entity')"
    ),
    ("user_context"): (
        "signal_type='acl-probe-signal' AND set_by_butler='general' AND expires_at>now()"
    ),
    ("model_round_robin_counters"): (
        "butler_name='acl-probe-butler' AND complexity_tier='workhorse' AND counter=0"
    ),
    ("token_usage_ledger"): (
        "butler_name='general' AND input_tokens=10 AND output_tokens=10 "
        "AND session_id IS NOT NULL AND catalog_entry_id IN (SELECT id "
        "FROM public.model_catalog)"
    ),
    ("ingestion_events"): (
        "source_channel='acl' AND source_provider='probe' AND "
        "source_endpoint_identity='ep-1' AND external_event_id='ext-1' "
        "AND dedupe_key='dk-acl-probe-1' AND dedupe_strategy='hash' AND "
        "ingestion_tier='full' AND policy_tier='standard'"
    ),
    ("healing_attempts"): (
        "fingerprint='acl-probe-fp' AND butler_name='general' AND "
        "severity=3 AND exception_type='AclProbeError' AND "
        "call_site='probe.py:1'"
    ),
    ("qa_dismissals"): (
        "fingerprint='acl-probe-fp' AND dismissed_by='general' AND dismissed_until>now()"
    ),
    ("qa_findings"): (
        "fingerprint='acl-probe-fp' AND source_type='log_scanner' AND "
        "source_butler='general' AND severity=3 AND "
        "exception_type='AclProbeError' AND event_summary='probe' AND "
        "call_site='probe.py:1' AND patrol_id IN (SELECT id FROM "
        "public.qa_patrols WHERE status='running')"
    ),
    "qa_repo_config": "singleton=true AND repo_url='https://example.com/acl-probe-updated.git'",
    "qa_patrols": "status='running'",
    ("memory_catalog"): (
        "source_schema='general' AND source_table='facts' AND source_id "
        "IN (SELECT id FROM general.facts WHERE "
        "subject='acl-probe-memory' AND predicate='acl' AND "
        "content='probe')"
    ),
    ("insight_candidates"): (
        "origin_butler='general' AND priority=1 AND category='acl' AND "
        "dedup_key='acl-probe-dk' AND expires_at>now() AND message='probe'"
    ),
    "insight_cooldowns": "dedup_key='acl-cooldown-probe' AND cooldown_until>now()",
    ("insight_engagement"): (
        "engaged=true AND insight_id IN (SELECT id FROM "
        "public.insight_candidates WHERE dedup_key='acl-probe-dk')"
    ),
    "insight_settings": "id=1 AND verbosity='normal'",
    ("expected_signals"): (
        "signal_key='general:acl-probe' AND producer='owner' AND "
        "producer_role='butler_general_rw' AND "
        "expected_cadence_seconds=3600 AND measurability='present' AND "
        "last_observed_at IS NOT NULL AND evaluated_at IS NOT NULL"
    ),
}


def test_set_role_allows_public_table_writes(postgres_container):
    """All 19 rows retain real owning positives, with explicit retired history.

    The old blanket General write promise has an executing successor for every
    moved producer: declared writer commits/readback, General write is denied.
    Retired healing attempts are administratively seeded history, QA-readable
    but not writable by any serving role. No healing producer is resurrected.
    """
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)
    control_url = _control_url(postgres_container)
    with _engine_connection(control_url) as connection:
        connection.execute(
            text(
                "INSERT INTO public.qa_repo_config(repo_url) VALUES "
                "('https://example.com/acl-probe.git') ON CONFLICT DO NOTHING"
            )
        )
        connection.execute(
            text(
                "INSERT INTO public.model_catalog(alias,runtime_type,model_id) "
                "VALUES ('acl-probe-model','claude','acl-probe-model') ON "
                "CONFLICT(alias) DO NOTHING"
            )
        )
    # Force an observable singleton UPDATE, rather than accepting a no-op.
    with _engine_connection(control_url) as connection:
        connection.execute(
            text("UPDATE public.insight_settings SET verbosity='minimal' WHERE id=1")
        )
    # Real memory row creates the catalog provenance referent under its owner.
    _execute_as_role(
        control_url,
        _RUNTIME_ROLES["general"],
        (
            "INSERT INTO general.facts(subject,predicate,content) VALUES "
            "('acl-probe-memory','acl','probe')"
        ),
    )
    for table_name, insert_sql in _PUBLIC_WRITE_MATRIX_INSERTS:
        witness = _PUBLIC_WITNESSES[table_name]
        before = _scalar(control_url, f"SELECT count(*) FROM public.{table_name} WHERE {witness}")
        if table_name == "healing_attempts":
            with _engine_connection(control_url) as connection:
                assert connection.execute(text(insert_sql)).rowcount == 1
            assert (
                _execute_as_role(
                    control_url,
                    "butler_qa_rw",
                    f"SELECT count(*) FROM public.healing_attempts WHERE {witness}",
                    scalar=True,
                )
                == 1
            )
            for role in (profile.role for profile in runtime_profiles("butlers")):
                with pytest.raises(ProgrammingError, match="permission denied"):
                    _execute_as_role(control_url, role, insert_sql)
        else:
            role = _PUBLIC_WRITERS.get(table_name, _RUNTIME_ROLES["general"])
            assert _execute_as_role(control_url, role, insert_sql) == 1, table_name
            if role != _RUNTIME_ROLES["general"]:
                with pytest.raises(ProgrammingError, match="permission denied"):
                    _execute_as_role(control_url, _RUNTIME_ROLES["general"], insert_sql)
        after = _scalar(control_url, f"SELECT count(*) FROM public.{table_name} WHERE {witness}")
        # Both singleton UPDATE rows are seeded to a different value above or
        # by the real migration; all other statements must add one actual row.
        assert after == (
            1 if table_name in {"qa_repo_config", "insight_settings"} else before + 1
        ), table_name


def test_set_role_blocks_private_restore_drill_ledger_for_every_runtime_role(postgres_container):
    """Negative-only ACL fault injection must be repaired by the real finalizer."""
    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)
    _require_runtime_acl(db_url)
    assert all(_role_exists(db_url, role) for role in _RESTORE_DRILL_DENIED_ROLES)
    migration_user = urlparse(db_url).username
    assert migration_user is not None
    control_db_url = _control_url(postgres_container)
    plans = postgres_container._schema_acl_deployment[2]
    try:
        with _engine_connection(control_db_url) as conn:
            for role in _RESTORE_DRILL_DENIED_ROLES:
                quoted_role = _quote_ident(role)
                conn.execute(text(f"GRANT USAGE ON SCHEMA restore_drill_executor TO {quoted_role}"))
                conn.execute(
                    text(
                        "GRANT SELECT, INSERT ON TABLE "
                        "restore_drill_executor.restore_drill_results "
                        f"TO {quoted_role}"
                    )
                )
        expected_legacy_grant = {"schema_usage": True, "ledger_select": True, "ledger_insert": True}
        for role in _RESTORE_DRILL_DENIED_ROLES:
            assert (
                _restore_drill_ledger_effective_privileges(control_db_url, role)
                == expected_legacy_grant
            )
    finally:
        _rerun_init_db_as_control(control_db_url, migration_user, plans)
    expected_denial = {"schema_usage": False, "ledger_select": False, "ledger_insert": False}
    for role in _RESTORE_DRILL_DENIED_ROLES:
        assert _restore_drill_ledger_effective_privileges(control_db_url, role) == expected_denial


def test_connector_writer_role_enforcement(postgres_container, request):
    """Exact connector partition/ingestion producers, foreign state denial."""
    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)
    _require_runtime_acl(db_url)
    _require_connector_writer(db_url)
    control_db_url = _control_url(postgres_container)
    with _serving_login(control_db_url, "connector_writer") as (session_role, _):
        _execute_as_role_via_session_auth(
            control_db_url,
            session_role,
            "connector_writer",
            "SELECT connectors.connectors_filtered_events_ensure_partition(now())",
        )
        assert (
            _execute_as_role_via_session_auth(
                control_db_url,
                session_role,
                "connector_writer",
                (
                    "INSERT INTO connectors.filtered_events(connector_type,endpoint_id"
                    "entity,external_message_id,source_channel,sender_identity,filter_"
                    "reason,full_payload) VALUES "
                    "('probe','ep-connector','ext-connector-1','acl','sender-1','acl-p"
                    "robe','{}'::jsonb)"
                ),
            )
            == 1
        )
        assert (
            _scalar(
                control_db_url,
                (
                    "SELECT count(*) FROM connectors.filtered_events WHERE "
                    "connector_type='probe' AND endpoint_identity='ep-connector' AND "
                    "external_message_id='ext-connector-1' AND source_channel='acl' "
                    "AND sender_identity='sender-1' AND filter_reason='acl-probe' AND "
                    "full_payload='{}'::jsonb"
                ),
            )
            == 1
        )
        assert (
            _execute_as_role_via_session_auth(
                control_db_url,
                session_role,
                "connector_writer",
                (
                    "INSERT INTO public.ingestion_events(id,source_channel,source_prov"
                    "ider,source_endpoint_identity,external_event_id,dedupe_key,dedupe"
                    "_strategy,ingestion_tier,policy_tier) VALUES "
                    "(gen_random_uuid(),'connector-acl','probe','ep-2','ext-2','dk-con"
                    "nector-probe-1','hash','full','standard')"
                ),
            )
            == 1
        )
        assert (
            _scalar(
                control_db_url,
                (
                    "SELECT count(*) FROM public.ingestion_events WHERE "
                    "source_channel='connector-acl' AND source_provider='probe' AND "
                    "source_endpoint_identity='ep-2' AND external_event_id='ext-2' "
                    "AND dedupe_key='dk-connector-probe-1' AND dedupe_strategy='hash' "
                    "AND ingestion_tier='full' AND policy_tier='standard'"
                ),
            )
            == 1
        )
        with pytest.raises(ProgrammingError, match="permission denied"):
            _execute_as_role_via_session_auth(
                control_db_url,
                session_role,
                "connector_writer",
                (
                    "INSERT INTO general.state(key,value) VALUES "
                    "('connector-probe-blocked','\"blocked\"'::jsonb)"
                ),
            )
        with pytest.raises(ProgrammingError, match="permission denied"):
            _execute_as_role_via_session_auth(
                control_db_url,
                session_role,
                "connector_writer",
                "CREATE TABLE connectors.acl_runtime_create(id int)",
            )


def test_role_absent_refuses_without_shared_user_pool(postgres_container):
    """Unknown named role is refused without a usable shared-user pool."""
    db_url = _create_db(postgres_container, _unique_db_name())
    parsed = urlparse(db_url)
    db = Database(
        db_name="butlers",
        role="butler_nonexistent_role_xyz",
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        min_pool_size=0,
        max_pool_size=1,
    )

    async def _exercise():
        try:
            with pytest.raises(RuntimeError, match="Runtime database identity is not declared"):
                await db.connect()
            assert db.pool is None
            assert not db._role_verified
            # Explicit successor to the retired opt-out diagnostic: neither
            # a verified capability nor a usable pool has been published.
            assert db._verified_role is None
        finally:
            await db.close()

    asyncio.run(_exercise())


def test_role_reset_on_connection_return(postgres_container):
    """Real dedicated LOGIN/session_user, native reset and every-acquire setup."""
    db_url = _create_db(postgres_container, _unique_db_name())
    _require_runtime_acl(db_url)
    role = _RUNTIME_ROLES["general"]
    with _serving_login(_control_url(postgres_container), role) as (login, password):

        async def _run() -> None:
            db = _database(db_url, user=login, password=password, role=role)
            try:
                pool = await db.connect()
                assert db._role_verified, "Role should have been verified on connect()"
                async with pool.acquire() as conn:
                    current_role = await conn.fetchval("SELECT current_user")
                    assert current_role == role, (
                        f"Expected role {role!r} after acquire, got {current_role!r}"
                    )
                    assert await conn.fetchval("SELECT session_user") == login
                    first_backend = await conn.fetchval("SELECT pg_backend_pid()")
                async with pool.acquire() as conn:
                    current_role_after_reset = await conn.fetchval("SELECT current_user")
                    assert current_role_after_reset == role, (
                        f"Expected role {role!r} after re-acquire (post-RESET ALL), "
                        f"got {current_role_after_reset!r}"
                    )
                    assert await conn.fetchval("SELECT session_user") == login
                    assert await conn.fetchval("SELECT pg_backend_pid()") == first_backend
            finally:
                await db.close()

        asyncio.run(_run())


def test_role_reset_requires_normal_migration_login_membership(postgres_container):
    """Successor: migrator cannot serve; dedicated membership is checked anew."""
    db_name = _unique_db_name()
    db_url = _create_db(postgres_container, db_name)
    _require_runtime_acl(db_url)
    role = _RUNTIME_ROLES["general"]
    parsed = urlparse(db_url)
    migration_user = parsed.username
    assert migration_user is not None
    with _engine_connection(db_url) as connection:
        login_attributes = connection.execute(
            text(
                "SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user"
            )
        ).one()
    assert login_attributes == (False, False, False)
    control_url = _control_url(postgres_container)

    async def _refuse_migrator():
        db = _database(
            db_url, user=unquote(migration_user), password=unquote(parsed.password or ""), role=role
        )
        try:
            with pytest.raises(RuntimeError, match="role acquisition failed"):
                await db.connect()
            assert db.pool is None
        finally:
            await db.close()

    asyncio.run(_refuse_migrator())
    with _serving_login(control_url, role) as (login, password):
        db = _database(db_url, user=login, password=password, role=role)

        async def _run() -> None:
            try:
                pool = await db.connect()
                async with pool.acquire() as conn:
                    assert await conn.fetchval("SELECT current_user") == role
                try:
                    with _engine_connection(control_url) as control:
                        control.execute(
                            text(f"REVOKE {_quote_ident(role)} FROM {_quote_ident(login)}")
                        )
                    with pytest.raises(RuntimeError, match="role acquisition failed") as error:
                        async with pool.acquire():
                            pytest.fail("revoked connection reached caller")
                    assert isinstance(
                        error.value.__cause__, asyncpg.exceptions.InsufficientPrivilegeError
                    )
                finally:
                    with _engine_connection(control_url) as control:
                        control.execute(
                            text(
                                f"GRANT {_quote_ident(role)} TO {_quote_ident(login)} WITH SET TRUE"
                            )
                        )
                # Actual object-catalog drift also invalidates fresh admission.
                try:
                    with _engine_connection(control_url) as control:
                        control.execute(text("ALTER TABLE general.state OWNER TO CURRENT_USER"))
                    with pytest.raises(RuntimeError, match="role acquisition failed"):
                        async with pool.acquire():
                            pytest.fail("owner-drifted connection reached caller")
                finally:
                    with _engine_connection(control_url) as control:
                        control.execute(
                            text(
                                f"ALTER TABLE general.state OWNER TO {_quote_ident(migration_user)}"
                            )
                        )
                async with pool.acquire() as conn:
                    assert await conn.fetchval("SELECT current_user") == role
                    assert await conn.fetchval("SELECT session_user") == login
            finally:
                await db.close()

        asyncio.run(_run())


def _native_dsn(db_url: str, login: str, password: str) -> str:
    """Synthetic fixture transport, with the actual deployed source identity."""
    from urllib.parse import quote

    parsed = urlparse(db_url)
    return (
        f"postgresql://{quote(login, safe='')}:{quote(password, safe='')}"
        f"@{parsed.hostname}:{parsed.port}/butlers?sslmode=disable"
    )


@contextmanager
def _foreign_membership(control_url: str, login: str):
    """Committed negative fault only; remove precisely the owned fault."""
    with _engine_connection(control_url) as connection:
        assert not connection.execute(
            text("SELECT pg_has_role(:login,'butler_general_rw','MEMBER')"), {"login": login}
        ).scalar_one()
        connection.execute(text(f'GRANT "butler_general_rw" TO {_quote_ident(login)}'))
    try:
        yield
    finally:
        with _engine_connection(control_url) as connection:
            connection.execute(text(f'REVOKE "butler_general_rw" FROM {_quote_ident(login)}'))


async def _native_identity(pool, login: str, role: str) -> None:
    async with pool.acquire() as connection:
        row = await connection.fetchrow("SELECT session_user,current_user,current_database()")
        assert tuple(row) == (login, role, "butlers")


async def _close_native_pool(pool) -> None:
    try:
        async with asyncio.timeout(5):
            await pool.close()
    except BaseException:
        pool.terminate()
        raise


def test_actual_native_credential_factory_keeps_writer_identity_and_fences_rotation(
    postgres_container, monkeypatch
):
    """Actual credential factory, native login, CAS and fresh drift refusal."""
    import json

    from butlers.credential_store import create_system_global_credential_store_from_env
    from butlers.db_authority.roles import AuthorityError

    db_url = _create_db(postgres_container, _unique_db_name())
    control = _control_url(postgres_container)
    role = purpose_profile("butlers", "credential").role
    with _serving_login(control, role) as (login, password):
        monkeypatch.setenv("BUTLERS_CREDENTIAL_DATABASE_URL", _native_dsn(db_url, login, password))
        monkeypatch.setenv("BUTLER_SHARED_DB_SCHEMA", "public")

        async def exercise():
            store = await create_system_global_credential_store_from_env()
            pool = store.require_system_global_pool()
            key = f"native-proof/{uuid.uuid4().hex}"
            old = None
            old_loaded = False
            first = json.dumps({"synthetic_native_proof": uuid.uuid4().hex})
            second = json.dumps({"synthetic_native_proof": uuid.uuid4().hex})
            try:
                old = await store.load_codex_cli_auth()
                old_loaded = True
                await _native_identity(pool, login, role)
                await store.store_codex_cli_auth(first)
                assert await store.store_codex_cli_auth_if_unchanged(second, expected_value=first)
                assert not await store.store_codex_cli_auth_if_unchanged(
                    first, expected_value=first
                )
                assert await store.record_codex_cli_auth_test_result_if_unchanged(
                    ok=True, expected_value=second
                )
                assert not await store.record_codex_cli_auth_test_result_if_unchanged(
                    ok=False, expected_value=first
                )
                assert await store.store_shared_if_unchanged(key, first, expected_value=None)
                assert await store.record_test_result_if_unchanged(
                    key, True, expected_value=first, use_shared_authority=True
                )
                assert (
                    _scalar(
                        control,
                        "SELECT secret_value FROM public.butler_secrets WHERE secret_key=:k",
                        {"k": "cli-auth/codex"},
                    )
                    == second
                )
                with _foreign_membership(control, login):
                    with pytest.raises(AuthorityError):
                        async with pool.acquire():
                            pytest.fail("Unsafe credential identity was handed out")
                    assert (
                        _scalar(
                            control,
                            "SELECT secret_value FROM public.butler_secrets WHERE secret_key=:k",
                            {"k": "cli-auth/codex"},
                        )
                        == second
                    )
                await _native_identity(pool, login, role)
                assert await store.load_codex_cli_auth() == second
            finally:
                await _close_native_pool(pool)
                with _engine_connection(control) as connection:
                    connection.execute(
                        text("DELETE FROM public.butler_secrets WHERE secret_key=:k"), {"k": key}
                    )
                    if not old_loaded:
                        # A failed initial read must never erase prior state.
                        pass
                    elif old is None:
                        connection.execute(
                            text("DELETE FROM public.butler_secrets WHERE secret_key=:k"),
                            {"k": "cli-auth/codex"},
                        )
                    else:
                        connection.execute(
                            text(
                                "UPDATE public.butler_secrets SET secret_value=:v WHERE secret_key=:k"
                            ),
                            {"k": "cli-auth/codex", "v": old},
                        )
            monkeypatch.delenv("BUTLERS_CREDENTIAL_DATABASE_URL")
            with pytest.raises(AuthorityError):
                await create_system_global_credential_store_from_env()
            monkeypatch.setenv(
                "BUTLERS_CREDENTIAL_DATABASE_URL",
                _native_dsn(db_url, login, password).replace("/butlers?", "/not_declared?"),
            )
            with pytest.raises((AuthorityError, ValueError)):
                await create_system_global_credential_store_from_env()

        asyncio.run(exercise())
    with _serving_login(control, "butler_general_rw") as (login, password):
        monkeypatch.setenv("BUTLERS_CREDENTIAL_DATABASE_URL", _native_dsn(db_url, login, password))
        with pytest.raises(AuthorityError):
            asyncio.run(create_system_global_credential_store_from_env())


def test_actual_native_dashboard_manager_proves_all_owned_pools_and_reacquires(postgres_container):
    """Both real manager pools prove the same dedicated session on every checkout."""
    from butlers.api.db import DatabaseManager
    from butlers.db_authority.roles import AuthorityError

    db_url = _create_db(postgres_container, _unique_db_name())
    control = _control_url(postgres_container)
    parsed = urlparse(db_url)
    role = purpose_profile("butlers", "dashboard").role
    with _serving_login(control, role) as (login, password):

        async def exercise():
            options = dict(
                host=parsed.hostname,
                port=parsed.port,
                user=login,
                password=password,
                ssl="disable",
                min_pool_size=0,
                max_pool_size=1,
            )
            manager = DatabaseManager(**options)
            try:
                await manager.add_butler(
                    "general", "butlers", "general", modules=frozenset({"memory"})
                )
                await manager.set_credential_shared_pool("butlers", "public")
                assert manager.butlers_with_module("memory") == ["general"]
                pools = (manager.pool("general"), manager.credential_shared_pool())
                for pool in pools:
                    await _native_identity(pool, login, role)
                    async with pool.acquire() as connection:
                        assert isinstance(
                            await connection.fetchval("SELECT count(*) FROM general.state_store"),
                            int,
                        )
                        with pytest.raises(asyncpg.InsufficientPrivilegeError):
                            await connection.execute(
                                "INSERT INTO general.state_store(key,value) VALUES ('native-dashboard-forbidden','{}')"
                            )
                assert (
                    _scalar(
                        control,
                        "SELECT count(*) FROM general.state_store WHERE key='native-dashboard-forbidden'",
                    )
                    == 0
                )
                with _foreign_membership(control, login):
                    for pool in pools:
                        with pytest.raises(AuthorityError):
                            async with pool.acquire():
                                pytest.fail("Unsafe dashboard connection was handed out")
                    failed = DatabaseManager(**options)
                    try:
                        with pytest.raises(AuthorityError):
                            await failed.add_butler("general", "butlers", "general")
                        with pytest.raises(KeyError):
                            failed.pool("general")
                    finally:
                        await failed.close()
                for pool in pools:
                    await _native_identity(pool, login, role)
            finally:
                async with asyncio.timeout(10):
                    await manager.close()

        asyncio.run(exercise())


def test_actual_native_restore_executor_loop_uses_fixed_login_and_rechecks(
    postgres_container, monkeypatch, tmp_path
):
    """Run the real loop/persistence as the fixed direct LOGIN, without a restore."""
    import subprocess
    from pathlib import Path

    from butlers.db_authority.roles import AuthorityError
    from butlers.jobs.restore_drill_executor import (
        PostgresRestoreDrillPersistence,
        RestoreDrillExecutorConfig,
        run_restore_drill_executor_loop,
    )

    _create_db(postgres_container, _unique_db_name())
    control = _control_url(postgres_container)
    admin = urlparse(control)
    password = uuid.uuid4().hex
    secret = tmp_path / "synthetic-executor-password"
    secret.write_text(password)
    secret.chmod(0o600)
    try:
        provision = subprocess.run(
            [
                str(
                    Path(__file__).resolve().parents[2]
                    / "scripts/provision_restore_drill_executor.sh"
                )
            ],
            env={
                **os.environ,
                "PGHOST": admin.hostname,
                "PGPORT": str(admin.port),
                "PGUSER": unquote(admin.username),
                "PGPASSWORD": unquote(admin.password),
                "PGDATABASE": "butlers",
                "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE": str(secret),
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert provision.returncode == 0, "Source executor provisioning did not complete"
    finally:
        secret.unlink()
    backup = tmp_path / "empty-backups"
    backup.mkdir()
    config = RestoreDrillExecutorConfig(
        host=admin.hostname,
        port=admin.port,
        application_db="butlers",
        maintenance_db="postgres",
        user="restore_drill_executor",
        password=password,
        backup_dir=backup,
        drill_interval_s=3600,
        check_interval_s=1,
        sslmode="disable",
    )
    original = PostgresRestoreDrillPersistence.is_due

    async def exercise():
        observations = asyncio.Queue()

        async def observe(self, interval):
            try:
                result = await original(self, interval)
            except AuthorityError:
                observations.put_nowait(("refused", self._pool))
                raise
            observations.put_nowait(("admitted", self._pool))
            return result

        monkeypatch.setattr(PostgresRestoreDrillPersistence, "is_due", observe)
        task = asyncio.create_task(run_restore_drill_executor_loop(config))
        pool = None
        try:
            async with asyncio.timeout(20):
                status, pool = await observations.get()
                assert status == "admitted"
                await _native_identity(pool, "restore_drill_executor", "restore_drill_executor")
                persistence = PostgresRestoreDrillPersistence(pool)
                result_id = await persistence.record_result(
                    backup_name="native-proof.dump",
                    result="pass",
                    detail="synthetic proof",
                    table_count=0,
                )
                assert (
                    _scalar(
                        control,
                        "SELECT count(*) FROM restore_drill_executor.restore_drill_results WHERE id=:id AND result='pass'",
                        {"id": result_id},
                    )
                    == 1
                )
                # Drain only already-observed healthy ticks before the committed fault.
                while not observations.empty():
                    observations.get_nowait()
                with _foreign_membership(control, "restore_drill_executor"):
                    while (await observations.get())[0] != "refused":
                        pass
                    with pytest.raises(AuthorityError):
                        async with pool.acquire():
                            pytest.fail("Unsafe fixed executor connection was handed out")
                while (await observations.get())[0] != "admitted":
                    pass
                await _native_identity(pool, "restore_drill_executor", "restore_drill_executor")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                async with asyncio.timeout(10):
                    await task
            assert pool is not None and pool._closed

    asyncio.run(exercise())


def test_actual_native_operator_deploy_pool_keeps_selected_session_and_ledger_boundary(
    postgres_container, monkeypatch
):
    """Administrative operator proof stays distinct from serving role admission."""
    from butlers.core.deploy import DeployError, _make_pool
    from butlers.db_authority.roles import AuthorityError

    db_url = _create_db(postgres_container, _unique_db_name())
    control = _control_url(postgres_container)
    selected = urlparse(db_url)
    normal = unquote(selected.username)
    monkeypatch.setenv(
        "BUTLERS_DEPLOY_DATABASE_URL", _native_dsn(db_url, normal, unquote(selected.password))
    )

    async def positive():
        pool = await _make_pool()
        marker = uuid.uuid4().hex
        try:
            await _native_identity(pool, normal, normal)
            async with pool.acquire() as connection:
                row_id = await connection.fetchval(
                    "INSERT INTO public.deployments(git_sha,result) VALUES ($1,'success') RETURNING id",
                    marker,
                )
            assert (
                _scalar(
                    control, "SELECT git_sha FROM public.deployments WHERE id=:id", {"id": row_id}
                )
                == marker
            )
            await _native_identity(pool, normal, normal)
        finally:
            await _close_native_pool(pool)

    asyncio.run(positive())
    monkeypatch.delenv("BUTLERS_DEPLOY_DATABASE_URL")
    with pytest.raises(DeployError):
        asyncio.run(_make_pool())
    for forbidden in ("butler_general_rw", "dashboard_api", "restore_drill_executor"):
        monkeypatch.setenv(
            "BUTLERS_DEPLOY_DATABASE_URL", _native_dsn(db_url, forbidden, "synthetic")
        )
        with pytest.raises(AuthorityError):
            asyncio.run(_make_pool())
    # A genuine selected native login with no ledger authority must not be
    # published. No fixture object grant supplies the missing positive.
    login = f"operator_denied_{uuid.uuid4().hex}"
    password = uuid.uuid4().hex
    created = False
    try:
        with _engine_connection(control) as connection:
            connection.execute(
                text(
                    f"CREATE ROLE {_quote_ident(login)} LOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOSUPERUSER NOREPLICATION NOBYPASSRLS PASSWORD :password"
                ),
                {"password": password},
            )
            created = True
        monkeypatch.setenv("BUTLERS_DEPLOY_DATABASE_URL", _native_dsn(db_url, login, password))

        async def missing_ledger():
            # Transport success is a prerequisite to this negative: CONNECT or
            # authentication refusal cannot stand in for same-session ledger
            # admission. No positive object privilege is injected.
            connection = await asyncpg.connect(_native_dsn(db_url, login, password), timeout=5)
            try:
                assert tuple(
                    await connection.fetchrow("SELECT session_user,current_user,current_database()")
                ) == (login, login, "butlers")
                assert tuple(
                    await connection.fetchrow(
                        "SELECT has_table_privilege(current_user,'public.deployments','SELECT'), "
                        "has_table_privilege(current_user,'public.deployments','INSERT')"
                    )
                ) == (False, False)
            finally:
                try:
                    await connection.close(timeout=5)
                except BaseException:
                    connection.terminate()
                    raise
            with pytest.raises(
                AuthorityError, match="Operator deployment authority is unavailable"
            ):
                await _make_pool()

        asyncio.run(missing_ledger())
    finally:
        if created:
            with _engine_connection(control) as connection:
                connection.execute(text(f"DROP ROLE {_quote_ident(login)}"))


def test_actual_native_connector_cursor_factory_keeps_login_and_fences_reacquisition(
    postgres_container, monkeypatch
):
    """Physical cursor-factory identity, committed checkpoint and fresh drift fence."""
    from butlers.connectors import cursor_store, db_role
    from butlers.db_authority.roles import AuthorityError

    db_url = _create_db(postgres_container, _unique_db_name())
    control = _control_url(postgres_container)
    parsed = urlparse(db_url)
    role = purpose_profile("butlers", "connector").role
    endpoint = f"native-cursor-{uuid.uuid4().hex}"
    value = "synthetic-native-cursor"
    with _serving_login(control, role) as (login, password):

        async def exercise():
            pools = []
            primary = None
            try:
                # Actual factory callback inverse: a physical NOINHERIT LOGIN
                # without setup must fail the real current_user assertion.
                async def omit_admission(connection):
                    return None

                with monkeypatch.context() as inverse:
                    inverse.setattr(db_role, "connector_setup_role", omit_admission)
                    unchecked = await cursor_store.create_cursor_pool(
                        host=parsed.hostname or "localhost",
                        port=parsed.port or 5432,
                        user=login,
                        password=password,
                        database="butlers",
                        ssl="disable",
                        min_size=1,
                        max_size=1,
                    )
                    pools.append(unchecked)
                    with pytest.raises(AssertionError):
                        await _native_identity(unchecked, login, role)
                    await _close_native_pool(unchecked)
                    pools.remove(unchecked)
                pool = await cursor_store.create_cursor_pool(
                    host=parsed.hostname or "localhost",
                    port=parsed.port or 5432,
                    user=login,
                    password=password,
                    database="butlers",
                    ssl="disable",
                    min_size=1,
                    max_size=1,
                )
                pools.append(pool)
                await _native_identity(pool, login, role)
                await cursor_store.save_cursor(
                    pool,
                    "gmail",
                    endpoint,
                    value,
                    parent_endpoint_identity=cursor_store.NO_PARENT,
                )
                assert await cursor_store.load_cursor(pool, "gmail", endpoint) == value
                assert (
                    _scalar(
                        control,
                        "SELECT checkpoint_cursor FROM switchboard.connector_registry "
                        "WHERE connector_type='gmail' AND endpoint_identity=:endpoint",
                        {"endpoint": endpoint},
                    )
                    == value
                )
                # Every helper acquisition has returned before committed drift.
                with _foreign_membership(control, login):
                    with pytest.raises(AuthorityError):
                        async with pool.acquire():
                            pytest.fail("Unsafe cursor LOGIN was handed out")
                    assert (
                        _scalar(
                            control,
                            "SELECT checkpoint_cursor FROM switchboard.connector_registry "
                            "WHERE connector_type='gmail' AND endpoint_identity=:endpoint",
                            {"endpoint": endpoint},
                        )
                        == value
                    )
                await _native_identity(pool, login, role)
                assert await cursor_store.load_cursor(pool, "gmail", endpoint) == value
            except BaseException as exc:
                primary = exc
                raise
            finally:
                failures = []
                for pool in reversed(pools):
                    try:
                        await _close_native_pool(pool)
                    except BaseException as exc:
                        failures.append(exc)
                try:
                    with _engine_connection(control) as connection:
                        connection.execute(
                            text(
                                "DELETE FROM switchboard.connector_registry "
                                "WHERE connector_type='gmail' AND endpoint_identity=:endpoint"
                            ),
                            {"endpoint": endpoint},
                        )
                except BaseException as exc:
                    failures.append(exc)
                if primary is not None:
                    if failures:
                        primary.add_note("Owned cursor fixture cleanup was incomplete")
                elif failures:
                    raise failures[0]

        asyncio.run(exercise())


def test_actual_native_operator_catalog_measurement_uses_selected_pool_readonly(
    postgres_container, monkeypatch, tmp_path
):
    """Real operator CLI/native transactions, exact comparator and unchanged rows."""
    import json
    from contextlib import asynccontextmanager

    from butlers.modules.memory import catalog_measurement, storage

    db_url = _create_db(postgres_container, _unique_db_name())
    control = _control_url(postgres_container)
    parsed = urlparse(db_url)
    login = unquote(parsed.username or "")
    native_url = _native_dsn(db_url, login, unquote(parsed.password or ""))
    tenant = f"native-measurement-{uuid.uuid4().hex}"
    source_ids = (uuid.uuid4(), uuid.uuid4())
    vectors = [[1.0, *([0.0] * 383)], [0.0, 1.0, *([0.0] * 382)]]
    vector_file = tmp_path / "synthetic-vectors.json"
    vector_file.write_text(json.dumps([vectors[0]]))
    args = catalog_measurement._parser().parse_args(
        [
            "--vectors-json",
            str(vector_file),
            "--tenant-id",
            tenant,
            "--memory-type",
            "fact",
            "--limit",
            "2",
            "--exact-candidate-cap",
            "2",
            "--query-timeout-seconds",
            "5",
        ]
    )
    monkeypatch.setenv("DATABASE_URL", native_url)
    monkeypatch.setenv("POSTGRES_DB", "not_the_selected_native_database")

    def snapshot():
        with _engine_connection(control) as connection:
            return tuple(
                connection.execute(
                    text(
                        "SELECT md5(row_to_json(c)::text) FROM public.memory_catalog c "
                        "WHERE source_schema='general' AND source_table='facts' "
                        "AND source_id IN (:first,:second) ORDER BY source_id"
                    ),
                    {"first": source_ids[0], "second": source_ids[1]},
                ).scalars()
            )

    async def exercise():
        real_create_pool = asyncpg.create_pool
        writer = None
        created = []
        observed = []
        omit_readonly = False
        primary = None

        class ObservedConnection:
            def __init__(self, raw, default_isolation):
                self.raw = raw
                self.default_isolation = default_isolation

            def transaction(self, **options):
                # Test-local real-driver inverse only: source requests readonly,
                # the negative removes it at the actual native transaction edge.
                if omit_readonly and options.get("isolation") == "repeatable_read":
                    options = dict(options, readonly=False)
                return self.raw.transaction(**options)

            async def inspect(self, statement):
                readonly = await self.raw.fetchval("SHOW transaction_read_only")
                isolation = await self.raw.fetchval("SHOW transaction_isolation")
                assert readonly == "on", "Native measurement transaction must be read-only"
                if "pg_stat_user_tables" in statement:
                    kind = "maintenance_table"
                elif "pg_relation_size" in statement:
                    kind = "maintenance_index"
                elif "COUNT(*) FILTER" in statement:
                    kind = "maintenance_lifecycle"
                elif statement.lstrip().startswith("EXPLAIN (FORMAT JSON)"):
                    kind = "plan"
                elif "WITH candidates AS MATERIALIZED" in statement:
                    kind = "exact"
                elif "SELECT COUNT(*) FROM public.memory_catalog" in statement:
                    kind = "count"
                elif "SELECT id, embedding <=>" in statement:
                    kind = "nearest"
                else:
                    pytest.fail("Unrecognized source operator query boundary")
                expected = (
                    self.default_isolation if kind.startswith("maintenance_") else "repeatable read"
                )
                assert isolation == expected
                observed.append((kind, readonly, isolation))

            async def fetch(self, statement, *parameters, **options):
                await self.inspect(statement)
                return await self.raw.fetch(statement, *parameters, **options)

            async def fetchrow(self, statement, *parameters, **options):
                await self.inspect(statement)
                return await self.raw.fetchrow(statement, *parameters, **options)

            async def fetchval(self, statement, *parameters, **options):
                await self.inspect(statement)
                return await self.raw.fetchval(statement, *parameters, **options)

        class ObservedPool:
            def __init__(self, raw, default_isolation):
                self.raw = raw
                self.default_isolation = default_isolation

            @asynccontextmanager
            async def acquire(self):
                async with self.raw.acquire() as connection:
                    yield ObservedConnection(connection, self.default_isolation)

            async def close(self):
                await _close_native_pool(self.raw)

        async def actual_factory(**options):
            assert options.get("database") == "butlers"
            pool = await real_create_pool(**options)
            created.append(pool)
            # Inspect the physical target before forwarding any business query.
            await _native_identity(pool, login, login)
            async with pool.acquire() as connection:
                default_isolation = await connection.fetchval("SHOW default_transaction_isolation")
            return ObservedPool(pool, default_isolation)

        try:
            writer = await real_create_pool(
                dsn=native_url,
                min_size=1,
                max_size=1,
                command_timeout=5,
            )
            await _native_identity(writer, login, login)
            for source_id, vector in zip(source_ids, vectors, strict=True):
                await storage._upsert_catalog(
                    writer,
                    source_schema="general",
                    source_table="facts",
                    source_id=source_id,
                    source_butler="general",
                    tenant_id=tenant,
                    entity_id=None,
                    summary="Synthetic native operator fact",
                    embedding=vector,
                    search_text="Synthetic native operator fact",
                    memory_type="fact",
                    sensitivity="normal",
                )
            before = snapshot()
            assert len(before) == 2, "Both actual catalog writes must be committed"
            with monkeypatch.context() as observer:
                observer.setattr(catalog_measurement.asyncpg, "create_pool", actual_factory)
                omit_readonly = True
                try:
                    with pytest.raises(AssertionError, match="must be read-only"):
                        await catalog_measurement._run_cli(args)
                finally:
                    omit_readonly = False
                assert all(pool.is_closing() for pool in created)
                assert snapshot() == before
                observed.clear()
                result = await catalog_measurement._run_cli(args)
            assert all(pool.is_closing() for pool in created)
            report = result["measurement"]
            assert len(report["observations"]) == 1
            observation = report["observations"][0]
            assert observation["filtered_candidate_count"] == 2
            assert observation["exact_result_count"] == 2
            assert observation["exact_status"] == "completed"
            assert 0 <= observation["approximate_result_count"] <= 2
            assert type(observation["ivfflat_plan_used"]) is bool
            assert report["safety"]["read_only_transaction"] is True
            assert report["safety"]["planner_or_index_settings_changed"] is False
            assert {kind for kind, _, _ in observed} == {
                "count",
                "nearest",
                "plan",
                "exact",
                "maintenance_lifecycle",
                "maintenance_table",
                "maintenance_index",
            }
            assert snapshot() == before
        except BaseException as exc:
            primary = exc
            raise
        finally:
            failures = []
            for pool in reversed([*created, *([writer] if writer is not None else [])]):
                try:
                    await _close_native_pool(pool)
                except BaseException as exc:
                    failures.append(exc)
            try:
                with _engine_connection(control) as connection:
                    connection.execute(
                        text(
                            "DELETE FROM public.memory_catalog WHERE source_schema='general' "
                            "AND source_table='facts' AND source_id IN (:first,:second)"
                        ),
                        {"first": source_ids[0], "second": source_ids[1]},
                    )
            except BaseException as exc:
                failures.append(exc)
            if primary is not None:
                if failures:
                    primary.add_note("Owned operator fixture cleanup was incomplete")
            elif failures:
                raise failures[0]

    asyncio.run(exercise())
