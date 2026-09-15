# Domain Module Package (`modules/`)

**Required if the butler defines domain tools in `roster/{name}/tools/`.** A
dedicated module package at `roster/{name}/modules/` wires those tools as MCP
tools on the daemon's FastMCP server.

**Why:** The daemon only registers MCP tools via the module system
(`_register_module_tools()`). Tool functions in `roster/{name}/tools/` are
importable Python, but are NOT callable via MCP unless a module wraps them with
`@mcp.tool()` closures. Without a domain module, the runtime LLM instance never
sees the tools — every call fails with "tool not found".

## Package structure

```
roster/{name}/modules/
├── __init__.py    # Module class (config, lifecycle, _get_pool, register_tools)
└── tools.py       # @mcp.tool() closure registrations
```

## `modules/__init__.py` — Module class

```python
"""<Name> module — wires <name> domain tools into the butler's MCP server."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class <Name>ModuleConfig(BaseModel):
    """Configuration for the <Name> module (empty — no settings needed yet)."""


class <Name>Module(Module):
    """<Name> module providing N MCP tools for <domain description>."""

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "<name>"

    @property
    def config_schema(self) -> type[BaseModel]:
        return <Name>ModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # tables via separate migrations

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        self._db = db

    async def on_shutdown(self) -> None:
        self._db = None

    def _get_pool(self):
        if self._db is None:
            raise RuntimeError("<Name>Module not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        self._db = db
        from .tools import register_tools
        register_tools(mcp, self)
```

## `modules/tools.py` — Tool wiring closures

```python
"""MCP tool wiring for the <name> module."""

from __future__ import annotations

from typing import Any


def register_tools(mcp: Any, module: Any) -> None:
    """Register all <name> MCP tools."""
    # Deferred imports
    from butlers.tools.<name> import <submodule> as _sub

    @mcp.tool()
    async def my_tool(param: str) -> dict[str, Any]:
        """Tool docstring (becomes MCP tool description)."""
        return await _sub.my_tool(module._get_pool(), param)
```

## Key conventions

- Closures inject `pool` via `module._get_pool()` — the MCP caller never sees the pool parameter.
- Deferred imports inside `register_tools()` to avoid import-time side effects.
- Type conversions at the MCP boundary (e.g., ISO string → `datetime.fromisoformat()`, float → `Decimal`) when the implementation expects richer types than MCP provides.
- Empty config is fine — the module just needs to exist to register tools.
- `migration_revisions()` returns `None` when migrations are handled separately in `roster/{name}/migrations/`.

## Wiring it in

**butler.toml:** Add `[modules.<name>]` (can be empty) so the daemon loads the module:

```toml
[modules.<name>]
```

**Auto-discovery:** `_register_roster_modules()` in `src/butlers/modules/registry.py`
scans `roster/*/modules/__init__.py` for `Module` subclasses. No manual
registration — placing the package at `roster/{name}/modules/` is sufficient.
Only butlers that declare `[modules.<name>]` in `butler.toml` load the module at startup.
