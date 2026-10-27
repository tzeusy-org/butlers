"""Real-migration coverage for gen_003's passive ordinary-source representation.

Drives the actual General Alembic chain against disposable PostgreSQL:
legacy objects and rows survive, the passive classification and ordinary-only
name index sit beside (never replace) the legacy global constraint, a
pre-existing compatible cut-over schema and its private rows are preserved,
an ambiguous reserved custody profile aborts the upgrade transactionally, and
downgrade either restores the legacy schema or refuses without repair.  None
of this is custody enrollment: nothing here sets a flag or writes a profile
except the synthetic fixtures that stand in for a future custody release.

Issue: bu-2jtfw.9.3
"""

from __future__ import annotations

import json
import shutil
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    assert_at_chain_head,
    create_migrated_test_db,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_SCHEMA = "general"
_LEGACY = "gen_002"


def _staged_at_legacy(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["general"],
        schemas={"general": _SCHEMA},
        revisions={"general": _LEGACY},
    )


def _migrate(db_url: str, target: str) -> None:
    config = _build_alembic_config(db_url, ["general"], target_schema=_SCHEMA)
    if target == "head":
        command.upgrade(config, "general@head")
    else:
        command.downgrade(config, f"general@{target}")


def _engine(db_url: str):
    return create_engine(db_url, connect_args={"options": f"-c search_path={_SCHEMA},public"})


def _version(conn) -> str:
    return conn.execute(text(f"SELECT version_num FROM {_SCHEMA}.alembic_version")).scalar_one()


def _has_column(conn, column: str) -> bool:
    return conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = 'collections' AND column_name = :col)"
        ),
        {"schema": _SCHEMA, "col": column},
    ).scalar_one()


def _name_indexes(conn) -> list[tuple[str, str | None]]:
    """Unique single-column indexes on collections(name) as (name, predicate)."""
    return sorted(
        conn.execute(
            text(
                """
                SELECT index_class.relname, pg_get_expr(ix.indpred, ix.indrelid)
                FROM pg_index AS ix
                JOIN pg_class AS index_class ON index_class.oid = ix.indexrelid
                JOIN pg_attribute AS attribute
                  ON attribute.attrelid = ix.indrelid AND attribute.attnum = ix.indkey[0]
                WHERE ix.indrelid = 'collections'::regclass
                  AND ix.indisunique AND ix.indnkeyatts = 1 AND attribute.attname = 'name'
                """
            )
        ).all()
    )


def _snapshot(conn) -> list[tuple]:
    return conn.execute(
        text(
            """
            SELECT c.id, c.name, c.description, i.id, i.data::text, i.tags::text
            FROM collections AS c
            LEFT JOIN collection_items AS i ON i.collection_id = c.id
            ORDER BY c.name, i.id
            """
        )
    ).all()


def _seed_legacy(conn) -> None:
    books = conn.execute(
        text("INSERT INTO collections (name, description) VALUES ('books', 'shelf') RETURNING id")
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO collection_items (collection_id, data, tags) "
            "VALUES (:c, CAST(:d AS jsonb), '[\"read\"]')"
        ),
        {"c": books, "d": json.dumps({"title": "Dune"})},
    )
    # The deployed legacy writer shape.
    conn.execute(
        text(
            "INSERT INTO collections (name) VALUES ('films') "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name"
        )
    )


def test_upgrade_adds_passive_ordinary_representation_beside_legacy_uniqueness(
    postgres_container,
) -> None:
    db_url = _staged_at_legacy(postgres_container)
    engine = _engine(db_url)
    try:
        with engine.begin() as conn:
            _seed_legacy(conn)
            before = _snapshot(conn)

        _migrate(db_url, "head")

        with engine.begin() as conn:
            assert_at_chain_head(conn, _SCHEMA, chain="general")
            assert _snapshot(conn) == before
            assert conn.execute(
                text(
                    "SELECT bool_or(custody_private), max(eligibility_generation) FROM collections"
                )
            ).one() == (False, 0)
            assert _name_indexes(conn) == [
                ("collections_name_key", None),
                ("collections_ordinary_name_key", "(custody_private = false)"),
            ]
            # Passive: no profile, version, vocabulary or enrollment is manufactured.
            assert (
                conn.execute(
                    text("SELECT count(*) FROM collection_items WHERE data ? 'possession_profile'")
                ).scalar_one()
                == 0
            )
            assert conn.execute(text("SELECT count(*) FROM source_versions")).scalar_one() == 0
            assert (
                conn.execute(text("SELECT count(*) FROM collection_vocabulary")).scalar_one() == 0
            )

            # The deployed ON CONFLICT (name) writer still works (global
            # constraint retained), as does the updated targetless writer.
            legacy_id = conn.execute(
                text(
                    "INSERT INTO collections (name) VALUES ('books') "
                    "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id"
                )
            ).scalar_one()
            assert legacy_id == before[0][0]
            assert (
                conn.execute(
                    text("INSERT INTO collections (name) VALUES ('books') ON CONFLICT DO NOTHING")
                ).rowcount
                == 0
            )
    finally:
        engine.dispose()


