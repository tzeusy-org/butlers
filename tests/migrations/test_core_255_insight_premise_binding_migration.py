"""core_255: premise-bound proactive speech objects (bu-q7vx1q.5).

Real PostgreSQL: the new columns/vocabulary arrive without disturbing existing
rows, the enqueue definer is idempotent per (candidate, resolution), a second
schema's populated core run preserves every outcome and its provenance, and
bounded downgrades retain their documented folds without narrowing the shared
ledger CHECK.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import runpy
import shutil
import warnings
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from butlers.migrations import ALEMBIC_DIR, _build_alembic_config, run_migrations
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    assert_at_chain_head,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_CORE_255 = ALEMBIC_DIR / "versions/core/core_255_insight_premise_binding.py"
_HISTORICAL_255 = Path(__file__).with_name("data") / "core_255_a090a8ac.py.txt"
_HISTORICAL_SHA = "a090a8acd39e08a32bf4971e265e401e7334f42d"
_HISTORICAL_DIGEST = "2fa2f2257f1416ab11f875a663674f80a8da182280d400c1b3e90927c6198a39"
_TABLE_GRANT = (
    "    _execute_best_effort(\n"
    '        f"GRANT SELECT, INSERT, UPDATE ON TABLE public.insight_amendments TO {_SWITCHBOARD_ROLE}"\n'
    "    )\n"
)


def _experiment_config(db_url: str, tmp_path: Path, variant: str, source: str):
    """Change only a test copy of core_255 in the complete canonical environment."""
    snapshot = tmp_path / variant / "alembic"
    shutil.copytree(ALEMBIC_DIR, snapshot, ignore=shutil.ignore_patterns("__pycache__"))
    # core_004/core_159 resolve this canonical seed file relative to their
    # revision path. Keep that input too, rather than silently omitting seeds.
    shutil.copyfile(
        ALEMBIC_DIR.parent / "model_catalog_defaults.toml",
        snapshot.parent / "model_catalog_defaults.toml",
    )
    (snapshot / "versions/core" / _CORE_255.name).write_text(source, encoding="utf-8")
    config = _build_alembic_config(db_url, ["core"])
    config.set_main_option("script_location", str(snapshot))
    locations = config.get_main_option("version_locations").split(os.pathsep)
    config.set_main_option(
        "version_locations",
        os.pathsep.join(
            str(snapshot / "versions/core") if Path(p) == _CORE_255.parent else p for p in locations
        ),
    )
    return config


def _identity(conn) -> dict:
    return dict(
        conn.execute(
            text(
                "SELECT session_user, current_user, current_setting('role') AS role, "
                "current_setting('server_version') AS postgres_version"
            )
        )
        .mappings()
        .one()
    )


def _amendment_catalog(db_url: str) -> dict:
    """Safe disposable-PG witness: never include connection URLs or passwords."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            identity = _identity(conn)
            ownership = dict(
                conn.execute(
                    text(
                        "SELECT pg_get_userbyid(d.datdba) AS database_owner, "
                        "pg_get_userbyid(n.nspowner) AS public_schema_owner, "
                        "pg_has_role(current_user, n.nspowner, 'USAGE') AS effective_schema_owner, "
                        "r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolinherit "
                        "FROM pg_database d CROSS JOIN pg_namespace n CROSS JOIN pg_roles r "
                        "WHERE d.datname=current_database() AND n.nspname='public' "
                        "AND r.rolname=current_user"
                    )
                )
                .mappings()
                .one()
            )
            table = (
                conn.execute(
                    text(
                        "SELECT pg_get_userbyid(relowner) AS owner, relacl::text AS acl, "
                        "relrowsecurity AS rls, relforcerowsecurity AS force_rls "
                        "FROM pg_class WHERE oid = to_regclass('public.insight_amendments')"
                    )
                )
                .mappings()
                .one_or_none()
            )
            columns = (
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='insight_amendments' "
                        "ORDER BY ordinal_position"
                    )
                )
                .scalars()
                .all()
            )
            functions = (
                conn.execute(
                    text(
                        "SELECT proname, pg_get_userbyid(proowner) AS owner, "
                        "proacl::text AS acl, proconfig, prosecdef "
                        "FROM pg_proc WHERE pronamespace='public'::regnamespace "
                        "AND proname IN ('enqueue_premise_amendments', 'resolve_finance_bill_status') "
                        "ORDER BY proname"
                    )
                )
                .mappings()
                .all()
            )
            policies = (
                conn.execute(
                    text(
                        "SELECT policyname, permissive, roles, cmd, qual, with_check "
                        "FROM pg_policies WHERE schemaname='public' AND tablename='insight_amendments' "
                        "ORDER BY policyname"
                    )
                )
                .mappings()
                .all()
            )
            defaults = (
                conn.execute(
                    text(
                        "SELECT pg_get_userbyid(defaclrole) AS creator, defaclacl::text AS acl "
                        "FROM pg_default_acl WHERE defaclnamespace='public'::regnamespace "
                        "AND defaclobjtype='r' ORDER BY creator"
                    )
                )
                .mappings()
                .all()
            )
            memberships = (
                conn.execute(
                    text(
                        "SELECT parent.rolname AS granted_role, inherit_option, set_option "
                        "FROM pg_auth_members JOIN pg_roles parent ON parent.oid=roleid "
                        "WHERE member=(SELECT oid FROM pg_roles WHERE rolname=session_user) "
                        "ORDER BY parent.rolname"
                    )
                )
                .mappings()
                .all()
            )
            return {
                "identity": identity,
                "ownership": ownership,
                "qualified_object": table is not None,
                "table": dict(table) if table else None,
                "ordinary_columns": columns,
                "functions": [dict(f) for f in functions],
                "policies": [dict(p) for p in policies],
                "creator_defaults": [dict(d) for d in defaults],
                "memberships": [dict(m) for m in memberships],
            }
    finally:
        engine.dispose()


