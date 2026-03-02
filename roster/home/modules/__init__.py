"""Home Assistant module — MCP tools for smart-home control via Home Assistant.

Provides tools for querying entity state, calling HA services, fetching history,
and logging all issued commands. Token is resolved from owner contact_info at
startup (type='home_assistant_token'). Credentials are never logged in full.

Transport layer:
- REST: httpx.AsyncClient with Bearer token, Content-Type: application/json
- WebSocket: aiohttp.ClientSession for the HA WebSocket API
  - Auth flow: auth_required → auth → auth_ok
  - Background message loop dispatching event/result/pong
  - Keepalive ping task with missed-pong detection
  - Auto-reconnect with exponential backoff (1s → 60s, with jitter)
  - REST polling fallback while WebSocket is disconnected
  - WebSocket command helper with auto-incrementing ID and response correlation
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from butlers.modules.base import Module, ToolMeta

logger = logging.getLogger(__name__)

# Matches valid HA entity IDs: "domain.object_id" where each segment contains
# only alphanumeric characters, underscores, or hyphens (no slashes or dots).
_ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")

# Matches valid HA service domain or service name (e.g. "light", "turn_on").
# Only lowercase alphanumeric characters and underscores are permitted to
# prevent path traversal attacks when constructing /api/services/<domain>/<svc>.
_HA_IDENT_RE = re.compile(r"^[a-z0-9_]+$")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WS_RECONNECT_INITIAL = 1.0  # seconds
_WS_RECONNECT_MAX = 60.0  # seconds
_WS_RECONNECT_JITTER = 0.5  # fraction of delay added as random jitter
_WS_PONG_TIMEOUT = 10.0  # seconds to wait for pong before treating as missed


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CachedEntity:
    """In-memory cached state for a single Home Assistant entity.

    Attributes
    ----------
    entity_id:
        HA entity ID (e.g. ``"sensor.living_room_temperature"``).
    state:
        Current state string (e.g. ``"on"``, ``"23.5"``).
    attributes:
        Arbitrary entity attributes from HA.
    last_changed:
        ISO 8601 timestamp of last state change.
    last_updated:
        ISO 8601 timestamp of last attribute update.
    area_id:
        Area ID from the entity registry (may be None).
    """

    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: str = ""
    last_updated: str = ""
    area_id: str | None = None


@dataclass
class CachedArea:
    """In-memory cached area from the HA area registry.

    Attributes
    ----------
    area_id:
        Unique area ID.
    name:
        Human-readable area name (e.g. ``"Living Room"``).
    """

    area_id: str
    name: str


@dataclass
class CachedEntityRegistryEntry:
    """In-memory cached entry from the HA entity registry.

    Populated from ``config/entity_registry/list`` at startup and refreshed
    on ``entity_registry_updated`` events.

    Attributes
    ----------
    entity_id:
        HA entity ID (e.g. ``"light.kitchen_ceiling"``).
    area_id:
        Area the entity is assigned to (may be ``None``).
    device_id:
        Device the entity belongs to (may be ``None``).
    platform:
        Integration platform that created the entity (e.g. ``"zha"``,
        ``"hue"``, ``"mqtt"``).  May be ``None`` for virtual entities.
    """

    entity_id: str
    area_id: str | None = None
    device_id: str | None = None
    platform: str | None = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class HomeAssistantConfig(BaseModel):
    """Configuration for the Home Assistant module.

    Attributes
    ----------
    url:
        Optional base URL of the Home Assistant instance.  Ignored at
        runtime — the URL is resolved from owner contact_info
        (type ``'home_assistant_url'``) at startup.
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

    url: str | None = None
    verify_ssl: bool = False
    websocket_ping_interval: int = 30
    poll_interval_seconds: int = 60
    snapshot_interval_seconds: int = 300
    read_only: bool = False

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class HomeAssistantModule(Module):
    """Home Assistant module providing smart-home MCP tools.

    Credentials (long-lived access token) are resolved from the owner
    contact's ``shared.contact_info`` (type ``'home_assistant_token'``)
    at startup.  The token is never written to logs in full — only the
    first 8 characters appear in debug output.

    Transport layer:
    - REST via ``httpx.AsyncClient``
    - WebSocket via ``aiohttp`` for the HA event bus subscription
    """

    def __init__(self) -> None:
        self._config: HomeAssistantConfig | None = None
        self._url: str | None = None
        self._token: str | None = None
        self._client: Any | None = None  # httpx.AsyncClient, imported lazily
        self._db: Any = None

        # ---- WebSocket state ----
        self._ws_session: Any | None = None  # aiohttp.ClientSession
        self._ws_connection: Any | None = None  # aiohttp.ClientWebSocketResponse
        self._ws_connected: bool = False
        self._ws_cmd_id: int = 0
        # Pending WS commands: id → asyncio.Future
        self._ws_pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        # Background tasks
        self._ws_loop_task: asyncio.Task[None] | None = None
        self._ws_ping_task: asyncio.Task[None] | None = None
        self._ws_reconnect_task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._snapshot_task: asyncio.Task[None] | None = None
        # Shutdown flag
        self._shutdown: bool = False
        # Last pong receipt time (monotonic)
        self._last_pong_time: float = 0.0

        # ---- Entity / registry caches ----
        # entity_id → CachedEntity
        self._entity_cache: dict[str, CachedEntity] = {}
        # area_id → CachedArea
        self._area_cache: dict[str, CachedArea] = {}
        # entity_id → CachedEntityRegistryEntry (area_id, device_id, platform)
        self._entity_registry: dict[str, CachedEntityRegistryEntry] = {}
        # Convenience alias: entity_id → area_id (derived from _entity_registry)
        self._entity_area_map: dict[str, str] = {}

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
        return ["contacts", "approvals"]

    def migration_revisions(self) -> str | None:
        return "home"

    def tool_metadata(self) -> dict[str, ToolMeta]:
        """Return approval sensitivity metadata for HA tools.

        ``ha_call_service`` has ``domain`` and ``service`` marked sensitive
        so the approvals module can classify risk dynamically (e.g., lock.unlock
        as always-require, cover.open_cover as medium).

        In read-only mode the write tools are not registered, so no approval
        metadata is needed.
        """
        if self._config and self._config.read_only:
            return {}
        return {
            "ha_call_service": ToolMeta(arg_sensitivities={"domain": True, "service": True}),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Resolve HA token, create HTTP client, connect WebSocket, seed caches.

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
        self._shutdown = False

        # --- Resolve URL and token from owner contact_info ---
        pool = getattr(db, "pool", None) if db is not None else None
        url: str | None = None
        token: str | None = None

        if pool is not None:
            url = await resolve_owner_contact_info(pool, "home_assistant_url")
            token = await resolve_owner_contact_info(pool, "home_assistant_token")

        if not url:
            raise RuntimeError(
                "Home Assistant URL is not configured. "
                "Add a 'home_assistant_url' entry (e.g. http://homeassistant.local:8123) "
                "to the owner contact's contact_info via the dashboard."
            )

        if not token:
            raise RuntimeError(
                "Home Assistant token is not configured. "
                "Add a 'home_assistant_token' entry to the owner contact's contact_info "
                "via the dashboard."
            )

        self._url = url
        self._token = token
        logger.debug(
            "HomeAssistantModule: resolved HA URL=%s, token prefix=%s...",
            url,
            token[:8],
        )

        # --- Create HTTP client ---
        self._client = httpx.AsyncClient(
            base_url=self._url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            verify=self._config.verify_ssl,
        )

        # --- Connect WebSocket and seed entity cache ---
        await self._ws_connect_and_seed()

        # --- Start periodic snapshot task ---
        self._start_snapshot_task()

    async def on_shutdown(self) -> None:
        """Clean up: close WebSocket, stop background tasks, close HTTP client.

        Writes a final entity snapshot to ``ha_entity_snapshot`` before teardown
        so that the last-known state survives across restarts.
        """
        self._shutdown = True

        # Write final snapshot before tearing down the DB connection
        try:
            await self._persist_entity_snapshot()
        except Exception as exc:
            logger.warning("HomeAssistantModule: final snapshot failed: %s", exc)

        # Cancel background tasks and await them with a short timeout to avoid
        # hanging shutdown if a task ignores CancelledError.
        _tasks = [
            t
            for t in (
                self._ws_loop_task,
                self._ws_ping_task,
                self._ws_reconnect_task,
                self._poll_task,
                self._snapshot_task,
            )
            if t is not None and not t.done()
        ]
        for task in _tasks:
            task.cancel()
        if _tasks:
            await asyncio.gather(*_tasks, return_exceptions=True)

        self._ws_loop_task = None
        self._ws_ping_task = None
        self._ws_reconnect_task = None
        self._poll_task = None
        self._snapshot_task = None

        # Fail all pending WebSocket commands
        for fut in self._ws_pending.values():
            if not fut.done():
                fut.cancel()
        self._ws_pending.clear()

        # Close WebSocket connection
        await self._ws_close()

        # Close aiohttp session
        if self._ws_session is not None:
            try:
                await self._ws_session.close()
            except Exception:
                pass
            self._ws_session = None

        # Close HTTP client
        if self._client is not None:
            await self._client.aclose()
            self._client = None

        self._url = None
        self._token = None
        self._config = None
        self._db = None
        self._ws_connected = False

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

            Serves from the in-memory entity cache when available; falls back
            to the HA REST API. Returns ``None`` if the entity does not exist.

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

            Returns compact summaries (entity_id, state, friendly_name,
            area_name, domain) sorted by entity_id.

            Parameters
            ----------
            domain:
                If provided, only entities whose ID starts with ``<domain>.``
                are included (e.g. ``"light"``).
            area:
                If provided, only entities assigned to this area name or area_id
                are included. Area filtering uses the entity and area registries
                populated at startup.
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

        async def ha_list_areas() -> list[dict[str, Any]]:
            """Return all Home Assistant areas/rooms sorted by name.

            Returns a list of area objects with ``area_id`` and ``name`` fields,
            taken from the cached area registry populated at startup.
            """
            return await module._list_areas()

        async def ha_list_services(domain: str | None = None) -> list[dict[str, Any]]:
            """Return available Home Assistant services, optionally filtered by domain.

            Queries ``GET /api/services`` when the REST client is available, or
            falls back to ``get_services`` via WebSocket.

            Parameters
            ----------
            domain:
                If provided, only services for this domain are returned
                (e.g. ``"light"``).
            """
            return await module._list_services(domain=domain)

        async def ha_get_history(
            entity_ids: list[str],
            start: str,
            end: str | None = None,
        ) -> list[list[dict[str, Any]]]:
            """Return state history for one or more entities over a time window.

            Calls ``GET /api/history/period/<start>`` with
            ``filter_entity_id``, ``end_time``, ``minimal_response``, and
            ``significant_changes_only`` query parameters.

            Parameters
            ----------
            entity_ids:
                Non-empty list of HA entity IDs to fetch history for.
            start:
                ISO 8601 start timestamp (e.g. ``"2026-02-01T00:00:00Z"``).
            end:
                Optional ISO 8601 end timestamp.  Defaults to now when omitted.

            Raises
            ------
            ValueError
                If ``entity_ids`` is empty (unbounded queries are too expensive).
            """
            return await module._get_history(entity_ids=entity_ids, start=start, end=end)

        async def ha_get_statistics(
            statistic_ids: list[str],
            start: str,
            end: str,
            period: str = "hour",
        ) -> dict[str, Any]:
            """Return aggregated statistics for sensor entities from HA's recorder.

            Sends a ``recorder/get_statistics_during_period`` WebSocket command.

            Parameters
            ----------
            statistic_ids:
                List of statistic IDs (usually entity IDs for sensor entities).
            start:
                ISO 8601 start timestamp.
            end:
                ISO 8601 end timestamp.
            period:
                Aggregation period: one of ``5minute``, ``hour``, ``day``,
                ``week``, ``month``.  Defaults to ``"hour"``.

            Raises
            ------
            ValueError
                If ``period`` is not one of the allowed values.
            """
            return await module._get_statistics(
                statistic_ids=statistic_ids, start=start, end=end, period=period
            )

        async def ha_render_template(template: str) -> str:
            """Render a Jinja2 template server-side on the Home Assistant instance.

            Calls ``POST /api/template`` with the template string and returns
            the rendered plaintext result.

            Parameters
            ----------
            template:
                Jinja2 template string (e.g. ``"{{ states('sensor.temperature') }} °C"``).
            """
            return await module._render_template(template=template)

        async def ha_activate_scene(
            entity_id: str,
            transition: float | None = None,
        ) -> dict[str, Any]:
            """Activate a Home Assistant scene.

            Convenience wrapper around ``ha_call_service`` for scene activation.
            The ``entity_id`` must start with ``"scene."``; passing any other
            domain raises ``ValueError`` to prevent accidental mis-use.

            Parameters
            ----------
            entity_id:
                Scene entity ID (e.g. ``"scene.movie_night"``).
            transition:
                Optional transition duration in seconds (not supported by all
                scenes; silently ignored by HA if the scene driver doesn't
                support it).
            """
            return await module._activate_scene(entity_id=entity_id, transition=transition)

        mcp.tool()(ha_get_entity_state)
        mcp.tool()(ha_list_entities)
        mcp.tool()(ha_list_areas)
        mcp.tool()(ha_list_services)
        mcp.tool()(ha_get_history)
        mcp.tool()(ha_get_statistics)
        mcp.tool()(ha_render_template)
        if not self._config.read_only:
            mcp.tool()(ha_call_service)
            mcp.tool()(ha_activate_scene)

    # ------------------------------------------------------------------
    # WebSocket transport — connection and authentication
    # ------------------------------------------------------------------

    def _ws_url(self) -> str:
        """Derive the WebSocket URL from the configured HA base URL.

        ``http://`` → ``ws://``, ``https://`` → ``wss://``.
        """
        assert self._url is not None
        url = self._url.rstrip("/")
        if url.startswith("https://"):
            ws_url = "wss://" + url[len("https://") :]
        elif url.startswith("http://"):
            ws_url = "ws://" + url[len("http://") :]
        else:
            ws_url = url  # already ws:// or wss://
        return ws_url + "/api/websocket"

    async def _ws_connect_and_seed(self) -> None:
        """Connect WebSocket, authenticate, seed caches, start background tasks.

        On failure the module remains operational in REST-only mode; auto-reconnect
        will retry in the background.
        """
        try:
            await self._ws_connect()
        except Exception as exc:
            logger.warning(
                "HomeAssistantModule: WebSocket connect failed (%s); "
                "falling back to REST polling and scheduling reconnect.",
                exc,
            )
            self._ws_connected = False
            self._start_poll_fallback()
            self._schedule_reconnect(delay=_WS_RECONNECT_INITIAL)
            return

        # Seed entity cache from REST (faster than WS for initial bulk load)
        await self._seed_entity_cache_from_rest()

        # Fetch registries via WebSocket
        await self._fetch_area_registry()
        await self._fetch_entity_registry()

        # Subscribe to state_changed and registry events
        await self._ws_subscribe_events()

        # Start background tasks
        self._start_ws_message_loop()
        self._start_ws_ping_task()

    async def _ws_connect(self) -> None:
        """Open WebSocket connection and complete the HA auth handshake.

        HA WebSocket auth flow:
        1. Server sends: ``{"type": "auth_required", "ha_version": "..."}``
        2. Client sends: ``{"type": "auth", "access_token": "..."}``
        3. Server replies: ``{"type": "auth_ok"}`` or ``{"type": "auth_invalid"}``

        After auth_ok, send supported_features with coalesce_messages=1.

        Raises
        ------
        RuntimeError
            If the server returns ``auth_invalid`` or an unexpected message.
        """
        import aiohttp

        assert self._config is not None
        assert self._token is not None

        ws_url = self._ws_url()
        logger.debug("HomeAssistantModule: connecting WebSocket to %s", ws_url)

        # Create aiohttp session if needed
        if self._ws_session is None or self._ws_session.closed:
            ssl_ctx: bool = self._config.verify_ssl
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self._ws_session = aiohttp.ClientSession(connector=connector)

        self._ws_connection = await self._ws_session.ws_connect(
            ws_url,
            heartbeat=None,  # we implement our own keepalive
        )

        try:
            # Step 1: expect auth_required
            msg = await self._ws_connection.receive_json(timeout=10.0)
            if msg.get("type") != "auth_required":
                raise RuntimeError(
                    f"HomeAssistantModule: expected auth_required, got: {msg.get('type')!r}"
                )

            # Step 2: send auth
            await self._ws_connection.send_json({"type": "auth", "access_token": self._token})

            # Step 3: expect auth_ok or auth_invalid
            msg = await self._ws_connection.receive_json(timeout=10.0)
            msg_type = msg.get("type")
            if msg_type == "auth_invalid":
                raise RuntimeError(
                    "HomeAssistantModule: WebSocket authentication failed (auth_invalid). "
                    "Check the home_assistant_token in owner contact_info."
                )
            if msg_type != "auth_ok":
                raise RuntimeError(
                    f"HomeAssistantModule: unexpected auth response type: {msg_type!r}"
                )
        except Exception:
            # Close the dangling connection before propagating to callers.
            await self._ws_close()
            raise

        logger.debug(
            "HomeAssistantModule: WebSocket authenticated (ha_version=%s)",
            msg.get("ha_version", "unknown"),
        )

        # Step 4: send supported_features with coalesce_messages
        self._ws_cmd_id += 1
        await self._ws_connection.send_json(
            {
                "type": "supported_features",
                "id": self._ws_cmd_id,
                "features": {"coalesce_messages": 1},
            }
        )

        self._ws_connected = True
        self._last_pong_time = asyncio.get_running_loop().time()
        logger.info("HomeAssistantModule: WebSocket connected and authenticated.")

    async def _ws_close(self) -> None:
        """Close the WebSocket connection gracefully."""
        if self._ws_connection is not None and not self._ws_connection.closed:
            try:
                await self._ws_connection.close()
            except Exception:
                pass
        self._ws_connection = None
        self._ws_connected = False

    # ------------------------------------------------------------------
    # WebSocket transport — background message loop
    # ------------------------------------------------------------------

    def _start_ws_message_loop(self) -> None:
        """Start the WebSocket message dispatch loop as a background task."""
        if self._ws_loop_task is not None and not self._ws_loop_task.done():
            return
        self._ws_loop_task = asyncio.ensure_future(self._ws_message_loop())

    async def _ws_message_loop(self) -> None:
        """Read messages from the WebSocket and dispatch by type.

        Dispatches:
        - ``event``: state_changed → update entity cache; registry updated → refresh
        - ``result``: correlate with pending WS command futures
        - ``pong``: update last pong time

        On any connection error, triggers auto-reconnect.
        """
        import aiohttp

        try:
            while not self._shutdown:
                if self._ws_connection is None or self._ws_connection.closed:
                    break

                try:
                    raw = await self._ws_connection.receive(timeout=5.0)
                except TimeoutError:
                    continue

                if raw.type == aiohttp.WSMsgType.TEXT:
                    try:
                        msg: dict[str, Any] = json.loads(raw.data)
                    except json.JSONDecodeError:
                        logger.warning("HomeAssistantModule: invalid JSON from WS: %r", raw.data)
                        continue
                    if not isinstance(msg, dict):
                        logger.debug("HomeAssistantModule: non-dict WS message: %r", type(msg))
                        continue
                    await self._dispatch_ws_message(msg)

                elif raw.type == aiohttp.WSMsgType.BINARY:
                    try:
                        msg = json.loads(raw.data)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        logger.warning("HomeAssistantModule: invalid binary WS message")
                        continue
                    if not isinstance(msg, dict):
                        logger.debug("HomeAssistantModule: non-dict binary WS msg: %r", type(msg))
                        continue
                    await self._dispatch_ws_message(msg)

                elif raw.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.ERROR,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    logger.warning(
                        "HomeAssistantModule: WebSocket closed/error (type=%s). "
                        "Scheduling reconnect.",
                        raw.type,
                    )
                    break

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("HomeAssistantModule: WebSocket message loop error: %s", exc)

        # Connection dropped — trigger reconnect unless shutting down
        if not self._shutdown:
            self._ws_connected = False
            self._start_poll_fallback()
            self._schedule_reconnect(delay=_WS_RECONNECT_INITIAL)

    async def _dispatch_ws_message(self, msg: dict[str, Any]) -> None:
        """Dispatch a single parsed WebSocket message."""
        msg_type = msg.get("type")

        if msg_type == "event":
            await self._handle_ws_event(msg)

        elif msg_type == "result":
            self._handle_ws_result(msg)

        elif msg_type == "pong":
            self._last_pong_time = asyncio.get_running_loop().time()
            logger.debug("HomeAssistantModule: received pong")

        else:
            logger.debug("HomeAssistantModule: unhandled WS message type: %r", msg_type)

    async def _handle_ws_event(self, msg: dict[str, Any]) -> None:
        """Handle a WebSocket event message.

        Supported events:
        - ``state_changed``: update entity cache
        - ``area_registry_updated``: refresh area registry
        - ``entity_registry_updated``: refresh entity registry
        """
        event = msg.get("event", {})
        event_type = event.get("event_type")

        if event_type == "state_changed":
            event_data = event.get("data", {})
            new_state = event_data.get("new_state")
            entity_id = event_data.get("entity_id", "")

            if new_state is None:
                # Entity removed
                self._entity_cache.pop(entity_id, None)
                self._entity_area_map.pop(entity_id, None)
                logger.debug("HomeAssistantModule: entity removed from cache: %s", entity_id)
            else:
                # Update or insert entity
                area_id = self._entity_area_map.get(entity_id)
                attributes = new_state.get("attributes", {})
                self._entity_cache[entity_id] = CachedEntity(
                    entity_id=entity_id,
                    state=new_state.get("state", ""),
                    attributes=attributes,
                    last_changed=new_state.get("last_changed", ""),
                    last_updated=new_state.get("last_updated", ""),
                    area_id=area_id,
                )
                logger.debug(
                    "HomeAssistantModule: cache updated for %s → %s",
                    entity_id,
                    new_state.get("state"),
                )

        elif event_type == "area_registry_updated":
            logger.debug("HomeAssistantModule: area_registry_updated event; refreshing.")
            await self._fetch_area_registry()

        elif event_type == "entity_registry_updated":
            logger.debug("HomeAssistantModule: entity_registry_updated event; refreshing.")
            await self._fetch_entity_registry()

    def _handle_ws_result(self, msg: dict[str, Any]) -> None:
        """Correlate a WS result message with a pending command future."""
        cmd_id = msg.get("id")
        if cmd_id is None:
            return
        fut = self._ws_pending.pop(cmd_id, None)
        if fut is None or fut.done():
            return
        if msg.get("success"):
            fut.set_result(msg.get("result", {}))
        else:
            error = msg.get("error", {})
            fut.set_exception(
                RuntimeError(
                    f"HomeAssistantModule: WS command {cmd_id} failed: "
                    f"{error.get('code')!r} — {error.get('message')!r}"
                )
            )

    # ------------------------------------------------------------------
    # WebSocket transport — command helper
    # ------------------------------------------------------------------

    async def _ws_command(
        self,
        command: dict[str, Any],
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Send a WebSocket command and await the correlated result.

        Assigns an auto-incrementing integer ``id`` to the command, registers
        a ``Future`` for the result, sends the message, and awaits the response.

        Parameters
        ----------
        command:
            Command dict (``type`` and any other fields). The ``id`` field
            will be overwritten with the next auto-increment value.
        timeout:
            Seconds to wait for the response before raising ``asyncio.TimeoutError``.

        Returns
        -------
        dict[str, Any]
            The ``result`` payload from the HA response.

        Raises
        ------
        RuntimeError
            If the WebSocket is not connected, or if HA returns an error response.
        asyncio.TimeoutError
            If the response does not arrive within ``timeout`` seconds.
        """
        if self._ws_connection is None or not self._ws_connected:
            raise RuntimeError("HomeAssistantModule: WebSocket not connected — cannot send command")

        self._ws_cmd_id += 1
        cmd_id = self._ws_cmd_id
        command = dict(command)
        command["id"] = cmd_id

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._ws_pending[cmd_id] = fut

        try:
            await self._ws_connection.send_json(command)
            return await asyncio.wait_for(fut, timeout=timeout)
        except Exception:
            self._ws_pending.pop(cmd_id, None)
            raise

    # ------------------------------------------------------------------
    # WebSocket transport — keepalive ping
    # ------------------------------------------------------------------

    def _start_ws_ping_task(self) -> None:
        """Start the keepalive ping task as a background asyncio task."""
        if self._ws_ping_task is not None and not self._ws_ping_task.done():
            return
        self._ws_ping_task = asyncio.ensure_future(self._ws_ping_loop())

    async def _ws_ping_loop(self) -> None:
        """Send keepalive pings and detect missed pongs.

        Sends ``{"type": "ping"}`` every ``websocket_ping_interval`` seconds.
        If a pong is not received within ``_WS_PONG_TIMEOUT`` seconds after
        a ping is sent, the connection is considered dead and auto-reconnect
        is triggered.
        """
        assert self._config is not None

        try:
            while not self._shutdown:
                await asyncio.sleep(self._config.websocket_ping_interval)
                if self._shutdown:
                    break

                if not self._ws_connected or self._ws_connection is None:
                    break

                # Record time before sending ping
                ping_sent_at = asyncio.get_running_loop().time()

                try:
                    self._ws_cmd_id += 1
                    await self._ws_connection.send_json({"type": "ping", "id": self._ws_cmd_id})
                    logger.debug("HomeAssistantModule: ping sent (id=%d)", self._ws_cmd_id)
                except Exception as exc:
                    logger.warning("HomeAssistantModule: failed to send ping: %s", exc)
                    break

                # Wait for pong — check after _WS_PONG_TIMEOUT
                await asyncio.sleep(_WS_PONG_TIMEOUT)
                if self._last_pong_time < ping_sent_at:
                    logger.warning(
                        "HomeAssistantModule: missed pong after %ss; "
                        "closing connection and reconnecting.",
                        _WS_PONG_TIMEOUT,
                    )
                    # Close and let the message loop or reconnect handle recovery
                    await self._ws_close()
                    break

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("HomeAssistantModule: ping loop error: %s", exc)

        if not self._shutdown:
            self._ws_connected = False
            self._start_poll_fallback()
            self._schedule_reconnect(delay=_WS_RECONNECT_INITIAL)

    # ------------------------------------------------------------------
    # WebSocket transport — auto-reconnect
    # ------------------------------------------------------------------

    def _schedule_reconnect(self, delay: float) -> None:
        """Schedule a reconnect attempt after ``delay`` seconds (with jitter)."""
        if self._shutdown:
            return
        if self._ws_reconnect_task is not None and not self._ws_reconnect_task.done():
            return  # reconnect already in progress
        self._ws_reconnect_task = asyncio.ensure_future(self._ws_reconnect_loop(delay))

    async def _ws_reconnect_loop(self, initial_delay: float) -> None:
        """Attempt WebSocket reconnection with exponential backoff.

        On each attempt:
        1. Wait ``delay`` seconds (with jitter)
        2. Try to connect and authenticate
        3. On success: re-seed caches, re-subscribe to events, start tasks,
           stop REST polling fallback
        4. On failure: double the delay (capped at ``_WS_RECONNECT_MAX``),
           retry

        Parameters
        ----------
        initial_delay:
            Starting backoff delay in seconds.
        """
        delay = initial_delay
        attempt = 0

        try:
            while not self._shutdown and not self._ws_connected:
                # Add jitter: delay ± (delay * jitter_fraction)
                jitter = delay * _WS_RECONNECT_JITTER * (2 * random.random() - 1)
                sleep_time = max(0.1, delay + jitter)
                logger.info(
                    "HomeAssistantModule: reconnect attempt %d in %.1fs",
                    attempt + 1,
                    sleep_time,
                )
                await asyncio.sleep(sleep_time)

                if self._shutdown:
                    break

                try:
                    await self._ws_connect()
                except Exception as exc:
                    logger.warning(
                        "HomeAssistantModule: reconnect attempt %d failed: %s",
                        attempt + 1,
                        exc,
                    )
                    delay = min(delay * 2, _WS_RECONNECT_MAX)
                    attempt += 1
                    continue

                # Reconnected — rehydrate state
                logger.info(
                    "HomeAssistantModule: WebSocket reconnected after %d attempt(s).",
                    attempt + 1,
                )
                try:
                    await self._seed_entity_cache_from_rest()
                    await self._fetch_area_registry()
                    await self._fetch_entity_registry()
                    await self._ws_subscribe_events()
                except Exception as exc:
                    logger.warning(
                        "HomeAssistantModule: error rehydrating state after reconnect: %s",
                        exc,
                    )

                # Stop polling fallback, start WS tasks
                self._stop_poll_fallback()
                self._start_ws_message_loop()
                self._start_ws_ping_task()
                break

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("HomeAssistantModule: reconnect loop error: %s", exc)

    # ------------------------------------------------------------------
    # REST polling fallback
    # ------------------------------------------------------------------

    def _start_poll_fallback(self) -> None:
        """Start the REST polling fallback task if not already running."""
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._poll_task = asyncio.ensure_future(self._poll_loop())

    def _stop_poll_fallback(self) -> None:
        """Cancel the REST polling fallback task."""
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = None

    async def _poll_loop(self) -> None:
        """Poll ``GET /api/states`` periodically while WebSocket is down.

        Replaces the full entity cache on each poll cycle. Stops once the
        WebSocket reconnects (``_ws_connected`` becomes True).
        """
        assert self._config is not None

        try:
            while not self._shutdown and not self._ws_connected:
                await asyncio.sleep(self._config.poll_interval_seconds)
                if self._shutdown or self._ws_connected:
                    break

                try:
                    await self._seed_entity_cache_from_rest()
                    logger.debug("HomeAssistantModule: REST poll refreshed entity cache.")
                except Exception as exc:
                    logger.warning("HomeAssistantModule: REST poll failed: %s", exc)
        except asyncio.CancelledError:
            return

    # ------------------------------------------------------------------
    # Cache seeding and registry fetching
    # ------------------------------------------------------------------

    async def _seed_entity_cache_from_rest(self) -> None:
        """Populate the entity cache from ``GET /api/states``."""
        if self._client is None:
            return
        resp = await self._client.get("/api/states")
        resp.raise_for_status()
        states: list[dict[str, Any]] = resp.json()

        new_cache: dict[str, CachedEntity] = {}
        for state in states:
            entity_id = state.get("entity_id", "")
            if not entity_id:
                continue
            area_id = self._entity_area_map.get(entity_id)
            attributes = state.get("attributes", {})
            new_cache[entity_id] = CachedEntity(
                entity_id=entity_id,
                state=state.get("state", ""),
                attributes=attributes,
                last_changed=state.get("last_changed", ""),
                last_updated=state.get("last_updated", ""),
                area_id=area_id,
            )

        self._entity_cache = new_cache
        logger.debug("HomeAssistantModule: seeded entity cache with %d entities.", len(new_cache))

    async def _fetch_area_registry(self) -> None:
        """Fetch area registry via WebSocket and populate ``_area_cache``.

        Uses the ``config/area_registry/list`` WS command. Falls back silently
        if the WebSocket is not connected.
        """
        if not self._ws_connected:
            return
        try:
            result = await self._ws_command({"type": "config/area_registry/list"}, timeout=10.0)
            areas: list[dict[str, Any]] = result if isinstance(result, list) else []
            self._area_cache = {
                a["area_id"]: CachedArea(area_id=a["area_id"], name=a.get("name", ""))
                for a in areas
                if "area_id" in a
            }
            logger.debug(
                "HomeAssistantModule: fetched %d areas from registry.", len(self._area_cache)
            )
        except Exception as exc:
            logger.warning("HomeAssistantModule: failed to fetch area registry: %s", exc)

    async def _fetch_entity_registry(self) -> None:
        """Fetch entity registry via WebSocket and populate ``_entity_area_map``.

        Uses the ``config/entity_registry/list`` WS command. Updates
        ``area_id`` in existing ``_entity_cache`` entries. Falls back silently
        if the WebSocket is not connected.
        """
        if not self._ws_connected:
            return
        try:
            result = await self._ws_command({"type": "config/entity_registry/list"}, timeout=10.0)
            entities: list[dict[str, Any]] = result if isinstance(result, list) else []
            new_registry: dict[str, CachedEntityRegistryEntry] = {}
            for ent in entities:
                eid = ent.get("entity_id")
                if not eid:
                    continue
                new_registry[eid] = CachedEntityRegistryEntry(
                    entity_id=eid,
                    area_id=ent.get("area_id") or None,
                    device_id=ent.get("device_id") or None,
                    platform=ent.get("platform") or None,
                )
            self._entity_registry = new_registry
            # Derive area map from registry (convenience alias)
            self._entity_area_map = {
                eid: entry.area_id for eid, entry in self._entity_registry.items() if entry.area_id
            }

            # Back-fill area_id into existing cached entities
            for eid, cached in self._entity_cache.items():
                cached.area_id = self._entity_area_map.get(eid)

            logger.debug(
                "HomeAssistantModule: entity registry loaded; %d entries, %d area-mapped.",
                len(self._entity_registry),
                len(self._entity_area_map),
            )
        except Exception as exc:
            logger.warning("HomeAssistantModule: failed to fetch entity registry: %s", exc)

    async def _ws_subscribe_events(self) -> None:
        """Subscribe to state_changed, area_registry_updated, entity_registry_updated events."""
        if not self._ws_connected:
            return
        events_to_subscribe = [
            "state_changed",
            "area_registry_updated",
            "entity_registry_updated",
        ]
        for event_type in events_to_subscribe:
            try:
                await self._ws_command(
                    {
                        "type": "subscribe_events",
                        "event_type": event_type,
                    },
                    timeout=5.0,
                )
                logger.debug("HomeAssistantModule: subscribed to %s events.", event_type)
            except Exception as exc:
                logger.warning(
                    "HomeAssistantModule: failed to subscribe to %s: %s",
                    event_type,
                    exc,
                )

    # ------------------------------------------------------------------
    # Internal helpers — REST
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return the HTTP client, raising if not initialised."""
        if self._client is None:
            raise RuntimeError("HomeAssistantModule not initialised — call on_startup() first")
        return self._client

    async def _get_entity_state(self, entity_id: str) -> dict[str, Any] | None:
        """Return entity state, preferring the in-memory cache.

        Falls back to ``GET /api/states/<entity_id>`` when the entity is not
        in the cache.  Returns ``None`` for 404.

        Raises
        ------
        ValueError
            If ``entity_id`` does not match the expected ``domain.object_id``
            format, to prevent path traversal into unintended HA API endpoints.
        """
        if not _ENTITY_ID_RE.match(entity_id):
            raise ValueError(
                f"Invalid entity_id {entity_id!r}: must be 'domain.object_id' "
                f"containing only lowercase alphanumeric characters and underscores."
            )

        # Serve from cache when available
        cached = self._entity_cache.get(entity_id)
        if cached is not None:
            area_name: str | None = None
            if cached.area_id and cached.area_id in self._area_cache:
                area_name = self._area_cache[cached.area_id].name
            return {
                "entity_id": cached.entity_id,
                "state": cached.state,
                "attributes": cached.attributes,
                "last_changed": cached.last_changed,
                "last_updated": cached.last_updated,
                "area_name": area_name,
            }

        # Cache miss — fall back to REST
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
        """List entity summaries from the cache with optional domain/area filtering.

        If the cache is empty, falls back to ``GET /api/states``.

        Parameters
        ----------
        domain:
            Optional domain prefix filter (e.g. ``"light"``).
        area:
            Optional area name or area_id filter. Matched against the area
            registry; entities not assigned to any area are excluded when
            this filter is specified.
        """
        if self._entity_cache:
            return self._list_entities_from_cache(domain=domain, area=area)

        # No cache — fall back to REST (area filtering not possible without registry)
        client = self._get_client()

        if area is not None:
            logger.warning(
                "HomeAssistantModule: area filtering requires WebSocket registry; "
                "ignoring area=%r and returning entities filtered by domain only.",
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
                    "area_name": None,
                    "domain": entity_id.split(".")[0] if "." in entity_id else entity_id,
                    "last_updated": state.get("last_updated"),
                }
            )

        results.sort(key=lambda x: x["entity_id"])
        return results

    def _list_entities_from_cache(
        self,
        domain: str | None = None,
        area: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build entity summaries from the in-memory cache."""
        # Resolve area filter to an area_id if possible
        filter_area_id: str | None = None
        if area is not None:
            # Try direct area_id lookup first, then by name
            if area in self._area_cache:
                filter_area_id = area
            else:
                for a in self._area_cache.values():
                    if a.name.lower() == area.lower():
                        filter_area_id = a.area_id
                        break
            if filter_area_id is None:
                logger.warning(
                    "HomeAssistantModule: area %r not found in registry; returning empty list.",
                    area,
                )
                return []

        results: list[dict[str, Any]] = []
        for cached in self._entity_cache.values():
            entity_id = cached.entity_id

            # Domain filter
            if domain is not None and not entity_id.startswith(f"{domain}."):
                continue

            # Area filter
            if filter_area_id is not None and cached.area_id != filter_area_id:
                continue

            area_name: str | None = None
            if cached.area_id and cached.area_id in self._area_cache:
                area_name = self._area_cache[cached.area_id].name

            results.append(
                {
                    "entity_id": entity_id,
                    "state": cached.state,
                    "friendly_name": cached.attributes.get("friendly_name"),
                    "area_name": area_name,
                    "domain": entity_id.split(".")[0] if "." in entity_id else entity_id,
                    "last_updated": cached.last_updated,
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
        """Call a HA service via REST API and log the call to ``ha_command_log``.

        Both successful and failed service calls are logged.  On failure the
        exception is re-raised after logging so callers still see the error.

        Parameters
        ----------
        domain:
            Service domain (e.g. ``"light"``).
        service:
            Service name within the domain (e.g. ``"turn_on"``).
        target:
            Optional target specification dict.
        data:
            Optional service-specific payload dict.

        Returns
        -------
        dict[str, Any]
            Parsed JSON response from Home Assistant (may be ``{}`` for
            services that return no body).

        Raises
        ------
        httpx.HTTPStatusError
            If HA returns a non-2xx response.
        RuntimeError
            If the HTTP client has not been initialised (module not started).
        """
        # Validate domain and service to prevent path traversal via the URL.
        if not _HA_IDENT_RE.match(domain):
            raise ValueError(
                f"Invalid service domain {domain!r}: must contain only lowercase "
                "alphanumeric characters and underscores."
            )
        if not _HA_IDENT_RE.match(service):
            raise ValueError(
                f"Invalid service name {service!r}: must contain only lowercase "
                "alphanumeric characters and underscores."
            )

        client = self._get_client()
        payload: dict[str, Any] = {}
        # Merge data first so that the explicit target argument always wins if
        # data happens to also carry a "target" key.
        if data is not None:
            payload.update(data)
        if target is not None:
            payload["target"] = target

        result: dict[str, Any] = {}
        context_id: str | None = None
        exc_to_raise: Exception | None = None

        try:
            resp = await client.post(f"/api/services/{domain}/{service}", json=payload)
            resp.raise_for_status()
            result = resp.json() if resp.content else {}
            # HA embeds the event context ID in the first state-change result entry.
            if isinstance(result, list) and result and isinstance(result[0], dict):
                context_id = result[0].get("context", {}).get("id")
            elif isinstance(result, dict):
                context_id = result.get("context", {}).get("id")
        except Exception as exc:
            exc_to_raise = exc
            result = {"error": str(exc)}

        # Log the command (fire-and-forget with error swallowing so a DB issue
        # never prevents the caller from seeing a service error).
        try:
            await self._log_command(
                domain=domain,
                service=service,
                target=target,
                data=data,
                result=result if not exc_to_raise else None,
                error_result=result if exc_to_raise else None,
                context_id=context_id,
            )
        except Exception as log_exc:
            logger.warning(
                "HomeAssistantModule: failed to log command %s.%s: %s",
                domain,
                service,
                log_exc,
            )

        if exc_to_raise is not None:
            raise exc_to_raise

        return result

    async def _list_areas(self) -> list[dict[str, Any]]:
        """Return all cached areas sorted by name.

        Returns a list of ``{"area_id": ..., "name": ...}`` dicts derived from
        the in-memory area registry.  The list is sorted alphabetically by
        ``name``.
        """
        areas = [{"area_id": a.area_id, "name": a.name} for a in self._area_cache.values()]
        areas.sort(key=lambda x: x["name"])
        return areas

    async def _list_services(self, domain: str | None = None) -> list[dict[str, Any]]:
        """Return available HA services, optionally filtered by domain.

        Prefers ``GET /api/services`` (REST) when the HTTP client is available.
        Falls back to the ``get_services`` WebSocket command when the REST
        client is not yet initialised.

        Parameters
        ----------
        domain:
            Optional domain filter (e.g. ``"light"``).

        Returns
        -------
        list[dict[str, Any]]
            Each element has ``"domain"`` (str) and ``"services"``
            (dict[str, dict]) keys, matching the HA API response shape.
        """
        raw: list[dict[str, Any]]

        if self._client is not None:
            resp = await self._client.get("/api/services")
            resp.raise_for_status()
            raw = resp.json()
        elif self._ws_connected:
            result = await self._ws_command({"type": "get_services"}, timeout=10.0)
            # WS get_services returns dict[domain, dict[service_name, service_obj]]
            # Convert to the same shape as the REST response.
            raw = [{"domain": d, "services": svcs} for d, svcs in result.items()]
        else:
            raise RuntimeError(
                "HomeAssistantModule: cannot list services — neither REST client "
                "nor WebSocket is available."
            )

        if domain is not None:
            raw = [entry for entry in raw if entry.get("domain") == domain]

        return raw

    async def _get_history(
        self,
        entity_ids: list[str],
        start: str,
        end: str | None = None,
    ) -> list[list[dict[str, Any]]]:
        """Fetch entity state history from ``GET /api/history/period/<start>``.

        Parameters
        ----------
        entity_ids:
            Non-empty list of entity IDs to query.
        start:
            ISO 8601 start timestamp.
        end:
            Optional ISO 8601 end timestamp.

        Raises
        ------
        ValueError
            If ``entity_ids`` is empty, or if ``start`` is not a valid ISO 8601
            timestamp (prevents path traversal via the URL-path segment).
        """
        if not entity_ids:
            raise ValueError(
                "ha_get_history requires at least one entity_id. "
                "Unbounded history queries are not supported."
            )

        if not self._ISO8601_RE.match(start):
            raise ValueError(
                f"Invalid start timestamp {start!r}: must be ISO 8601 format "
                "(e.g. '2026-02-01T00:00:00Z' or '2026-02-01T00:00:00+01:00')."
            )

        client = self._get_client()
        params: dict[str, str] = {
            "filter_entity_id": ",".join(entity_ids),
            "minimal_response": "1",
            "significant_changes_only": "1",
        }
        if end is not None:
            params["end_time"] = end

        resp = await client.get(f"/api/history/period/{quote(start, safe=':')}", params=params)
        resp.raise_for_status()
        result: list[list[dict[str, Any]]] = resp.json()
        return result

    # Matches ISO 8601 timestamps with optional fractional seconds and timezone.
    # Allows: 2026-02-01T12:00:00Z, 2026-02-01T12:00:00+01:00, 2026-02-01T12:00:00.123Z
    _ISO8601_RE = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
    )

    _VALID_STATISTICS_PERIODS = frozenset({"5minute", "hour", "day", "week", "month"})

    async def _get_statistics(
        self,
        statistic_ids: list[str],
        start: str,
        end: str,
        period: str = "hour",
    ) -> dict[str, Any]:
        """Fetch aggregated statistics via the ``recorder/get_statistics_during_period`` WS command.

        Parameters
        ----------
        statistic_ids:
            List of statistic IDs (usually entity IDs for sensor entities).
        start:
            ISO 8601 start timestamp.
        end:
            ISO 8601 end timestamp.
        period:
            One of ``5minute``, ``hour``, ``day``, ``week``, ``month``.

        Raises
        ------
        ValueError
            If ``period`` is not one of the valid values.
        RuntimeError
            If the WebSocket is not connected.
        """
        if period not in self._VALID_STATISTICS_PERIODS:
            raise ValueError(
                f"Invalid period {period!r}. "
                f"Must be one of: {', '.join(sorted(self._VALID_STATISTICS_PERIODS))}."
            )

        result = await self._ws_command(
            {
                "type": "recorder/get_statistics_during_period",
                "statistic_ids": statistic_ids,
                "start_time": start,
                "end_time": end,
                "period": period,
                "types": ["mean", "min", "max", "sum", "state"],
            },
            timeout=30.0,
        )
        return result

    async def _render_template(self, template: str) -> str:
        """Render a Jinja2 template via ``POST /api/template``.

        Parameters
        ----------
        template:
            Jinja2 template string to evaluate server-side.

        Returns
        -------
        str
            The rendered plaintext result from HA.
        """
        client = self._get_client()
        resp = await client.post("/api/template", json={"template": template})
        resp.raise_for_status()
        return resp.text

    async def _log_command(
        self,
        domain: str,
        service: str,
        target: dict[str, Any] | None,
        data: dict[str, Any] | None,
        result: dict[str, Any] | None,
        error_result: dict[str, Any] | None = None,
        context_id: str | None = None,
    ) -> None:
        """Insert a row into ``ha_command_log`` for every service call.

        Uses the DB pool directly (``asyncpg``).  Silently skips when no
        pool is available (e.g. tests that bypass ``on_startup``).

        Parameters
        ----------
        domain:
            HA service domain.
        service:
            HA service name.
        target:
            Service call target dict (may be ``None``).
        data:
            Service call data payload (may be ``None``).
        result:
            Parsed HA response on success (may be ``None`` when an error
            occurred before a response was received).
        error_result:
            Error payload when the call failed (mutually exclusive with
            ``result``).
        context_id:
            HA event context ID extracted from the response (may be ``None``).
        """
        import json as _json

        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        log_result = result if result is not None else error_result

        await pool.execute(
            """
            INSERT INTO ha_command_log
                (domain, service, target, data, result, context_id)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6)
            """,
            domain,
            service,
            _json.dumps(target) if target is not None else None,
            _json.dumps(data) if data is not None else None,
            _json.dumps(log_result) if log_result is not None else None,
            context_id,
        )
        logger.debug(
            "HomeAssistantModule: logged command %s.%s (context=%s)",
            domain,
            service,
            context_id,
        )

    async def _activate_scene(
        self,
        entity_id: str,
        transition: float | None = None,
    ) -> dict[str, Any]:
        """Activate a HA scene, delegating to ``_call_service``.

        Parameters
        ----------
        entity_id:
            Scene entity ID; must begin with ``"scene."`` to prevent
            accidental misuse (e.g. calling a cover or lock service).
        transition:
            Optional transition duration in seconds.

        Returns
        -------
        dict[str, Any]
            Response from the underlying ``ha_call_service`` call.

        Raises
        ------
        ValueError
            If ``entity_id`` does not start with ``"scene."`` or is not a
            valid HA entity ID format.
        """
        if not entity_id.startswith("scene."):
            raise ValueError(
                f"ha_activate_scene requires a scene entity_id (must start with 'scene.'), "
                f"got: {entity_id!r}"
            )
        if not _ENTITY_ID_RE.match(entity_id):
            raise ValueError(
                f"Invalid entity_id {entity_id!r}: must be 'domain.object_id' "
                f"containing only lowercase alphanumeric characters and underscores."
            )

        call_data: dict[str, Any] = {}
        if transition is not None:
            call_data["transition"] = transition

        return await self._call_service(
            domain="scene",
            service="turn_on",
            target={"entity_id": entity_id},
            data=call_data if call_data else None,
        )

    # ------------------------------------------------------------------
    # Snapshot persistence
    # ------------------------------------------------------------------

    def _start_snapshot_task(self) -> None:
        """Start the periodic entity snapshot persistence task."""
        if self._snapshot_task is not None and not self._snapshot_task.done():
            return
        self._snapshot_task = asyncio.ensure_future(self._snapshot_loop())

    async def _snapshot_loop(self) -> None:
        """Persist entity cache to ``ha_entity_snapshot`` at configured intervals.

        Runs until ``_shutdown`` is set.  On each cycle, waits
        ``snapshot_interval_seconds``, then calls ``_persist_entity_snapshot``.
        Errors are logged and do not abort the loop.
        """
        assert self._config is not None

        try:
            while not self._shutdown:
                await asyncio.sleep(self._config.snapshot_interval_seconds)
                if self._shutdown:
                    break
                try:
                    await self._persist_entity_snapshot()
                except Exception as exc:
                    logger.warning("HomeAssistantModule: snapshot persistence failed: %s", exc)
        except asyncio.CancelledError:
            return

    async def _persist_entity_snapshot(self) -> None:
        """UPSERT the current entity cache into ``ha_entity_snapshot``.

        Each entity is written as a single row keyed by ``entity_id``.
        Uses ``ON CONFLICT (entity_id) DO UPDATE`` so that existing rows
        are refreshed rather than duplicated.

        Silently skips when no DB pool is available.
        """
        import json as _json

        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        entities = list(self._entity_cache.values())
        if not entities:
            logger.debug("HomeAssistantModule: entity cache empty; skipping snapshot.")
            return

        # Bulk UPSERT via executemany for efficiency
        rows = [
            (
                e.entity_id,
                e.state,
                _json.dumps(e.attributes),
                e.last_updated if e.last_updated else None,
            )
            for e in entities
        ]

        await pool.executemany(
            """
            INSERT INTO ha_entity_snapshot (entity_id, state, attributes, last_updated)
            VALUES ($1, $2, $3::jsonb, $4::timestamptz)
            ON CONFLICT (entity_id) DO UPDATE SET
                state        = EXCLUDED.state,
                attributes   = EXCLUDED.attributes,
                last_updated = EXCLUDED.last_updated,
                captured_at  = now()
            """,
            rows,
        )
        logger.debug(
            "HomeAssistantModule: persisted snapshot of %d entities.",
            len(entities),
        )
