"""travel_004: blank leg cities are backfilled from the airport/station code."""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.testing.migration import load_migration

_MIGRATION_PATH = Path(__file__).resolve().parents[1] / "migrations" / "004_leg_city_fallback.py"


class _CollectingOp:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(str(statement))


@pytest.mark.unit
def test_revision_chain() -> None:
    mod = load_migration(_MIGRATION_PATH)
    assert mod.revision == "travel_004"
    assert mod.down_revision == "travel_003"


@pytest.mark.unit
def test_upgrade_backfills_blank_cities_from_stations_only(monkeypatch) -> None:
    mod = load_migration(_MIGRATION_PATH)
    fake = _CollectingOp()
    monkeypatch.setattr(mod, "op", fake)
    mod.upgrade()
    assert len(fake.statements) == 2
    for side, stmt in zip(("departure", "arrival"), fake.statements, strict=True):
        assert f"SET {side}_city = {side}_airport_station" in stmt
        assert f"{side}_city IS NULL OR btrim({side}_city) = ''" in stmt
        assert f"{side}_airport_station IS NOT NULL" in stmt


@pytest.mark.unit
def test_downgrade_is_noop(monkeypatch) -> None:
    mod = load_migration(_MIGRATION_PATH)
    fake = _CollectingOp()
    monkeypatch.setattr(mod, "op", fake)
    mod.downgrade()
    assert fake.statements == []
