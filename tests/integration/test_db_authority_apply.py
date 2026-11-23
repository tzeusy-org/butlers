"""Real-Postgres check: grant apply converges and strands no runtime role (bu-dss7he.6 S2b).

A freshly migrated database is the state every daemon's own tests and a fresh deploy run
against, so it is the coverage baseline: applying the manifest to a database that
``init-db.sql`` has re-widened must reproduce exactly that baseline, revoking only the
replay-widened privileges and granting nothing the fresh state lacks.
"""

from __future__ import annotations

import shutil
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine

from butlers.db_authority.apply import apply_public_grants, column_acls, unmanaged_relations
from butlers.db_authority.catalog import read_public_catalog
from butlers.db_authority.grants import REPLAY_WIDENED
from butlers.testing.migration import (
    create_migrated_test_db,
    init_db_sql_for_dbapi,
    migration_bootstrap_db_url,
    migration_db_name,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _privileges(db_url: str) -> dict:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return dict(read_public_catalog(conn).privileges)
    finally:
        engine.dispose()


def _replay_init_db(postgres_container, db_name: str, migration_user: str) -> None:
    engine = create_engine(
        migration_bootstrap_db_url(postgres_container, db_name), isolation_level="AUTOCOMMIT"
    )
    raw = engine.raw_connection()
    try:
        raw.autocommit = True
        with raw.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('butlers.connecting_user', %s, false)", (migration_user,)
            )
            cursor.execute(init_db_sql_for_dbapi())
    finally:
        raw.close()
        engine.dispose()


def test_apply_after_replay_restores_the_fresh_migrated_state(postgres_container) -> None:
    db_name = migration_db_name()
    db_url = create_migrated_test_db(
        postgres_container,
        db_name,
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )
    fresh = _privileges(db_url)

    _replay_init_db(postgres_container, db_name, str(urlparse(db_url).username))
    replayed = _privileges(db_url)
    assert replayed != fresh, "replay must widen something for this test to mean anything"

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            columns_before = column_acls(conn)
        with engine.begin() as conn:
            unmanaged = unmanaged_relations(conn)
            changes = apply_public_grants(conn)
        with engine.connect() as conn:
            columns_after = column_acls(conn)
            prune_ok = conn.exec_driver_sql(
                "SELECT has_column_privilege('butler_qa_rw', 'public.qa_publication_exports',"
                " 'pruned_at', 'UPDATE')"
            ).scalar()
    finally:
        engine.dispose()

    # Column grants live outside the manifest; a table-level REVOKE drops them unless apply
    # restores them (QA export pruning updates pruned_at under a column-only grant).
    assert columns_after == columns_before
    assert prune_ok, "butler_qa_rw lost UPDATE (pruned_at) on qa_publication_exports"

    # Definer-owned relations (the migration login is neither owner nor member) cannot be
    # revoked by this login; apply must list them as skipped rather than pretend.
    assert unmanaged, "expected definer-owned public relations the migrator cannot administer"
    assert all(c.relation in unmanaged for c in changes if c.skipped)

    applied = _privileges(db_url)
    managed = {key for key in fresh if key[0] in _manifest_relations() and key[0] not in unmanaged}
    # No runtime role ends up with less than the fresh baseline, except privileges the
    # init-db replay itself had removed (the manifest never grants those back).
    assert all(applied[k] <= fresh[k] for k in managed), "apply must never widen beyond fresh"
    stripped = {k for k in managed if fresh[k] - applied[k]}
    assert all(fresh[k] - applied[k] <= fresh[k] - replayed[k] for k in stripped), (
        "apply stripped a privilege the fresh baseline has and the replay kept"
    )
    # Only replay-widened relations changed.
    assert {c.relation for c in changes} <= REPLAY_WIDENED

    # Idempotent: a second apply has nothing to do.
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            assert [c for c in apply_public_grants(conn) if not c.skipped] == []
    finally:
        engine.dispose()


def _manifest_relations() -> frozenset[str]:
    from butlers.db_authority.grants import PUBLIC_RELATIONS

    return frozenset(PUBLIC_RELATIONS)
