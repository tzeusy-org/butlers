"""core_372 (bu-ejow5p.9): ``public.qa_known_issue_links`` and its per-schema install rules.

Real-Postgres contract; Docker-only like its migration siblings.
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_INSERT = (
    "INSERT INTO public.qa_known_issue_links "
    "(fingerprint, tracker_kind, tracker_ref, linked_by, expires_at) "
    "VALUES ('{fp}', '{kind}', '{ref}', 'owner', now() + interval '1 day')"
)


def _run(db_url: str, statement: str):
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            result = connection.execute(text(statement))
            return [tuple(row) for row in result.fetchall()] if result.returns_rows else None
    finally:
        engine.dispose()


def test_the_table_accepts_valid_links_and_refuses_invalid_ones(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(
        _build_alembic_config(db_url, chains=["core"], target_schema="general"), "core_372"
    )

    _run(db_url, _INSERT.format(fp="a" * 64, kind="bead", ref="bu-1"))
    assert _run(db_url, "SELECT state FROM public.qa_known_issue_links") == [("tracked",)]
    with pytest.raises(Exception, match="uq_qa_known_issue_links_ref"):
        _run(db_url, _INSERT.format(fp="a" * 64, kind="bead", ref="bu-1"))
    with pytest.raises(Exception, match="tracker_kind"):
        _run(db_url, _INSERT.format(fp="b" * 64, kind="email", ref="x"))
    with pytest.raises(Exception, match="state"):
        _run(db_url, "UPDATE public.qa_known_issue_links SET state = 'bogus'")


def test_only_the_last_schema_drops_the_shared_table(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="health")
    command.upgrade(first, "core_372")
    command.upgrade(second, "core_372")
    _run(db_url, _INSERT.format(fp="c" * 64, kind="github_pr", ref="https://github.com/o/r/pull/1"))

    command.downgrade(first, "core_371")
    assert _run(db_url, "SELECT count(*) FROM public.qa_known_issue_links") == [(1,)]

    command.downgrade(second, "core_371")
    assert _run(db_url, "SELECT to_regclass('public.qa_known_issue_links')") == [(None,)]


def test_only_the_qa_role_can_write_the_suppression_control(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(
        _build_alembic_config(db_url, chains=["core"], target_schema="general"), "core_372"
    )
    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            held = {
                role: {
                    p
                    for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
                    if connection.execute(
                        text("SELECT has_table_privilege(:r, 'public.qa_known_issue_links', :p)"),
                        {"r": role, "p": p},
                    ).scalar_one()
                }
                for role in ("butler_qa_rw", "butler_general_rw", "connector_writer")
            }
        assert held["butler_qa_rw"] == {"SELECT", "INSERT", "UPDATE", "DELETE"}
        assert held["butler_general_rw"] == set()
        assert held["connector_writer"] == set()

        with engine.begin() as connection:
            connection.execute(text('SET LOCAL ROLE "butler_general_rw"'))
            with connection.begin_nested() as blocked:
                with pytest.raises(Exception, match="permission denied"):
                    connection.execute(text(_INSERT.format(fp="d" * 64, kind="bead", ref="bu-9")))
                blocked.rollback()
    finally:
        engine.dispose()


_ACL = """
SELECT COALESCE(r.rolname, 'PUBLIC'), a.privilege_type, a.is_grantable
FROM pg_class c
CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) a
LEFT JOIN pg_roles r ON r.oid = a.grantee
WHERE c.oid = 'public.qa_known_issue_links'::regclass AND a.grantee <> c.relowner
ORDER BY 1, 2
"""
_EXACT_ACL = [("butler_qa_rw", p, False) for p in ("DELETE", "INSERT", "SELECT", "UPDATE")]


def test_a_drifted_grant_is_repaired_when_the_next_schema_runs(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(db_url, chains=["core"], target_schema="general")
    second = _build_alembic_config(db_url, chains=["core"], target_schema="health")
    command.upgrade(first, "core_372")
    assert _run(db_url, _ACL) == _EXACT_ACL

    for drift in (
        "GRANT SELECT ON TABLE public.qa_known_issue_links TO PUBLIC",
        "GRANT INSERT ON TABLE public.qa_known_issue_links TO butler_general_rw",
        "GRANT SELECT ON TABLE public.qa_known_issue_links TO connector_writer",
        "GRANT DELETE ON TABLE public.qa_known_issue_links TO butler_qa_rw WITH GRANT OPTION",
        "REVOKE UPDATE ON TABLE public.qa_known_issue_links FROM butler_qa_rw",
        "GRANT TRUNCATE ON TABLE public.qa_known_issue_links TO butler_qa_rw",
    ):
        _run(db_url, drift)
    assert _run(db_url, _ACL) != _EXACT_ACL

    command.upgrade(second, "core_372")  # an already-installed table must not skip drift
    assert _run(db_url, _ACL) == _EXACT_ACL


def test_expiry_is_required_and_capped_at_ninety_days_from_linking(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(
        _build_alembic_config(db_url, chains=["core"], target_schema="general"), "core_372"
    )
    base = (
        "INSERT INTO public.qa_known_issue_links "
        "(fingerprint, tracker_kind, tracker_ref, linked_by, expires_at) "
        "VALUES ('{fp}', 'bead', 'bu-x1', 'owner', {expiry})"
    )
    with pytest.raises(Exception, match="expires_at"):
        _run(db_url, base.format(fp="e" * 64, expiry="NULL"))
    with pytest.raises(Exception, match="ck_qa_known_issue_links_expiry"):
        _run(db_url, base.format(fp="e" * 64, expiry="now() + interval '91 days'"))
    _run(db_url, base.format(fp="e" * 64, expiry="now() + interval '89 days'"))
