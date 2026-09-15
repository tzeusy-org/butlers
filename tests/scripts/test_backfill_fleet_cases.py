"""Tests for scripts/backfill_fleet_cases.py (bu-vl0kh, RFC 0032 Slice 6).

Covers:
1. run() connects with the butler_switchboard_rw role (RFC 0032 write
   authority) and the standard POSTGRES_*/DATABASE_URL-derived params.
2. --dry-run reports the resolved-episode count and never calls the backfill.
3. Apply mode delegates to butlers.core.fleet_cases.backfill_from_owner_conditions
   and returns its result verbatim.
4. The pool is closed in both modes, even when the backfill raises.
5. main() surfaces a run() failure as a clean error exit rather than a
   traceback.

The DB layer is mocked (Database/pool) so these are fast unit tests; the
actual backfill logic is covered against real Postgres in
tests/integration/test_fleet_case_contribution_roundtrip.py and against a
mocked pool in tests/core/test_fleet_cases.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "backfill_fleet_cases.py"
_MODULE_NAME = "backfill_fleet_cases_script"


def _load_script():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_script()


class _FakeDatabase:
    """Stand-in for butlers.db.Database capturing its constructor kwargs."""

    instances: list[_FakeDatabase] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.pool = AsyncMock()
        self.pool.close = AsyncMock()
        self.pool.fetchval = AsyncMock(return_value=7)
        type(self).instances.append(self)

    async def connect(self):
        return self.pool


@pytest.fixture(autouse=True)
def _reset_fake_database():
    _FakeDatabase.instances = []
    yield
    _FakeDatabase.instances = []


@pytest.fixture(autouse=True)
def _patch_db_wiring(monkeypatch):
    """Patch at butlers.db (the home module) since the script imports these
    lazily inside run(), mirroring tests/scripts/test_backfill_email_identity_facts.py's
    documented anchor convention."""
    monkeypatch.setattr("butlers.db.Database", _FakeDatabase)
    monkeypatch.setattr(
        "butlers.db.db_params_from_env",
        lambda: {"host": "db-host", "port": 5432, "user": "u", "password": "p", "ssl": None},
    )
    monkeypatch.setattr("butlers.db.database_name_from_env", lambda fallback: "butlers")


async def test_run_connects_with_the_switchboard_role(monkeypatch):
    monkeypatch.setattr(
        "butlers.core.fleet_cases.backfill_from_owner_conditions",
        AsyncMock(return_value={"created_case_ids": [], "created_count": 0, "skipped_count": 0}),
    )

    await _mod.run(dry_run=False)

    assert len(_FakeDatabase.instances) == 1
    assert _FakeDatabase.instances[0].kwargs["role"] == "butler_switchboard_rw"
    assert _FakeDatabase.instances[0].kwargs["db_name"] == "butlers"


async def test_dry_run_reports_the_resolved_count_and_never_calls_the_backfill(monkeypatch):
    backfill_mock = AsyncMock()
    monkeypatch.setattr("butlers.core.fleet_cases.backfill_from_owner_conditions", backfill_mock)

    result = await _mod.run(dry_run=True)

    assert len(_FakeDatabase.instances) == 1
    pool = _FakeDatabase.instances[0].pool
    pool.fetchval.assert_awaited_once()
    backfill_mock.assert_not_awaited()
    pool.close.assert_awaited_once()
    assert result == {"created_case_ids": [], "created_count": 0, "skipped_count": 7}


async def test_apply_mode_delegates_to_backfill_from_owner_conditions(monkeypatch):
    expected = {"created_case_ids": ["case-1"], "created_count": 1, "skipped_count": 0}
    backfill_mock = AsyncMock(return_value=expected)
    monkeypatch.setattr("butlers.core.fleet_cases.backfill_from_owner_conditions", backfill_mock)

    result = await _mod.run(dry_run=False)

    assert result == expected
    pool = _FakeDatabase.instances[0].pool
    backfill_mock.assert_awaited_once_with(pool)
    pool.close.assert_awaited_once()


async def test_pool_is_closed_even_when_the_backfill_raises(monkeypatch):
    monkeypatch.setattr(
        "butlers.core.fleet_cases.backfill_from_owner_conditions",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        await _mod.run(dry_run=False)

    _FakeDatabase.instances[0].pool.close.assert_awaited_once()


async def test_main_reports_a_run_failure_as_a_clean_error_exit(monkeypatch, capsys):
    monkeypatch.setattr(_mod, "run", AsyncMock(side_effect=RuntimeError("db unreachable")))

    exit_code = await _mod.main([])

    assert exit_code == 1
    assert "db unreachable" in capsys.readouterr().err


async def test_main_prints_the_apply_summary_and_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(
        _mod,
        "run",
        AsyncMock(
            return_value={"created_case_ids": ["a", "b"], "created_count": 2, "skipped_count": 1}
        ),
    )

    exit_code = await _mod.main([])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "APPLY" in out
    assert "Cases created: 2" in out
