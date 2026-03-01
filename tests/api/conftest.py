"""Shared fixtures and helpers for butler API tests.

Covers:
- Notification test helpers (row factory, app builders for list/stats/error endpoints)
- Butler API scaffolding (roster directory setup, mock MCP client managers, test app factory)
- Shared app fixture (module-scoped) to avoid per-test create_app() overhead
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.routers.butlers import _get_roster_dir
from butlers.api.routers.notifications import _get_db_manager

# ---------------------------------------------------------------------------
# Notification row factory
# ---------------------------------------------------------------------------


def make_notification_row(
    *,
    source_butler: str = "atlas",
    channel: str = "telegram",
    recipient: str = "12345",
    message: str = "Hello!",
    metadata: dict | None = None,
    status: str = "sent",
    error: str | None = None,
    session_id=None,
    trace_id: str | None = None,
    created_at: datetime | None = None,
) -> dict:
    """Build a dict mimicking an asyncpg Record for the notifications table."""
    return {
        "id": uuid4(),
        "source_butler": source_butler,
        "channel": channel,
        "recipient": recipient,
        "message": message,
        "metadata": metadata or {},
        "status": status,
        "error": error,
        "session_id": session_id,
        "trace_id": trace_id,
        "created_at": created_at or datetime.now(tz=UTC),
    }


# ---------------------------------------------------------------------------
# Notification app builder helpers
# ---------------------------------------------------------------------------


def build_notifications_app(
    rows: list[dict],
    total: int | None = None,
    app: FastAPI | None = None,
) -> tuple:
    """Wire a FastAPI app with mocked DatabaseManager for list endpoints.

    If ``app`` is provided (e.g. the shared module-scoped fixture), it is
    mutated in-place; otherwise a fresh app is created.  The caller is
    responsible for clearing ``app.dependency_overrides`` after the test
    (the ``clear_dependency_overrides`` autouse fixture handles this when
    the shared fixture is used).

    Returns (app, mock_pool, mock_db) so tests can inspect call args.
    """
    if total is None:
        total = len(rows)

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(
        return_value=[
            MagicMock(
                **{
                    "__getitem__": lambda self, key, row=row: row[key],
                }
            )
            for row in rows
        ]
    )

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    if app is None:
        app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app, mock_pool, mock_db


def build_stats_app(
    *,
    total: int = 0,
    sent: int = 0,
    failed: int = 0,
    channel_rows: list[dict] | None = None,
    butler_rows: list[dict] | None = None,
    app: FastAPI | None = None,
) -> tuple:
    """Wire a FastAPI app with mocked DatabaseManager for the /stats endpoint.

    If ``app`` is provided (e.g. the shared module-scoped fixture), it is
    mutated in-place; otherwise a fresh app is created.

    Returns (app, mock_pool, mock_db) so tests can inspect call args.
    """
    if channel_rows is None:
        channel_rows = []
    if butler_rows is None:
        butler_rows = []

    mock_pool = AsyncMock()

    async def _fetchval(sql, *args):
        if "status = 'sent'" in sql:
            return sent
        elif "status = 'failed'" in sql:
            return failed
        else:
            return total

    mock_pool.fetchval = AsyncMock(side_effect=_fetchval)

    def _make_record(row: dict) -> MagicMock:
        m = MagicMock()
        m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
        return m

    async def _fetch(sql, *args):
        if "GROUP BY channel" in sql:
            return [_make_record(r) for r in channel_rows]
        elif "GROUP BY source_butler" in sql:
            return [_make_record(r) for r in butler_rows]
        return []

    mock_pool.fetch = AsyncMock(side_effect=_fetch)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    if app is None:
        app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app, mock_pool, mock_db


def build_app_missing_switchboard(app: FastAPI | None = None) -> object:
    """Return an app where the switchboard pool lookup raises KeyError.

    If ``app`` is provided it is mutated in-place; otherwise a fresh app
    is created.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")

    if app is None:
        app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# TOML templates (module-level for reuse within conftest)
# ---------------------------------------------------------------------------

_BUTLER_TOML_MINIMAL = """\
[butler]
name = "{name}"
port = {port}
description = "{description}"

[butler.db]
name = "butler_{name}"

[runtime]
type = "claude-code"
"""

_BUTLER_TOML_WITH_MODULES = """\
[butler]
name = "{name}"
port = {port}
description = "{description}"

[butler.db]
name = "butler_{name}"

[runtime]
type = "claude-code"

{modules_section}
"""

_BUTLER_TOML_WITH_SCHEDULE = """\
[butler]
name = "{name}"
port = {port}
description = "{description}"

[butler.db]
name = "butler_{name}"

[runtime]
type = "claude-code"

[[butler.schedule]]
name = "morning-check"
cron = "0 8 * * *"
prompt = "Run the morning check"

[[butler.schedule]]
name = "nightly-cleanup"
cron = "0 2 * * *"
prompt = "Clean up old data"
"""


# ---------------------------------------------------------------------------
# Roster directory helpers
# ---------------------------------------------------------------------------


