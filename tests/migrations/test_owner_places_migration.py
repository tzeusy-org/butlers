"""core_303: the owner place registry table (bu-jv1moa.7).

Real-Postgres contract for the storage invariants the editor relies on and for the
rollback rule: rolling the schema back never destroys owner-entered places.  Role
access and the editor itself are covered in ``tests/api/test_home_owner_places.py``.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_303_owner_places.py"
)


def _replay(db_url: str, direction: str) -> None:
    """Execute the migration's real SQL as the last schema still holding core_303."""
    spec = importlib.util.spec_from_file_location("core_303_replay", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with (
        patch.object(migration, "op", fake_op),
        patch.object(migration, "_installation_count", return_value=1),
    ):
        getattr(migration, direction)()
    _run(db_url, *statements)


def _run(db_url: str, *statements: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.exec_driver_sql(statement)
    finally:
        engine.dispose()


def _insert(label: str, kind: str = "other", **columns: str) -> str:
    values = {"lat": "1.5", "lon": "2.5", "radius_m": "100", **columns}
    return (
        "INSERT INTO public.owner_places (label, kind, lat, lon, radius_m) "
        f"VALUES ('{label}', '{kind}', {values['lat']}, {values['lon']}, {values['radius_m']})"
    )


@pytest.fixture(scope="module")
def db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


def test_storage_invariants_hold_for_live_places(db_url: str) -> None:
    """Ranges, one live home, unique live labels; a deleted row frees its label and slot."""
    _run(db_url, "TRUNCATE public.owner_places")
    for bad in (
        _insert("Bad lat", lat="90.5"),
        _insert("Bad lon", lon="-181"),
        _insert("Bad radius", radius_m="49"),
        _insert("Bad kind", kind="gym"),
        _insert(" Padded "),
    ):
        with pytest.raises(DBAPIError, match="ck_owner_places"):
            _run(db_url, bad)
    _run(db_url, _insert("Home", "home"))
    for duplicate in (_insert("Cottage", "home"), _insert("HOME")):
        with pytest.raises(DBAPIError, match="uq_owner_places_live"):
            _run(db_url, duplicate)
    _run(db_url, "UPDATE public.owner_places SET deleted_at = now()")
    _run(db_url, _insert("Home", "home"))


def test_downgrade_refuses_while_places_exist_and_upgrade_is_idempotent(db_url: str) -> None:
    _replay(db_url, "upgrade")  # idempotent re-apply on a migrated database
    _run(db_url, "TRUNCATE public.owner_places", _insert("Kept"))
    with pytest.raises(DBAPIError, match="cannot drop public.owner_places"):
        _replay(db_url, "downgrade")

    _run(db_url, "TRUNCATE public.owner_places")
    _replay(db_url, "downgrade")
    _replay(db_url, "upgrade")
    _run(db_url, _insert("Restored"))