def test_upgrade_preserves_a_compatible_cut_over_schema_and_its_private_rows(
    postgres_container,
) -> None:
    """Synthetic post-cutover fixture: General readiness, not a custody implementation."""
    db_url = _staged_at_legacy(postgres_container)
    engine = _engine(db_url)
    profile = {"possession_profile": {"schema_version": 1, "revision": 3}}
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE collections "
                    "ADD COLUMN custody_private BOOLEAN NOT NULL DEFAULT false"
                )
            )
            conn.execute(text("ALTER TABLE collections DROP CONSTRAINT collections_name_key"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX custody_ordinary_names ON collections (name) "
                    "WHERE custody_private = false"
                )
            )
            private_id = conn.execute(
                text(
                    "INSERT INTO collections (name, custody_private) "
                    "VALUES ('keepsakes', true) RETURNING id"
                )
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO collection_items (collection_id, data) "
                    "VALUES (:c, CAST(:d AS jsonb))"
                ),
                {"c": private_id, "d": json.dumps(profile)},
            )
            # A private-only name coexists with an ordinary row of the same name.
            conn.execute(text("INSERT INTO collections (name) VALUES ('keepsakes')"))
            before = _snapshot(conn)

        _migrate(db_url, "head")

        with engine.begin() as conn:
            assert _snapshot(conn) == before
            assert (
                conn.execute(
                    text("SELECT custody_private FROM collections WHERE id = :c"), {"c": private_id}
                ).scalar_one()
                is True
            )
            # Global uniqueness is not reintroduced; the compatible index is reused.
            assert _name_indexes(conn) == [("custody_ordinary_names", "(custody_private = false)")]
    finally:
        engine.dispose()


@pytest.mark.parametrize("flag_column", [False, True], ids=["no-classification", "ordinary-flag"])
def test_upgrade_refuses_a_reserved_profile_without_private_classification(
    postgres_container, flag_column: bool
) -> None:
    db_url = _staged_at_legacy(postgres_container)
    engine = _engine(db_url)
    try:
        with engine.begin() as conn:
            if flag_column:
                conn.execute(
                    text(
                        "ALTER TABLE collections "
                        "ADD COLUMN custody_private BOOLEAN NOT NULL DEFAULT false"
                    )
                )
            parent = conn.execute(
                text("INSERT INTO collections (name) VALUES ('drawer') RETURNING id")
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO collection_items (collection_id, data) "
                    "VALUES (:c, CAST(:d AS jsonb))"
                ),
                {"c": parent, "d": json.dumps({"possession_profile": {"revision": 1}})},
            )
            before = _snapshot(conn)

        with pytest.raises(DBAPIError, match="possession_profile"):
            _migrate(db_url, "head")

        with engine.begin() as conn:
            # pinned-revision: a refused upgrade must leave the chain where it was.
            assert _version(conn) == _LEGACY
            assert _snapshot(conn) == before
            assert _has_column(conn, "custody_private") is flag_column
            assert _has_column(conn, "eligibility_generation") is False
            assert conn.execute(text("SELECT to_regclass('source_versions') IS NULL")).scalar_one()
    finally:
        engine.dispose()


def test_downgrade_before_private_use_restores_legacy_schema_and_keeps_history(
    postgres_container,
) -> None:
    db_url = _staged_at_legacy(postgres_container)
    _migrate(db_url, "head")
    engine = _engine(db_url)
    item_id = uuid.uuid4()
    try:
        with engine.begin() as conn:
            parent = conn.execute(
                text("INSERT INTO collections (name) VALUES ('notes') RETURNING id")
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO source_versions "
                    "(item_id, version, collection_id, eligibility_generation, operation, "
                    " digest, content) "
                    "VALUES (:i, 1, :c, 0, 'create', :d, '{}'::jsonb)"
                ),
                {"i": item_id, "c": parent, "d": "a" * 64},
            )

        _migrate(db_url, _LEGACY)

        with engine.begin() as conn:
            # pinned-revision: the downgrade target is the legacy revision itself.
            assert _version(conn) == _LEGACY
            assert not _has_column(conn, "custody_private")
            assert not _has_column(conn, "eligibility_generation")
            assert _name_indexes(conn) == [("collections_name_key", None)]
            # Empty vocabulary is dropped; non-empty history is retained inert.
            assert conn.execute(
                text("SELECT to_regclass('collection_vocabulary') IS NULL")
            ).scalar_one()
            assert (
                conn.execute(
                    text("SELECT count(*) FROM source_versions WHERE item_id = :i"), {"i": item_id}
                ).scalar_one()
                == 1
            )

        _migrate(db_url, "head")

        with engine.begin() as conn:
            assert_at_chain_head(conn, _SCHEMA, chain="general")
            assert (
                conn.execute(
                    text("SELECT count(*) FROM source_versions WHERE item_id = :i"), {"i": item_id}
                ).scalar_one()
                == 1
            )
            with pytest.raises(DBAPIError, match="immutable"):
                with conn.begin_nested():
                    conn.execute(text("DELETE FROM source_versions"))
    finally:
        engine.dispose()


@pytest.mark.parametrize("state", ["private-row", "duplicate-names"])
def test_downgrade_refuses_private_rows_or_duplicate_names_without_repair(
    postgres_container, state: str
) -> None:
    db_url = _staged_at_legacy(postgres_container)
    _migrate(db_url, "head")
    engine = _engine(db_url)
    try:
        with engine.begin() as conn:
            if state == "private-row":
                conn.execute(
                    text("INSERT INTO collections (name, custody_private) VALUES ('vault', true)")
                )
            else:
                # Synthetic malformed state: no uniqueness at all, two equal names.
                conn.execute(text("DROP INDEX collections_ordinary_name_key"))
                conn.execute(text("ALTER TABLE collections DROP CONSTRAINT collections_name_key"))
                conn.execute(text("INSERT INTO collections (name) VALUES ('twin'), ('twin')"))
            before = _snapshot(conn)

        with pytest.raises(DBAPIError, match="downgrade refused"):
            _migrate(db_url, _LEGACY)

        with engine.begin() as conn:
            assert_at_chain_head(conn, _SCHEMA, chain="general")
            assert _snapshot(conn) == before
            assert _has_column(conn, "custody_private")
    finally:
        engine.dispose()
