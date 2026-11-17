"""core_347 endpoint custody command engine on migrated PostgreSQL (bu-s11n0s.1 slice 2a).

openspec change ``endpoint-custody-holds``: REQ-001 (exact owner targets, ambiguity refuses),
REQ-002 (peer DML and caller strings never mint custody), the protected half of REQ-003
(current owner session and CSRF checked in the committing transaction; host acts) and REQ-004
(hold, case and receipt commit together; replay, convergence, stale release, failure before
commit). Real migrated roles: runtime calls run under ``SET LOCAL ROLE``; the host is the
migration login that owns the database.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

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
CONFIG = {"origin": "https://custody.test", "rp_id": "custody.test", "key_generation": None}
DASHBOARD_FN = "public.custody_dashboard(text, jsonb)"
HOST_FN = "public.custody_host(text, jsonb)"
APPLY_FN = "public.custody_engine_apply(text, text, jsonb)"


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
                "VALUES ('custody test owner', ARRAY['owner']) RETURNING id"
            )
        ).scalar_one()
        other = c.execute(
            text(
                "INSERT INTO public.entities (canonical_name, roles) "
                "VALUES ('custody test other', ARRAY[]::text[]) RETURNING id"
            )
        ).scalar_one()
        c.execute(
            text(
                "UPDATE dashboard_auth.instance SET state = 'keyless_enrolled', "
                "origin = :origin, rp_id = :rp WHERE singleton"
            ),
            {"origin": CONFIG["origin"], "rp": CONFIG["rp_id"]},
        )
    yield {"admin": admin, "host": host, "owner": owner, "other": other}
    admin.dispose()
    host.dispose()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def session(dbs) -> dict:
    """A current owner session with one CSRF token; only digests reach the database."""
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


def fact(dbs, predicate: str, obj: str, *, subject=None) -> uuid.UUID:
    with dbs["admin"].begin() as c:
        return c.execute(
            text(
                "INSERT INTO relationship.entity_facts "
                '(subject, predicate, object, object_kind, src, "primary", validity) '
                "VALUES (:s, :p, :o, 'literal', 'test', false, 'active') RETURNING id"
            ),
            {"s": subject or dbs["owner"], "p": predicate, "o": obj},
        ).scalar_one()


def unique(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def dashboard(dbs, action: str, payload: dict, proof: dict, *, role: str = API) -> dict:
    with dbs["admin"].begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        raw = c.execute(
            text("SELECT public.custody_dashboard(:a, CAST(:p AS jsonb))"),
            {"a": action, "p": json.dumps(CONFIG | proof | payload)},
        ).scalar_one()
    return raw if isinstance(raw, dict) else json.loads(raw)


def host(dbs, action: str, payload: dict) -> dict:
    with dbs["host"].begin() as c:
        raw = c.execute(
            text("SELECT public.custody_host(:a, CAST(:p AS jsonb))"),
            {"a": action, "p": json.dumps(payload)},
        ).scalar_one()
    return raw if isinstance(raw, dict) else json.loads(raw)


def hold_payload(*facts, reason: str = "lost", command_id=None) -> dict:
    return {
        "command_id": str(command_id or uuid.uuid4()),
        "fact_ids": [str(f) for f in facts],
        "reason": reason,
    }


def resolves(dbs, candidate: str) -> bool:
    with dbs["admin"].connect() as c:
        found = c.execute(
            text("SELECT entity_id FROM public.resolve_owner_triple('owner-channel', ARRAY[:c])"),
            {"c": candidate},
        ).scalar()
    return found == dbs["owner"]


def scalar(dbs, sql: str, **params):
    with dbs["admin"].connect() as c:
        return c.execute(text(sql), params).scalar()


def as_role(dbs, role: str, sql: str, **params):
    with dbs["admin"].begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        return c.execute(text(sql), params).scalar()


# --------------------------------------------------------------------------- authority


def test_only_the_dashboard_auth_role_and_the_host_reach_the_engine(dbs) -> None:
    """REQ-002: no runtime role, and nothing via PUBLIC, can call any engine entry point."""
    with dbs["admin"].connect() as c:

        def can(role: str, fn: str) -> bool:
            return c.execute(
                text("SELECT has_function_privilege(:r, :f, 'EXECUTE')"), {"r": role, "f": fn}
            ).scalar_one()

        for role in runtime_roles():
            for fn in (DASHBOARD_FN, HOST_FN, APPLY_FN):
                assert not can(role, fn), (role, fn)
        assert can(API, DASHBOARD_FN)
        assert not can(API, HOST_FN)
        assert not can(API, APPLY_FN)
        public_acl = c.execute(
            text(
                "SELECT count(*) FROM pg_proc p, aclexplode(p.proacl) a "
                "WHERE p.oid IN (CAST(:d AS regprocedure), CAST(:h AS regprocedure), "
                "CAST(:x AS regprocedure)) AND a.grantee = 0"
            ),
            {"d": DASHBOARD_FN, "h": HOST_FN, "x": APPLY_FN},
        ).scalar_one()
        assert public_acl == 0


@pytest.mark.parametrize("role", ["butler_general_rw", "butler_finance_rw"])
def test_peer_dml_does_not_forge_custody_even_after_a_regrant(dbs, role) -> None:
    """REQ-002 'Peer DML does not forge custody': RLS holds after a bootstrap-style regrant."""
    target = fact(dbs, "has-email", unique("peer-") + "@example.test")
    proof = session(dbs)
    held = dashboard(dbs, "hold", hold_payload(target), proof)
    assert held["outcome"] == "held"
    hold_id = held["holds"][0]["hold_id"]
    with dbs["admin"].begin() as c:  # what an init-db.sql replay leaves behind
        c.execute(text(f'GRANT ALL ON public.custody_holds, public.custody_commands TO "{role}"'))
    try:
        assert as_role(dbs, role, "SELECT count(*) FROM public.custody_holds") == 0
        assert as_role(dbs, role, "SELECT count(*) FROM public.custody_commands") == 0
        for sql in (
            "UPDATE public.custody_holds SET released_at = now() WHERE id = CAST(:i AS uuid)",
            "DELETE FROM public.custody_holds WHERE id = CAST(:i AS uuid)",
            "DELETE FROM public.custody_commands",
        ):
            with dbs["admin"].begin() as c:
                c.execute(text(f'SET LOCAL ROLE "{role}"'))
                assert c.execute(text(sql), {"i": hold_id}).rowcount == 0
        for sql in (
            "INSERT INTO public.custody_commands (command_id, operation, authority, "
            "request_digest, episode_id, case_id, result) VALUES (gen_random_uuid(), 'release', "
            "'host', repeat('0', 64), gen_random_uuid(), gen_random_uuid(), '{}'::jsonb)",
            "SELECT public.custody_host('release', '{}'::jsonb)",
            "SELECT public.custody_engine_apply('host', 'release', '{}'::jsonb)",
            "SELECT public.custody_dashboard('list', '{}'::jsonb)",
        ):
            with pytest.raises(DBAPIError):
                as_role(dbs, role, sql)
    finally:
        with dbs["admin"].begin() as c:
            c.execute(
                text(f'REVOKE ALL ON public.custody_holds, public.custody_commands FROM "{role}"')
            )
    assert scalar(
        dbs,
        "SELECT released_at IS NULL FROM public.custody_holds WHERE id = CAST(:i AS uuid)",
        i=hold_id,
    )


def test_caller_strings_do_not_authenticate(dbs) -> None:
    """REQ-002 'Caller strings do not authenticate': no forged session, field or target works."""
    address = unique("strings-") + "@example.test"
    target = fact(dbs, "has-email", address)
    proof = session(dbs)
    forged = {"session_digest": _digest("forged"), "csrf_digest": proof["csrf_digest"]}
    assert dashboard(dbs, "hold", hold_payload(target), forged) == {"error": "UNAUTHORIZED"}
    no_csrf = {"session_digest": proof["session_digest"], "csrf_digest": _digest("nope")}
    assert dashboard(dbs, "hold", hold_payload(target), no_csrf) == {"error": "FORBIDDEN"}
    for extra in ({"authority": "host"}, {"actor": "owner"}, {"owner_entity_id": "x"}):
        assert dashboard(dbs, "hold", hold_payload(target) | extra, proof) == {
            "error": "BAD_REQUEST"
        }
    wrong_origin = proof | {"origin": "https://attacker.test"}
    assert dashboard(dbs, "hold", hold_payload(target), wrong_origin) == {"error": "FORBIDDEN"}
    stranger = fact(dbs, "has-email", unique("stranger-") + "@example.test", subject=dbs["other"])
    refused = dashboard(dbs, "hold", hold_payload(stranger), proof)
    assert refused == {"outcome": "refused", "reason": "not_owner_target"}
    # A host function is never reachable from the dashboard role, whatever it claims.
    with pytest.raises(DBAPIError):
        as_role(
            dbs,
            API,
            "SELECT public.custody_host('hold', CAST(:p AS jsonb))",
            p=json.dumps(hold_payload(target) | {"confirm": True}),
        )
    assert resolves(dbs, f"has-email:{address}")
    assert not scalar(dbs, "SELECT public.custody_target_held('has-email', :o)", o=address)


def test_an_ambiguous_identifier_is_not_held(dbs) -> None:
    """REQ-001 'Ambiguity refuses success'."""
    shared = unique("shared-") + "@example.test"
    target = fact(dbs, "has-email", shared)
    fact(dbs, "has-email", shared, subject=dbs["other"])
    result = dashboard(dbs, "hold", hold_payload(target), session(dbs))
    assert result == {"outcome": "refused", "reason": "unresolved_target"}
    assert not scalar(dbs, "SELECT public.custody_target_held('has-email', :o)", o=shared)


def test_a_revoked_session_cannot_commit_and_a_fresh_one_can(dbs) -> None:
    """REQ-003: revocation before commit fences the command; a current owner positive commits."""
    address = unique("revoked-") + "@example.test"
    target = fact(dbs, "has-email", address)
    proof = session(dbs)
    with dbs["admin"].begin() as c:
        c.execute(
            text("UPDATE dashboard_auth.sessions SET revoked = true WHERE digest = :d"),
            {"d": proof["session_digest"]},
        )
    command = hold_payload(target)
    assert dashboard(dbs, "hold", command, proof) == {"error": "UNAUTHORIZED"}
    assert resolves(dbs, f"has-email:{address}")
    epoch_proof = session(dbs)
    with dbs["admin"].begin() as c:
        c.execute(text("UPDATE dashboard_auth.instance SET session_epoch = session_epoch + 1"))
    assert dashboard(dbs, "hold", command, epoch_proof) == {"error": "UNAUTHORIZED"}
    assert dashboard(dbs, "hold", command, session(dbs))["outcome"] == "held"
    assert not resolves(dbs, f"has-email:{address}")


# --------------------------------------------------------------------------- hold engine


def test_a_dashboard_hold_commits_holds_case_and_receipt_together(dbs) -> None:
    """REQ-001 'Multiple targets are explicit', REQ-004 atomic hold/case/receipt."""
    address = unique("multi-") + "@example.test"
    handle = unique("telegram:")
    email_fact = fact(dbs, "has-email", address)
    handle_fact = fact(dbs, "has-handle", handle)
    sibling = unique("sibling-") + "@example.test"
    fact(dbs, "has-email", sibling)
    command = hold_payload(email_fact, handle_fact, reason="stolen")
    result = dashboard(dbs, "hold", command, session(dbs))
    assert result["outcome"] == "held"
    assert result["operation"] == "hold"
    assert result["command_id"] == command["command_id"]
    assert sorted(h["target_kind"] for h in result["holds"]) == ["has-email", "has-handle"]
    assert {h["state"] for h in result["holds"]} == {"held"}
    assert {h["generation"] for h in result["holds"]} == {1}
    assert not resolves(dbs, f"has-email:{address}")
    assert not resolves(dbs, f"has-handle:{handle}")
    assert resolves(dbs, f"has-email:{sibling}")  # unselected endpoints stay eligible
    episodes = scalar(
        dbs,
        "SELECT count(DISTINCT episode_id) FROM public.custody_holds "
        "WHERE source_command_id = CAST(:c AS uuid)",
        c=command["command_id"],
    )
    assert episodes == 1
    case = scalar(
        dbs,
        "SELECT correlation_key FROM public.fleet_cases WHERE id = CAST(:c AS uuid)",
        c=result["case_id"],
    )
    assert case == f"endpoint_custody:{result['episode_id']}"
    evidence = scalar(
        dbs,
        "SELECT count(*) FROM public.fleet_case_evidence WHERE case_id = CAST(:c AS uuid) "
        "AND contributor = 'endpoint_custody' AND kind = 'hold'",
        c=result["case_id"],
    )
    assert evidence == 2
    # Raw identifiers never reach the receipt or the shared case evidence.
    receipt = scalar(
        dbs,
        "SELECT result::text FROM public.custody_commands WHERE command_id = CAST(:c AS uuid)",
        c=command["command_id"],
    )
    payloads = scalar(
        dbs,
        "SELECT string_agg(payload::text, ' ') FROM public.fleet_case_evidence "
        "WHERE case_id = CAST(:c AS uuid)",
        c=result["case_id"],
    )
    for text_blob in (receipt, payloads):
        assert address not in text_blob and handle not in text_blob
    # A lost acknowledgement is resolved from the same command identity.
    status = dashboard(dbs, "status", {"command_id": command["command_id"]}, session(dbs))
    assert status == result


def test_a_replayed_command_returns_its_result_and_a_reused_id_refuses(dbs) -> None:
    """REQ-004 'Unknown commit uses same command': replay never re-executes."""
    target = fact(dbs, "has-handle", unique("telegram:"))
    command = hold_payload(target)
    first = dashboard(dbs, "hold", command, session(dbs))
    again = dashboard(dbs, "hold", command, session(dbs))
    assert again == first | {"replayed": True}
    reused = dashboard(dbs, "hold", command | {"reason": "stolen"}, session(dbs))
    assert reused == {"outcome": "refused", "reason": "command_conflict"}
    unknown = str(uuid.uuid4())
    assert dashboard(dbs, "status", {"command_id": unknown}, session(dbs)) == {
        "outcome": "unknown_command",
        "command_id": unknown,
    }
    assert (
        scalar(
            dbs,
            "SELECT count(*) FROM public.custody_commands WHERE command_id = CAST(:c AS uuid)",
            c=command["command_id"],
        )
        == 1
    )


def test_competing_holds_converge_on_one_episode(dbs) -> None:
    """REQ-004 'Competing holds converge': one active hold, both callers see it."""
    target = fact(dbs, "has-email", unique("race-") + "@example.test")
    barrier = Barrier(2)

    def attempt() -> dict:
        proof = session(dbs)
        barrier.wait()
        return dashboard(dbs, "hold", hold_payload(target), proof)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(r["outcome"] for r in results) == ["already_held", "held"]
    assert len({r["holds"][0]["hold_id"] for r in results}) == 1
    assert len({r["episode_id"] for r in results}) == 1
    assert len({r["case_id"] for r in results}) == 1
    assert (
        scalar(
            dbs,
            "SELECT count(*) FROM public.custody_holds WHERE released_at IS NULL "
            "AND id = CAST(:i AS uuid)",
            i=results[0]["holds"][0]["hold_id"],
        )
        == 1
    )


def test_a_stale_release_cannot_clear_a_rehold(dbs) -> None:
    """REQ-004 'Stale release cannot clear rehold'; host release restores eligibility."""
    address = unique("rehold-") + "@example.test"
    target = fact(dbs, "has-email", address)
    first = dashboard(dbs, "hold", hold_payload(target), session(dbs))["holds"][0]
    released = host(
        dbs,
        "release",
        {
            "command_id": str(uuid.uuid4()),
            "hold_id": first["hold_id"],
            "expected_generation": first["generation"],
            "confirm": True,
        },
    )
    assert released["outcome"] == "released"
    assert resolves(dbs, f"has-email:{address}")
    second = dashboard(dbs, "hold", hold_payload(target), session(dbs))["holds"][0]
    assert second["generation"] == 2
    stale = dashboard(
        dbs,
        "release",
        {"command_id": str(uuid.uuid4()), "hold_id": first["hold_id"], "expected_generation": 1},
        session(dbs),
    )
    assert stale == {"outcome": "refused", "reason": "not_active"}
    wrong_generation = dashboard(
        dbs,
        "release",
        {"command_id": str(uuid.uuid4()), "hold_id": second["hold_id"], "expected_generation": 1},
        session(dbs),
    )
    assert wrong_generation == {"outcome": "refused", "reason": "stale_generation"}
    assert not resolves(dbs, f"has-email:{address}")
    unconfirmed = {
        "command_id": str(uuid.uuid4()),
        "hold_id": second["hold_id"],
        "expected_generation": 2,
    }
    assert host(dbs, "release", unconfirmed) == {"error": "FORBIDDEN"}


def test_failure_before_commit_leaves_no_hold_case_or_receipt(dbs) -> None:
    """REQ-004 'Failure before commit is not success'."""
    address = unique("fault-") + "@example.test"
    target = fact(dbs, "has-email", address)
    cases_before = scalar(dbs, "SELECT count(*) FROM public.fleet_cases")
    with dbs["admin"].begin() as c:
        c.execute(
            text(
                "CREATE FUNCTION public.test_custody_receipt_fault() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'receipt write failed'; END $$"
            )
        )
        c.execute(
            text(
                "CREATE TRIGGER test_custody_receipt_fault BEFORE INSERT ON "
                "public.custody_commands FOR EACH ROW "
                "EXECUTE FUNCTION public.test_custody_receipt_fault()"
            )
        )
    command = hold_payload(target)
    try:
        with pytest.raises(DBAPIError, match="receipt write failed"):
            dashboard(dbs, "hold", command, session(dbs))
    finally:
        with dbs["admin"].begin() as c:
            c.execute(text("DROP TRIGGER test_custody_receipt_fault ON public.custody_commands"))
            c.execute(text("DROP FUNCTION public.test_custody_receipt_fault()"))
    assert resolves(dbs, f"has-email:{address}")
    assert not scalar(dbs, "SELECT public.custody_target_held('has-email', :o)", o=address)
    assert scalar(dbs, "SELECT count(*) FROM public.fleet_cases") == cases_before
    assert dashboard(dbs, "status", {"command_id": command["command_id"]}, session(dbs)) == {
        "outcome": "unknown_command",
        "command_id": command["command_id"],
    }


def test_replace_needs_the_old_binding_retired_first(dbs) -> None:
    """A replaced disposition never hands owner authority back to a still-bound endpoint."""
    digits = "6598" + str(uuid.uuid4().int)[:4]
    held_fact = fact(dbs, "has-phone", "+65 " + digits)
    other_spelling = fact(dbs, "has-phone", "0065" + digits)
    hold = dashboard(dbs, "hold", hold_payload(held_fact), session(dbs))["holds"][0]

    def replace() -> dict:
        return dashboard(
            dbs,
            "replace",
            {
                "command_id": str(uuid.uuid4()),
                "hold_id": hold["hold_id"],
                "expected_generation": hold["generation"],
            },
            session(dbs),
        )

    assert replace() == {"outcome": "refused", "reason": "still_bound"}
    with dbs["admin"].begin() as c:
        c.execute(
            text("UPDATE relationship.entity_facts SET validity = 'superseded' WHERE id = :i"),
            {"i": held_fact},
        )
    # An equivalent spelling of the same number is still the owner's.
    assert replace() == {"outcome": "refused", "reason": "still_bound"}
    with dbs["admin"].begin() as c:
        c.execute(
            text("UPDATE relationship.entity_facts SET validity = 'superseded' WHERE id = :i"),
            {"i": other_spelling},
        )
    replaced = replace()
    assert replaced["outcome"] == "replaced"
    assert replaced["disposition"] == "replaced"
    evidence = scalar(
        dbs,
        "SELECT count(*) FROM public.fleet_case_evidence WHERE case_id = CAST(:c AS uuid) "
        "AND kind = 'replace'",
        c=replaced["case_id"],
    )
    assert evidence == 1


def test_list_shows_active_holds_without_identifiers(dbs) -> None:
    address = unique("listed-") + "@example.test"
    target = fact(dbs, "has-email", address)
    held = dashboard(dbs, "hold", hold_payload(target), session(dbs))["holds"][0]
    listed = dashboard(dbs, "list", {}, session(dbs))
    row = next(h for h in listed["holds"] if h["hold_id"] == held["hold_id"])
    assert set(row) == {"hold_id", "target_kind", "generation", "episode_id", "reason", "held_from"}
    assert address not in json.dumps(listed)


def test_downgrade_refuses_while_a_receipt_exists_and_reapplies(postgres_container) -> None:
    """Rolling back never discards a committed custody receipt."""
    from alembic import command
    from butlers.migrations import _build_alembic_config

    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "relationship"],
        schemas={"relationship": "relationship"},
        revisions={"core": "core_347"},
    )
    config = _build_alembic_config(url, chains=["core"])
    engine = create_engine(url)
    try:
        command.downgrade(config, "core_346")
        with engine.connect() as c:
            assert c.execute(text("SELECT to_regclass('public.custody_commands')")).scalar() is None
            assert (
                c.execute(
                    text(
                        "SELECT count(*) FROM pg_policies "
                        "WHERE policyname = 'fleet_cases_custody_engine'"
                    )
                ).scalar()
                == 0
            )
        command.upgrade(config, "core_347")
        with engine.begin() as c:
            owner = c.execute(
                text(
                    "INSERT INTO public.entities (canonical_name, roles) "
                    "VALUES ('downgrade owner', ARRAY['owner']) RETURNING id"
                )
            ).scalar_one()
            target = c.execute(
                text(
                    "INSERT INTO relationship.entity_facts "
                    '(subject, predicate, object, object_kind, src, "primary", validity) '
                    "VALUES (:s, 'has-email', 'downgrade@example.test', 'literal', 'test', "
                    "true, 'active') RETURNING id"
                ),
                {"s": owner},
            ).scalar_one()
            held = c.execute(
                text("SELECT public.custody_host('hold', CAST(:p AS jsonb))"),
                {"p": json.dumps(hold_payload(target) | {"confirm": True})},
            ).scalar_one()
        assert held["outcome"] == "held"
        with pytest.raises(Exception, match="receipts exist"):
            command.downgrade(config, "core_346")
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM public.custody_commands")).scalar() == 1
    finally:
        engine.dispose()


def test_receipts_are_immutable(dbs) -> None:
    target = fact(dbs, "has-handle", unique("telegram:"))
    command = hold_payload(target)
    dashboard(dbs, "hold", command, session(dbs))
    for sql in (
        "UPDATE public.custody_commands SET result = '{}'::jsonb "
        "WHERE command_id = CAST(:c AS uuid)",
        "DELETE FROM public.custody_commands WHERE command_id = CAST(:c AS uuid)",
    ):
        with pytest.raises(DBAPIError, match="custody command receipts"):
            with dbs["host"].begin() as c:
                c.execute(text(sql), {"c": command["command_id"]})
