"""RFC 0038 S6 capability health: the system.dep value is {state, cause, observed_at}.

The Python validator and the SQL ``valid_value`` accept and refuse exactly the
same shapes, so a bad value never reaches a slot from either side.
"""

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.core.capability_dependencies import (
    DEPENDENCY_IDS,
    dependency_evidence_ref,
    dependency_subject,
)
from butlers.perception.model import (
    HEALTH_CAUSES,
    HEALTH_STATES,
    EventRequest,
    validate_value,
)
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.perception.factories import AT, request, wire_time
from tests.perception.test_perception_postgres import call, prepared_database, write

NOW = wire_time(AT)


def value(state="down", cause="oauth_revoked", observed_at=NOW, **extra):
    return {"state": state, "cause": cause, "observed_at": observed_at, **extra}


VALID = [
    value("available", None),
    *(
        value(state, cause)
        for state in sorted(HEALTH_STATES - {"available"})
        for cause in sorted(HEALTH_CAUSES)
    ),
]
INVALID = {
    "legacy bare string": "available",
    "missing cause": {"state": "down", "observed_at": NOW},
    "extra key": value(extra="x"),
    "unknown state": value("sleeping"),
    "unknown cause": value(cause="bad_weather"),
    "available with a cause": value("available", "unknown"),
    "down without a cause": value("down", None),
    "non-string cause": value(cause=7),
    "local instant": value(observed_at="2026-01-01T00:00:00"),
    "non-utc instant": value(observed_at="2026-01-01T00:00:00+02:00"),
    "not an instant": value(observed_at="yesterday"),
    "null observed_at": value(observed_at=None),
    "list": [value()],
}


def test_closed_vocabularies_match_the_rfc() -> None:
    assert HEALTH_STATES == {"available", "degraded", "blind", "down", "held", "unconfigured"}
    assert HEALTH_CAUSES == {
        "oauth_revoked",
        "credential_missing",
        "provider_unavailable",
        "domain_unreachable",
        "signal_unmeasurable",
        "policy_held",
        "not_configured",
        "unknown",
    }


@pytest.mark.unit
@pytest.mark.parametrize("good", VALID, ids=lambda v: f"{v['state']}-{v['cause']}")
def test_python_accepts_a_closed_health_value(good) -> None:
    validate_value("system.dep", good)
    assert (
        EventRequest(request(kind="system.dep", value=good)).payload()["typed_value_or_ref"] == good
    )


@pytest.mark.unit
@pytest.mark.parametrize("bad", INVALID.values(), ids=INVALID.keys())
def test_python_refuses_everything_else(bad) -> None:
    with pytest.raises(ValueError):
        validate_value("system.dep", bad)


@pytest.mark.unit
def test_dependency_subjects_are_deterministic_and_distinct() -> None:
    subjects = {dependency_subject(name) for name in DEPENDENCY_IDS}
    evidence = {dependency_evidence_ref(name) for name in DEPENDENCY_IDS}
    assert len(subjects) == len(evidence) == len(DEPENDENCY_IDS)
    assert not subjects & evidence
    assert dependency_subject("google") == uuid.UUID("{}".format(dependency_subject("google")))


def health_body(name, **kwargs):
    body = request(kind="system.dep", **kwargs)
    body["subject"] = str(dependency_subject(name))
    body["evidence_refs"] = [
        {
            "ref": str(dependency_evidence_ref(name)),
            "source": "capability_health",
            "expires_at": wire_time(AT + timedelta(hours=1)),
        }
    ]
    return body


@pytest.mark.integration
@pytest.mark.db
def test_sql_accepts_and_refuses_the_same_values(postgres_container) -> None:
    with prepared_database(postgres_container) as database:
        with database[1].connect() as c:
            for index, good in enumerate(VALID):
                tx = c.begin()
                try:
                    body = request(kind="system.dep", key=f"good-{index}", value=good)
                    assert write(c, body, "perception_health_writer")["status"] == "committed"
                finally:
                    tx.rollback()
            for name, bad in INVALID.items():
                tx = c.begin()
                try:
                    with pytest.raises(DBAPIError):
                        write(
                            c,
                            request(kind="system.dep", key=f"bad-{name}", value=bad),
                            "perception_health_writer",
                        )
                finally:
                    tx.rollback()


