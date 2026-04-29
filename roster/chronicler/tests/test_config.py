"""Unit tests for the Chronicler roster configuration and identity."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROSTER_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def butler_toml() -> dict:
    with open(ROSTER_DIR / "butler.toml", "rb") as fh:
        return tomllib.load(fh)


def test_chronicler_butler_identity(butler_toml: dict) -> None:
    butler = butler_toml["butler"]
    assert butler["name"] == "chronicler"
    assert butler["port"] == 41111
    assert "retrospective" in butler["description"].lower()


def test_chronicler_db_schema_and_name(butler_toml: dict) -> None:
    db = butler_toml["butler"]["db"]
    assert db["name"] == "butlers"
    assert db["schema"] == "chronicler"


def test_chronicler_is_not_staffer(butler_toml: dict) -> None:
    butler = butler_toml["butler"]
    # The absence of `type = "staffer"` (or explicit type="butler") means
    # it defaults to butler-typed, which is what we want.
    assert butler.get("type", "butler") == "butler"


def test_chronicler_configures_only_own_mcp_module(butler_toml: dict) -> None:
    """Projection adapters are scheduled jobs, while the read/bundle tool
    surface is provided by Chronicler's own MCP module."""
    modules = butler_toml.get("modules", {})
    assert modules == {"chronicler": {}}


def test_chronicler_schedule_uses_jobs_for_projection(butler_toml: dict) -> None:
    schedules = butler_toml["butler"]["schedule"]
    projection_jobs = {
        s["job_name"] for s in schedules if s.get("dispatch_mode") == "job" and "job_name" in s
    }
    assert "chronicler_project_sessions" in projection_jobs
    assert "chronicler_project_calendar" in projection_jobs
    assert "chronicler_project_owntracks" in projection_jobs
    assert "chronicler_project_steam" in projection_jobs


def test_chronicler_project_job_returns_serializable_dict(monkeypatch) -> None:
    """Projection-job wrappers must await the adapter and return a JSON-friendly dict.

    Regression guard: a previous form ``return await adapter.run(...).__dict__``
    awaited the coroutine's ``__dict__`` (a plain dict, not awaitable) and
    crashed at runtime. This test exercises the wrapper end-to-end with a
    fake adapter to ensure the wrapper actually awaits the result and converts
    the ``datetime`` watermark to an ISO-8601 string.
    """
    import asyncio
    from datetime import UTC, datetime

    from butlers.chronicler import jobs as chronicler_jobs
    from butlers.chronicler.adapters import AdapterResult

    captured_kwargs: dict = {}
    expected_watermark = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)

    class _FakeCoreSessionsAdapter:
        def __init__(self, *args, **kwargs) -> None:
            captured_kwargs["init"] = kwargs

        async def run(self, *, pool, chronicler_pool):  # noqa: ARG002
            return AdapterResult(
                source_name="core_sessions",
                rows_projected=3,
                watermark=expected_watermark,
            )

    async def _noop_seed(_pool) -> None:
        return None

    monkeypatch.setattr(chronicler_jobs, "CoreSessionsAdapter", _FakeCoreSessionsAdapter)
    monkeypatch.setattr(chronicler_jobs, "seed_source_registry", _noop_seed)
    monkeypatch.setattr(
        chronicler_jobs,
        "_discover_session_schemas",
        lambda: ("health",),
    )

    result = asyncio.run(chronicler_jobs.run_project_sessions(db_pool=None, job_args=None))  # type: ignore[arg-type]

    assert isinstance(result, dict)
    assert result["source_name"] == "core_sessions"
    assert result["rows_projected"] == 3
    assert result["watermark"] == expected_watermark.isoformat()
    assert captured_kwargs["init"]["butler_schemas"] == ("health",)


def test_chronicler_roster_files_present() -> None:
    assert (ROSTER_DIR / "MANIFESTO.md").is_file()
    assert (ROSTER_DIR / "AGENTS.md").is_file()
    assert (ROSTER_DIR / "CLAUDE.md").is_file()
    assert (ROSTER_DIR / "api" / "router.py").is_file()
    assert (ROSTER_DIR / "api" / "models.py").is_file()
    assert (ROSTER_DIR / "migrations" / "001_chronicler_tables.py").is_file()


def test_init_db_sql_declares_chronicler_schema() -> None:
    sql = (PROJECT_ROOT / "scripts" / "init-db.sql").read_text()
    assert "'chronicler'" in sql
    assert "butler_chronicler_rw" in sql


def test_rfc_0014_exists() -> None:
    rfc_path = (
        PROJECT_ROOT / "about" / "legends-and-lore" / "rfcs" / "0014-chronicler-time-butler.md"
    )
    assert rfc_path.is_file()
    text = rfc_path.read_text()
    assert "Status:** Accepted" in text
    assert "retrospective" in text.lower()


def test_v1_heart_and_soul_mentions_chronicler() -> None:
    v1 = (PROJECT_ROOT / "about" / "heart-and-soul" / "v1.md").read_text()
    assert "Chronicler" in v1
