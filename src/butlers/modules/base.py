"""Abstract base class for butler modules."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass
class ToolMeta:
    """Metadata for a single MCP tool registered by a module.

    Attributes:
        arg_sensitivities: Mapping of argument name to whether it is
            safety-critical (sensitive). Arguments not listed are resolved
            via the heuristic fallback in the approvals sensitivity module.
    """

    arg_sensitivities: dict[str, bool] = field(default_factory=dict)


class Module(abc.ABC):
    """Abstract base class for butler modules.

    Every pluggable module must subclass Module and implement all abstract
    members. Modules add domain-specific MCP tools to a butler but never
    touch core infrastructure.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique module name (e.g., 'email', 'telegram')."""
        ...

    @property
    @abc.abstractmethod
    def config_schema(self) -> type[BaseModel]:
        """Pydantic model class for this module's configuration."""
        ...

    @property
    @abc.abstractmethod
    def dependencies(self) -> list[str]:
        """Names of modules this module depends on."""
        ...

    @abc.abstractmethod
    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register MCP tools on the butler's FastMCP server."""
        ...

    @abc.abstractmethod
    def migration_revisions(self) -> str | None:
        """Return Alembic branch label for module migrations, or None."""
        ...

    @abc.abstractmethod
    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Called after dependency resolution and migrations.

        Parameters
        ----------
        config:
            Module-specific validated configuration object.
        db:
            Butler database instance (provides ``db.pool`` for asyncpg).
        credential_store:
            Optional :class:`~butlers.credential_store.CredentialStore` for
            DB-first credential resolution.  Passed by the daemon since
            step 8b.  May be ``None`` in tests that do not need DB credential
            resolution.
        """
        ...

    @abc.abstractmethod
    async def on_shutdown(self) -> None:
        """Called during butler shutdown."""
        ...

    def tool_metadata(self) -> dict[str, ToolMeta]:
        """Return sensitivity metadata for tools registered by this module.

        Keys are tool names, values are ``ToolMeta`` instances describing
        which arguments are safety-critical.  Modules that do not override
        this method get an empty dict (no explicit declarations), and the
        approvals subsystem will fall back to heuristic classification.
        """
        return {}