def _amendment_rows(db_url: str) -> list[dict]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return (
                conn.execute(
                    text("SELECT to_jsonb(a) FROM public.insight_amendments a ORDER BY id")
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()


def _expect_permission_denied(conn, statement: str, params: dict | None = None) -> str:
    with pytest.raises(DBAPIError) as error:
        with conn.begin_nested():
            conn.execute(text(statement), params or {})
    assert error.value.orig.pgcode == "42501"
    return error.value.orig.pgcode


def _publish_disposable_receipt(receipt: dict) -> None:
    # Successful captured stdout and JUnit properties are intentionally stripped
    # by CI's privacy-minimal duration artifacts. One synthetic-only warning
    # leaves the causal SQL observations in the normal hosted test-step log.
    receipt["checkout_sha"] = os.environ.get("GITHUB_SHA", "local-not-hosted")
    warnings.warn("core_255 disposable PostgreSQL receipt: " + json.dumps(receipt), stacklevel=2)


def _metadata_inventory(db_url: str) -> dict[str, set[str]]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            inventory: dict[str, set[str]] = {}
            for schema, table, column in conn.execute(
                text(
                    "SELECT table_schema, table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')"
                )
            ):
                inventory.setdefault(f"{schema}.{table}", set()).add(column)
            return inventory
    finally:
        engine.dispose()


def _assert_bootstrap_metadata_visible(ordinary: dict, administrative: dict) -> None:
    assert administrative["qualified_object"] and administrative["ordinary_columns"]
    assert ordinary["ordinary_columns"] == administrative["ordinary_columns"], (
        "targeted grant must expose bootstrap-created amendment metadata"
    )


def _historical_visibility_controls(postgres_container, tmp_path: Path) -> list[dict]:
    historical = _HISTORICAL_255.read_text(encoding="utf-8")
    assert hashlib.sha256(historical.encode()).hexdigest() == _HISTORICAL_DIGEST
    assert historical.count('    op.execute("DROP TABLE IF EXISTS public.insight_amendments")') == 1
    current = _CORE_255.read_text(encoding="utf-8")
    assert current.count(_TABLE_GRANT) == 1
    grant_only = historical.replace(
        "    _execute_best_effort(CREATE_BILL_STATUS_FN)",
        _TABLE_GRANT + "    _execute_best_effort(CREATE_BILL_STATUS_FN)",
        1,
    )
    keep_only = historical.replace(
        '    op.execute("DROP TABLE IF EXISTS public.insight_amendments")',
        "    # Isolated empty-database retention-only control.",
        1,
    )
    current_drop = current.replace(
        "    # public.insight_amendments is deliberately kept:",
        '    op.execute("DROP TABLE IF EXISTS public.insight_amendments")\n'
        "    # public.insight_amendments is deliberately kept:",
        1,
    )
    assert current_drop != current
    variants = (
        ("historic-initial", historical, False, True),
        ("historic-grant-only", grant_only, True, True),
        ("historic-keep-only", keep_only, True, False),
        ("current-hypothetical-drop-with-grant", current_drop, True, True),
        (
            "current-hypothetical-drop-without-grant",
            current_drop.replace(_TABLE_GRANT, ""),
            False,
            True,
        ),
    )
    fresh_name = migration_db_name()
    fresh_url = create_migration_db(postgres_container, fresh_name)
    fresh_config = _experiment_config(fresh_url, tmp_path, "historic-fresh", historical)
    command.upgrade(fresh_config, "core_255")
    fresh_inventory = _metadata_inventory(fresh_url)
    assert "public.insight_amendments" in fresh_inventory
    receipts = []
    for variant, source, visible, recreated in variants:
        db_name = migration_db_name()
        db_url = create_migration_db(postgres_container, db_name)
        admin_url = migration_bootstrap_db_url(postgres_container, db_name)
        config = _experiment_config(db_url, tmp_path, variant, source)
        command.upgrade(config, "core_255")
        before = _amendment_catalog(db_url)
        assert before["qualified_object"] and before["ordinary_columns"]
        # Destructive historical/hypothetical variants run only on empty ledgers.
        assert _amendment_rows(admin_url) == []
        config.set_main_option("sqlalchemy.url", admin_url.replace("%", "%%"))
        command.downgrade(config, "core_254")
        command.upgrade(config, "core_255")
        admin = _amendment_catalog(admin_url)
        ordinary = _amendment_catalog(db_url)
        assert admin["qualified_object"] and admin["ordinary_columns"]
        assert ordinary["qualified_object"]  # catalog presence is not metadata visibility
        assert ordinary["table"]["owner"] == (
            admin["identity"]["session_user"] if recreated else before["identity"]["session_user"]
        )
        inventory = _metadata_inventory(db_url)
        only_fresh = set(fresh_inventory) - set(inventory)
        assert set(inventory) - set(fresh_inventory) == set()
        assert only_fresh == (set() if visible else {"public.insight_amendments"})
        if visible:
            assert inventory == fresh_inventory
        else:
            # The actual smoke equality goes RED while its qualified object
            # witness stays positive; grant-only and keep-only both restore it.
            with pytest.raises(AssertionError, match="privilege-filtered inventory"):
                assert inventory == fresh_inventory, "privilege-filtered inventory differs"
            assert ordinary["ordinary_columns"] == []
        if recreated:
            # A database owner implicitly owns public through pg_database_owner
            # on fresh PG17. Schema ownership permits DROP, but not relation
            # ALTER. Keep those authorities separate; data variants stay empty.
            assert ordinary["ownership"]["public_schema_owner"] == "pg_database_owner"
            assert ordinary["ownership"]["effective_schema_owner"] is True
            engine = create_engine(db_url)
            try:
                with engine.begin() as conn:
                    _expect_permission_denied(
                        conn, "ALTER TABLE public.insight_amendments ENABLE ROW LEVEL SECURITY"
                    )
            finally:
                engine.dispose()
            config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
            command.downgrade(config, "core_254")
            assert _amendment_catalog(admin_url)["qualified_object"] is False
            assert _amendment_catalog(admin_url)["functions"] == []
            command.upgrade(config, "core_255")
            assert (
                _amendment_catalog(db_url)["table"]["owner"] == before["identity"]["session_user"]
            )
            assert _metadata_inventory(db_url) == fresh_inventory
        receipts.append(
            {
                "variant": variant,
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "historical_git_sha": _HISTORICAL_SHA,
                "initial": before,
                "administrative": admin,
                "ordinary": ordinary,
                "only_fresh": sorted(only_fresh),
                "inventory_equality": "PASS" if visible else "RED with positive object",
                "ordinary_ownership": "ALTER 42501; schema-owner DROP and ordinary recreate succeed"
                if recreated
                else "retained normal table owner",
            }
        )
    # Falsify the exact supported-install metadata assertion by neutralizing
    # only the grant in an empty test snapshot, before bootstrap can re-widen it.
    neutral_name = migration_db_name()
    neutral_url = create_migration_db(postgres_container, neutral_name)
    neutral_admin_url = migration_bootstrap_db_url(postgres_container, neutral_name)
    neutral_source = current.replace(_TABLE_GRANT, "")
    neutral_config = _experiment_config(
        neutral_url, tmp_path, "current-grant-neutralized", neutral_source
    )
    command.upgrade(neutral_config, "core_254")
    neutral_config.set_main_option("sqlalchemy.url", neutral_admin_url.replace("%", "%%"))
    command.upgrade(neutral_config, "core_255")
    neutral_ordinary = _amendment_catalog(neutral_url)
    neutral_admin = _amendment_catalog(neutral_admin_url)
    with pytest.raises(AssertionError, match="targeted grant must expose"):
        _assert_bootstrap_metadata_visible(neutral_ordinary, neutral_admin)
    assert neutral_ordinary["ordinary_columns"] == []
    receipts.append(
        {
            "variant": "current-grant-neutralized-first-install",
            "source_sha256": hashlib.sha256(neutral_source.encode()).hexdigest(),
            "ordinary": neutral_ordinary,
            "administrative": neutral_admin,
            "same_install_assertion": "RED with positive object",
        }
    )
    _publish_disposable_receipt({"historical_controls": receipts})
    return receipts


def test_core_255_bootstrap_install_grants_visibility_without_peer_row_authority(
    postgres_container, tmp_path
) -> None:
    """Real grants, inherited inventory, RLS and definers are distinct witnesses."""
    historical = _historical_visibility_controls(postgres_container, tmp_path)
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    admin_url = migration_bootstrap_db_url(postgres_container, db_name)
    command.upgrade(_build_alembic_config(db_url, ["core"]), "core_254")
    assert _amendment_catalog(db_url)["qualified_object"] is False
    command.upgrade(_build_alembic_config(admin_url, ["core"]), "core_255")
    ordinary = _amendment_catalog(db_url)
    administrative = _amendment_catalog(admin_url)
    assert ordinary["ownership"]["database_owner"] == ordinary["identity"]["session_user"]
    assert ordinary["ownership"]["effective_schema_owner"] is True
    assert all(
        ordinary["ownership"][flag] is False
        for flag in ("rolsuper", "rolcreaterole", "rolcreatedb")
    )
    # This is the grant regression seam: bootstrap creates the table after the
    # normal login's per-creator defaults, and before any grant-all replay.
    _assert_bootstrap_metadata_visible(ordinary, administrative)
    assert ordinary["ordinary_columns"]
    assert ordinary["table"]["owner"] == administrative["identity"]["session_user"]
    switchboard_membership = next(
        m for m in ordinary["memberships"] if m["granted_role"] == "butler_switchboard_rw"
    )
    assert switchboard_membership["inherit_option"] and switchboard_membership["set_option"]
    assert ordinary["table"]["rls"] is True
    assert ordinary["table"]["force_rls"] is False  # existing posture; no hardening adoption
    for function in administrative["functions"]:
        assert function["prosecdef"] is True
        assert function["proconfig"] == ["search_path=pg_catalog, pg_temp"]
    assert len(administrative["functions"]) == 2

    # The real finance chain supplies the bill; no copied two-column stand-in.
    asyncio.run(run_migrations(db_url, chain="finance", schema="finance"))
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            candidate = conn.execute(
                text(
                    "INSERT INTO public.insight_candidates "
                    "(origin_butler, priority, category, dedup_key, expires_at, message, status, "
                    "delivered_at, premise, delivery_ref) VALUES "
                    "('finance', 80, 'synthetic', 'synthetic:grant-sentinel', now()+interval '1 day', "
                    "'Synthetic grant witness', 'delivered', '2080-01-01'::timestamptz, "
                    '\'{"kind":"owner_condition","source":"synthetic-source",'
                    '"fingerprint":"synthetic-fingerprint"}\', jsonb_build_object(\'synthetic\', true)) RETURNING id'
                )
            ).scalar_one()
            for status, delivered_at, kind in (
                ("pending", None, "owner_condition"),
                ("delivered", "2100-01-01", "owner_condition"),
                ("delivered", "2080-01-01", "probe"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO public.insight_candidates "
                        "(origin_butler, priority, category, dedup_key, expires_at, message, status, "
                        "delivered_at, premise) VALUES ('finance', 80, 'synthetic', :dedup, "
                        "now()+interval '1 day', 'Synthetic exclusion witness', :status, "
                        "CAST(:delivered AS timestamptz), jsonb_build_object('kind', CAST(:kind AS text), "
                        "'source', 'synthetic-source', 'fingerprint', 'synthetic-fingerprint'))"
                    ),
                    {
                        "status": status,
                        "delivered": delivered_at,
                        "kind": kind,
                        "dedup": f"synthetic:{status}:{kind}:{delivered_at}",
                    },
                )
            conn.execute(
                text(
                    "INSERT INTO public.insight_amendments "
                    "(candidate_id, episode_key, reason, summary) "
                    "VALUES (:candidate, 'synthetic-planted', 'synthetic', 'Synthetic planted witness')"
                ),
                {"candidate": candidate},
            )
            bill = conn.execute(
                text(
                    "INSERT INTO finance.bills (payee, amount, currency, due_date, frequency, status) "
                    "VALUES ('Synthetic payee', 1, 'USD', '2099-01-01', 'one_time', 'pending') RETURNING id"
                )
            ).scalar_one()
            enqueue_roles = set(runpy.run_path(str(_CORE_255))["_ENQUEUE_ROLES"])
            roles = (
                conn.execute(
                    text("SELECT rolname FROM pg_roles WHERE rolname=ANY(:roles) ORDER BY rolname"),
                    {"roles": sorted(enqueue_roles | {"connector_writer"})},
                )
                .scalars()
                .all()
            )
            unrelated_role = f"synthetic_unrelated_{db_name}"
            conn.execute(
                text(
                    f'CREATE ROLE "{unrelated_role}" NOLOGIN NOINHERIT NOSUPERUSER '
                    "NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS"
                )
            )
    finally:
        engine.dispose()
    assert set(roles) - enqueue_roles == {"connector_writer"}
    assert enqueue_roles - set(roles) <= {"butler_calendar_rw"}
    assert _run(db_url, "SELECT count(*) FROM public.insight_amendments") == 0
    assert len(_amendment_rows(admin_url)) == 1
    receipts = []
    for replay in range(3):
        if replay:
            _bootstrap_migration_prerequisites(admin_url, urlparse(db_url).username)
        for index, role in enumerate(roles):
            # Bootstrap does not grant optional Calendar membership to the
            # migration login. Observe that role under the disposable control
            # login when present; do not manufacture a production membership.
            role_url = admin_url if role == "butler_calendar_rw" else db_url
            engine = create_engine(role_url)
            try:
                with engine.begin() as conn:
                    conn.execute(text(f'SET LOCAL ROLE "{role}"'))
                    identity = _identity(conn)
                    assert identity["session_user"] == (
                        administrative["identity"]["session_user"]
                        if role == "butler_calendar_rw"
                        else ordinary["identity"]["session_user"]
                    )
                    assert identity["current_user"] == identity["role"] == role
                    grants = dict(
                        conn.execute(
                            text(
                                "SELECT has_table_privilege(current_user, 'public.insight_amendments', 'SELECT') AS can_select, "
                                "has_table_privilege(current_user, 'public.insight_amendments', 'INSERT') AS can_insert, "
                                "has_table_privilege(current_user, 'public.insight_amendments', 'UPDATE') AS can_update, "
                                "has_table_privilege(current_user, 'public.insight_amendments', 'DELETE') AS can_delete"
                            )
                        )
                        .mappings()
                        .one()
                    )
                    if replay and role != "butler_calendar_rw":
                        # Position the RLS negative after actual grant widening;
                        # a missing table ACL must not substitute for the policy.
                        assert all(grants.values()), (role, replay, grants)
                    params = {
                        "candidate": candidate,
                        "episode": f"synthetic-direct:{replay}:{role}",
                    }
                    insert = (
                        "INSERT INTO public.insight_amendments (candidate_id, episode_key, reason) "
                        "VALUES (:candidate, :episode, 'synthetic-direct') RETURNING id"
                    )
                    if role == "butler_switchboard_rw":
                        assert (
                            conn.execute(
                                text("SELECT count(*) FROM public.insight_amendments")
                            ).scalar_one()
                            > 0
                        )
                        direct_id = conn.execute(text(insert), params).scalar_one()
                        assert (
                            conn.execute(
                                text(
                                    "UPDATE public.insight_amendments SET attempts=attempts+1 WHERE id=:id"
                                ),
                                {"id": direct_id},
                            ).rowcount
                            == 1
                        )
                        assert (
                            conn.execute(
                                text(
                                    "SELECT status FROM public.resolve_finance_bill_status(:bill)"
                                ),
                                {"bill": bill},
                            ).scalar_one()
                            == "pending"
                        )
                        assert (
                            conn.execute(
                                text(
                                    "SELECT status FROM public.resolve_finance_bill_status(gen_random_uuid())"
                                )
                            ).all()
                            == []
                        )
                        _expect_permission_denied(conn, "SELECT status FROM finance.bills")
                        direct = "INSERT/UPDATE positive; peer SELECT 42501"
                    else:
                        _expect_permission_denied(conn, insert, params)
                        has_select = conn.execute(
                            text(
                                "SELECT has_table_privilege(current_user, 'public.insight_amendments', 'SELECT')"
                            )
                        ).scalar_one()
                        if has_select:
                            assert (
                                conn.execute(
                                    text("SELECT count(*) FROM public.insight_amendments")
                                ).scalar_one()
                                == 0
                            )
                        else:
                            _expect_permission_denied(
                                conn, "SELECT * FROM public.insight_amendments"
                            )
                        for verb in (
                            "UPDATE public.insight_amendments SET attempts=999",
                            "DELETE FROM public.insight_amendments",
                        ):
                            privilege = "UPDATE" if verb.startswith("UPDATE") else "DELETE"
                            if conn.execute(
                                text(
                                    "SELECT has_table_privilege(current_user, 'public.insight_amendments', :privilege)"
                                ),
                                {"privilege": privilege},
                            ).scalar_one():
                                assert conn.execute(text(verb)).rowcount == 0
                            else:
                                _expect_permission_denied(conn, verb)
                        direct = "INSERT 42501; planted rows invisible and unchanged"
                        _expect_permission_denied(
                            conn,
                            "SELECT * FROM public.resolve_finance_bill_status(:bill)",
                            {"bill": bill},
                        )
                    enqueue = (
                        "SELECT public.enqueue_premise_amendments(:source, :fingerprint, "
                        "CAST(:resolved AS timestamptz), 'Synthetic function witness')"
                    )
                    args = {
                        "source": "synthetic-source",
                        "fingerprint": "synthetic-fingerprint",
                        "resolved": f"2099-01-01 00:{replay:02d}:{index:02d}+00",
                    }
                    if role in enqueue_roles:
                        assert conn.execute(text(enqueue), args).scalar_one() == 1
                        assert conn.execute(text(enqueue), args).scalar_one() == 0
                        for changes in (
                            {"source": "synthetic-mismatch"},
                            {"fingerprint": "synthetic-mismatch"},
                            {"resolved": "2079-01-01 00:00:00+00"},
                        ):
                            assert conn.execute(text(enqueue), args | changes).scalar_one() == 0
                        execution = "matching 1; duplicate/mismatch/before-delivery 0"
                    else:
                        _expect_permission_denied(conn, enqueue, args)
                        execution = "42501"
                    receipts.append(
                        {
                            "replay": replay,
                            "identity": identity,
                            "direct": direct,
                            "enqueue": execution,
                            "effective_table_grants": grants,
                        }
                    )
            finally:
                engine.dispose()
        rows = _amendment_rows(admin_url)  # new acquisition proves each role's commit
        assert len(rows) == 1 + (replay + 1) * (len(roles) - 1 + 1)
        assert all(row["candidate_id"] == str(candidate) for row in rows)
        assert max(row["attempts"] for row in rows) == 1
        assert any(
            row["episode_key"] == "synthetic-planted" and row["attempts"] == 0 for row in rows
        )
        assert _run(db_url, "SELECT count(*) FROM public.insight_amendments") == 0
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn:
                conn.execute(text(f'SET LOCAL ROLE "{unrelated_role}"'))
                assert (
                    conn.execute(
                        text("SELECT has_schema_privilege(current_user, 'public', 'USAGE')")
                    ).scalar_one()
                    is True
                )
                for signature in (
                    "public.enqueue_premise_amendments(text,text,timestamptz,text)",
                    "public.resolve_finance_bill_status(uuid)",
                ):
                    assert (
                        conn.execute(
                            text(
                                "SELECT has_function_privilege(current_user, :signature, 'EXECUTE')"
                            ),
                            {"signature": signature},
                        ).scalar_one()
                        is False
                    )
                _expect_permission_denied(
                    conn,
                    "SELECT public.enqueue_premise_amendments('synthetic-source', "
                    "'synthetic-fingerprint', '2099-01-01'::timestamptz, NULL)",
                )
                _expect_permission_denied(
                    conn, "SELECT * FROM public.resolve_finance_bill_status(:bill)", {"bill": bill}
                )
                receipts.append(
                    {
                        "replay": replay,
                        "identity": _identity(conn),
                        "enqueue": "42501",
                        "finance_probe": "42501",
                    }
                )
        finally:
            engine.dispose()
    _publish_disposable_receipt(
        {
            "historical_controls": [r["variant"] for r in historical],
            "current_source_sha256": hashlib.sha256(_CORE_255.read_bytes()).hexdigest(),
            "current_install": ordinary,
            "current_role_matrix": receipts,
            "retained_rows": len(rows),
            "optional_calendar": "present" if "butler_calendar_rw" in roles else "absent",
        }
    )


def _run(db_url: str, statement: str):
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            return conn.execute(text(statement)).scalar()
    finally:
        engine.dispose()


def _attention_ledger_snapshot(db_url: str) -> tuple[list[dict], list[tuple], list[tuple]]:
    """Read persisted provenance and catalog shape through a new connection."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        "SELECT to_jsonb(ledger) FROM public.attention_ledger AS ledger ORDER BY id"
                    )
                )
                .scalars()
                .all()
            )
            columns = conn.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'attention_ledger' "
                    "ORDER BY ordinal_position"
                )
            ).all()
            checks = conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid), convalidated "
                    "FROM pg_constraint WHERE conrelid = 'public.attention_ledger'::regclass "
                    "AND contype = 'c' ORDER BY conname"
                )
            ).all()
            return rows, [tuple(c) for c in columns], [tuple(c) for c in checks]
    finally:
        engine.dispose()


def _seed_attention_ledger(conn, outcomes: tuple[str, ...], provenance: str) -> None:
    """Plant the same full outcome/provenance witness for replay and rollback."""
    conn.execute(
        text(
            "INSERT INTO public.attention_ledger "
            "(origin_butler, source, outcome, reason, dedup_key, notification_ref, metadata) "
            "VALUES ('general', 'insight', :outcome, :reason, :dedup, :notification, "
            "jsonb_build_object('seed', CAST(:outcome AS text), 'provenance', CAST(:provenance AS text)))"
        ),
        [
            {
                "outcome": outcome,
                "reason": f"synthetic-reason:{outcome}",
                "dedup": f"synthetic-dedup:{outcome}",
                "notification": f"synthetic-notification:{outcome}",
                "provenance": provenance,
            }
            for outcome in outcomes
        ],
    )


def test_current_attention_outcomes_survive_populated_schema_replay(postgres_container) -> None:
    """A new core schema must preserve an already-current shared ledger."""
    outcomes = (
        "delivered",
        "coalesced",
        "deferred",
        "suppressed",
        "failed",
        "expired",
        "withdrawn",
        "amended",
    )
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core", schema="general"))
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            assert_at_chain_head(conn, schema="general")
            assert conn.execute(
                text(
                    "SELECT NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).scalar_one()
            _seed_attention_ledger(conn, outcomes, "synthetic-replay-sentinel")
            candidate = conn.execute(
                text(
                    "INSERT INTO public.insight_candidates "
                    "(origin_butler, priority, category, dedup_key, expires_at, message, status) "
                    "VALUES ('general', 50, 'synthetic', 'synthetic-replay-candidate', "
                    "now() + interval '1 day', 'Synthetic replay sentinel', 'withdrawn') RETURNING id"
                )
            ).scalar_one()
        before = _attention_ledger_snapshot(db_url)
        assert len(before[0]) == len(outcomes)
        assert {row["outcome"] for row in before[0]} == set(outcomes)

        # Baseline falsification: before the repair this genuine production
        # traversal reaches core_168's narrower CHECK with all eight rows present.
        # Do not catch that failure, stamp past it, or hand-build a newer table.
        asyncio.run(run_migrations(db_url, chain="core", schema="health"))
        for schema in ("general", "health", "health"):
            asyncio.run(run_migrations(db_url, chain="core", schema=schema))
            with engine.connect() as conn:
                assert_at_chain_head(conn, schema=schema)
            assert _attention_ledger_snapshot(db_url) == before
        with engine.begin() as conn:
            assert (
                conn.execute(
                    text("SELECT status FROM public.insight_candidates WHERE id = :id"),
                    {"id": candidate},
                ).scalar_one()
                == "withdrawn"
            )
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO public.attention_ledger (origin_butler, source, outcome) "
                            "VALUES ('general', 'insight', 'unadopted-outcome')"
                        )
                    )
        assert _attention_ledger_snapshot(db_url) == before
    finally:
        engine.dispose()

    # A genuinely empty database is a positive control, not the reproduction.
    fresh_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(fresh_url, chain="core", schema="health"))
    fresh = _attention_ledger_snapshot(fresh_url)
    assert fresh[0] == []
    assert fresh[1:] == before[1:]
    outcome_check = next(check for check in fresh[2] if check[0] == "chk_attention_ledger_outcome")
    assert outcome_check[2] is True
    assert set(re.findall(r"'([^']+)'", outcome_check[1])) == set(outcomes)


@pytest.mark.parametrize(
    "boundaries",
    [
        pytest.param(
            (
                ("core_168", "core_167", {"failed": "deferred"}),
                ("core_241", "core_240", {"expired": "suppressed"}),
            ),
            id="older-single-outcome-folds",
        ),
        pytest.param(
            (("core_255", "core_254", {"withdrawn": "suppressed", "amended": "delivered"}),),
            id="premise-amendment-folds",
        ),
    ],
)
def test_bounded_attention_downgrades_preserve_cumulative_check_and_provenance(
    postgres_container, boundaries
) -> None:
    """Each isolated boundary preserves its complete outcome/provenance witness.

    Group the two older single-outcome folds; retain core_255's separate control.
    Every boundary still provisions a fresh database and executes the original
    upgrade/seed/downgrade/invalid-value/reupgrade assertion body unchanged.
    """
    for revision, predecessor, folds in boundaries:
        try:
            _assert_bounded_attention_downgrade(postgres_container, revision, predecessor, folds)
        except Exception as error:
            error.add_note(f"Isolated boundary {revision} -> {predecessor}; owned folds={folds!r}")
            raise


def _assert_bounded_attention_downgrade(postgres_container, revision, predecessor, folds) -> None:
    outcomes = (
        "delivered",
        "coalesced",
        "deferred",
        "suppressed",
        "failed",
        "expired",
        "withdrawn",
        "amended",
    )
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, ["core"])
    # Bound the actual upgrade: this never rolls back unrelated later boundaries.
    command.upgrade(config, revision)
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            _seed_attention_ledger(conn, outcomes, "synthetic-downgrade-sentinel")
        before = _attention_ledger_snapshot(db_url)
        assert len(before[0]) == len(outcomes)
        assert {row["outcome"] for row in before[0]} == set(outcomes)
        expected = (
            [dict(row, outcome=folds.get(row["outcome"], row["outcome"])) for row in before[0]],
            before[1],
            before[2],
        )
        command.downgrade(config, predecessor)
        assert _attention_ledger_snapshot(db_url) == expected
        check = next(c for c in expected[2] if c[0] == "chk_attention_ledger_outcome")
        assert check[2] is True
        assert set(re.findall(r"'([^']+)'", check[1])) == set(outcomes)
        # Keeping newer values must not turn the constraint into a fail-open one.
        with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "INSERT INTO public.attention_ledger (origin_butler, source, outcome) "
                            "VALUES ('general', 'insight', 'unadopted-outcome')"
                        )
                    )
        command.upgrade(config, revision)
        assert _attention_ledger_snapshot(db_url) == expected
    finally:
        engine.dispose()


def test_core_255_adds_premise_objects_idempotently_and_downgrades_cleanly(
    postgres_container,
) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, ["core"])
    command.upgrade(config, "core_254")
    candidate = _run(
        db_url,
        "INSERT INTO public.insight_candidates (origin_butler, priority, category, dedup_key,"
        " expires_at, message) VALUES ('finance', 80, 'bill-due', 'finance:bill-due:b:d',"
        " now() + interval '1 day', 'Bill due') RETURNING id::text",
    )

    command.upgrade(config, "core_255")
    unbound = (
        f"SELECT premise IS NULL AND delivery_ref IS NULL FROM public.insight_candidates"
        f" WHERE id = '{candidate}'"
    )
    assert _run(db_url, unbound) is True
    # The core chain replays per butler schema against the shared public objects.
    asyncio.run(run_migrations(db_url, chain="core", schema="general"))

    _run(
        db_url,
        "UPDATE public.insight_candidates SET status = 'delivered',"
        " delivered_at = now() - interval '1 hour',"
        ' premise = \'{"kind": "owner_condition", "source": "s",'
        f' "fingerprint": "f"}}\'::jsonb WHERE id = \'{candidate}\' RETURNING 1',
    )
    resolved = "'2099-01-01T00:00:00Z'::timestamptz"
    enqueue = f"SELECT public.enqueue_premise_amendments('s', 'f', {resolved}, NULL)"
    assert _run(db_url, enqueue) == 1
    assert _run(db_url, enqueue) == 0  # the same resolution never queues twice
    assert _run(db_url, "SELECT count(*) FROM public.insight_amendments") == 1

    _run(
        db_url,
        "INSERT INTO public.attention_ledger (origin_butler, source, outcome)"
        " VALUES ('finance', 'insight', 'withdrawn') RETURNING 1",
    )
    _run(
        db_url,
        f"UPDATE public.insight_candidates SET status = 'withdrawn' WHERE id = '{candidate}'"
        " RETURNING 1",
    )

    command.downgrade(config, "core_254")
    # The amendment ledger is durable evidence and survives a downgrade.
    assert _run(db_url, "SELECT to_regclass('public.insight_amendments') IS NOT NULL") is True
    assert (
        _run(
            db_url,
            "SELECT to_regprocedure('public.enqueue_premise_amendments(text, text, timestamptz, text)') IS NULL",
        )
        is True
    )
    status = f"SELECT status FROM public.insight_candidates WHERE id = '{candidate}'"
    assert _run(db_url, status) == "filtered"
    assert _run(db_url, "SELECT outcome FROM public.attention_ledger") == "suppressed"

    command.upgrade(config, "core_255")
    assert _run(db_url, "SELECT count(*) FROM public.insight_amendments") == 1
    pins = (
        "SELECT bool_and(proconfig = ARRAY['search_path=pg_catalog, pg_temp']) FROM pg_proc"
        " WHERE proname IN ('enqueue_premise_amendments', 'resolve_finance_bill_status')"
    )
    assert _run(db_url, pins) is True

    # Extend the original enqueue/rollback witness with every stored amendment
    # state and complete row equality under both authorized rollback owners.
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.insight_amendments "
                    "(candidate_id, episode_key, reason, summary, state, attempts, created_at, resolved_at) "
                    "VALUES (CAST(:candidate AS uuid), :episode, 'synthetic-retention', :summary, :state, "
                    "3, '2099-01-01'::timestamptz, '2099-01-02'::timestamptz)"
                ),
                [
                    {
                        "candidate": candidate,
                        "episode": f"synthetic-retention:{state}",
                        "summary": f"Synthetic {state} evidence",
                        "state": state,
                    }
                    for state in ("applied", "fold", "folded")
                ],
            )
    finally:
        engine.dispose()
    before = _amendment_rows(db_url)
    assert len(before) == 4
    assert {row["state"] for row in before} == {"pending", "applied", "fold", "folded"}
    admin_url = migration_bootstrap_db_url(postgres_container, urlparse(db_url).path.lstrip("/"))
    before_catalog = _amendment_catalog(db_url)
    assert (
        _run(db_url, "SELECT count(*) FROM public.insight_amendments") == 4
    )  # owner bypasses ENABLE RLS
    bootstrap_config = _build_alembic_config(admin_url, ["core"])
    receipts = []
    for authority, rollback_config in (("owning", config), ("managed-bootstrap", bootstrap_config)):
        assert (
            _run(
                db_url,
                f"UPDATE public.insight_candidates SET status='withdrawn', "
                'premise=\'{"synthetic":"loss-witness"}\', '
                f"delivery_ref='{{\"synthetic\":\"delivery-witness\"}}' WHERE id='{candidate}' RETURNING 1",
            )
            == 1
        )
        assert (
            _run(
                db_url,
                "SELECT premise->>'synthetic'='loss-witness' AND "
                f"delivery_ref->>'synthetic'='delivery-witness' FROM public.insight_candidates WHERE id='{candidate}'",
            )
            is True
        )
        command.downgrade(rollback_config, "core_254")
        assert _amendment_rows(db_url) == before
        down_catalog = _amendment_catalog(db_url)
        assert down_catalog["table"] == before_catalog["table"]
        assert down_catalog["policies"] == before_catalog["policies"]
        assert _run(db_url, status) == "filtered"
        assert (
            _run(
                db_url,
                "SELECT count(*)=0 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='insight_candidates' AND column_name IN ('premise', 'delivery_ref')",
            )
            is True
        )
        assert _amendment_catalog(admin_url)["functions"] == []
        command.upgrade(rollback_config, "core_255")
        assert _amendment_rows(db_url) == before
        up_catalog = _amendment_catalog(db_url)
        assert up_catalog["table"] == before_catalog["table"]
        assert up_catalog["policies"] == before_catalog["policies"]
        assert _run(db_url, unbound) is True
        receipts.append(
            {"authority": authority, "after_upgrade": _amendment_catalog(db_url), "rows": before}
        )
    # Retained table ownership lets the next ordinary schema replay proceed.
    # REPLACE still needs function ownership; the database owner's implicit
    # public-schema ownership permits DROP. Verify both actual authorities.
    bootstrap_functions = _amendment_catalog(db_url)["functions"]
    assert all(
        f["owner"] == _amendment_catalog(admin_url)["identity"]["session_user"]
        for f in bootstrap_functions
    )
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for source_name in ("CREATE_ENQUEUE_FN", "CREATE_BILL_STATUS_FN"):
                _expect_permission_denied(conn, runpy.run_path(str(_CORE_255))[source_name])
    finally:
        engine.dispose()
    asyncio.run(run_migrations(db_url, chain="core", schema="health"))
    assert _amendment_catalog(db_url)["functions"] == bootstrap_functions
    command.downgrade(config, "core_254")
    assert _amendment_rows(db_url) == before
    assert _amendment_catalog(db_url)["functions"] == []
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text('SET LOCAL ROLE "butler_switchboard_rw"'))
            assert (
                conn.execute(text("SELECT count(*) FROM public.insight_amendments")).scalar_one()
                == 4
            )
            with pytest.raises(DBAPIError) as error:
                with conn.begin_nested():
                    conn.execute(
                        text(
                            "SELECT public.enqueue_premise_amendments('s', 'f', '2099-01-01'::timestamptz, NULL)"
                        )
                    )
            assert error.value.orig.pgcode == "42883"
    finally:
        engine.dispose()
    command.upgrade(config, "core_255")
    ordinary_functions = _amendment_catalog(db_url)["functions"]
    assert len(ordinary_functions) == 2
    assert all(f["owner"] == before_catalog["identity"]["session_user"] for f in ordinary_functions)
    assert all(f["proconfig"] == ["search_path=pg_catalog, pg_temp"] for f in ordinary_functions)
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text('SET LOCAL ROLE "butler_switchboard_rw"'))
            assert (
                conn.execute(
                    text(
                        "SELECT public.enqueue_premise_amendments('s', 'f', '2099-01-01'::timestamptz, NULL)"
                    )
                ).scalar_one()
                == 0
            )
            assert (
                conn.execute(text("SELECT count(*) FROM public.insight_amendments")).scalar_one()
                == 4
            )
    finally:
        engine.dispose()
    assert _amendment_rows(db_url) == before
    _publish_disposable_receipt(
        {
            "bounded_retention": receipts,
            "ordinary_definer_downgrade": "schema-owner DROP succeeds; function call raises 42883 until ordinary recreate",
            "ordinary_definer_replace": "42501 while bootstrap-owned; best-effort schema replay leaves owner unchanged",
            "ordinary_replay_functions": _amendment_catalog(db_url)["functions"],
            "all_states_preserved": sorted(row["state"] for row in before),
        }
    )
