"""The retraction listing says what each stale approval card was about (bu-dhloi1.167)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from butlers.api.routers.owner_delivery import _card_subject, _plain
from butlers.core.owner_delivery import RetractTarget

pytestmark = pytest.mark.unit

_ACTION = "5d1c0a7e-8b2f-5c3e-9a41-24c068020001"


def _target(**overrides) -> RetractTarget:
    base = {
        "presentation_id": UUID(int=1),
        "surface": "telegram",
        "route_ref": "9001",
        "provider_ref": "55",
        "kind": "approval",
        "source_ref": _ACTION,
        "owning_schema": "messenger",
    }
    return RetractTarget(**{**base, **overrides})


def _conn(row) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow.return_value = row
    return conn


async def test_subject_names_the_butler_and_the_unregistered_tool_safely(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    conn = _conn({"tool_name": "some_tool`[x](http://evil)", "tool_args": "{}"})

    shown = await _card_subject(conn, _target())

    assert shown == {"subject_line": "[messenger] Approval: some_toolxhttpevil"}
    conn.fetchrow.assert_awaited_once()


async def test_open_link_comes_from_the_verified_dashboard_origin(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example.com")

    shown = await _card_subject(_conn(None), _target())

    assert shown == {
        "open_label": "Open dash.example.com (dashboard)",
        "open_url": f"https://dash.example.com/approvals/{_ACTION}",
    }


@pytest.mark.parametrize(
    "target", [_target(kind="goal_answer"), _target(source_ref="not-a-uuid"), _target(kind=None)]
)
async def test_other_subjects_and_bad_refs_get_the_generic_card(target, monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example.com")
    conn = _conn(None)

    assert await _card_subject(conn, target) == {}
    conn.fetchrow.assert_not_awaited()


def test_plain_strips_markup_and_bounds_length() -> None:
    assert _plain("a*b_c`d<e>[f](g)") == "ab_cdefg"
    assert len(_plain("x" * 500)) == 80


async def test_a_failing_link_resolver_keeps_the_subject_line(monkeypatch) -> None:
    import importlib

    real = importlib.import_module

    def broken(name, *args, **kwargs):
        if name.endswith("self_links"):
            raise ImportError("no resolver")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("butlers.api.routers.owner_delivery.importlib.import_module", broken)
    conn = _conn({"tool_name": "some_tool", "tool_args": "{}"})

    assert await _card_subject(conn, _target()) == {
        "subject_line": "[messenger] Approval: some_tool"
    }
