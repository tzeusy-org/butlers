"""core_311: the Health refill probe counts doses by medication subject (bu-uc6wh2).

``medication_update`` supersedes the medication fact (new id) on every edit, and each
``took_dose`` keeps the id current when it was logged. core_272's definer matched the
current id only, so an edit forgot the earlier doses. These pin the replacement on real
PostgreSQL (CI-only): doses across an edit count, a refill resets the count, another
medication's doses never count, and downgrade restores the core_272 body.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migrated_test_db, migration_db_name

_CORE = Path(__file__).resolve().parents[2] / "alembic/versions/core"
_SWITCHBOARD = "butler_switchboard_rw"
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


def _load(filename: str):
    spec = importlib.util.spec_from_file_location(f"_{filename[:8]}_refill", _CORE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _core_311():
    return _load("core_311_health_refill_probe_counts_by_subject.py")


def test_the_revision_follows_core_310_and_downgrade_restores_core_272_body() -> None:
    mod = _core_311()
    assert (mod.revision, mod.down_revision) == ("core_311", "core_310")
    core_272 = _load("core_272_health_refill_premise_probe.py")
    assert mod.CORE_272_REFILL_FN == core_272.CREATE_REFILL_FN


@pytest.fixture
def engine(postgres_container):
    db_url = create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS health"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS health.facts (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
                "subject text, predicate text, scope text, validity text, metadata jsonb, "
                "valid_at timestamptz, created_at timestamptz DEFAULT clock_timestamp())"
            )
        )
    yield engine
    engine.dispose()


def _med(conn, subject: str, *, quantity: int, filled: str, validity: str = "active") -> str:
    """Write a medication fact the way medication_add/medication_update do."""
    return str(
        conn.execute(
            text(
                "INSERT INTO health.facts (subject, predicate, scope, validity, metadata) "
                "VALUES (:subject, 'medication', 'health', :validity, jsonb_build_object("
                "'active', true, 'quantity', :quantity, "
                "'quantity_updated_at', (now() - CAST(:filled AS interval))::text)) RETURNING id"
            ),
            {"subject": subject, "validity": validity, "quantity": quantity, "filled": filled},
        ).scalar()
    )


def _supersede(conn, med_id: str) -> None:
    conn.execute(
        text("UPDATE health.facts SET validity = 'superseded' WHERE id = CAST(:id AS uuid)"),
        {"id": med_id},
    )


def _doses(conn, med_id: str, count: int, *, ago: str = "1 hour") -> None:
    for _ in range(count):
        conn.execute(
            text(
                "INSERT INTO health.facts (subject, predicate, scope, validity, metadata, valid_at) "
                "VALUES ('owner', 'took_dose', 'health', 'active', "
                "jsonb_build_object('medication_id', CAST(:id AS text), 'skipped', false), "
                "now() - CAST(:ago AS interval))"
            ),
            {"id": med_id, "ago": ago},
        )


def _due(engine, subject: str, warning_days: int):
    """The Switchboard's verdict with one dose a day: is supply within ``warning_days``?"""
    with engine.connect() as conn:
        conn.execute(text(f"SET ROLE {_SWITCHBOARD}"))
        try:
            return conn.execute(
                text("SELECT public.resolve_health_medication_refill(:s, 1, :w)"),
                {"s": subject, "w": warning_days},
            ).scalar()
        finally:
            conn.rollback()


def _edited_metformin(engine) -> None:
    """10 pills filled 2 days ago; 3 doses, a dosage edit, then 3 more: 4 days left."""
    with engine.begin() as conn:
        first = _med(conn, "medication:Metformin", quantity=10, filled="2 days")
        _doses(conn, first, 3)
        _supersede(conn, first)
        second = _med(conn, "medication:Metformin", quantity=10, filled="2 days")
        _doses(conn, second, 3)


@_docker
@pytest.mark.integration
def test_doses_before_an_edit_still_count(engine) -> None:
    _edited_metformin(engine)
    # 10 - 6 = 4 days left: due at a 4-day window, not at 3. Forgetting 3 doses says 7.
    assert _due(engine, "medication:Metformin", 4) is True
    assert _due(engine, "medication:Metformin", 3) is False


@_docker
@pytest.mark.integration
def test_a_refill_resets_the_count(engine) -> None:
    with engine.begin() as conn:
        first = _med(conn, "medication:Lisinopril", quantity=10, filled="3 days")
        _doses(conn, first, 6, ago="2 days")
        _supersede(conn, first)
        refilled = _med(conn, "medication:Lisinopril", quantity=20, filled="1 day")
        _doses(conn, refilled, 8)
    # 20 - 8 doses since the refill = 12 days; the 6 earlier doses drew the old supply.
    assert _due(engine, "medication:Lisinopril", 12) is True
    assert _due(engine, "medication:Lisinopril", 11) is False


@_docker
@pytest.mark.integration
def test_another_medications_doses_do_not_count(engine) -> None:
    with engine.begin() as conn:
        metformin = _med(conn, "medication:Metformin", quantity=10, filled="2 days")
        aspirin = _med(conn, "medication:Aspirin", quantity=100, filled="2 days")
        _doses(conn, aspirin, 5)
        _doses(conn, metformin, 2)
    assert _due(engine, "medication:Metformin", 8) is True
    assert _due(engine, "medication:Metformin", 7) is False


@_docker
@pytest.mark.integration
def test_downgrade_restores_the_id_only_count_and_upgrade_replays(engine) -> None:
    _edited_metformin(engine)
    migration = _core_311()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
    assert _due(engine, "medication:Metformin", 4) is False  # core_272 sees 3 doses: 7 days
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
    assert _due(engine, "medication:Metformin", 4) is True
