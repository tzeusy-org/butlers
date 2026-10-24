"""REQ-perception-registry-001/002/003: migrated SQL, real roles, no live activation.

The fixture uses the repository's distinct privileged test bootstrap. Ordinary
runtime/migration roles are NOT granted service membership to make a test pass.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.db import register_jsonb_codec
from butlers.migrations import run_migrations
from butlers.perception import EventRequest, PerceptionStore, event_id
from butlers.testing.migration import (
    create_migrated_test_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.perception.factories import AT, EVIDENCE, OWNER, request, wire_time

pytestmark = [pytest.mark.integration, pytest.mark.db]


@contextmanager
def prepared_database(postgres_container):
    name = migration_db_name()
    ordinary = create_migrated_test_db(postgres_container, name, chains=["core"])
    bootstrap = migration_bootstrap_db_url(postgres_container, name)
    engine = create_engine(bootstrap)
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO public.entities(id,canonical_name,entity_type,roles) VALUES(:id,'opaque test owner','person',ARRAY['owner'])"
            ),
            {"id": OWNER},
        )
        c.execute(
            text(
                "UPDATE perception_private.sources SET subjects=ARRAY[CAST(:id AS uuid)],null_subject=true,max_age_seconds=3600,expectation_kinds=ARRAY['confirmed_routine'],evidence_sources=ARRAY['fixture.evidence']"
            ),
            {"id": OWNER},
        )
        c.execute(
            text(
                "INSERT INTO perception_private.source_refs(id,source,ref_type) SELECT :ref,source,kind FROM perception_private.sources CROSS JOIN (VALUES ('evidence'),('expectation_kind'),('suppression')) r(kind)"
            ),
            {"ref": EVIDENCE},
        )
        c.execute(text("SET LOCAL ROLE butler_general_rw"))
        c.execute(
            text(
                "SELECT * FROM public.context_dnd_mutate(:id,'general','clear',NULL,NULL,NULL,NULL)"
            ),
            {"id": EVIDENCE},
        )
        c.execute(text("RESET ROLE"))
        c.execute(
            text(
                "INSERT INTO perception_private.suppression_refs(id,source) VALUES(:id,'attention.suppression')"
            ),
            {"id": EVIDENCE},
        )
    try:
        yield ordinary, engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def database(postgres_container):
    with prepared_database(postgres_container) as data:
        yield data


@pytest.fixture
def db(database):
    with database[1].connect() as c:
        tx = c.begin()
        try:
            yield c
        finally:
            tx.rollback()


def call(c, function, params, role):
    with c.begin_nested():
        c.execute(text(f'SET LOCAL ROLE "{role}"'))
        value = c.execute(text(function), params).scalar_one()
        c.execute(text("RESET ROLE"))
        return value


def write(c, body, role="perception_dots_writer"):
    return call(
        c, "SELECT public.perception_write(CAST(:body AS jsonb))", {"body": json.dumps(body)}, role
    )


def read(c, kind="owner.place", subject=OWNER, at=AT, role="perception_owner_reader"):
    return call(
        c,
        "SELECT public.perception_read(:kind,:subject,:at)",
        {"kind": kind, "subject": subject, "at": at},
        role,
    )


def timeline(c, *, until=AT + timedelta(hours=1), live=False, size=100, before=None):
    return call(
        c,
        "SELECT public.perception_timeline(:start,:end,:kind,:subject,:before,:size,:live)",
        {
            "start": AT - timedelta(seconds=1),
            "end": until,
            "kind": "owner.place",
            "subject": OWNER,
            "before": before,
            "size": size,
            "live": live,
        },
        "perception_owner_reader",
    )


def counts(c):
    return tuple(
        c.execute(
            text(
                "SELECT (SELECT count(*) FROM public.situation_slots),(SELECT count(*) FROM public.timeline),(SELECT count(*) FROM perception_private.receipts)"
            )
        ).one()
    )


def test_actual_writer_and_private_reader_authority(db, database):
    body = request()
    before = counts(db)
    for role in (
        "butler_home_rw",
        "butler_general_rw",
        "butler_switchboard_rw",
        "connector_writer",
        urlparse(database[0]).username,
        "perception_health_writer",
    ):
        with pytest.raises(DBAPIError):
            write(db, body, role)
        assert counts(db) == before
        with pytest.raises(DBAPIError):
            call(
                db,
                "SELECT public.perception_read('owner.place',:owner,:at)",
                {"owner": OWNER, "at": AT},
                role,
            )
    result = write(db, body)
    assert result["status"] == "committed" and result["generation"] == 1
    row = read(db)
    assert row["state"] == "known" and row["value"]["presence"] == "inside"
    assert row["source"] == "home.dots" and row["age_seconds"] == 0
    assert set(row["labels"]["label_sens"]) == {
        "health",
        "finance",
        "location",
        "routine",
        "relationship_notes",
        "credentials",
    }
    assert row["labels"]["label_instr"] == ["world"]
    restricted = read(db, role="perception_availability_reader")
    assert restricted == {
        "state": "unknown",
        "busy_until": None,
        "availability_reason": "detail_restricted",
    }
    for table in (
        "situation_slots",
        "timeline",
        "owner_modes",
        "perception_conditions",
        "perception_suppressions",
    ):
        with pytest.raises(DBAPIError):
            call(db, f"SELECT count(*) FROM public.{table}", {}, "butler_home_rw")
        with pytest.raises(DBAPIError):
            call(db, f"DELETE FROM public.{table} RETURNING 1", {}, "perception_dots_writer")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.update(writer="perception_dots_writer"),
        lambda b: b["typed_value_or_ref"].update(presence=None),
        lambda b: b["labels"].pop("label_instr"),
        lambda b: b.update(expected_generation=True),
        lambda b: b.update(expected_generation=2**63 - 1),
        lambda b: b.update(source="health.dependencies"),
        lambda b: b["typed_value_or_ref"].update(latitude=1),
        lambda b: b["typed_value_or_ref"].update(title="not a typed primitive"),
        lambda b: b["typed_value_or_ref"].update(place_ref=str(uuid.uuid4())),
        lambda b: b["labels"].update(label_instr=["owner"]),
        lambda b: b["labels"].update(label_sens=["not-a-category"]),
        lambda b: b.update(confirmed_at=wire_time(AT - timedelta(seconds=1))),
    ],
    ids=[
        "forged-writer",
        "null-presence",
        "missing-label-component",
        "bool-generation",
        "overflow",
        "foreign-source",
        "coordinate",
        "prose",
        "unresolved-ref",
        "payload-owner",
        "unknown-category",
        "negative-confirmation",
    ],
)
def test_sql_revalidates_types_and_authority_without_python_wrapper(db, mutation):
    body = request()
    mutation(body)
    before = counts(db)
    with pytest.raises(DBAPIError):
        write(db, body)
    assert counts(db) == before


def test_replay_digest_and_stale_cas_are_immutable(db):
    body = request()
    first = write(db, body)
    assert write(db, body) == first
    changed = copy.deepcopy(body)
    changed["labels"]["label_sens"].append("health")
    with pytest.raises(DBAPIError):
        write(db, changed)
    stale = request(key="other", generation=0, minute=1)
    assert write(db, stale) == {"status": "conflict", "generation": 1}
    assert counts(db) == (1, 1, 1)
    assert write(db, body) == first
    assert read(db)["generation"] == 1
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE public.timeline SET late=true"))


def test_all_four_states_and_expiry_preserve_last_evidence(db):
    assert read(db)["availability_reason"] == "configured_empty"
    db.execute(
        text("UPDATE perception_private.sources SET max_age_seconds=NULL WHERE source='home.dots'")
    )
    assert read(db)["state"] == "unconfigured"
    db.execute(
        text("UPDATE perception_private.sources SET max_age_seconds=3600 WHERE source='home.dots'")
    )
    for i, state in enumerate(("unconfigured", "unmeasurable", "unknown", "known")):
        body = request(key=state, generation=i, minute=i, state=state)
        write(db, body)
        assert read(db, at=AT + timedelta(minutes=i))["state"] == state
    db.execute(
        text("UPDATE perception_private.sources SET max_age_seconds=NULL WHERE source='home.dots'")
    )
    assert read(db, at=AT + timedelta(minutes=4))["state"] == "unconfigured"
    db.execute(
        text("UPDATE perception_private.sources SET max_age_seconds=3600 WHERE source='home.dots'")
    )
    expired = read(db, at=AT + timedelta(hours=2))
    assert expired["state"] == "unknown" and expired["value"] is None
    assert expired["stale_last_value"]["presence"] == "inside"
    assert expired["availability_reason"] == "stale" and expired["evidence_refs"][0]["ref"] == str(
        EVIDENCE
    )
    assert expired["age_seconds"] == 7020
    # Configured healthy absence is an explicit typed assertion, not missing data.
    write(db, request(kind="owner.suppression"), "perception_attention_writer")
    assert read(db, kind="owner.suppression")["value"] == {"active": False, "record_ref": None}


def test_late_historical_input_does_not_roll_current_state_back(db):
    current = request(minute=10)
    write(db, current)
    old = request(key="historical", generation=1, minute=1)
    old["confirmed_at"] = wire_time(AT + timedelta(minutes=20))
    result = write(db, old)
    assert result["status"] == "historical" and result["generation"] == 1
    assert read(db, at=AT + timedelta(minutes=11))["observed_at"].startswith("2026-01-01T00:10:00")
    assert counts(db) == (1, 2, 2)
    assert write(db, old) == result
    rows = timeline(db, until=AT + timedelta(minutes=21))
    assert [r["class"] for r in rows] == ["transition", "record"]
    assert rows[1]["late"] is True and rows[1]["from_state"] is None
    assert timeline(db, until=AT + timedelta(minutes=21), live=True) == []


def test_confirmation_fifteen_minute_boundary_is_derived_and_replay_stable(db):
    for i, seconds in enumerate((900, 901)):
        body = request(key=f"late-{i}", generation=i, minute=i)
        body["confirmed_at"] = wire_time(AT + timedelta(minutes=i, seconds=seconds))
        result = write(db, body)
        row = db.execute(
            text("SELECT late,confirmed_at FROM public.timeline WHERE id=:id"),
            {"id": result["event_id"]},
        ).one()
        assert row.late is (seconds > 900)
        assert write(db, body) == result
        assert row.confirmed_at == AT + timedelta(minutes=i, seconds=seconds)


def seed_leave(c):
    write(c, request())
    leave = request(
        key="leave",
        generation=1,
        minute=1,
        value={"presence": "outside", "place_ref": None, "locality_ref": None},
    )
    return write(c, leave)


def test_retraction_is_atomic_stable_and_visible_outside_query_page(db):
    leave = seed_leave(db)
    enter = request(key="enter", generation=2, minute=11)
    enter["retracts_event_id"] = leave["event_id"]
    result = write(db, enter)
    assert result["generation"] == 3 and result["retraction_event_id"] is not None
    assert write(db, enter) == result and counts(db) == (1, 4, 4)
    rows = timeline(db, until=AT + timedelta(minutes=2), size=1)
    assert rows[0]["id"] == leave["event_id"] and rows[0]["retracted"] is True
    assert rows[0]["retraction_event_id"] == result["retraction_event_id"]
    assert (
        rows[0]["from_value"]["presence"] == "inside"
        and rows[0]["to_value"]["presence"] == "outside"
    )
    all_rows = timeline(db)
    retract = next(r for r in all_rows if r["operation"] == "retract")
    assert retract["class"] == "record" and retract["typed_value"] is None
    assert all(retract[n] is None for n in ("from_state", "to_state", "from_value", "to_value"))
    live = timeline(db, until=AT + timedelta(minutes=12), live=True)
    assert len(live) == 1 and live[0]["id"] == result["event_id"]
    assert read(db, at=AT + timedelta(minutes=12))["generation"] == 3


@pytest.mark.parametrize(
    "target", ["missing", "non-leave", "late", "foreign-source", "foreign-subject"]
)
def test_unearned_retraction_cannot_mutate_any_projection(db, target):
    leave = seed_leave(db)
    enter = request(key="enter", generation=2, minute=11)
    enter["retracts_event_id"] = leave["event_id"]
    if target == "missing":
        enter["retracts_event_id"] = str(uuid.uuid4())
    elif target == "non-leave":
        enter["retracts_event_id"] = timeline(db)[-1]["id"]
    elif target == "late":
        enter["at"] = enter["observed_at"] = wire_time(AT + timedelta(minutes=11, seconds=1))
    else:
        # Manufacture impossible ancestry only under privileged test control,
        # preserving the immutable-event trigger for the actual call.
        db.execute(text("ALTER TABLE public.timeline DISABLE TRIGGER perception_history_immutable"))
        column = "source" if target == "foreign-source" else "subject_entity"
        value = "foreign.reducer" if target == "foreign-source" else str(uuid.uuid4())
        db.execute(
            text(f"UPDATE public.timeline SET {column}=:v WHERE id=:id"),
            {"v": value, "id": leave["event_id"]},
        )
        db.execute(text("ALTER TABLE public.timeline ENABLE TRIGGER perception_history_immutable"))
    before = counts(db)
    with pytest.raises(DBAPIError):
        write(db, enter)
    assert counts(db) == before and read(db, at=AT + timedelta(minutes=2))["generation"] == 2


@pytest.mark.parametrize("failure", ["event", "receipt", "retraction"])
def test_storage_fault_rolls_back_slot_enter_and_both_receipts(db, failure):
    leave = seed_leave(db)
    before = counts(db)
    relation = "perception_private.receipts" if failure == "receipt" else "public.timeline"
    condition = "NEW.operation='retract'" if failure == "retraction" else "true"
    db.execute(
        text(
            f"CREATE FUNCTION public.perception_test_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN RAISE EXCEPTION 'injected storage failure'; END IF; RETURN NEW; END $$"
        )
    )
    db.execute(
        text(
            f"CREATE TRIGGER injected_fault BEFORE INSERT ON {relation} FOR EACH ROW EXECUTE FUNCTION public.perception_test_fault()"
        )
    )
    enter = request(key="enter", generation=2, minute=2)
    enter["retracts_event_id"] = leave["event_id"]
    with pytest.raises(DBAPIError):
        write(db, enter)
    assert (
        counts(db) == before
        and read(db, at=AT + timedelta(minutes=2))["value"]["presence"] == "outside"
    )


def test_null_identity_race_and_replay_have_one_committed_projection(postgres_container):
    with prepared_database(postgres_container) as database:
        barrier = Barrier(2)
        key = uuid.uuid4().hex
        body = request(kind="system.dep", key=key)
        body["subject"] = None

        def contender(index):
            with database[1].begin() as c:
                barrier.wait(timeout=10)
                return write(
                    c,
                    body if index == 0 else {**body, "event_key": key + "-other"},
                    "perception_health_writer",
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contender, i) for i in range(2)]
            results = [f.result(timeout=30) for f in futures]
        assert sorted(r["status"] for r in results) == ["committed", "conflict"]
        with database[1].begin() as c:
            assert (
                c.execute(
                    text(
                        "SELECT count(*) FROM public.situation_slots WHERE kind='system.dep' AND subject_entity IS NULL"
                    )
                ).scalar_one()
                == 1
            )
            winner = next(r for r in results if r["status"] == "committed")
            original = (
                body
                if winner["event_id"] == str(event_id(body["source"], key))
                else {**body, "event_key": key + "-other"}
            )
            assert write(c, original, "perception_health_writer") == winner


def test_owner_modes_are_one_projection_and_all_kind_values_round_trip(db):
    registry = call(db, "SELECT public.perception_registry()", {}, "perception_owner_reader")
    assert len(registry) == 8 and {r["schema_version"] for r in registry} == {1}
    assert (
        next(r for r in registry if r["kind"] == "owner.place")["writer_role"]
        == "perception_dots_writer"
    )
    assert set(next(r for r in registry if r["kind"] == "owner.mode")["field_schema"]["enum"]) == {
        "asleep",
        "travel",
        "house_unattended",
        "sick",
        "focus",
        "hosting",
        "holiday",
        "emergency",
    }
    roles = {
        "owner.activity": "perception_context_writer",
        "owner.clock": "perception_context_writer",
        "owner.mode": "perception_context_writer",
        "system.dep": "perception_health_writer",
        "expect.window": "perception_expect_writer",
        "home.sensing": "perception_sensing_writer",
        "owner.suppression": "perception_attention_writer",
    }
    for kind, role in roles.items():
        result = write(db, request(kind=kind), role)
        assert result["generation"] == 1
        row = read(db, kind=kind)
        assert row["state"] == "known" and row["value"] == request(kind=kind)["typed_value_or_ref"]
    assert (
        db.execute(
            text("SELECT count(*) FROM public.situation_slots WHERE kind='owner.mode'")
        ).scalar_one()
        == 0
    )
    assert db.execute(text("SELECT count(*) FROM public.owner_modes")).scalar_one() == 1
    for kind, value in [
        ("owner.mode", ["in_flight"]),
        ("owner.activity", ["focus"]),
        ("owner.clock", {"zone": "Invalid/Zone", "basis": "home", "evidence_ref": str(EVIDENCE)}),
    ]:
        with pytest.raises(DBAPIError):
            write(db, request(key="bad", kind=kind, generation=1, value=value), roles[kind])


def test_bindings_remain_dark_immutable_finite_and_source_bound(db):
    b = {
        "version": 1,
        "id": str(uuid.uuid4()),
        "kind": "owner.activity",
        "subject": str(OWNER),
        "goal_ref": str(uuid.uuid4()),
        "predicate": {"tag": "at", "instant": wire_time(AT), "zone": "UTC"},
        "source_boundary": ["fixture.evidence"],
        "source": "context.activity",
        "starts_at": wire_time(AT),
        "expires_at": wire_time(AT + timedelta(minutes=5)),
        "evidence_refs": [str(EVIDENCE)],
        "authority_ref": None,
    }

    def bind(body, role="perception_context_writer"):
        return call(
            db,
            "SELECT public.perception_bind(CAST(:body AS jsonb))",
            {"body": json.dumps(body)},
            role,
        )

    first = bind(b)
    assert first == bind(b) and first["state"] == "open" and first["generation"] == 1
    changed = {**b, "expires_at": wire_time(AT + timedelta(minutes=6))}
    with pytest.raises(DBAPIError):
        bind(changed)
    for bad in (
        {**b, "source_boundary": ["undeclared"]},
        {**b, "predicate": {"tag": "sql", "text": "SELECT 1"}},
        {**b, "authority_ref": str(EVIDENCE)},
    ):
        with pytest.raises(DBAPIError):
            bind(bad)
    suppression = {
        **b,
        "id": str(uuid.uuid4()),
        "kind": "owner.suppression",
        "source": "attention.suppression",
        "authority_ref": str(EVIDENCE),
    }
    assert bind(suppression, "perception_attention_writer")["state"] == "open"
    with pytest.raises(DBAPIError):
        bind(b, "butler_general_rw")
    assert db.execute(text("SELECT state FROM public.perception_conditions")).scalar_one() == "open"
    assert (
        db.execute(text("SELECT state FROM public.perception_suppressions")).scalar_one() == "open"
    )
    assert counts(db) == (0, 0, 0)  # no slot/CAS, subscriber, evaluator, effect or activation


def test_bootstrap_replay_after_seed_restores_effective_role_boundary(db, database):
    write(db, request())
    before = counts(db)
    # The real bootstrap starts by re-widening public grants. Its later fixed
    # finalizer must restore this boundary even on an already-migrated database.
    migration_user = urlparse(database[0]).username
    db.execute(
        text("SELECT set_config('butlers.connecting_user',:role,false)"), {"role": migration_user}
    )
    raw = db.connection.driver_connection
    with raw.cursor() as cursor:
        cursor.execute(init_db_sql_for_dbapi())
        cursor.execute(init_db_sql_for_dbapi())
    assert counts(db) == before and read(db)["generation"] == 1
    for role in ("butler_home_rw", migration_user):
        with pytest.raises(DBAPIError):
            call(db, "SELECT * FROM public.timeline", {}, role)
        with pytest.raises(DBAPIError):
            write(db, request(key="forged", generation=1), role)
    catalog = db.execute(
        text(
            "SELECT bool_and(c.relrowsecurity AND c.relforcerowsecurity AND c.relowner='perception_owner'::regrole) FROM pg_class c WHERE c.oid IN ('public.timeline'::regclass,'public.situation_slots'::regclass,'public.owner_modes'::regclass)"
        )
    ).scalar_one()
    assert catalog is True
    assert (
        db.execute(
            text(
                "SELECT count(*) FROM pg_auth_members WHERE roleid='perception_owner'::regrole OR member='perception_owner'::regrole"
            )
        ).scalar_one()
        == 0
    )


def test_core_only_second_schema_and_empty_downgrade_reupgrade(postgres_container):
    # Independent empty database: avoid deleting/rewriting any prior test's history.
    name = migration_db_name()
    url = create_migrated_test_db(postgres_container, name, chains=["core"])
    asyncio.run(run_migrations(url, chain="core", schema="second_core"))
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text("SELECT perception_admin.rollback_interface()"))
        # Shared empty additive objects survive the logical downgrade. Re-running
        # the fixed installer verifies the exact same namespace safely.
        with engine.begin() as c:
            c.execute(text("SELECT perception_admin.install_interface()"))
        with create_engine(migration_bootstrap_db_url(postgres_container, name)).connect() as c:
            assert c.execute(text("SELECT count(*) FROM public.timeline")).scalar_one() == 0
            assert (
                c.execute(
                    text(
                        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname='situation_slots'"
                    )
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_nonempty_downgrade_refuses_without_history_loss(db, database):
    write(db, request())
    before = counts(db)
    with pytest.raises(DBAPIError):
        call(db, "SELECT perception_admin.rollback_interface()", {}, urlparse(database[0]).username)
    assert counts(db) == before


@pytest.mark.asyncio
async def test_real_python_writer_and_reader_keep_caller_transaction_and_jsonb(database):
    conn = await asyncpg.connect(
        database[1].url.render_as_string(hide_password=False), command_timeout=30
    )
    await register_jsonb_codec(conn)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("SET LOCAL ROLE perception_dots_writer")
        store = PerceptionStore(conn)
        first = await store.write(EventRequest(request()))
        assert first == await store.write(EventRequest(request()))
        await conn.execute("SET LOCAL ROLE perception_owner_reader")
        row = await store.read("owner.place", OWNER, as_of=AT)
        assert row["value"]["presence"] == "inside" and row["generation"] == 1
        history = await store.timeline(
            since=AT - timedelta(seconds=1),
            until=AT + timedelta(minutes=1),
            kind="owner.place",
            subject=OWNER,
        )
        assert len(history) == 1 and history[0]["id"] == first["event_id"]
        assert isinstance(row["labels"], dict) and isinstance(history[0]["evidence_refs"], list)
    finally:
        await tx.rollback()
        await conn.close()
    with database[1].connect() as c:
        assert counts(c) == (0, 0, 0)
