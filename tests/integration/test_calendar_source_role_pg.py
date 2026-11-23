# Spec: REQ-module-calendar-034
"""Source roles through the real core chain (bu-5ii4gj.1).

The production ``_ensure_calendar_source`` upsert against a migrated database: a new source gets the
fail-safe default, discovery replaces a provider-inferred role, an owner-confirmed role survives it,
and a caller that names no role leaves the stored one alone. Synthetic calendar ids only.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from butlers.modules.calendar import CalendarModule

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

KEY = "provider:google:partner@example.test"


async def _ensure(module: CalendarModule, *, role: str | None) -> None:
    await module._ensure_calendar_source(
        source_key=KEY,
        source_kind="provider_event",
        lane="user",
        provider="google",
        calendar_id="partner@example.test",
        display_name="Partner",
        writable=False,
        metadata={"projection": "provider_sync"},
        time_role=role,
    )


async def _stored(pool) -> tuple[str, str]:
    row = await pool.fetchrow(
        "SELECT time_role, role_basis FROM calendar_sources WHERE source_key = $1", KEY
    )
    return row["time_role"], row["role_basis"]


async def test_roles_are_inferred_kept_and_never_override_the_owner(migrated_core_postgres_pool):
    async with migrated_core_postgres_pool() as pool:
        module = CalendarModule()
        module._db = SimpleNamespace(pool=pool, db_name="general")
        module._butler_name = "general"
        assert await module._projection_tables_available() is True

        await _ensure(module, role=None)
        assert await _stored(pool) == ("unclassified", "provider_inferred")

        await _ensure(module, role="holidays")  # discovery replaces an inferred role
        assert await _stored(pool) == ("holidays", "provider_inferred")

        await pool.execute(
            "UPDATE calendar_sources SET time_role = 'household_member', "
            "role_basis = 'owner_confirmed' WHERE source_key = $1",
            KEY,
        )
        await _ensure(module, role="holidays")
        assert await _stored(pool) == ("household_member", "owner_confirmed")

        await _ensure(module, role=None)  # a sync path that infers nothing keeps it
        assert await _stored(pool) == ("household_member", "owner_confirmed")
