"""core_388 RSVP policies on migrated PostgreSQL (bu-5ii4gj.9 S1).

The table is written only by the dashboard definer on a current owner session and CSRF; no runtime
role reads or writes it; the closed predicate vocabulary holds in the database as well as in the
route; a policy is revoked, never deleted. Real migrated roles: calls run under ``SET LOCAL ROLE``.
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
from butlers.core.temporal.rsvp_policy import PolicyError, parse_predicates
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
CONFIG = {"origin": "https://rsvp.test", "rp_id": "rsvp.test", "key_generation": None}
FN = "public.calendar_rsvp_policy_dashboard"
GOOD = [{"kind": "overlaps_focus_or_out_of_office", "value": True}]


@pytest.fixture(scope="module")
def dbs(postgres_container):
    name = migration_db_name()
    host_url = create_migrated_test_db(postgres_container, name, chains=["core"])
    admin = create_engine(migration_bootstrap_db_url(postgres_container, name))
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE dashboard_auth.instance SET state = 'keyless_enrolled', "
                "origin = :origin, rp_id = :rp WHERE singleton"
            ),
            {"origin": CONFIG["origin"], "rp": CONFIG["rp_id"]},
        )
    yield {"admin": admin, "host": create_engine(host_url)}
    admin.dispose()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def session(dbs) -> dict:
    sd, cd = _digest(uuid.uuid4().hex), _digest(uuid.uuid4().hex)
    with dbs["admin"].begin() as c:
        c.execute(
            text(
                "INSERT INTO dashboard_auth.sessions "
                "(digest, credential_epoch, session_epoch, expires_at) "
                "SELECT :sd, credential_epoch, session_epoch, now() + interval '1 hour' "
                "FROM dashboard_auth.instance WHERE singleton"
            ),
            {"sd": sd},
        )
        c.execute(
            text(
                "INSERT INTO dashboard_auth.csrf (session_digest, digest, expires_at) "
                "VALUES (:sd, :cd, now() + interval '30 minutes')"
            ),
            {"sd": sd, "cd": cd},
        )
    return {"session_digest": sd, "csrf_digest": cd}


def call(dbs, action: str, payload: dict, proof: dict, *, role: str = API) -> dict:
    with dbs["admin"].begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        raw = c.execute(
            text(f"SELECT {FN}(:a, CAST(:p AS jsonb))"),
            {"a": action, "p": json.dumps(CONFIG | proof | payload)},
        ).scalar_one()
    return raw if isinstance(raw, dict) else json.loads(raw)


def scalar(dbs, sql: str, **params):
    with dbs["admin"].connect() as c:
        return c.execute(text(sql), params).scalar()


def create(dbs, **over) -> dict:
    body = {"position": 1, "predicates": GOOD, "verdict": "declined", "send_updates": "none"}
    return call(dbs, "create", body | over, session(dbs))


# --------------------------------------------------------------------------- authority


def test_only_the_dashboard_auth_role_reaches_the_definer(dbs) -> None:
    fn = f"{FN}(text, jsonb)"
    with dbs["admin"].connect() as c:
        for role in runtime_roles():
            assert not c.execute(
                text("SELECT has_function_privilege(:r, :f, 'EXECUTE')"), {"r": role, "f": fn}
            ).scalar_one(), role
        assert c.execute(
            text("SELECT has_function_privilege(:r, :f, 'EXECUTE')"), {"r": API, "f": fn}
        ).scalar_one()


def test_no_runtime_role_reads_or_writes_the_table(dbs) -> None:
    create(dbs)
    for role in [*runtime_roles(), API]:
        for sql in (
            "SELECT count(*) FROM public.calendar_rsvp_policies",
            "INSERT INTO public.calendar_rsvp_policies (position, predicates, verdict, "
            "send_updates) VALUES (1, CAST(:p AS jsonb), 'accepted', 'none')",
            "UPDATE public.calendar_rsvp_policies SET verdict = 'accepted'",
            "DELETE FROM public.calendar_rsvp_policies",
        ):
            with pytest.raises(DBAPIError):
                with dbs["admin"].begin() as c:
                    c.execute(text(f'SET LOCAL ROLE "{role}"'))
                    c.execute(text(sql), {"p": json.dumps(GOOD)})


def test_a_write_needs_a_current_session_and_csrf(dbs) -> None:
    body = {"position": 1, "predicates": GOOD, "verdict": "declined", "send_updates": "none"}
    proof = session(dbs)
    assert call(dbs, "create", body, proof | {"csrf_digest": _digest("wrong")})["error"] == (
        "FORBIDDEN"
    )
    assert (
        call(dbs, "create", body, {"session_digest": _digest("none"), "csrf_digest": "x"})["error"]
        == "UNAUTHORIZED"
    )
    # a list carries no CSRF token
    assert "policies" in call(dbs, "list", {}, {"session_digest": proof["session_digest"]})


# --------------------------------------------------------------------------- the vocabulary


@pytest.mark.parametrize(
    "predicates",
    [
        [{"kind": "title_contains", "value": "board"}],
        [{"kind": "description", "value": "x"}],
        [{"kind": "recurring", "value": "yes"}],
        [{"kind": "organizer_verdict", "value": "first_contact"}],
        [{"kind": "attendee_count", "value": 5}],
        [{"kind": "attendee_count", "value": 5, "op": "eq"}],
        [{"kind": "lead_time_hours", "value": 99999, "op": "lte"}],
        [{"kind": "recurring", "value": True}, {"kind": "recurring", "value": False}],
        [{"kind": "recurring", "value": True, "extra": 1}],
        [{"kind": "recurring"}],
        [{"kind": "organizer_verdict"}],
        [{"kind": "attendee_count", "op": "lte"}],
        [{"kind": "recurring", "value": None}],
        [],
    ],
)
def test_a_predicate_outside_the_vocabulary_is_refused_and_stores_nothing(dbs, predicates) -> None:
    before = scalar(dbs, "SELECT count(*) FROM public.calendar_rsvp_policies")
    assert create(dbs, predicates=predicates) == {"outcome": "refused", "reason": "invalid_policy"}
    assert scalar(dbs, "SELECT count(*) FROM public.calendar_rsvp_policies") == before


def test_the_table_itself_refuses_a_free_text_predicate(dbs) -> None:
    with pytest.raises(DBAPIError):
        with dbs["admin"].begin() as c:
            c.execute(
                text(
                    "INSERT INTO public.calendar_rsvp_policies (position, predicates, verdict, "
                    "send_updates) VALUES (1, CAST(:p AS jsonb), 'accepted', 'none')"
                ),
                {"p": json.dumps([{"kind": "title_contains", "value": "x"}])},
            )


# --------------------------------------------------------------------------- lifecycle


def test_create_update_and_revoke_raise_the_version_and_keep_the_row(dbs) -> None:
    made = create(dbs, predicates=[{"kind": "attendee_count", "value": 30, "op": "gte"}])
    assert made["outcome"] == "created" and made["version"] == 1
    updated = call(
        dbs,
        "update",
        {"id": made["id"], "expected_version": 1, "verdict": "tentative"},
        session(dbs),
    )
    assert updated == {"outcome": "updated", "id": made["id"], "version": 2}
    revoked = call(dbs, "revoke", {"id": made["id"]}, session(dbs))
    assert revoked == {"outcome": "revoked", "id": made["id"], "version": 3}
    assert call(
        dbs,
        "update",
        {"id": made["id"], "expected_version": 3, "verdict": "accepted"},
        session(dbs),
    ) == {"outcome": "refused", "reason": "policy_revoked"}
    listed = call(dbs, "list", {}, {"session_digest": session(dbs)["session_digest"]})
    row = next(p for p in listed["policies"] if p["id"] == made["id"])
    assert row["revoked_at"] is not None and row["version"] == 3 and row["verdict"] == "tentative"


def test_a_policy_is_never_deleted_or_unrevoked(dbs) -> None:
    made = create(dbs)
    call(dbs, "revoke", {"id": made["id"]}, session(dbs))
    for sql in (
        "DELETE FROM public.calendar_rsvp_policies WHERE id = CAST(:i AS uuid)",
        "UPDATE public.calendar_rsvp_policies SET revoked_at = NULL WHERE id = CAST(:i AS uuid)",
        "UPDATE public.calendar_rsvp_policies SET version = 1 WHERE id = CAST(:i AS uuid)",
        "TRUNCATE public.calendar_rsvp_policies",
    ):
        with pytest.raises(DBAPIError):
            with dbs["admin"].begin() as c:
                c.execute(text(sql), {"i": made["id"]})


def test_the_active_policy_count_is_bounded(dbs) -> None:
    for _ in range(25):
        result = create(dbs)
        if result.get("outcome") == "refused":
            assert result["reason"] == "too_many_policies"
            break
    else:
        pytest.fail("the 21st active policy was accepted")
    assert (
        scalar(dbs, "SELECT count(*) FROM public.calendar_rsvp_policies WHERE revoked_at IS NULL")
        == 20
    )


def _retire_all(dbs) -> None:
    with dbs["admin"].begin() as c:
        c.execute(
            text(
                "UPDATE public.calendar_rsvp_policies SET revoked_at = now(), "
                "version = version + 1 WHERE revoked_at IS NULL"
            )
        )


def test_an_update_from_a_stale_version_is_refused_and_one_without_a_version_is_bad(dbs) -> None:
    _retire_all(dbs)
    made = create(dbs)
    call(
        dbs,
        "update",
        {"id": made["id"], "expected_version": 1, "verdict": "tentative"},
        session(dbs),
    )
    stale = call(
        dbs,
        "update",
        {"id": made["id"], "expected_version": 1, "verdict": "accepted"},
        session(dbs),
    )
    assert stale == {"outcome": "refused", "reason": "stale_version"}
    missing = call(dbs, "update", {"id": made["id"], "verdict": "accepted"}, session(dbs))
    assert missing == {"outcome": "refused", "reason": "stale_version"} or "error" in missing
    assert (
        scalar(
            dbs,
            "SELECT verdict FROM public.calendar_rsvp_policies WHERE id = CAST(:i AS uuid)",
            i=made["id"],
        )
        == "tentative"
    )


def test_revoked_policies_never_hide_an_active_one_in_the_list(dbs) -> None:
    _retire_all(dbs)
    keep = create(dbs, position=1000)
    for _ in range(100):
        with dbs["admin"].begin() as c:
            c.execute(
                text(
                    "INSERT INTO public.calendar_rsvp_policies (position, predicates, verdict, "
                    "send_updates, revoked_at) VALUES (0, CAST(:p AS jsonb), 'accepted', 'none', now())"
                ),
                {"p": json.dumps(GOOD)},
            )
    listed = call(dbs, "list", {}, {"session_digest": session(dbs)["session_digest"]})["policies"]
    assert keep["id"] in {p["id"] for p in listed}
    assert sum(1 for p in listed if p["revoked_at"] is not None) <= 80


# --------------------------------------------------------------------------- Python and SQL agree


_PARITY = [
    [{"kind": "recurring", "value": True}],
    [{"kind": "recurring", "value": False}],
    [{"kind": "recurring", "value": "yes"}],
    [{"kind": "recurring"}],
    [{"kind": "recurring", "value": None}],
    [{"kind": "recurring", "value": True, "op": "lte"}],
    [{"kind": "organizer_verdict", "value": "owner"}],
    [{"kind": "organizer_verdict", "value": "vouched"}],
    [{"kind": "organizer_verdict", "value": "first_contact"}],
    [{"kind": "organizer_verdict"}],
    [{"kind": "organizer_role", "value": "manager"}],
    [{"kind": "organizer_role", "value": "Manager"}],
    [{"kind": "organizer_role", "value": ""}],
    [{"kind": "attendee_count", "value": 5, "op": "lte"}],
    [{"kind": "attendee_count", "value": 5, "op": "gte"}],
    [{"kind": "attendee_count", "value": 5, "op": "eq"}],
    [{"kind": "attendee_count", "value": 5}],
    [{"kind": "attendee_count", "op": "lte"}],
    [{"kind": "attendee_count", "value": 10000, "op": "lte"}],
    [{"kind": "attendee_count", "value": 10001, "op": "lte"}],
    [{"kind": "lead_time_hours", "value": 8760, "op": "gte"}],
    [{"kind": "lead_time_hours", "value": 8761, "op": "gte"}],
    [{"kind": "lead_time_hours", "value": -1, "op": "gte"}],
    [{"kind": "lead_time_hours", "value": 1.5, "op": "gte"}],
    [{"kind": "title_contains", "value": "x"}],
    [{"kind": "recurring", "value": True}, {"kind": "recurring", "value": False}],
    [{"kind": "recurring", "value": True}, {"kind": "outside_declared_hours", "value": True}],
    [{"kind": "recurring", "value": True, "extra": 1}],
    [{"kind": k, "value": True} for k in ("recurring", "outside_declared_hours")]
    + [{"kind": "conflicts_with_accepted", "value": True}] * 2,
    [],
    [{"kind": "recurring", "value": True}] * 9,
    "recurring",
]


@pytest.mark.parametrize("predicates", _PARITY, ids=range(len(_PARITY)))
def test_the_python_parser_and_the_sql_check_accept_exactly_the_same_predicates(
    dbs, predicates
) -> None:
    try:
        parse_predicates(predicates)
        python_accepts = True
    except PolicyError:
        python_accepts = False
    sql_accepts = scalar(
        dbs,
        "SELECT public.calendar_rsvp_predicates_valid(CAST(:p AS jsonb))",
        p=json.dumps(predicates),
    )
    assert sql_accepts == python_accepts


# --------------------------------------------------------------------------- two schemas

_CORE_388 = Path(__file__).parents[2] / "alembic/versions/core/core_388_calendar_rsvp_policies.py"


def _below_core_388() -> str:
    spec = importlib.util.spec_from_file_location("core_388_under_test", _CORE_388)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return f"core@{module.down_revision}"


def _upgrade(db_url: str, schema: str):
    config = _build_alembic_config(db_url, chains=["core"], target_schema=schema)
    command.upgrade(config, "core_388")
    return config


def test_a_second_schema_replays_harmlessly_and_only_the_last_downgrade_drops_the_table(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _upgrade(db_url, "general")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.calendar_rsvp_policies (position, predicates, verdict, "
                    "send_updates) VALUES (1, CAST(:p AS jsonb), 'accepted', 'none')"
                ),
                {"p": json.dumps(GOOD)},
            )
        second = _upgrade(db_url, "travel")
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT count(*) FROM public.calendar_rsvp_policies")).scalar()
                == 1
            )
            assert not conn.execute(
                text(
                    "SELECT has_table_privilege('butler_general_rw', 'public.calendar_rsvp_policies', 'SELECT')"
                )
            ).scalar()
        # a peer still at core_388 keeps the shared objects
        command.downgrade(first, _below_core_388())
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT to_regclass('public.calendar_rsvp_policies')")
            ).scalar()
        # the last schema refuses while a policy exists
        with pytest.raises(DBAPIError, match="downgrade refused"):
            command.downgrade(second, _below_core_388())
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE public.calendar_rsvp_policies DISABLE TRIGGER USER"))
            conn.execute(text("DELETE FROM public.calendar_rsvp_policies"))
            conn.execute(text("ALTER TABLE public.calendar_rsvp_policies ENABLE TRIGGER USER"))
        command.downgrade(second, _below_core_388())
        with engine.connect() as conn:
            assert (
                conn.execute(text("SELECT to_regclass('public.calendar_rsvp_policies')")).scalar()
                is None
            )
    finally:
        engine.dispose()