@pytest.mark.integration
@pytest.mark.db
def test_activated_profile_writes_cas_and_reads_by_alias(postgres_container) -> None:
    name = migration_db_name()
    create_migrated_test_db(postgres_container, name, chains=["core"])
    engine = create_engine(migration_bootstrap_db_url(postgres_container, name))
    try:
        with engine.begin() as c:
            profile = c.execute(
                text(
                    "SELECT writer, max_age_seconds, evidence_sources, subjects, null_subject "
                    "FROM perception_private.sources WHERE source='health.dependencies'"
                )
            ).one()
            assert profile.writer == "perception_health_writer"
            assert profile.max_age_seconds == 900 and not profile.null_subject
            assert profile.evidence_sources == ["capability_health"]
            assert set(profile.subjects) == {dependency_subject(n) for n in DEPENDENCY_IDS}
            refs = c.execute(
                text(
                    "SELECT ref_type, id, read_alias FROM perception_private.source_refs "
                    "WHERE source='health.dependencies'"
                )
            ).all()
            assert {(r.id, r.read_alias) for r in refs if r.ref_type == "dependency"} == {
                (dependency_subject(n), n) for n in DEPENDENCY_IDS
            }
            assert {r.id for r in refs if r.ref_type == "evidence"} == {
                dependency_evidence_ref(n) for n in DEPENDENCY_IDS
            }

            first = health_body("google", key="g1", value=value("down", "oauth_revoked"))
            committed = write(c, first, "perception_health_writer")
            assert committed["status"] == "committed" and committed["generation"] == 1
            # A second writer holding the old generation conflicts and learns the current one.
            stale = health_body("google", key="g2", value=value("available", None))
            assert write(c, stale, "perception_health_writer") == {
                "status": "conflict",
                "generation": 1,
            }
            retried = health_body("google", key="g2", generation=1, value=value("available", None))
            assert write(c, retried, "perception_health_writer")["generation"] == 2

            row = call(
                c,
                "SELECT public.perception_read('system.dep.google',NULL,:at)",
                {"at": AT},
                "perception_owner_reader",
            )
            assert row["state"] == "known" and row["value"]["state"] == "available"
            assert row["generation"] == 2 and row["subject"] == str(dependency_subject("google"))
            # Another dependency is its own slot.
            assert (
                call(
                    c,
                    "SELECT public.perception_read('system.dep.spotify',NULL,:at)",
                    {"at": AT},
                    "perception_owner_reader",
                )["state"]
                == "unknown"
            )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.db
def test_reinstalling_never_overwrites_an_operator_profile(postgres_container) -> None:
    name = migration_db_name()
    create_migrated_test_db(postgres_container, name, chains=["core"])
    engine = create_engine(migration_bootstrap_db_url(postgres_container, name))
    try:
        with engine.begin() as c:
            c.execute(
                text(
                    "UPDATE perception_private.sources SET max_age_seconds=123 "
                    "WHERE source='health.dependencies'"
                )
            )
            c.execute(text("SELECT perception_admin.install_interface()"))
            assert (
                c.execute(
                    text(
                        "SELECT max_age_seconds FROM perception_private.sources "
                        "WHERE source='health.dependencies'"
                    )
                ).scalar_one()
                == 123
            )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.db
def test_installer_refuses_legacy_string_values(postgres_container) -> None:
    name = migration_db_name()
    create_migrated_test_db(postgres_container, name, chains=["core"])
    engine = create_engine(migration_bootstrap_db_url(postgres_container, name))
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL ROLE perception_owner"))
            c.execute(text("ALTER TABLE public.situation_slots DISABLE ROW LEVEL SECURITY"))
            c.execute(text("RESET ROLE"))
            c.execute(
                text(
                    "INSERT INTO public.situation_slots(id,kind,subject_entity,state,typed_value,"
                    "source,observed_at,fresh_until,evidence_refs,labels,generation,writer) "
                    "VALUES(gen_random_uuid(),'system.dep',:s,'known',CAST(:v AS jsonb),"
                    "'health.dependencies',now(),now()+interval '1 hour','[]','{}',1,"
                    "'perception_health_writer')"
                ),
                {"s": dependency_subject("google"), "v": json.dumps("available")},
            )
        with pytest.raises(DBAPIError, match="legacy system.dep"):
            with engine.begin() as c:
                c.execute(text("SELECT perception_admin.install_interface()"))
    finally:
        engine.dispose()
