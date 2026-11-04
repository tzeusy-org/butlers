"""A real dashboard ``DatabaseManager`` over pools a test already holds (bu-rvgzmf.1).

Dashboard routers and jobs take a :class:`butlers.api.db.DatabaseManager` and ask
it for a butler's pool, schema and module census. Tests used to hand-write a
fake manager class per file; :func:`db_manager` instead returns the production
class with the given pools registered, so lookups, ``KeyError`` refusals and
``fan_out_with_status`` behave exactly as they do in the dashboard. Nothing
connects: the pools are whatever the test passes (``db`` clones, ``fake_pool``
fakes or bare sentinels). To count or refuse lookups, wrap the method with
``unittest.mock.patch.object(manager, "pool", ...)``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from butlers.api.db import DatabaseManager


def db_manager(
    pools: Mapping[str, Any],
    *,
    schemas: Mapping[str, str | None] | None = None,
    memory_schemas: Mapping[str, str | None] | None = None,
    modules: Mapping[str, Iterable[str]] | None = None,
    credential_pool: Any = None,
) -> DatabaseManager:
    """A ``DatabaseManager`` serving *pools* by butler name.

    *schemas* and *memory_schemas* set what ``schema_for_butler`` and the memory
    override report; *modules* sets the module census (unknown when omitted);
    *credential_pool* backs ``credential_shared_pool``.
    """
    manager = DatabaseManager()
    # The manager has no public way to adopt an existing pool; this is the one
    # place tests reach into its registry.
    manager._pools.update(pools)
    manager._butler_schemas.update(schemas or {})
    manager._butler_memory_schema_overrides.update(memory_schemas or {})
    for name, enabled in (modules or {}).items():
        manager._butler_modules[name] = frozenset(enabled)
        manager.register_configured_modules(name, frozenset(enabled))
    manager._shared_pool = credential_pool
    return manager
