"""home_007: away-safety declarations, settings, streaks and the away-door column (bu-jv1moa.87).

The statement-shape tests run anywhere. The constraint and downgrade tests need real
PostgreSQL and run CI-first where Docker is unavailable. They assert only the error class,
never PostgreSQL's message text, so they do not depend on the server version.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HOME_006 = _ROOT / "roster/home/migrations/006_departure_watch.py"
_HOME_007 = _ROOT / "roster/home/migrations/007_away_safety.py"
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")

_FREEZER = (
    "INSERT INTO temperature_watch (entity_id, role, owner_label, unit, max_c, hysteresis_c, "
    "dwell_minutes, max_reading_age_minutes) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
)
_GOOD = ("sensor.garage_freezer", "freezer", "Garage freezer", "°C", -15, 2, 30, 60)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(module, direction: str) -> list[str]:
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        getattr(module, direction)()
    return statements


@pytest.mark.unit
def test_home_007_extends_the_home_chain() -> None:
    module = _load(_HOME_007, "home_007")
    assert (module.revision, module.down_revision) == ("home_007", "home_006")


@pytest.mark.unit
def test_no_threshold_has_a_default() -> None:
    """Away-safety requirement 002: a missing value is unconfigured, never filled in."""
    upgrade = " ".join(" ".join(_statements(_load(_HOME_007, "h007"), "upgrade")).split())
    for column in (
        "max_c",
        "hysteresis_c",
        "dwell_minutes",
        "max_reading_age_minutes",
        "blind_after_minutes",
        "away_alert_after_minutes",
    ):
        definition = upgrade[upgrade.index(f" {column} ") :].split(",", 1)[0]
        assert "DEFAULT" not in definition, column


@_docker
@pytest.mark.integration
async def test_away_safety_tables_hold_their_bounds_and_downgrade(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        for path, name in ((_HOME_006, "h006_pg"), (_HOME_007, "h007_pg")):
            for statement in _statements(_load(path, name), "upgrade"):
                await pool.execute(statement)

        await pool.execute(_FREEZER, *_GOOD)
        other = "sensor.chest_freezer"
        bad_rows = (
            ("switch.freezer_plug", *_GOOD[1:]),
            (other, "fridge", *_GOOD[2:]),
            (other, "freezer", "   ", *_GOOD[3:]),
            (other, "freezer", "Freezer", "K", *_GOOD[4:]),
            (other, *_GOOD[1:4], 1, *_GOOD[5:]),
            (other, *_GOOD[1:4], -31, *_GOOD[5:]),
            (other, *_GOOD[1:5], 0.4, *_GOOD[6:]),
            (other, *_GOOD[1:6], 4, _GOOD[7]),
            (other, *_GOOD[1:6], 241, _GOOD[7]),
            (other, *_GOOD[1:7], 9),
            (other, *_GOOD[1:7], 361),
        )
        for row in bad_rows:
            with pytest.raises(asyncpg.CheckViolationError):
                await pool.execute(_FREEZER, *row)

        await pool.execute("INSERT INTO away_safety_settings (blind_after_minutes) VALUES (15)")
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO away_safety_settings (singleton, blind_after_minutes) "
                "VALUES (false, 15)"
            )
        for minutes in (4, 1441):
            with pytest.raises(asyncpg.CheckViolationError):
                await pool.execute(
                    "UPDATE away_safety_settings SET blind_after_minutes = $1", minutes
                )

        await pool.execute(
            "INSERT INTO departure_watch (entity_id, owner_label, secure_states) "
            "VALUES ('binary_sensor.front_door', 'Front door', ARRAY['off'])"
        )
        assert await pool.fetchval("SELECT away_alert_after_minutes FROM departure_watch") is None
        await pool.execute("UPDATE departure_watch SET away_alert_after_minutes = 30")
        for minutes in (29, 1441):
            with pytest.raises(asyncpg.CheckViolationError):
                await pool.execute(
                    "UPDATE departure_watch SET away_alert_after_minutes = $1", minutes
                )

        await pool.execute(
            "INSERT INTO away_safety_streaks (kind, streak_key, streak_start, last_run_at) "
            "VALUES ('blind', 'leak', now(), now())"
        )
        for kind, start, last in (
            ("door", "now()", "now()"),
            ("freezer", "now()", "now() - interval '1 minute'"),
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                await pool.execute(
                    "INSERT INTO away_safety_streaks (kind, streak_key, streak_start, "
                    f"last_run_at) VALUES ('{kind}', 'sensor.x', {start}, {last})"
                )

        for statement in _statements(_load(_HOME_007, "h007_down"), "downgrade"):
            await pool.execute(statement)
        for table in ("temperature_watch", "away_safety_settings", "away_safety_streaks"):
            assert await pool.fetchval("SELECT to_regclass($1)", table) is None
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'departure_watch' AND column_name = 'away_alert_after_minutes'"
            )
            == 0
        )
