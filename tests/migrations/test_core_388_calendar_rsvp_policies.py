"""core_388 RSVP policies on migrated PostgreSQL (bu-5ii4gj.9 S1).

The table is written only by the dashboard definer on a current owner session and CSRF; no runtime
role reads or writes it; the closed predicate vocabulary holds in the database as well as in the
route; a policy is revoked, never deleted. Real migrated roles: calls run under ``SET LOCAL ROLE``.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.db_authority.roles import runtime_roles
from butlers.testing.migration import (
    create_migrated_test_db,
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
    updated = call(dbs, "update", {"id": made["id"], "verdict": "tentative"}, session(dbs))
    assert updated == {"outcome": "updated", "id": made["id"], "version": 2}
    revoked = call(dbs, "revoke", {"id": made["id"]}, session(dbs))
    assert revoked == {"outcome": "revoked", "id": made["id"], "version": 3}
    assert call(dbs, "update", {"id": made["id"], "verdict": "accepted"}, session(dbs)) == {
        "outcome": "refused",
        "reason": "policy_revoked",
    }
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
