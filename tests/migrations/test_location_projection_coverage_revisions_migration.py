"""chronicler_034: exact coverage records (content digest, config revision) and attempts.

Seeds a coverage record and an open member at chronicler_033, applies the real upgrade, and
checks that existing rows read ``'legacy'`` (never current), that the key now admits one record
per revision and content, that malformed values are refused, that a re-run writes nothing, and
that the downgrade keeps the earliest record per raw row.

Issue: bu-s11n0s.7 (slice 2, PR-C)
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from butlers.chronicler.projection_coverage import config_revision
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_AT = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
_REVISION = config_revision("owntracks.points", {"case": "migration"})
_DIGEST = "v1:" + "a" * 64

_INSERT_033_COVERAGE = (
    "INSERT INTO chronicler.location_projection_coverage (adapter_name, raw_source_id, "
    "logical_source_key, retention_at, disposition, projection_batch_id, covered_at) "
    "VALUES ('owntracks.points', :raw, 'k', :at, 'evaluated_no_output', :batch, :covered)"
)
_INSERT_034_COVERAGE = (
    "INSERT INTO chronicler.location_projection_coverage (adapter_name, raw_source_id, "
    "config_revision, content_digest, logical_source_key, retention_at, disposition, "
    "projection_batch_id) VALUES ('owntracks.points', :raw, :revision, :digest, 'k', :at, "
    "'evaluated_no_output', :batch)"
)
_INSERT_033_OPEN = (
    "INSERT INTO chronicler.location_projection_open_members (adapter_name, carry_keys, "
    "raw_source_id, logical_source_key, retention_at, joined_batch_id) "
    "VALUES ('owntracks.points', ARRAY['movement:owntracks:alice'], :raw, 'k', :at, :batch)"
)

_CATALOG = """
SELECT
    (SELECT xmin::text FROM pg_class WHERE oid = 'chronicler.location_projection_coverage'::regclass),
    (SELECT xmin::text FROM pg_class
     WHERE oid = 'chronicler.location_projection_open_members'::regclass),
    (SELECT xmin::text FROM pg_class WHERE oid = 'chronicler.location_projection_attempts'::regclass),
    (SELECT array_agg(conname || ':' || oid ORDER BY conname) FROM pg_constraint
     WHERE conrelid IN ('chronicler.location_projection_coverage'::regclass,
                        'chronicler.location_projection_open_members'::regclass,
                        'chronicler.location_projection_attempts'::regclass))
