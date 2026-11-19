"""core_352 owner-channel candidate read on migrated PostgreSQL (bu-s11n0s.1 S5h).

REQ-endpoint-custody-holds-011: a manual hold names owner-selected fact ids the server listed. The
read is content-blind (kind, masked hint, held flag), gated to ``dashboard_auth_api`` and a current
cookie session, and uses the same candidate universe and masking as a security question (core_350).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from butlers.db_authority.roles import runtime_roles
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]

API = "dashboard_auth_api"
CONFIG = {"origin": "https://candidates.test", "rp_id": "candidates.test", "key_generation": None}
FN = "public.custody_candidates_dashboard(text, jsonb)"


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
                "VALUES ('candidates test owner', ARRAY['owner']) RETURNING id"
            )
        ).scalar_one()
        stranger = c.execute(
            text(
                "INSERT INTO public.entities (canonical_name, roles) VALUES ('other', '{}') "
                "RETURNING id"
            )
        ).scalar_one()
        c.execute(
            text(
                "UPDATE dashboard_auth.instance SET state = 'keyless_enrolled', "
                "origin = :origin, rp_id = :rp WHERE singleton"
            ),
            {"origin": CONFIG["origin"], "rp": CONFIG["rp_id"]},
        )
    yield {"admin": admin, "host": host, "owner": owner, "stranger": stranger}
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


def fact(dbs, subject, predicate: str, obj: str) -> uuid.UUID:
    with dbs["admin"].begin() as c:
        return c.execute(
            text(
                "INSERT INTO relationship.entity_facts "
                '(subject, predicate, object, object_kind, src, "primary", validity) '
                "VALUES (:s, :p, :o, 'literal', 'test', false, 'active') RETURNING id"
            ),
            {"s": subject, "p": predicate, "o": obj},
        ).scalar_one()


def read(dbs, payload: dict, *, role: str = API, action: str = "list") -> dict:
    with dbs["admin"].begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        raw = c.execute(
            text("SELECT public.custody_candidates_dashboard(:a, CAST(:p AS jsonb))"),
            {"a": action, "p": json.dumps(CONFIG | payload)},
        ).scalar_one()
    return raw if isinstance(raw, dict) else json.loads(raw)


def test_only_the_dashboard_auth_role_reaches_the_definer(dbs) -> None:
    with dbs["admin"].connect() as c:
        for role in runtime_roles():
            assert not c.execute(
                text("SELECT has_function_privilege(:r, :f, 'EXECUTE')"), {"r": role, "f": FN}
            ).scalar_one(), role
        assert c.execute(
            text("SELECT has_function_privilege(:r, :f, 'EXECUTE')"), {"r": API, "f": FN}
        ).scalar_one()
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


def test_the_read_lists_the_owners_channels_as_masked_hints_only(dbs) -> None:
    email = fact(dbs, dbs["owner"], "has-email", "private.person@example.test")
    handle = fact(dbs, dbs["owner"], "has-handle", "telegram:987654321")
    fact(dbs, dbs["stranger"], "has-email", "stranger@example.test")
    result = read(dbs, session(dbs))
    by_id = {c["fact_id"]: c for c in result["candidates"]}
    assert str(email) in by_id and str(handle) in by_id
    assert by_id[str(email)]["hint"] == "p***@example.test"
    assert by_id[str(handle)]["hint"] == "...4321"
    text_blob = json.dumps(result)
    assert "private.person" not in text_blob and "987654321" not in text_blob
    assert "stranger" not in text_blob
    assert all(set(c) == {"fact_id", "kind", "hint", "held"} for c in result["candidates"])


def test_a_held_target_is_flagged_held(dbs) -> None:
    target = fact(dbs, dbs["owner"], "has-phone", "+6587650123")
    proof = session(dbs)
    with dbs["admin"].begin() as c:
        c.execute(text('SET LOCAL ROLE "dashboard_auth_api"'))
        c.execute(
            text("SELECT public.custody_dashboard('hold', CAST(:p AS jsonb))"),
            {
                "p": json.dumps(
                    CONFIG
                    | proof
                    | {
                        "command_id": str(uuid.uuid4()),
                        "fact_ids": [str(target)],
                        "reason": "lost",
                    }
                )
            },
        )
    held = {c["fact_id"]: c["held"] for c in read(dbs, session(dbs))["candidates"]}
    assert held[str(target)] is True


def test_the_hints_match_a_security_questions_candidates(dbs) -> None:
    fact(dbs, dbs["owner"], "has-email", "parity.owner@example.test")
    event = str(uuid.uuid4())
    with dbs["admin"].begin() as c:
        c.execute(
            text(
                "INSERT INTO public.security_questions (event_id, provider, kind) "
                "VALUES (:e, 'google', 'new_sign_in')"
            ),
            {"e": event},
        )
        c.execute(text('SET LOCAL ROLE "dashboard_auth_api"'))
        raw = c.execute(
            text("SELECT public.security_question_dashboard('get', CAST(:p AS jsonb))"),
            {"p": json.dumps(CONFIG | session(dbs) | {"event_id": event})},
        ).scalar_one()
    question = raw if isinstance(raw, dict) else json.loads(raw)
    assert read(dbs, session(dbs))["candidates"] == question["candidates"]


def test_a_bad_session_or_extra_field_is_refused(dbs) -> None:
    assert read(dbs, {"session_digest": _digest("nope")}) == {"error": "UNAUTHORIZED"}
    assert read(dbs, session(dbs) | {"fact_id": "x"}) == {"error": "BAD_REQUEST"}
    assert read(dbs, session(dbs), action="hold") == {"error": "BAD_REQUEST"}
    wrong_origin = dict(CONFIG, origin="https://elsewhere.test")
    with dbs["admin"].begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{API}"'))
        raw = c.execute(
            text("SELECT public.custody_candidates_dashboard('list', CAST(:p AS jsonb))"),
            {"p": json.dumps(wrong_origin | session(dbs))},
        ).scalar_one()
    assert (raw if isinstance(raw, dict) else json.loads(raw)) == {"error": "FORBIDDEN"}


def _migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/core/core_352_custody_candidates_read.py"
    )
    spec = importlib.util.spec_from_file_location("_core352_downgrade", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step(dbs, operation: str) -> None:
    with dbs["admin"].begin() as c, Operations.context(MigrationContext.configure(c)):
        getattr(_migration(), operation)()


def _exists(dbs) -> bool:
    with dbs["admin"].connect() as c:
        return c.execute(text(f"SELECT to_regprocedure('{FN}') IS NOT NULL")).scalar_one()


def test_the_last_schema_stepping_down_to_351_drops_the_function(dbs) -> None:
    """Two schema chains at core_352 share one function; only the last one leaving drops it.

    The migrated database already holds two ``alembic_version`` tables (``public`` for core and
    ``relationship`` for its chain); the test points both at core_352 and restores them after.
    """
    with dbs["admin"].begin() as c:
        schemas = (
            c.execute(
                text(
                    "SELECT n.nspname FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE t.relname = 'alembic_version' AND t.relkind IN ('r', 'p') "
                    "AND left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema' "
                    "ORDER BY n.nspname"
                )
            )
            .scalars()
            .all()
        )
        assert len(schemas) >= 2
        saved = {
            schema: c.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).all()
            for schema in schemas
        }
    first, second = schemas[0], schemas[1]

    def point(schema: str, version: str) -> None:
        with dbs["admin"].begin() as c:
            c.execute(text(f"UPDATE \"{schema}\".alembic_version SET version_num = '{version}'"))

    try:
        for schema in schemas:
            point(schema, "core_352" if schema in (first, second) else "core_351")
        assert _exists(dbs)
        _step(dbs, "downgrade")  # first leaves while second is still at 352: the function stays
        assert _exists(dbs)
        point(first, "core_351")
        _step(dbs, "downgrade")  # second is the last one still at 352: the function goes
        assert not _exists(dbs)
    finally:
        with dbs["admin"].begin() as c:
            for schema, rows in saved.items():
                c.execute(text(f'DELETE FROM "{schema}".alembic_version'))
                for (version,) in rows:
                    c.execute(
                        text(f'INSERT INTO "{schema}".alembic_version VALUES (:v)'), {"v": version}
                    )
