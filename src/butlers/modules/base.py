"""Abstract base class for butler modules."""

from __future__ import annotations

import abc
import functools
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from butlers.core.tool_catalog import (
    EffectClass,
    Idempotency,
    OutputSensitivity,
    OutputTrust,
    RecipientParameters,
)


class ToolGroupMixin(BaseModel):
    """Mixin providing optional tool group filtering for module configs.

    When a butler's ``butler.toml`` specifies ``groups = ["core", "entity"]``
    under a module section, only tools in those groups are registered.
    When ``groups`` is absent or empty, all tools are registered (backwards
    compatible).

    Usage in module config::

        class MyModuleConfig(ToolGroupMixin, BaseModel):
            some_setting: str = "default"

    Usage in register_tools()::

        if group_enabled(config, "core"):
            @mcp.tool()
            async def my_core_tool(...): ...
    """

    groups: list[str] | None = None


def group_enabled(config: Any, group: str) -> bool:
    """Return True if the given tool group should be registered.

    When *config* has no ``groups`` field or it is ``None``/empty, all groups
    are enabled (backwards compatible).  Otherwise, only groups listed in the
    config are enabled.
    """
    groups = getattr(config, "groups", None)
    if not groups:
        return True
    return group in groups


def tool_registrar(mcp: Any, groups: Any) -> Callable[[str], Callable[[Any], Any]]:
    """Return the group-aware ``@tool("group")`` decorator every module registers tools with.

    *groups* is the enabled group list, or a module config carrying a ``groups`` field (or
    ``None``): no groups means every group is enabled.  ``@tool("g")`` registers the function
    on *mcp* when group ``g`` is enabled; otherwise the function is returned unregistered.
    """
    if not isinstance(groups, (list, tuple, set, frozenset)):
        groups = getattr(groups, "groups", None)

    def tool(group: str) -> Callable[[Any], Any]:
        if not groups or group in groups:
            return mcp.tool()
        return lambda fn: fn  # defined, not registered

    return tool


