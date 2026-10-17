"""Readable-permission-store default for Spawner tests that use a bare mock pool.

The spawn gate fails closed on an unreadable ``public.permissions`` store, and a
bare ``AsyncMock`` pool answers every query with a non-row. A test module that is
about spawn behavior other than the permission gate imports this autouse fixture
to model a readable store; tests that patch ``check_permission`` themselves are
unaffected. Import it into the module; do not register it with ``pytest_plugins``,
which would make it autouse for the whole session.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.permissions import PermissionStatus


@pytest.fixture(autouse=True)
def spawner_permission_store_readable():
    with patch(
        "butlers.core.spawner.check_permission",
        new_callable=AsyncMock,
        return_value=PermissionStatus(allowed=True),
    ):
        yield
