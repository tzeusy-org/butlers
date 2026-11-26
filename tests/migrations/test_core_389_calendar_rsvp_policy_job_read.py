"""core_389: only the Relationship butler reads the RSVP policies, and nobody but the definer writes.

REQ-calendar-invitation-docket-001 and -008 on real migrated roles (``SET LOCAL ROLE``).
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
RELATIONSHIP = "butler_relationship_rw"
CONFIG = {"origin": "https://rsvp.test", "rp_id": "rsvp.test", "key_generation": None}


@pytest.fixture(scope="module")
def admin(postgres_container):
    name = migration_db_name()
    create_migrated_test_db(postgres_container, name, chains=["core"])
    engine = create_engine(migration_bootstrap_db_url(postgres_container, name))
    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE dashboard_auth.instance SET state = 'keyless_enrolled', "
                "origin = :origin, rp_id = :rp WHERE singleton"
            ),
            {"origin": CONFIG["origin"], "rp": CONFIG["rp_id"]},
        )
        sd = hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest()
        cd = hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest()
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
        payload = CONFIG | {
            "session_digest": sd,
            "csrf_digest": cd,
            "position": 1,
            "predicates": [{"kind": "recurring", "value": True}],
            "verdict": "declined",
            "send_updates": "none",
        }
        c.execute(text(f'SET LOCAL ROLE "{API}"'))
        c.execute(
            text("SELECT public.calendar_rsvp_policy_dashboard('create', CAST(:p AS jsonb))"),
            {"p": json.dumps(payload)},
        )
    yield engine
    engine.dispose()


def _run(engine, role: str, sql: str):
    with engine.begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        return c.execute(text(sql)).scalar()


def test_only_the_relationship_butler_reads_the_policies(admin) -> None:
    assert _run(admin, RELATIONSHIP, "SELECT count(*) FROM public.calendar_rsvp_policies") >= 1
    for role in [*runtime_roles(), API]:
        if role == RELATIONSHIP:
            continue
        # Row level security hides the rows from a role with no policy; a role with no grant
        # at all is refused outright. Neither may see a row.
        try:
            seen = _run(admin, role, "SELECT count(*) FROM public.calendar_rsvp_policies")
        except DBAPIError:
            continue
        assert seen == 0, role


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO public.calendar_rsvp_policies (position, predicates, verdict, send_updates) "
        "VALUES (1, CAST('[]' AS jsonb), 'accepted', 'none')",
        "UPDATE public.calendar_rsvp_policies SET verdict = 'accepted'",
        "DELETE FROM public.calendar_rsvp_policies",
    ],
)
def test_the_relationship_butler_cannot_write_a_policy(admin, sql) -> None:
    with pytest.raises(DBAPIError):
        _run(admin, RELATIONSHIP, sql)