def make_butler_dir(
    roster_dir: Path,
    name: str,
    port: int,
    *,
    description: str = "Test butler",
    claude_md: str | None = None,
    manifesto_md: str | None = None,
    agents_md: str | None = None,
    modules: dict[str, str] | None = None,
    skills: list[str] | None = None,
    skills_with_content: dict[str, str] | None = None,
    with_schedule: bool = False,
    extra_toml: str = "",
) -> Path:
    """Create a butler directory under roster_dir with a butler.toml and optional extras.

    Parameters
    ----------
    roster_dir:
        Parent directory (e.g. tmp_path) under which the butler dir is created.
    name:
        Butler name; directory will be roster_dir/name.
    port:
        Butler port written into butler.toml.
    description:
        Butler description written into butler.toml.
    claude_md:
        If provided, writes CLAUDE.md with this content.
    manifesto_md:
        If provided, writes MANIFESTO.md with this content.
    agents_md:
        If provided, writes AGENTS.md with this content.
    modules:
        Mapping of module name to additional TOML body; adds [modules.*] sections.
    skills:
        List of skill names to create under skills/; each gets a stub SKILL.md.
    skills_with_content:
        Mapping of skill name to exact SKILL.md content. Takes precedence over skills.
    with_schedule:
        If True, uses the two-entry schedule TOML template.
    extra_toml:
        Raw TOML appended verbatim after the base config (for special cases).
    """
    butler_dir = roster_dir / name
    butler_dir.mkdir(parents=True, exist_ok=True)

    if with_schedule:
        toml_content = _BUTLER_TOML_WITH_SCHEDULE.format(
            name=name, port=port, description=description
        )
    elif modules:
        mod_sections = ""
        for mod_name, mod_body in modules.items():
            mod_sections += f"[modules.{mod_name}]\n"
            if mod_body:
                mod_sections += f"{mod_body}\n"
        toml_content = _BUTLER_TOML_WITH_MODULES.format(
            name=name, port=port, description=description, modules_section=mod_sections
        )
    else:
        toml_content = _BUTLER_TOML_MINIMAL.format(name=name, port=port, description=description)

    if extra_toml:
        toml_content += "\n" + extra_toml

    (butler_dir / "butler.toml").write_text(toml_content)

    if claude_md is not None:
        (butler_dir / "CLAUDE.md").write_text(claude_md)
    if manifesto_md is not None:
        (butler_dir / "MANIFESTO.md").write_text(manifesto_md)
    if agents_md is not None:
        (butler_dir / "AGENTS.md").write_text(agents_md)

    # skills_with_content takes precedence
    all_skills = skills_with_content or {}
    if skills and not skills_with_content:
        all_skills = {s: f"# {s}\n\nSkill description.\n" for s in skills}

    if all_skills:
        skills_dir = butler_dir / ".agents" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        for skill_name, content in all_skills.items():
            skill_dir = skills_dir / skill_name
            skill_dir.mkdir(exist_ok=True)
            (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    return butler_dir


# ---------------------------------------------------------------------------
# Mock MCP manager helper
# ---------------------------------------------------------------------------


def make_mock_mcp_manager(*, online: bool = True) -> MCPClientManager:
    """Create a mock MCPClientManager with configurable reachability.

    Parameters
    ----------
    online:
        If True, get_client returns a mock with a successful ping.
        If False, get_client raises ButlerUnreachableError.
    """
    mgr = MagicMock(spec=MCPClientManager)
    if online:
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        mgr.get_client = AsyncMock(return_value=mock_client)
    else:
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("test", cause=ConnectionRefusedError("refused"))
        )
    return mgr


# ---------------------------------------------------------------------------
# Test app factory helper
# ---------------------------------------------------------------------------


def make_test_app(
    roster_dir: Path,
    configs: list[ButlerConnectionInfo],
    mcp_manager: MCPClientManager | None = None,
    app: FastAPI | None = None,
):
    """Wire a FastAPI test app with butler API dependency overrides.

    If ``app`` is provided (e.g. the shared module-scoped fixture), it is
    mutated in-place; otherwise a fresh app is created.

    Parameters
    ----------
    roster_dir:
        The roster root directory exposed via the _get_roster_dir override.
    configs:
        Butler configs injected via get_butler_configs override.
    mcp_manager:
        MCP manager injected via get_mcp_manager override.
        Defaults to an online mock manager if not provided.
    app:
        Optional existing FastAPI app to mutate instead of creating a new one.
    """
    if mcp_manager is None:
        mcp_manager = make_mock_mcp_manager(online=True)

    if app is None:
        app = create_app()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    app.dependency_overrides[_get_roster_dir] = lambda: roster_dir
    return app


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def roster_dir(tmp_path: Path) -> Path:
    """An empty temporary directory suitable for use as a roster root."""
    return tmp_path


@pytest.fixture
def online_mcp_manager() -> MCPClientManager:
    """A mock MCPClientManager that reports all butlers as online."""
    return make_mock_mcp_manager(online=True)


@pytest.fixture
def offline_mcp_manager() -> MCPClientManager:
    """A mock MCPClientManager that raises ButlerUnreachableError for all butlers."""
    return make_mock_mcp_manager(online=False)


# ---------------------------------------------------------------------------
# Shared app fixture (module-scoped) for performance optimisation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """A single FastAPI app instance shared across all tests in a module.

    Using module scope avoids the ~0.3 s overhead of create_app() per test
    (router discovery, middleware setup, dependency wiring).

    Tests must not rely on ``dependency_overrides`` persisting between
    tests — the ``clear_dependency_overrides`` autouse fixture resets the
    overrides dict after every test function so there is no state leakage.

    Tests that need a completely isolated app (e.g. tests for create_app()
    itself or CORS configuration) should call ``create_app()`` directly
    rather than requesting this fixture.
    """
    return create_app()


@pytest.fixture(autouse=True)
def clear_dependency_overrides(request: pytest.FixtureRequest) -> None:
    """Clear dependency_overrides on the shared app after each test function.

    Only active when the ``app`` fixture is in scope for the current test.
    This ensures no mock state leaks between tests that share a single app
    instance.
    """
    yield
    # Post-test teardown: clear overrides only if the shared app fixture was used
    if "app" in request.fixturenames:
        shared_app = request.getfixturevalue("app")
        shared_app.dependency_overrides.clear()
