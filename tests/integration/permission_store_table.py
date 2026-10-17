"""Readable ``public.permissions`` table for tests that provision their own database.

notify() and the other permission gates refuse an unreadable store (bu-svqpzo.3). A
test module whose fixture builds a bare database imports this autouse fixture so the
real read runs and finds no row (a readable missing permission stays default-allow).
It needs a ``pending_actions_pool`` fixture in the importing module. Import it into
the module; do not register it with ``pytest_plugins``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
async def permission_store_table(pending_actions_pool):
    await pending_actions_pool.execute(
        "CREATE TABLE IF NOT EXISTS public.permissions ("
        "butler TEXT NOT NULL, permission TEXT NOT NULL, "
        "granted BOOL NOT NULL DEFAULT TRUE, reason TEXT, "
        "PRIMARY KEY (butler, permission))"
    )
