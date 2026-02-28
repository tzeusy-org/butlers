"""Home Assistant module — MCP tools for smart-home control via Home Assistant.

Provides tools for querying entity state, calling HA services, fetching history,
and logging all issued commands. Token is resolved from owner contact_info at
startup (type='home_assistant_token'). Credentials are never logged in full.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class HomeAssistantConfig(BaseModel):
    """Configuration for the Home Assistant module.

    Attributes
    ----------
    url:
        Required base URL of the Home Assistant instance
        (e.g. ``http://homeassistant.local:8123``).
    verify_ssl:
        Whether to verify SSL certificates when using HTTPS. Defaults to
        ``False`` since many local HA installs use self-signed certs.
    websocket_ping_interval:
        Seconds between WebSocket keepalive pings. Defaults to ``30``.
    poll_interval_seconds:
        REST polling interval (seconds) used as fallback when the WebSocket
        is disconnected. Defaults to ``60``.
    snapshot_interval_seconds:
        How often (seconds) to persist the in-memory entity cache to
        ``ha_entity_snapshot``. Defaults to ``300``.
    """

    url: str
    verify_ssl: bool = False
    websocket_ping_interval: int = 30
    poll_interval_seconds: int = 60
    snapshot_interval_seconds: int = 300

    model_config = ConfigDict(extra="forbid")


class HomeAssistantModule(Module):
    """Home Assistant module providing smart-home MCP tools.

    Credentials (long-lived access token) are resolved from the owner
    contact's ``shared.contact_info`` (type ``'home_assistant_token'``)
    at startup.  The token is never written to logs in full — only the
    first 8 characters appear in debug output.
    """

    def __init__(self) -> None:
        self._config: HomeAssistantConfig | None = None
        self._token: str | None = None
        self._client: Any | None = None  # httpx.AsyncClient, imported lazily
        self._db: Any = None

    # ------------------------------------------------------------------
    # Module ABC
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "home_assistant"

    @property
    def config_schema(self) -> type[BaseModel]:
        return HomeAssistantConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return "home_assistant"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Resolve HA token, create HTTP client, and initialise state.

        Parameters
        ----------
        config:
            Module configuration (``HomeAssistantConfig`` or raw dict).
        db:
            Butler database instance (provides ``db.pool`` for asyncpg).
        credential_store:
            Optional :class:`~butlers.credential_store.CredentialStore`.
            Not used directly — the HA token is resolved exclusively from
            owner contact_info via ``resolve_owner_contact_info()``.

        Raises
        ------
        RuntimeError
            When the Home Assistant token cannot be resolved from
            ``shared.contact_info`` (the owner contact must have a
            ``home_assistant_token`` contact_info entry).
        """
        import httpx

        from butlers.credential_store import resolve_owner_contact_info

        self._config = (
            config
            if isinstance(config, HomeAssistantConfig)
            else HomeAssistantConfig(**(config or {}))
        )
        self._db = db

        # --- Resolve token from owner contact_info ---
        pool = getattr(db, "pool", None) if db is not None else None
        token: str | None = None

        if pool is not None:
            token = await resolve_owner_contact_info(pool, "home_assistant_token")

        if not token:
            raise RuntimeError(
                "Home Assistant token is not configured. "
                "Add a 'home_assistant_token' entry to the owner contact's contact_info "
                "via the dashboard or shared.contact_info table."
            )

        self._token = token
        logger.debug(
            "HomeAssistantModule: resolved HA token (prefix=%s...)",
            token[:8],
        )

        # --- Create HTTP client ---
        self._client = httpx.AsyncClient(
            base_url=self._config.url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            verify=self._config.verify_ssl,
        )

    async def on_shutdown(self) -> None:
        """Clean up: close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._token = None
        self._config = None
        self._db = None

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register Home Assistant MCP tools on the butler's FastMCP server.

        Tools are registered as closures that capture the module instance
        so they can access the HTTP client at call time.
        """
        self._config = (
            config
            if isinstance(config, HomeAssistantConfig)
            else HomeAssistantConfig(**(config or {}))
        )
        self._db = db
        module = self  # capture for closures

        async def ha_get_entity_state(entity_id: str) -> dict[str, Any] | None:
            """Return the current state of a Home Assistant entity.

            Queries the HA REST API for the given entity and returns its
            full state object, or ``None`` if the entity does not exist.

            Parameters
            ----------
            entity_id:
                HA entity ID (e.g. ``"sensor.living_room_temperature"``).
            """
            return await module._get_entity_state(entity_id)

        async def ha_list_entities(
            domain: str | None = None,
            area: str | None = None,
        ) -> list[dict[str, Any]]:
            """List Home Assistant entities, optionally filtered by domain or area.

            Parameters
            ----------
            domain:
                If provided, only entities whose ID starts with ``<domain>.``
                are included (e.g. ``"light"``).
            area:
                Area filtering is not yet implemented in this scaffold. Passing
                a value will log a warning and the filter will be ignored.
                Full area-based filtering requires the HA entity/area registry
                API and will be added in a follow-up.
            """
            return await module._list_entities(domain=domain, area=area)

        async def ha_call_service(
            domain: str,
            service: str,
            target: dict[str, Any] | None = None,
            data: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Call a Home Assistant service.

            Parameters
            ----------
            domain:
                Service domain (e.g. ``"light"``, ``"switch"``, ``"script"``).
            service:
                Service name within the domain (e.g. ``"turn_on"``).
            target:
                Optional target specification (entity_id, area_id, device_id).
            data:
                Optional service-specific data payload.
            """
            return await module._call_service(
                domain=domain, service=service, target=target, data=data
            )

        mcp.tool()(ha_get_entity_state)
        mcp.tool()(ha_list_entities)
        mcp.tool()(ha_call_service)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return the HTTP client, raising if not initialised."""
        if self._client is None:
            raise RuntimeError("HomeAssistantModule not initialised — call on_startup() first")
        return self._client

    async def _get_entity_state(self, entity_id: str) -> dict[str, Any] | None:
        """Call HA REST API to get a single entity state."""
        client = self._get_client()
        resp = await client.get(f"/api/states/{entity_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def _list_entities(
        self,
        domain: str | None = None,
        area: str | None = None,
    ) -> list[dict[str, Any]]:
        """Call HA REST API to list entity states with optional filters.

        Note: ``area`` filtering is not yet implemented. HA area membership
        requires the entity/area registry (``/api/config/entity_registry/list``),
        which is not fetched in this scaffold. Passing a non-None ``area``
        will log a warning and return all entities (possibly filtered by domain).
        """
        client = self._get_client()

        if area is not None:
            logger.warning(
                "HomeAssistantModule: area filtering is not yet implemented; "
                "ignoring area=%r and returning all entities (filtered by domain only).",
                area,
            )

        resp = await client.get("/api/states")
        resp.raise_for_status()
        states: list[dict[str, Any]] = resp.json()

        results: list[dict[str, Any]] = []
        for state in states:
            entity_id: str = state.get("entity_id", "")
            if domain is not None and not entity_id.startswith(f"{domain}."):
                continue
            attributes = state.get("attributes", {})
            results.append(
                {
                    "entity_id": entity_id,
                    "state": state.get("state"),
                    "friendly_name": attributes.get("friendly_name"),
                    "domain": entity_id.split(".")[0] if "." in entity_id else entity_id,
                    "last_updated": state.get("last_updated"),
                }
            )

        results.sort(key=lambda x: x["entity_id"])
        return results

    async def _call_service(
        self,
        domain: str,
        service: str,
        target: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a HA service via REST API."""
        client = self._get_client()
        payload: dict[str, Any] = {}
        # Merge data first so that the explicit target argument always wins if
        # data happens to also carry a "target" key.
        if data is not None:
            payload.update(data)
        if target is not None:
            payload["target"] = target
        resp = await client.post(f"/api/services/{domain}/{service}", json=payload)
        resp.raise_for_status()
        result: dict[str, Any] = resp.json() if resp.content else {}
        return result
