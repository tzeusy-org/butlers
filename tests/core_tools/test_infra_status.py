"""Tests for the infra ``status`` MCP tool — extra_status_fields() integration.

Verifies that the status() tool merges per-module extra fields (oauth_status,
credential_health, etc.) returned by Module.extra_status_fields() into the
modules dict of the response.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.core_tools._base import ToolContext
from butlers.core_tools._infra import register_infra_tools

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_module(name: str, extra_fields: dict | None = None, raises: bool = False):
    """Return a minimal module-like object with extra_status_fields()."""

    async def extra_status_fields():
        if raises:
            raise RuntimeError("db unavailable")
        return extra_fields or {}

    return SimpleNamespace(name=name, extra_status_fields=extra_status_fields)


def _register_and_grab_status(modules, module_statuses=None):
    """Register infra tools on a minimal daemon and return the status() function."""
    registered: dict = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    # tool_span is a decorator that wraps async functions; bypass it for tests.
    import butlers.core_tools._infra as _infra_mod

    original_tool_span = _infra_mod.tool_span
    _infra_mod.tool_span = lambda *_a, **_kw: lambda fn: fn

    try:
        mcp = SimpleNamespace()
        daemon = SimpleNamespace(
            _started_at=0.0,
            _check_health=AsyncMock(return_value="ok"),
            _modules=modules,
            _module_statuses=module_statuses or {},
            config=SimpleNamespace(name="messenger", description="test", port=41104),
        )
        ctx = ToolContext(
            daemon=daemon,
            pool=None,
            spawner=None,
            butler_name="messenger",
            butler_type=None,
            is_switchboard=False,
            is_messenger=True,
            route_metrics=None,
        )
        register_infra_tools(ctx, mcp, _core_tool)
    finally:
        _infra_mod.tool_span = original_tool_span

    return registered["status"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "oauth_status, credential_health",
    [
        ("granted", "ok"),
        ("not_configured", "warning"),
    ],
)
async def test_status_merges_extra_fields_from_active_module(oauth_status, credential_health):
    """Extra fields from extra_status_fields() are forwarded into the module entry."""
    mod = _make_module(
        "email",
        extra_fields={"oauth_status": oauth_status, "credential_health": credential_health},
    )
    status = _register_and_grab_status([mod])

    result = await status()

    email_entry = result["modules"]["email"]
    assert email_entry["status"] == "active"
    assert email_entry["oauth_status"] == oauth_status
    assert email_entry["credential_health"] == credential_health


async def test_status_extra_fields_exception_is_silenced():
    """Exception in extra_status_fields() is silenced; module still shows active."""
    mod = _make_module("email", raises=True)
    status = _register_and_grab_status([mod])

    result = await status()

    email_entry = result["modules"]["email"]
    assert email_entry["status"] == "active"
    # No oauth_status key when extra_status_fields raised.
    assert "oauth_status" not in email_entry


async def test_status_empty_extra_fields_leaves_entry_unchanged():
    """Empty extra_status_fields() leaves module entry as {'status': 'active'}."""
    mod = _make_module("email", extra_fields={})
    status = _register_and_grab_status([mod])

    result = await status()

    assert result["modules"]["email"] == {"status": "active"}


async def test_status_extra_fields_cannot_clobber_lifecycle_status():
    """extra_status_fields() returning a 'status' key cannot overwrite the lifecycle status."""
    mod = _make_module(
        "email",
        extra_fields={"status": "reauth_needed", "oauth_status": "granted"},
    )
    status = _register_and_grab_status([mod])

    result = await status()

    email_entry = result["modules"]["email"]
    # Lifecycle status must remain "active" even though extra_fields tried to overwrite it.
    assert email_entry["status"] == "active"
    # Other extra fields are still merged.
    assert email_entry["oauth_status"] == "granted"


async def test_status_failed_module_does_not_call_extra_fields():
    """Modules in failed/cascade_failed state skip extra_status_fields()."""
    from butlers.module_state import ModuleStartupStatus

    calls = []

    async def extra_status_fields():
        calls.append(True)
        return {"oauth_status": "granted"}

    mod = SimpleNamespace(name="email", extra_status_fields=extra_status_fields)
    module_statuses = {"email": ModuleStartupStatus(status="failed", phase="startup", error="boom")}
    status = _register_and_grab_status([mod], module_statuses=module_statuses)

    result = await status()

    assert result["modules"]["email"]["status"] == "failed"
    assert result["modules"]["email"]["error"] == "boom"
    assert calls == [], "extra_status_fields should not be called for failed modules"