def guarded_tool(
    *,
    credential: Callable[[], bool] | None = None,
    missing: Callable[[], Any] | None = None,
    errors: Callable[[Exception], Any] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Wrap an async tool with the credential check and error mapping modules repeat per tool.

    - *credential*: returns True when the tool can run.  Evaluated per call, so a credential
      that appears after registration is honoured.  Omit it for tools that need none.
    - *missing*: builds the response returned instead of running when *credential* is False;
      required with *credential*.
    - *errors*: maps an exception raised by the tool to its response.  Without it, exceptions
      propagate.

    The wrapper keeps the tool's name, docstring and signature, which FastMCP reads to build
    the tool schema.  Apply it under ``mcp.tool()`` / the group registrar.
    """

    if (credential is None) != (missing is None):
        raise TypeError("guarded_tool needs credential and missing together")

    def decorate(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            if credential is not None and missing is not None and not credential():
                return missing()
            if errors is None:
                return await fn(*args, **kwargs)
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                return errors(exc)

        return guarded

    return decorate


@dataclass
class ToolMeta:
    """Metadata for a single MCP tool registered by a module.

    Attributes:
        arg_sensitivities: Mapping of argument name to whether it is
            safety-critical (sensitive). Arguments not listed are resolved
            via the heuristic fallback in the approvals sensitivity module.
        canonical_name: Exact name of the already-registered FastMCP handler.
        module_name: Core or the owning module's canonical name.
        group_name: Registration group that admitted the handler.
        namespace: Logical discovery namespace; it never renames the handler.
        llm_presentable: Whether a future adapter may include this definition.
        load_posture: Whether a presentable definition is eager or deferrable.

    Presentation fields are optional so an unclassified legacy module keeps
    its eager, presentable compatibility behavior while native classification
    remains incomplete. Optional RFC0038 authorization fields describe future
    broker eligibility only. The central inventory supplies them, while the
    daemon binds owner_domain and the existing approval_policy. Sensitivity
    declarations still control current approvals; authorization adds no gate.
    """

    arg_sensitivities: dict[str, bool] = field(default_factory=dict)
    canonical_name: str | None = None
    module_name: str | None = None
    group_name: str | None = None
    namespace: str | None = None
    llm_presentable: bool | None = None
    load_posture: Literal["eager", "deferred"] | None = None
    owner_domain: str | None = None
    effect_class: EffectClass | None = None
    output_trust: OutputTrust | None = None
    output_sensitivity: OutputSensitivity | None = None
    resource_params: tuple[str, ...] | None = None
    selection_bound: Mapping[str, bool] | None = None
    safety_args: tuple[str, ...] | None = None
    approval_policy: Mapping[str, Any] | None = None
    idempotency: Idempotency | None = None
    owner_authority: bool | None = None
    recipient_params: RecipientParameters | None = None
    read_stores: tuple[str, ...] = ()
    write_stores: tuple[str, ...] = ()
    unknown_output_source: bool = True


class Module(abc.ABC):
    """Abstract base class for butler modules.

    Every pluggable module must subclass Module and implement the three
    abstract members: ``name``, ``config_schema`` and ``register_tools``.
    Every other hook has a default and is overridden only when needed.
    Modules add domain-specific MCP tools to a butler but never touch core
    infrastructure.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique name, implemented as a class-level string for discovery."""
        ...

    @property
    @abc.abstractmethod
    def config_schema(self) -> type[BaseModel]:
        """Pydantic model class for this module's configuration."""
        ...

    @property
    def dependencies(self) -> list[str]:
        """Names of modules this module depends on; none by default."""
        return []

    @abc.abstractmethod
    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register MCP tools on the butler's FastMCP server.

        Parameters
        ----------
        mcp:
            FastMCP server instance to register tools on.
        config:
            Module-specific validated configuration object.
        db:
            Butler database instance (provides ``db.pool`` for asyncpg).
        butler_name:
            Canonical butler identity string, passed by the daemon from its
            loaded configuration.  Modules MUST use this parameter for
            identity — never derive it from database attributes
            (``db.schema``, ``db.db_name``, or similar).
        """
        ...

    def migration_revisions(self) -> str | None:
        """Return Alembic branch label for module migrations, or None (the default)."""
        return None

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
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
        blob_store:
            Optional :class:`~butlers.storage.BlobStore` for reading and
            writing blobs (e.g. CSV attachments for import tools).
            May be ``None`` when blob storage is not configured.

        The default does nothing.
        """
        return None

    async def on_shutdown(self) -> None:
        """Called during butler shutdown.  The default does nothing."""
        return None

    @property
    def credentials_env(self) -> list[str]:
        """Daemon-side credential requirements; not forwarded to runtime children."""
        return []

    def on_all_modules_ready(self, module_map: dict[str, Module]) -> None:
        """Called once every active module has registered its tools, to cross-wire modules.

        The default does nothing.  A failure is logged by the daemon and does not stop startup.
        """
        return None

    def wire_audit_pool(self, audit_pool: Any) -> None:
        """Wire the switchboard audit pool for egress audit logging.

        Called by the daemon lifecycle after the audit pool is created (step
        10a), giving modules that emit egress audit entries access to the
        switchboard-scoped audit pool (egress entries land in the canonical
        ``public.audit_log`` via the audit append writer; the legacy
        ``dashboard_audit_log`` table was retired by switchboard sw_026).

        The default implementation is a no-op.  Modules that emit egress
        audit entries (e.g. telegram, email, calendar) should override this
        method to store *audit_pool* for use in their outbound call paths.

        Parameters
        ----------
        audit_pool:
            asyncpg connection pool scoped to the switchboard schema, or
            ``None`` when the audit pool could not be created.
        """
        return None

    def tool_metadata(self) -> dict[str, ToolMeta]:
        """Return sensitivity and optional presentation metadata for tools.

        Keys are tool names, values are ``ToolMeta`` instances describing
        which arguments are safety-critical.  Modules that do not override
        this method get an empty dict (no explicit declarations), and the
        approvals subsystem will fall back to heuristic classification.
        """
        return {}

    async def extra_status_fields(self) -> dict[str, Any]:
        """Return optional extra fields to merge into this module's status() entry.

        Called by the ``status()`` core tool for each active module.  The
        returned dict is merged into the module's entry in the ``modules``
        section of the status response (e.g. ``oauth_status``,
        ``oauth_expires_at``, ``credential_health``).

        The default implementation returns an empty dict.  Modules that track
        OAuth or external credential health should override this method to
        surface live credential state to the dashboard.
        """
        return {}
