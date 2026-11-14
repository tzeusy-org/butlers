"""core_336 vendor doors on migrated PostgreSQL: roles, authority, CAS, caps and the domain twin.

REQ-vendor-doors-002/003 (``openspec/changes/specify-vendor-doors``). Real migrated roles; every
call runs under ``SET LOCAL ROLE`` so the ``current_setting('role')`` binding is what is tested.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.core.registrable_domain import (
    registrable_domain,
    rule_lines,
    sender_registrable_domain,
)
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]

SWITCHBOARD = "butler_switchboard_rw"
FINANCE = "butler_finance_rw"
DASH = "dashboard_vendor_doors"
OWNER = uuid.UUID("00000000-0000-4000-8000-0000000000a1")

PUBLIC_FUNCTIONS = {
    "vendor_door_propose": {SWITCHBOARD, FINANCE},
    "vendor_door_create": {DASH},
    "vendor_door_confirm": {DASH},
    "vendor_door_revoke": {DASH},
    "vendor_door_reject": {DASH},
}
HELPERS = (
    "registrable_domain",
    "vendor_door_sender_domain",
    "vendor_door_normalize",
    "vendor_door_host",
    "vendor_door_template_problem",
    "vendor_door_owner",
    "vendor_door_caller_origin",
    "vendor_door_require_dashboard",
    "vendor_door_state",
    "vendor_door_insert",
    "vendor_doors_no_delete",
)


@pytest.fixture(scope="module")
def engine(postgres_container):
    name = migration_db_name()
    create_migrated_test_db(postgres_container, name, chains=["core"])
    eng = create_engine(migration_bootstrap_db_url(postgres_container, name))
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO public.entities(id, canonical_name, entity_type, roles) "
                "VALUES (:id, 'opaque test owner', 'person', ARRAY['owner'])"
            ),
            {"id": OWNER},
        )
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def clean(engine):
    yield
    with engine.begin() as c:
        c.execute(text("ALTER TABLE public.vendor_doors DISABLE TRIGGER vendor_doors_no_delete"))
        c.execute(text("UPDATE public.vendor_doors SET supersedes_id = NULL"))
        c.execute(text("DELETE FROM public.vendor_doors"))
        c.execute(text("ALTER TABLE public.vendor_doors ENABLE TRIGGER vendor_doors_no_delete"))
        c.execute(text("DELETE FROM public.ingestion_events WHERE dedupe_key LIKE 'vd-test:%'"))


def run(engine, role, sql, **params):
    with engine.begin() as c:
        if role:
            c.execute(text(f'SET LOCAL ROLE "{role}"'))
        return c.execute(text(sql), params).scalar_one()


def event(engine, sender, verdict):
    event_id = uuid.uuid4()
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO public.ingestion_events (id, source_channel, source_provider, "
                "source_endpoint_identity, source_sender_identity, external_event_id, dedupe_key, "
                "dedupe_strategy, ingestion_tier, policy_tier, sender_authentication) "
                "VALUES (:id, 'email', 'gmail', 'owner@example.test', :sender, :ext, :key, "
                "'native', 'full', 'default', :verdict)"
            ),
            {
                "id": event_id,
                "sender": sender,
                "ext": str(event_id),
                "key": f"vd-test:{event_id}",
                "verdict": verdict,
            },
        )
    return event_id


def propose(engine, role, template, *, kind="cancel", slots="{}", event_id=None):
    return run(
        engine,
        role,
        "SELECT public.vendor_door_propose(:k, :t, CAST(:s AS jsonb), :e, NULL, '{}'::jsonb)",
        k=kind,
        t=template,
        s=slots,
        e=event_id,
    )


def create(engine, template, *, kind="cancel", slots="{}", supersedes=None):
    return run(
        engine,
        DASH,
        "SELECT public.vendor_door_create(:k, :t, CAST(:s AS jsonb), NULL, :u)",
        k=kind,
        t=template,
        s=slots,
        u=supersedes,
    )


def confirm(engine, door_id, version, role=DASH):
    return run(
        engine,
        role,
        "SELECT public.vendor_door_confirm(:i, :v, 'session-hash')",
        i=door_id,
        v=version,
    )


def row(engine, door_id):
    with engine.connect() as c:
        return c.execute(
            text(
                "SELECT status, version, tier, origin, expires_at FROM public.vendor_doors WHERE id=:i"
            ),
            {"i": door_id},
        ).one()


def refused(engine, role, sql, code="42501", **params):
    with pytest.raises(DBAPIError) as caught:
        run(engine, role, sql, **params)
    assert getattr(caught.value.orig, "pgcode", None) == code


# --------------------------------------------------------------------------- domain twin


def test_sql_registrable_domain_matches_the_python_twin(engine) -> None:
    names = [
        "example.com",
        "www.netflix.com",
        "A.B.Example.CO.UK",
        "attacker.com.my",
        "vendor.com.my",
        "a.myshopify.com",
        "x.a.myshopify.com",
        "myshopify.com",
        "com",
        "com.my",
        "b.kobe.jp",
        "a.b.kobe.jp",
        "city.kobe.jp",
        "foo.invalidtld",
        "127.0.0.1",
        "example.com.",
        "a..com",
        "xn--bcher-kva.de",
        "example\u212a.com",
        "",
        "a" * 64 + ".com",
        "exa mple.com",
        "www.bbc.co.uk",
    ]
    for name in names:
        assert run(engine, None, "SELECT public.registrable_domain(:n)", n=name) == (
            registrable_domain(name)
        ), name


def test_sql_rules_table_equals_the_vendored_snapshot(engine) -> None:
    expected = sorted(rule_lines())
    with engine.connect() as c:
        stored = sorted(
            ("*." + n if k == "wildcard" else "!" + n if k == "exception" else n)
            for n, k in c.execute(text("SELECT name, kind FROM public.public_suffix_rules"))
        )
    assert stored == expected


def test_sql_sender_domain_matches_the_python_twin(engine) -> None:
    for address in [
        "billing@mail.netflix.com",
        "Billing <b@netflix.com>",
        "x@y@z.com",
        "a@localhost",
    ]:
        assert run(engine, None, "SELECT public.vendor_door_sender_domain(:a)", a=address) == (
            sender_registrable_domain(address)
        ), address


# --------------------------------------------------------------------------- template rules


@pytest.mark.parametrize(
    ("template", "slots", "problem"),
    [
        ("https://www.netflix.com/cancel", "{}", None),
        ("https://www.netflix.com/orders/{order_ref}/return", '{"order_ref":"order_ref"}', None),
        ("http://www.netflix.com/cancel", "{}", "scheme"),
        ("https://www.netflix.com/cancel?next=https://x.test", "{}", "path"),
        ("https://www.netflix.com/cancel?amount=10", "{}", "path"),
        ("https://www.netflix.com/cancel#frag", "{}", "path"),
        ("https://user@www.netflix.com/cancel", "{}", "host"),
        ("https://www.netflix.com:8443/cancel", "{}", "host"),
        ("https://127.0.0.1/cancel", "{}", "host"),
        ("https://xn--bcher-kva.de/cancel", "{}", "host"),
        ("https://netflix/cancel", "{}", "host"),
        ("https://{vendor}.example.com/cancel", '{"vendor":"account_ref"}', "host"),
        ("https://www.netflix.com", "{}", "path"),
        ("https://www.netflix.com/a/../b", "{}", "dot_segment"),
        ("https://www.netflix.com/a%2fb", "{}", "path"),
        ("https://www.netflix.com/{order_ref", '{"order_ref":"order_ref"}', "slot_syntax"),
        ("https://www.netflix.com/{order_ref}", "{}", "slot_types"),
        ("https://www.netflix.com/cancel", '{"order_ref":"order_ref"}', "slot_types"),
        ("https://www.netflix.com/{order_ref}", '{"order_ref":"amount"}', "slot_type"),
        ("https://www.netflix.com/" + "a" * 300, "{}", "length"),
    ],
)
def test_template_rules(engine, template, slots, problem) -> None:
    assert (
        run(
            engine,
            None,
            "SELECT public.vendor_door_template_problem(:t, CAST(:s AS jsonb))",
            t=template,
            s=slots,
        )
        == problem
    )


# --------------------------------------------------------------------------- grants


def test_execute_is_closed_to_public_and_every_other_role(engine) -> None:
    roles = [
        "PUBLIC",
        SWITCHBOARD,
        FINANCE,
        "butler_messenger_rw",
        "butler_general_rw",
        DASH,
        "connector_writer",
    ]
    with engine.connect() as c:
        for function, allowed in PUBLIC_FUNCTIONS.items():
            oid = c.execute(
                text(
                    "SELECT oid FROM pg_proc WHERE proname=:n AND pronamespace='public'::regnamespace"
                ),
                {"n": function},
            ).scalar_one()
            for role in roles:
                has = c.execute(
                    text("SELECT has_function_privilege(:r, :o, 'EXECUTE')"),
                    {"r": "public" if role == "PUBLIC" else role, "o": oid},
                ).scalar_one()
                assert has == (role in allowed), (function, role)
        for helper in HELPERS:
            oid = c.execute(
                text(
                    "SELECT oid FROM pg_proc WHERE proname=:n AND pronamespace='public'::regnamespace"
                ),
                {"n": helper},
            ).scalar_one()
            for role in roles:
                assert not c.execute(
                    text("SELECT has_function_privilege(:r, :o, 'EXECUTE')"),
                    {"r": "public" if role == "PUBLIC" else role, "o": oid},
                ).scalar_one(), (helper, role)


def test_no_role_writes_the_table_and_only_the_dashboard_reads_it(engine) -> None:
    with engine.connect() as c:
        for role in [
            "PUBLIC",
            SWITCHBOARD,
            FINANCE,
            "butler_messenger_rw",
            DASH,
            "connector_writer",
        ]:
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not c.execute(
                    text("SELECT has_table_privilege(:r, 'public.vendor_doors', :p)"),
                    {"r": "public" if role == "PUBLIC" else role, "p": privilege},
                ).scalar_one(), (role, privilege)
            reads = c.execute(
                text("SELECT has_table_privilege(:r, 'public.vendor_doors', 'SELECT')"),
                {"r": "public" if role == "PUBLIC" else role},
            ).scalar_one()
            assert reads == (role == DASH), role
        for role in [SWITCHBOARD, FINANCE, "butler_messenger_rw", DASH]:
            assert c.execute(
                text("SELECT has_table_privilege(:r, 'public.vendor_doors_current', 'SELECT')"),
                {"r": role},
            ).scalar_one(), role
        assert not c.execute(
            text(
                "SELECT has_table_privilege('butler_general_rw', 'public.vendor_doors_current', 'SELECT')"
            )
        ).scalar_one()


def test_delete_and_truncate_are_refused_even_for_the_owner(engine) -> None:
    propose(engine, FINANCE, "https://www.netflix.com/cancel")
    for statement in ("DELETE FROM public.vendor_doors", "TRUNCATE public.vendor_doors"):
        with engine.connect() as c:
            with pytest.raises(DBAPIError) as caught:
                c.execute(text(statement))
            assert getattr(caught.value.orig, "pgcode", None) == "42501"


def test_signature_takes_no_origin_or_verdict_argument(engine) -> None:
    with engine.connect() as c:
        names = (
            c.execute(
                text(
                    "SELECT unnest(proargnames) FROM pg_proc "
                    "WHERE proname='vendor_door_propose' AND pronamespace='public'::regnamespace"
                )
            )
            .scalars()
            .all()
        )
    assert not {"p_origin", "p_verdict", "p_sender", "p_tier"} & set(names)


# --------------------------------------------------------------------------- origin and verdict


def test_origin_follows_the_role_setting(engine) -> None:
    a = propose(engine, SWITCHBOARD, "https://www.netflix.com/a")
    b = propose(engine, FINANCE, "https://www.netflix.com/b")
    assert row(engine, uuid.UUID(a["id"])).origin == "email"
    assert row(engine, uuid.UUID(b["id"])).origin == "extracted"
    for role in ("butler_messenger_rw", "butler_general_rw", DASH, None):
        refused(
            engine,
            role,
            "SELECT public.vendor_door_propose('cancel', 'https://www.netflix.com/c', "
            "'{}'::jsonb, NULL, NULL, '{}'::jsonb)",
        )


def test_dashboard_functions_refuse_every_other_role(engine) -> None:
    created = create(engine, "https://www.netflix.com/mine")
    door = created["id"]
    for role in (SWITCHBOARD, FINANCE, "butler_messenger_rw", None):
        for call in (
            "SELECT public.vendor_door_confirm(CAST(:i AS uuid), 1, 'h')",
            "SELECT public.vendor_door_revoke(CAST(:i AS uuid), 1)",
            "SELECT public.vendor_door_reject(CAST(:i AS uuid), 1)",
            "SELECT public.vendor_door_create('cancel', 'https://www.netflix.com/x', '{}'::jsonb, NULL, NULL)",
        ):
            refused(engine, role, call, i=door)


def test_verified_tier_needs_authentication_and_sender_domain_equal_to_link_host(engine) -> None:
    good = propose(
        engine,
        SWITCHBOARD,
        "https://www.netflix.com/g",
        event_id=event(engine, "billing@mail.netflix.com", "authenticated"),
    )
    spoof = propose(
        engine,
        SWITCHBOARD,
        "https://www.netflix.com/s",
        event_id=event(engine, "billing@attacker.com", "authenticated"),
    )
    unauth = propose(
        engine,
        SWITCHBOARD,
        "https://www.netflix.com/u",
        event_id=event(engine, "billing@netflix.com", "unverified"),
    )
    missing = propose(engine, SWITCHBOARD, "https://www.netflix.com/m", event_id=uuid.uuid4())
    unlabeled = propose(
        engine,
        SWITCHBOARD,
        "https://www.netflix.com/n",
        event_id=event(engine, "billing@netflix.com", None),
    )
    assert [
        row(engine, uuid.UUID(x["id"])).tier for x in (good, spoof, unauth, missing, unlabeled)
    ] == ["verified", "unverified", "unverified", "unverified", "unverified"]
    with engine.connect() as c:
        stored = c.execute(
            text("SELECT sender_verdict, sender_domain FROM public.vendor_doors WHERE id=:i"),
            {"i": spoof["id"]},
        ).one()
    assert tuple(stored) == ("authenticated", "attacker.com")


def test_cross_suffix_domains_do_not_align(engine) -> None:
    result = propose(
        engine,
        SWITCHBOARD,
        "https://www.vendor.com.my/c",
        event_id=event(engine, "x@attacker.com.my", "authenticated"),
    )
    assert row(engine, uuid.UUID(result["id"])).tier == "unverified"


# --------------------------------------------------------------------------- confirm and races


def test_confirm_is_cas_idempotent_and_stamps_the_row(engine) -> None:
    door = uuid.UUID(propose(engine, FINANCE, "https://www.netflix.com/cancel")["id"])
    assert confirm(engine, door, 9)["reason"] == "stale_version"
    ok = confirm(engine, door, 1)
    assert (ok["outcome"], ok["status"], ok["version"]) == ("ok", "confirmed", 2)
    again = confirm(engine, door, 1)
    assert (again["outcome"], again["version"]) == ("unchanged", 2)
    state = row(engine, door)
    assert state.status == "confirmed" and state.expires_at is None


def test_two_confirms_at_the_same_version_apply_once(engine) -> None:
    door = uuid.UUID(propose(engine, FINANCE, "https://www.netflix.com/race")["id"])
    barrier = Barrier(2)

    def attempt():
        barrier.wait()
        return confirm(engine, door, 1)["outcome"]

    with ThreadPoolExecutor(2) as pool:
        outcomes = sorted(pool.map(lambda _: attempt(), range(2)))
    assert outcomes == ["ok", "unchanged"]
    assert row(engine, door).version == 2


def test_revoke_beats_a_concurrent_reconfirm_and_confirm_races_reject(engine) -> None:
    door = uuid.UUID(propose(engine, FINANCE, "https://www.netflix.com/rv")["id"])
    confirm(engine, door, 1)
    barrier = Barrier(2)

    def revoke():
        barrier.wait()
        return run(engine, DASH, "SELECT public.vendor_door_revoke(:i, 2)", i=door)["outcome"]

    def reconfirm():
        barrier.wait()
        return confirm(engine, door, 2)["outcome"]

    with ThreadPoolExecutor(2) as pool:
        first, second = pool.submit(revoke), pool.submit(reconfirm)
        # A confirm of an already confirmed door is a no-op whichever side runs first, and the
        # revoke applies exactly once: the door ends revoked.
        assert (first.result(), second.result()) in {("ok", "unchanged"), ("ok", "refused")}
    assert row(engine, door).status == "revoked"
    assert confirm(engine, door, 3)["reason"] == "not_pending"

    other = uuid.UUID(propose(engine, FINANCE, "https://www.netflix.com/cr")["id"])
    barrier = Barrier(2)

    def reject():
        barrier.wait()
        return run(engine, DASH, "SELECT public.vendor_door_reject(:i, 1)", i=other)["outcome"]

    def confirm_it():
        barrier.wait()
        return confirm(engine, other, 1)["outcome"]

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(reject), pool.submit(confirm_it)]
        results = sorted(f.result() for f in futures)
    # Exactly one wins by the CAS; the loser reads the winner's state without a write.
    final = row(engine, other)
    assert (final.status, final.version) in {("rejected", 2), ("confirmed", 2)}
    assert results in (["ok", "refused"], ["ok", "unchanged"])


def test_expired_proposals_cannot_be_confirmed_and_a_resighting_revives_them(engine) -> None:
    template = "https://www.netflix.com/old"
    door = uuid.UUID(propose(engine, FINANCE, template)["id"])
    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE public.vendor_doors SET expires_at = now() - interval '1 day' WHERE id=:i"
            ),
            {"i": door},
        )
    assert confirm(engine, door, 1)["reason"] == "expired"
    revived = propose(engine, FINANCE, template)
    assert (revived["outcome"], revived["id"], revived["version"]) == ("exists", str(door), 2)
    assert confirm(engine, door, 2)["outcome"] == "ok"


def test_confirming_a_replacement_revokes_the_door_it_supersedes(engine) -> None:
    old = uuid.UUID(create(engine, "https://www.netflix.com/v1")["id"])
    confirm(engine, old, 1)
    new = uuid.UUID(create(engine, "https://www.netflix.com/v2", supersedes=old)["id"])
    assert row(engine, old).status == "confirmed"
    confirm(engine, new, 1)
    assert row(engine, old).status == "revoked" and row(engine, new).status == "confirmed"


# --------------------------------------------------------------------------- caps, reject, view


def test_a_flood_of_unverified_proposals_does_not_drop_other_tiers(engine) -> None:
    for i in range(7):
        propose(engine, SWITCHBOARD, f"https://www.netflix.com/flood{i}")
    outcomes = [
        propose(engine, SWITCHBOARD, f"https://www.netflix.com/more{i}")["outcome"]
        for i in range(2)
    ]
    assert outcomes == ["dropped", "dropped"]
    verified = propose(
        engine,
        SWITCHBOARD,
        "https://www.netflix.com/real",
        event_id=event(engine, "billing@netflix.com", "authenticated"),
    )
    extracted = propose(engine, FINANCE, "https://www.netflix.com/extracted")
    owner_made = create(engine, "https://www.netflix.com/owner")
    assert [x["outcome"] for x in (verified, extracted, owner_made)] == ["stored"] * 3


def test_fifty_distinct_unverified_hosts_fill_only_the_unverified_owner_cap(engine) -> None:
    results = [
        propose(engine, SWITCHBOARD, f"https://www.vendor{i}.com/cancel")["outcome"]
        for i in range(51)
    ]
    assert results[:50] == ["stored"] * 50 and results[50] == "dropped"
    assert propose(engine, FINANCE, "https://www.vendor0.com/manage")["outcome"] == "stored"


def test_verified_tier_is_capped_per_sender_domain(engine) -> None:
    for i in range(5):
        assert (
            propose(
                engine,
                SWITCHBOARD,
                f"https://www.netflix.com/v{i}",
                event_id=event(engine, "billing@netflix.com", "authenticated"),
            )["outcome"]
            == "stored"
        )
    assert (
        propose(
            engine,
            SWITCHBOARD,
            "https://www.netflix.com/v5",
            event_id=event(engine, "billing@netflix.com", "authenticated"),
        )["outcome"]
        == "dropped"
    )


def test_reject_is_retained_and_suppresses_reproposal_but_not_the_dashboard(engine) -> None:
    template = "https://www.netflix.com/rej"
    door = uuid.UUID(propose(engine, SWITCHBOARD, template)["id"])
    assert run(engine, DASH, "SELECT public.vendor_door_reject(:i, 1)", i=door)["outcome"] == "ok"
    assert propose(engine, SWITCHBOARD, template)["outcome"] == "suppressed"
    assert propose(engine, FINANCE, template)["outcome"] == "suppressed"
    assert create(engine, template)["outcome"] == "stored"


def test_the_view_shows_only_confirmed_rows_of_the_live_owner(engine) -> None:
    pending = uuid.UUID(propose(engine, FINANCE, "https://www.netflix.com/p")["id"])
    done = uuid.UUID(propose(engine, FINANCE, "https://www.netflix.com/c")["id"])
    confirm(engine, done, 1)
    ids = {
        r[0]
        for r in [
            (x,)
            for x in run(
                engine,
                FINANCE,
                "SELECT coalesce(array_agg(id), ARRAY[]::uuid[]) FROM public.vendor_doors_current",
            )
        ]
    }
    assert done in ids and pending not in ids
    refused(engine, FINANCE, "SELECT count(*) FROM public.vendor_doors", code="42501")


def expire(engine, door):
    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE public.vendor_doors SET expires_at = now() - interval '1 day' WHERE id=:i"
            ),
            {"i": door},
        )


def verified_event(engine, template_host="netflix.com"):
    return event(engine, f"billing@{template_host}", "authenticated")


@pytest.mark.parametrize("expired", [False, True])
def test_a_verified_proposal_takes_over_an_unverified_squat(engine, expired) -> None:
    template = "https://www.netflix.com/squat"
    squat = uuid.UUID(propose(engine, SWITCHBOARD, template)["id"])
    assert row(engine, squat).tier == "unverified"
    if expired:
        expire(engine, squat)
    result = propose(engine, SWITCHBOARD, template, event_id=verified_event(engine))
    assert (result["outcome"], result["id"]) == ("exists", str(squat))
    taken = row(engine, squat)
    assert (taken.tier, taken.version, taken.status) == ("verified", 2, "proposed")
    assert taken.expires_at is not None


@pytest.mark.parametrize("expired", [False, True])
def test_the_owners_create_takes_over_a_squatted_template(engine, expired) -> None:
    template = "https://www.netflix.com/mine2"
    squat = uuid.UUID(propose(engine, SWITCHBOARD, template)["id"])
    if expired:
        expire(engine, squat)
    result = create(engine, template)
    assert result["id"] == str(squat)
    taken = row(engine, squat)
    assert (taken.origin, taken.tier, taken.version) == ("dashboard", "exempt", 2)


def test_an_equal_or_lower_sighting_does_not_take_over_a_live_proposal(engine) -> None:
    template = "https://www.netflix.com/held"
    first = uuid.UUID(propose(engine, SWITCHBOARD, template, event_id=verified_event(engine))["id"])
    for again in (propose(engine, SWITCHBOARD, template), propose(engine, FINANCE, template)):
        assert (again["outcome"], again["version"]) == ("exists", 1)
    assert row(engine, first).tier == "verified"


def test_a_takeover_is_subject_to_the_new_tiers_caps(engine) -> None:
    squat = uuid.UUID(propose(engine, SWITCHBOARD, "https://www.netflix.com/squat2")["id"])
    for i in range(5):
        stored = propose(
            engine,
            SWITCHBOARD,
            f"https://www.netflix.com/vfull{i}",
            event_id=verified_event(engine),
        )
        assert row(engine, uuid.UUID(stored["id"])).tier == "verified"
    blocked = propose(
        engine, SWITCHBOARD, "https://www.netflix.com/squat2", event_id=verified_event(engine)
    )
    assert (blocked["outcome"], blocked["reason"]) == ("dropped", "host_cap")
    assert (row(engine, squat).tier, row(engine, squat).version) == ("unverified", 1)


def test_expired_rows_do_not_revive_past_a_full_bucket(engine) -> None:
    expired = []
    for i in range(5):
        door = uuid.UUID(propose(engine, SWITCHBOARD, f"https://www.netflix.com/exp{i}")["id"])
        expired.append(door)
    for door in expired:
        expire(engine, door)
    for i in range(5):
        assert propose(engine, SWITCHBOARD, f"https://www.netflix.com/fresh{i}")["outcome"] == (
            "stored"
        )
    revived = propose(engine, SWITCHBOARD, "https://www.netflix.com/exp0")
    assert revived["outcome"] == "dropped" and revived["reason"] == "host_cap"
    assert row(engine, expired[0]).version == 1


def test_a_proposer_cannot_supersede_a_door_and_the_dashboard_only_its_own_host(engine) -> None:
    door = uuid.UUID(create(engine, "https://www.netflix.com/real")["id"])
    confirm(engine, door, 1)
    assert "p_supersedes" not in set(
        run(
            engine,
            None,
            "SELECT coalesce(array_agg(a), ARRAY[]::text[]) FROM pg_proc, unnest(proargnames) a "
            "WHERE proname='vendor_door_propose' AND pronamespace='public'::regnamespace",
        )
    )
    other_host = create(engine, "https://www.hulu.com/cancel", supersedes=door)
    assert (other_host["outcome"], other_host["reason"]) == ("refused", "supersedes")
    other_kind = create(engine, "https://www.netflix.com/manage", kind="manage", supersedes=door)
    assert other_kind["reason"] == "supersedes"
    assert create(engine, "https://www.netflix.com/real2", supersedes=door)["outcome"] == "stored"
    assert row(engine, door).status == "confirmed"


def test_a_takeover_replaces_the_sightings_facts_not_the_squatters(engine) -> None:
    vendor = uuid.uuid4()
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO public.entities(id, canonical_name, entity_type, roles) "
                "VALUES (:id, 'opaque vendor', 'organization', ARRAY[]::text[])"
            ),
            {"id": vendor},
        )
    template = "https://www.netflix.com/facts"
    squat = uuid.UUID(
        run(
            engine,
            SWITCHBOARD,
            "SELECT public.vendor_door_propose('cancel', :t, '{}'::jsonb, NULL, :v, '{}'::jsonb)",
            t=template,
            v=vendor,
        )["id"]
    )
    run(
        engine,
        DASH,
        "SELECT public.vendor_door_create('cancel', :t, '{}'::jsonb, NULL, NULL)",
        t=template,
    )
    with engine.connect() as c:
        stored = c.execute(
            text("SELECT vendor_entity_id, slot_types FROM public.vendor_doors WHERE id=:i"),
            {"i": squat},
        ).one()
    assert stored.vendor_entity_id is None and stored.slot_types == {}


def test_a_create_cannot_supersede_the_row_it_would_take_over(engine) -> None:
    template = "https://www.netflix.com/self"
    squat = uuid.UUID(propose(engine, SWITCHBOARD, template)["id"])
    result = create(engine, template, supersedes=squat)
    assert (result["outcome"], result["reason"]) == ("refused", "supersedes")
    assert row(engine, squat).version == 1
    door = uuid.UUID(create(engine, "https://www.netflix.com/self2")["id"])
    confirm(engine, door, 1)
    assert create(engine, "https://www.netflix.com/self2", supersedes=door)["reason"] == (
        "supersedes"
    )


@pytest.mark.parametrize("verified", [False, True])
def test_an_equal_tier_sighting_changes_neither_version_nor_expiry(engine, verified) -> None:
    template = f"https://www.netflix.com/equal{int(verified)}"

    def sight():
        return propose(
            engine,
            SWITCHBOARD,
            template,
            event_id=verified_event(engine) if verified else None,
        )

    first = uuid.UUID(sight()["id"])
    before = row(engine, first)
    again = sight()
    after = row(engine, first)
    assert (again["outcome"], again["id"]) == ("exists", str(first))
    assert (after.version, after.expires_at) == (before.version, before.expires_at)
