"""core_350 security questions and the dashboard answer on migrated PostgreSQL (bu-s11n0s.1 S5c).

openspec change ``endpoint-custody-holds``, REQ-endpoint-custody-holds-008 (dashboard half):
one durable question per authenticated event (duplicates and retries reuse it); the answer is
committed by a current owner session and CSRF only; ``yes`` changes nothing; ``no`` binds an
owner-selected exact target through ``custody_dashboard`` in the same transaction; at most one
initial decision, a retained ``no`` is never erased by ``yes`` or a duplicate. Real migrated roles:
runtime calls run under ``SET LOCAL ROLE``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.core.security_questions import admit_event, admit_missing
from butlers.db_authority.roles import runtime_roles
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]

API = "dashboard_auth_api"
SWITCHBOARD = "butler_switchboard_rw"
CONFIG = {"origin": "https://question.test", "rp_id": "question.test", "key_generation": None}
FN = "public.security_question_dashboard(text, jsonb)"


@pytest.fixture(scope="module")
def dbs(postgres_container):
    name = migration_db_name()
    host_url = create_migrated_test_db(
        postgres_container,
        name,
        chains=["core", "relationship"],
        schemas={"relationship": "relationship"},
    )
    admin = create_engine(migration_bootstrap_db_url(postgres_container, name))
    host = create_engine(host_url)
    with admin.begin() as c:
        owner = c.execute(
            text(
                "INSERT INTO public.entities (canonical_name, roles) "
                "VALUES ('question test owner', ARRAY['owner']) RETURNING id"
            )
        ).scalar_one()
        c.execute(
            text(
                "UPDATE dashboard_auth.instance SET state = 'keyless_enrolled', "
                "origin = :origin, rp_id = :rp WHERE singleton"
            ),
            {"origin": CONFIG["origin"], "rp": CONFIG["rp_id"]},
        )
    yield {"admin": admin, "host": host, "owner": owner}
    admin.dispose()
    host.dispose()


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


def fact(dbs, predicate: str, obj: str) -> uuid.UUID:
    with dbs["admin"].begin() as c:
        return c.execute(
            text(
                "INSERT INTO relationship.entity_facts "
                '(subject, predicate, object, object_kind, src, "primary", validity) '
                "VALUES (:s, :p, :o, 'literal', 'test', false, 'active') RETURNING id"
            ),
            {"s": dbs["owner"], "p": predicate, "o": obj},
        ).scalar_one()


def question(dbs, *, provider: str = "google", kind: str = "new_sign_in") -> str:
    event_id = str(uuid.uuid4())
    with dbs["admin"].begin() as c:
        c.execute(
            text(
                "INSERT INTO public.security_questions (event_id, provider, kind) VALUES (:e, :p, :k)"
            ),
            {"e": event_id, "p": provider, "k": kind},
        )
    return event_id


def call(dbs, action: str, payload: dict, proof: dict, *, role: str = API) -> dict:
    with dbs["admin"].begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        raw = c.execute(
            text(f"SELECT {FN.split('(')[0]}(:a, CAST(:p AS jsonb))"),
            {"a": action, "p": json.dumps(CONFIG | proof | payload)},
        ).scalar_one()
    return raw if isinstance(raw, dict) else json.loads(raw)


def no(event_id: str, target, *, command_id=None) -> dict:
    return {
        "event_id": event_id,
        "answer": "no",
        "command_id": str(command_id or uuid.uuid4()),
        "fact_ids": [str(target)],
        "reason": "disowned_access",
    }


def scalar(dbs, sql: str, **params):
    with dbs["admin"].connect() as c:
        return c.execute(text(sql), params).scalar()


def as_role(dbs, role: str, sql: str, **params):
    with dbs["admin"].begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        return c.execute(text(sql), params).scalar()


def unique(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------- authority


def test_only_the_dashboard_auth_role_reaches_the_definer(dbs) -> None:
    with dbs["admin"].connect() as c:

        def can(role: str) -> bool:
            return c.execute(
                text("SELECT has_function_privilege(:r, :f, 'EXECUTE')"), {"r": role, "f": FN}
            ).scalar_one()

        for role in runtime_roles():
            assert not can(role), role
        assert can(API)
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM pg_proc p, aclexplode(p.proacl) a "
                    "WHERE p.oid = CAST(:f AS regprocedure) AND a.grantee = 0"
                ),
                {"f": FN},
            ).scalar_one()
            == 0
        )


def test_runtime_roles_cannot_forge_an_answer_or_walk_a_question_back(dbs) -> None:
    """Switchboard owns the outbox and reads answers; no one else, and nobody writes answers."""
    event_id = question(dbs)
    for role in runtime_roles():
        if role == SWITCHBOARD:
            continue
        for relation in ("security_questions", "security_answers"):
            with pytest.raises(DBAPIError):
                as_role(dbs, role, f"SELECT count(*) FROM public.{relation}")
    with pytest.raises(DBAPIError):
        as_role(
            dbs,
            SWITCHBOARD,
            "INSERT INTO public.security_answers (event_id, answer) VALUES (gen_random_uuid(), 'yes')",
        )
    assert as_role(dbs, SWITCHBOARD, "SELECT count(*) FROM public.security_answers") is not None
    with dbs["admin"].begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{SWITCHBOARD}"'))
        c.execute(
            text(
                "UPDATE public.security_questions SET state = 'presenting', "
                "attempted_at = now() WHERE event_id = CAST(:e AS uuid)"
            ),
            {"e": event_id},
        )
    # Once claimed it never goes back: a possibly-sent question is never re-sent.
    with pytest.raises(DBAPIError):
        as_role(
            dbs,
            SWITCHBOARD,
            "UPDATE public.security_questions SET state = 'pending' "
            "WHERE event_id = CAST(:e AS uuid)",
            e=event_id,
        )
    with pytest.raises(DBAPIError):
        as_role(
            dbs,
            SWITCHBOARD,
            "DELETE FROM public.security_questions WHERE event_id = CAST(:e AS uuid)",
            e=event_id,
        )


def test_caller_strings_do_not_authenticate(dbs) -> None:
    event_id = question(dbs)
    proof = session(dbs)
    forged = {"session_digest": _digest("forged"), "csrf_digest": proof["csrf_digest"]}
    body = {"event_id": event_id, "answer": "yes"}
    assert call(dbs, "answer", body, forged) == {"error": "UNAUTHORIZED"}
    bad_csrf = {"session_digest": proof["session_digest"], "csrf_digest": _digest("nope")}
    assert call(dbs, "answer", body, bad_csrf) == {"error": "FORBIDDEN"}
    assert call(dbs, "answer", body, proof | {"origin": "https://evil.test"}) == {
        "error": "FORBIDDEN"
    }
    for extra in ({"authority": "host"}, {"actor": "owner"}):
        assert call(dbs, "answer", body | extra, proof) == {"error": "BAD_REQUEST"}
    assert call(dbs, "answer", {"event_id": event_id, "answer": "maybe"}, proof) == {
        "error": "BAD_REQUEST"
    }
    assert (
        scalar(
            dbs,
            "SELECT count(*) FROM public.security_answers WHERE event_id = CAST(:e AS uuid)",
            e=event_id,
        )
        == 0
    )


def test_a_read_needs_a_session_but_no_csrf(dbs) -> None:
    event_id = question(dbs)
    proof = session(dbs)
    nocsrf = {"session_digest": proof["session_digest"], "csrf_digest": ""}
    read = call(dbs, "get", {"event_id": event_id}, nocsrf)
    assert read["event_id"] == event_id and read["answer"] is None
    assert read["presentation"] == "pending"
    forged = {"session_digest": _digest("forged"), "csrf_digest": ""}
    assert call(dbs, "get", {"event_id": event_id}, forged) == {"error": "UNAUTHORIZED"}
    unknown = call(dbs, "get", {"event_id": str(uuid.uuid4())}, nocsrf)
    assert unknown == {"outcome": "refused", "reason": "unknown_question"}


# --------------------------------------------------------------------------- answers


def test_yes_changes_nothing(dbs) -> None:
    """REQ-endpoint-custody-holds-008 'Yes SHALL neither open nor close a case'."""
    event_id = question(dbs)
    proof = session(dbs)
    holds_before = scalar(dbs, "SELECT count(*) FROM public.custody_holds")
    cases_before = scalar(dbs, "SELECT count(*) FROM public.fleet_cases")
    result = call(dbs, "answer", {"event_id": event_id, "answer": "yes"}, proof)
    assert result["outcome"] == "answered" and result["answer"] == "yes"
    again = call(dbs, "answer", {"event_id": event_id, "answer": "yes"}, proof)
    assert again["answer"] == "yes"
    assert scalar(dbs, "SELECT count(*) FROM public.custody_holds") == holds_before
    assert scalar(dbs, "SELECT count(*) FROM public.fleet_cases") == cases_before
    assert (
        scalar(
            dbs,
            "SELECT count(*) FROM public.security_answers WHERE event_id = CAST(:e AS uuid)",
            e=event_id,
        )
        == 1
    )


def test_unknown_question_is_refused(dbs) -> None:
    result = call(dbs, "answer", {"event_id": str(uuid.uuid4()), "answer": "yes"}, session(dbs))
    assert result == {"outcome": "refused", "reason": "unknown_question"}


def test_no_binds_the_exact_target_and_is_idempotent(dbs) -> None:
    """REQ-endpoint-custody-holds-008 'No binds exact target': hold, answer and case commit together; replay converges."""
    address = unique("no-") + "@example.test"
    target = fact(dbs, "has-email", address)
    event_id = question(dbs)
    proof = session(dbs)
    command = uuid.uuid4()
    first = call(dbs, "answer", no(event_id, target, command_id=command), proof)
    assert first["outcome"] == "held" and first["answer"] == "no"
    assert first["event_id"] == event_id
    row = scalar(
        dbs,
        "SELECT command_id FROM public.security_answers WHERE event_id = CAST(:e AS uuid)",
        e=event_id,
    )
    assert str(row) == str(command)
    replay = call(dbs, "answer", no(event_id, target, command_id=command), proof)
    assert replay["answer"] == "no" and replay["case_id"] == first["case_id"]
    other = call(dbs, "answer", no(event_id, target), proof)
    assert other["outcome"] == "already_held"
    assert (
        scalar(
            dbs,
            "SELECT count(*) FROM public.custody_holds WHERE released_at IS NULL "
            "AND target_kind = 'has-email' AND target_digest = "
            "public.custody_target_digest('has-email', :a)",
            a=address,
        )
        == 1
    )
    # The first decision's command stays the recorded one.
    assert str(
        scalar(
            dbs,
            "SELECT command_id FROM public.security_answers WHERE event_id = CAST(:e AS uuid)",
            e=event_id,
        )
    ) == str(command)


def test_a_refused_no_records_no_answer(dbs) -> None:
    """A target that is not an owner fact binds nothing and leaves the question open."""
    event_id = question(dbs)
    result = call(dbs, "answer", no(event_id, uuid.uuid4()), session(dbs))
    assert result == {"outcome": "refused", "reason": "not_owner_target"}
    assert (
        scalar(
            dbs,
            "SELECT count(*) FROM public.security_answers WHERE event_id = CAST(:e AS uuid)",
            e=event_id,
        )
        == 0
    )


def test_a_retained_no_is_never_erased_by_yes(dbs) -> None:
    """REQ-endpoint-custody-holds-008 'Answer races retain conservative truth'."""
    target = fact(dbs, "has-email", unique("keep-") + "@example.test")
    event_id = question(dbs)
    proof = session(dbs)
    assert call(dbs, "answer", no(event_id, target), proof)["answer"] == "no"
    late_yes = call(dbs, "answer", {"event_id": event_id, "answer": "yes"}, proof)
    assert late_yes["answer"] == "no" and late_yes["retained"] is True
    assert (
        scalar(
            dbs,
            "SELECT answer FROM public.security_answers WHERE event_id = CAST(:e AS uuid)",
            e=event_id,
        )
        == "no"
    )
    with pytest.raises(DBAPIError):
        with dbs["admin"].begin() as c:
            c.execute(
                text(
                    "UPDATE public.security_answers SET answer = 'yes', command_id = NULL "
                    "WHERE event_id = CAST(:e AS uuid)"
                ),
                {"e": event_id},
            )


def test_no_after_yes_raises_the_answer(dbs) -> None:
    target = fact(dbs, "has-email", unique("raise-") + "@example.test")
    event_id = question(dbs)
    proof = session(dbs)
    assert call(dbs, "answer", {"event_id": event_id, "answer": "yes"}, proof)["answer"] == "yes"
    assert call(dbs, "answer", no(event_id, target), proof)["answer"] == "no"


def test_racing_yes_and_no_leave_one_retained_no(dbs) -> None:
    target = fact(dbs, "has-email", unique("race-") + "@example.test")
    event_id = question(dbs)
    proofs = [session(dbs) for _ in range(4)]
    bodies = [
        {"event_id": event_id, "answer": "yes"},
        no(event_id, target),
        {"event_id": event_id, "answer": "yes"},
        no(event_id, target),
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda pair: call(dbs, "answer", *pair), zip(bodies, proofs, strict=True))
        )
    assert all("error" not in r for r in results)
    assert (
        scalar(
            dbs,
            "SELECT answer FROM public.security_answers WHERE event_id = CAST(:e AS uuid)",
            e=event_id,
        )
        == "no"
    )
    assert (
        scalar(
            dbs,
            "SELECT count(*) FROM public.security_answers WHERE event_id = CAST(:e AS uuid)",
            e=event_id,
        )
        == 1
    )


def test_the_list_and_get_show_candidates_and_state(dbs) -> None:
    address = unique("cand-") + "@example.test"
    target = fact(dbs, "has-email", address)
    event_id = question(dbs)
    proof = session(dbs)
    read = call(dbs, "get", {"event_id": event_id}, proof)
    candidate = next(c for c in read["candidates"] if c["fact_id"] == str(target))
    assert candidate["kind"] == "has-email" and candidate["held"] is False
    assert address not in json.dumps(read)
    call(dbs, "answer", no(event_id, target), proof)
    assert call(dbs, "get", {"event_id": event_id}, proof)["answer"] == "no"
    listed = call(dbs, "list", {}, proof)["questions"]
    assert any(q["event_id"] == event_id and q["answer"] == "no" for q in listed)


# --------------------------------------------------------------------------- outbox


async def test_admission_is_one_row_per_event_and_recovers_a_crash_gap(dbs) -> None:
    """REQ-endpoint-custody-holds-008 'Authenticated alert asks once': retries reuse the row; the sweep recovers it."""
    import asyncpg

    url = dbs["admin"].url.set(drivername="postgresql").render_as_string(hide_password=False)
    conn = await asyncpg.connect(url)
    try:
        authenticated = str(uuid.uuid4())
        unverified = str(uuid.uuid4())
        for event_id, verdict in ((authenticated, "authenticated"), (unverified, "unverified")):
            await conn.execute(
                "INSERT INTO public.domain_events (id, event_type, source_butler, payload) "
                "VALUES ($1::uuid, 'switchboard.security_event', 'switchboard', $2::jsonb)",
                event_id,
                json.dumps(
                    {"provider": "google", "kind": "new_sign_in", "sender_verification": verdict}
                ),
            )
        assert await admit_event(conn, event_id=authenticated, provider="google", kind="x")
        assert not await admit_event(conn, event_id=authenticated, provider="google", kind="x")
        await admit_missing(conn)
        rows = await conn.fetch(
            "SELECT event_id FROM public.security_questions WHERE event_id = ANY($1::uuid[])",
            [authenticated, unverified],
        )
        assert [str(r["event_id"]) for r in rows] == [authenticated]
        assert await admit_missing(conn) == 0
    finally:
        await conn.close()
