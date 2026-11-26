"""core_390: the RSVP agreement ledger on migrated PostgreSQL (bu-5ii4gj.9 S5).

REQ-calendar-invitation-docket-006 on real migrated roles: the recorder is the only writer and only
the docket butler may call it; the dashboard reads counts on a current owner session; the table is
fenced from every runtime role and is append-only; a second schema's replay and a last-schema
downgrade behave like the other core_38x objects.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.db_authority.roles import runtime_roles
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migrated_test_db,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]

API = "dashboard_auth_api"
WRITER = "butler_relationship_rw"
CONFIG = {"origin": "https://rsvp.test", "rp_id": "rsvp.test", "key_generation": None}
RECORD = "public.calendar_rsvp_ledger_record"
DASH = "public.calendar_rsvp_ledger_dashboard"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(scope="module")
def env(postgres_container):
    name = migration_db_name()
    create_migrated_test_db(postgres_container, name, chains=["core"])
    admin = create_engine(migration_bootstrap_db_url(postgres_container, name))
    sd = _digest(uuid.uuid4().hex)
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE dashboard_auth.instance SET state = 'keyless_enrolled', "
                "origin = :origin, rp_id = :rp WHERE singleton"
            ),
            {"origin": CONFIG["origin"], "rp": CONFIG["rp_id"]},
        )
        c.execute(
            text(
                "INSERT INTO dashboard_auth.sessions "
                "(digest, credential_epoch, session_epoch, expires_at) "
                "SELECT :sd, credential_epoch, session_epoch, now() + interval '1 hour' "
                "FROM dashboard_auth.instance WHERE singleton"
            ),
            {"sd": sd},
        )
        policy = c.execute(
            text(
                "INSERT INTO public.calendar_rsvp_policies (position, predicates, verdict, "
                "send_updates, version) VALUES (1, CAST(:p AS jsonb), 'declined', 'none', 3) "
                "RETURNING id"
            ),
            {"p": json.dumps([{"kind": "recurring", "value": True}])},
        ).scalar_one()
    yield {"admin": admin, "session": sd, "policy": str(policy)}
    admin.dispose()


def _as(env, role: str, sql: str, **params):
    with env["admin"].begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        return c.execute(text(sql), params).scalar()


def _record(env, role, outcome="owner_rejected", action=None, version=3):
    return _as(
        env,
        role,
        f"SELECT {RECORD}(CAST(:a AS uuid), CAST(:p AS uuid), :v, :o)",
        a=action or str(uuid.uuid4()),
        p=env["policy"],
        v=version,
        o=outcome,
    )


def _counts(env, session=None):
    payload = CONFIG | {"session_digest": session or env["session"]}
    raw = _as(env, API, f"SELECT {DASH}('counts', CAST(:p AS jsonb))", p=json.dumps(payload))
    return raw if isinstance(raw, dict) else json.loads(raw)


def test_only_the_docket_butler_may_call_the_recorder(env) -> None:
    assert _record(env, WRITER) is True
    for role in [*runtime_roles(), API]:
        if role == WRITER:
            continue
        with pytest.raises(DBAPIError):
            _record(env, role)


def test_the_recorder_is_idempotent_per_card_and_refuses_what_is_not_an_outcome(env) -> None:
    action = str(uuid.uuid4())
    assert _record(env, WRITER, "approved_as_prepared", action) is True
    assert _record(env, WRITER, "owner_rejected", action) is False  # the first outcome stands
    for bad in ("edited", "", "system_rejected"):
        with pytest.raises(DBAPIError):
            _record(env, WRITER, bad)
    with pytest.raises(DBAPIError):
        _record(env, WRITER, version=99)  # a version the policy never had
    with pytest.raises(DBAPIError):
        _as(
            env,
            WRITER,
            f"SELECT {RECORD}(CAST(:a AS uuid), CAST(:p AS uuid), 1, 'owner_rejected')",
            a=str(uuid.uuid4()),
            p=str(uuid.uuid4()),
        )


def test_no_runtime_role_touches_the_table_and_it_is_append_only(env) -> None:
    for role in [*runtime_roles(), API]:
        try:
            seen = _as(env, role, "SELECT count(*) FROM public.calendar_rsvp_ledger")
        except DBAPIError:
            continue
        assert seen == 0, role
    for sql in (
        "INSERT INTO public.calendar_rsvp_ledger (action_id, policy_id, policy_version, outcome) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'owner_rejected')",
        "UPDATE public.calendar_rsvp_ledger SET outcome = 'owner_rejected'",
        "DELETE FROM public.calendar_rsvp_ledger",
    ):
        with pytest.raises(DBAPIError):
            _as(env, WRITER, sql)
    for sql in (
        "UPDATE public.calendar_rsvp_ledger SET outcome = 'owner_rejected'",
        "DELETE FROM public.calendar_rsvp_ledger",
        "TRUNCATE public.calendar_rsvp_ledger",
    ):
        with pytest.raises(DBAPIError):
            with env["admin"].begin() as c:
                c.execute(text(sql))


def test_the_dashboard_reads_counts_per_policy_and_version_on_a_current_session(env) -> None:
    _record(env, WRITER, "answered_same")
    _record(env, WRITER, "answered_same")
    rows = _counts(env)["counts"]
    same = next(r for r in rows if r["outcome"] == "answered_same")
    assert same["policy_id"] == env["policy"] and same["policy_version"] == 3
    assert same["count"] >= 2
    assert _counts(env, session=_digest("no-such-session")) == {"error": "UNAUTHORIZED"}
    for role in [*runtime_roles()]:
        with pytest.raises(DBAPIError):
            _as(env, role, f"SELECT {DASH}('counts', '{{}}'::jsonb)")


_CORE_390 = Path(__file__).parents[2] / "alembic/versions/core/core_390_calendar_rsvp_ledger.py"


def _below_core_390() -> str:
    spec = importlib.util.spec_from_file_location("core_390_under_test", _CORE_390)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return f"core@{module.down_revision}"


def _upgrade(db_url: str, schema: str):
    config = _build_alembic_config(db_url, chains=["core"], target_schema=schema)
    command.upgrade(config, "core_390")
    return config


def test_a_second_schema_replays_and_only_the_last_downgrade_drops_the_table(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _upgrade(db_url, "general")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as c:
            policy = c.execute(
                text(
                    "INSERT INTO public.calendar_rsvp_policies (position, predicates, verdict, "
                    "send_updates) VALUES (1, CAST(:p AS jsonb), 'accepted', 'none') RETURNING id"
                ),
                {"p": json.dumps([{"kind": "recurring", "value": True}])},
            ).scalar_one()
            c.execute(
                text(
                    "INSERT INTO public.calendar_rsvp_ledger (action_id, policy_id, "
                    "policy_version, outcome) VALUES (gen_random_uuid(), :p, 1, 'owner_rejected')"
                ),
                {"p": policy},
            )
        second = _upgrade(db_url, "travel")
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM public.calendar_rsvp_ledger")).scalar() == 1
        command.downgrade(first, _below_core_390())
        with engine.connect() as c:
            assert c.execute(text("SELECT to_regclass('public.calendar_rsvp_ledger')")).scalar()
        with pytest.raises(DBAPIError, match="downgrade refused"):
            command.downgrade(second, _below_core_390())
    finally:
        engine.dispose()