"""


def _config(db_url: str):
    return _build_alembic_config(db_url, chains=["chronicler"], target_schema="chronicler")


@pytest.fixture
def chronicler_033_db(postgres_container):
    # chronicler_030 refuses to run until the private memory storage exists, so stop before
    # it, prepare memory, then continue to the seeding revision.
    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["chronicler"],
        schemas={"chronicler": "chronicler"},
        revisions={"chronicler": "chronicler_029"},
    )
    from tests.chronicler._private_memory_setup import prepare_chronicler_private_memory_sync

    prepare_chronicler_private_memory_sync(db_url)
    command.upgrade(_config(db_url), "chronicler@chronicler_033")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        yield db_url, engine
    finally:
        engine.dispose()


def _primary_key(conn) -> list[str]:
    return list(
        conn.execute(
            text(
                """
                SELECT a.attname
                FROM pg_constraint c
                CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                WHERE c.conrelid = 'chronicler.location_projection_coverage'::regclass
                  AND c.contype = 'p'
                ORDER BY k.ord
                """
            )
        ).scalars()
    )


def test_existing_records_become_legacy_and_new_ones_are_exact(chronicler_033_db) -> None:
    db_url, engine = chronicler_033_db
    covered_raw, open_raw = uuid4(), uuid4()
    with engine.connect() as conn:
        conn.execute(
            text(_INSERT_033_COVERAGE),
            {"raw": covered_raw, "at": _AT, "batch": uuid4(), "covered": _AT},
        )
        conn.execute(text(_INSERT_033_OPEN), {"raw": open_raw, "at": _AT, "batch": uuid4()})

    command.upgrade(_config(db_url), "chronicler@chronicler_034")

    with engine.connect() as conn:
        record = conn.execute(
            text(
                "SELECT config_revision, content_digest FROM chronicler."
                "location_projection_coverage WHERE raw_source_id = :raw"
            ),
            {"raw": covered_raw},
        ).one()
        member_digest = conn.execute(
            text(
                "SELECT content_digest FROM chronicler.location_projection_open_members "
                "WHERE raw_source_id = :raw"
            ),
            {"raw": open_raw},
        ).scalar_one()
        assert tuple(record) == ("legacy", "legacy")
        assert member_digest == "legacy"
        assert _primary_key(conn) == [
            "adapter_name",
            "raw_source_id",
            "config_revision",
            "content_digest",
        ]
        defaults = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = 'chronicler' AND column_default IS NOT NULL "
                "AND column_name IN ('config_revision', 'content_digest')"
            )
        ).scalar_one()
        assert defaults == 0, "new writers must name the revision and digest"
        # A current record for the same raw row sits beside the legacy one.
        conn.execute(
            text(_INSERT_034_COVERAGE),
            {
                "raw": covered_raw,
                "revision": _REVISION,
                "digest": _DIGEST,
                "at": _AT,
                "batch": uuid4(),
            },
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM chronicler.location_projection_coverage "
                    "WHERE raw_source_id = :raw"
                ),
                {"raw": covered_raw},
            ).scalar_one()
            == 2
        )

    for revision, digest in (("v1:short", _DIGEST), (_REVISION, "V1:" + "A" * 64), (_REVISION, "")):
        with pytest.raises(IntegrityError, match="_check"):
            with engine.connect() as conn:
                conn.execute(
                    text(_INSERT_034_COVERAGE),
                    {
                        "raw": uuid4(),
                        "revision": revision,
                        "digest": digest,
                        "at": _AT,
                        "batch": uuid4(),
                    },
                )


def test_attempts_accept_only_well_formed_outcomes_and_grant_no_delete(chronicler_033_db) -> None:
    db_url, engine = chronicler_033_db
    command.upgrade(_config(db_url), "chronicler@chronicler_034")
    insert = text(
        "INSERT INTO chronicler.location_projection_attempts (adapter_name, config_revision, "
        "last_outcome, last_attempt_at, last_error_class) "
        "VALUES (:adapter, :revision, :outcome, now(), :error)"
    )
    with engine.connect() as conn:
        conn.execute(
            insert,
            {
                "adapter": "owntracks.points",
                "revision": _REVISION,
                "outcome": "succeeded",
                "error": None,
            },
        )
        privileges = {
            privilege: conn.execute(
                text(
                    "SELECT has_table_privilege('butler_chronicler_rw', "
                    "'chronicler.location_projection_attempts', :privilege)"
                ),
                {"privilege": privilege},
            ).scalar_one()
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
    assert privileges == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False}
    for adapter, outcome, error in (
        ("google_calendar.completed", "succeeded", None),
        ("owntracks.place_cluster", "skipped", None),
        ("owntracks.place_cluster", "failed", None),  # a failure names its class
    ):
        with pytest.raises(IntegrityError):
            with engine.connect() as conn:
                conn.execute(
                    insert,
                    {"adapter": adapter, "revision": _REVISION, "outcome": outcome, "error": error},
                )


def test_a_rerun_writes_nothing_and_the_downgrade_keeps_the_earliest_record(
    chronicler_033_db,
) -> None:
    db_url, engine = chronicler_033_db
    raw = uuid4()
    with engine.connect() as conn:
        conn.execute(
            text(_INSERT_033_COVERAGE), {"raw": raw, "at": _AT, "batch": uuid4(), "covered": _AT}
        )
    command.upgrade(_config(db_url), "chronicler@chronicler_034")
    with engine.connect() as conn:
        conn.execute(
            text(_INSERT_034_COVERAGE),
            {"raw": raw, "revision": _REVISION, "digest": _DIGEST, "at": _AT, "batch": uuid4()},
        )
        installed = tuple(conn.execute(text(_CATALOG)).one())

    # Re-run the revision over its own result: the catalog is only read.
    command.stamp(_config(db_url), "chronicler_033")
    command.upgrade(_config(db_url), "chronicler@chronicler_034")
    with engine.connect() as conn:
        assert tuple(conn.execute(text(_CATALOG)).one()) == installed

    command.downgrade(_config(db_url), "chronicler_033")
    with engine.connect() as conn:
        kept = (
            conn.execute(
                text(
                    "SELECT covered_at FROM chronicler.location_projection_coverage "
                    "WHERE raw_source_id = :raw"
                ),
                {"raw": raw},
            )
            .scalars()
            .all()
        )
        assert kept == [_AT]
        assert _primary_key(conn) == ["adapter_name", "raw_source_id"]
        assert (
            conn.execute(
                text("SELECT to_regclass('chronicler.location_projection_attempts')")
            ).scalar_one()
            is None
        )
        # The append-only guard is back on after the downgrade's own delete.
        with pytest.raises(Exception, match="append-only"):
            conn.execute(
                text(
                    "DELETE FROM chronicler.location_projection_coverage WHERE raw_source_id = :raw"
                ),
                {"raw": raw},
            )

    command.upgrade(_config(db_url), "chronicler@chronicler_034")
