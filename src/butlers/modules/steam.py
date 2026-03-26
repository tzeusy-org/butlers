"""Steam module — MCP tools for Steam profile, library, and social data.

Wraps ``SteamAPIClient`` as a butler module with 9 read-only MCP tools:
- steam_get_player_summary   — profile info
- steam_get_owned_games      — game library with playtime
- steam_get_recently_played  — recently played games
- steam_get_achievements     — per-game achievements
- steam_get_friend_list      — friends with optional batch enrich
- steam_get_game_news        — public game news
- steam_get_player_level     — Steam XP level
- steam_get_current_players  — live player count (public)
- steam_resolve_vanity_url   — resolve vanity URL → SteamID

Credentials are resolved via the primary Steam account's companion entity in
``public.entity_info`` (type='steam_api_key').  Missing credentials produce
actionable error messages rather than exceptions.  Privacy errors are returned
as structured ``{"error", "message", "hint"}`` dicts.

Configured via ``[modules.steam]`` in ``butler.toml``.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict

from butlers.modules.base import Module, ToolMeta
from butlers.steam.client import SteamAPIClient, SteamAPIError, SteamRateLimitError
from butlers.steam_account_registry import MissingSteamCredentialsError, resolve_steam_account

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FRIEND_ENRICH_BATCH = 100

# ---------------------------------------------------------------------------
# Sentinel error builders
# ---------------------------------------------------------------------------


def _no_credentials_error() -> dict[str, Any]:
    return {
        "error": "steam_not_connected",
        "message": "No Steam account is connected.",
        "hint": "Connect a Steam account via the dashboard settings and set it as primary.",
    }


def _rate_limited_error(retry_after_s: float) -> dict[str, Any]:
    return {
        "error": "steam_rate_limited",
        "message": f"Steam API rate limited. Retry after {retry_after_s:.0f}s.",
        "hint": "Wait before retrying. Steam enforces strict rate limits on API key calls.",
    }


def _privacy_error(detail: str) -> dict[str, Any]:
    return {
        "error": "steam_privacy",
        "message": f"Steam privacy settings blocked the request: {detail}",
        "hint": (
            "The target profile is set to private or friends-only. "
            "Ask the user to make their profile public, or use your own SteamID."
        ),
    }


def _api_error(status_code: int, body: str) -> dict[str, Any]:
    return {
        "error": "steam_api_error",
        "message": f"Steam API returned HTTP {status_code}: {body[:200]}",
        "hint": "Check Steam API status at https://steamstat.us/ and retry.",
    }


def _handle_steam_error(exc: Exception) -> dict[str, Any]:
    """Convert a ``SteamAPIClient`` exception to a structured error dict."""
    if isinstance(exc, SteamRateLimitError):
        return _rate_limited_error(exc.retry_after_s)
    if isinstance(exc, SteamAPIError):
        if exc.status_code == 401 or (exc.status_code == 403 and "Forbidden" in exc.body):
            return _privacy_error(exc.body[:200])
        return _api_error(exc.status_code, exc.body)
    return {
        "error": "steam_unexpected_error",
        "message": f"Unexpected error: {exc}",
        "hint": "Retry the operation. If the problem persists, check the daemon logs.",
    }


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class SteamModuleConfig(BaseModel):
    """Configuration for the Steam module."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Helper: fetch API key from entity_info
# ---------------------------------------------------------------------------


async def _fetch_api_key(pool: Any, entity_id: Any) -> str | None:
    """Fetch the Steam API key from ``public.entity_info`` for the given entity."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT value FROM public.entity_info
            WHERE entity_id = $1 AND type = 'steam_api_key'
            LIMIT 1
            """,
            entity_id,
        )
    return row["value"] if row else None


# ---------------------------------------------------------------------------
# Module implementation
# ---------------------------------------------------------------------------


class SteamModule(Module):
    """Steam module providing 9 read-only MCP tools.

    On startup, resolves the primary Steam account and fetches its API key.
    All tools degrade gracefully when no credentials are available.
    """

    def __init__(self) -> None:
        self._client: SteamAPIClient | None = None
        self._primary_steam_id: str | None = None
        self._credentials_ok: bool = False

    @property
    def name(self) -> str:
        return "steam"

    @property
    def config_schema(self) -> type[BaseModel]:
        return SteamModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        """Resolve the primary Steam account and open the API client.

        Parameters
        ----------
        config:
            Module configuration (``SteamModuleConfig`` or raw dict — ignored,
            no module-level settings yet).
        db:
            Butler database instance providing ``db.pool`` for asyncpg queries.
        credential_store:
            Unused by this module — the API key is fetched from
            ``public.entity_info`` via the primary account's companion entity.
        blob_store:
            Unused by this module.
        """
        self._credentials_ok = False
        self._primary_steam_id = None
        self._client = None

        if db is None:
            logger.warning("Steam module: no database available, tools will be disabled.")
            return

        pool = getattr(db, "pool", None)
        if pool is None:
            logger.warning("Steam module: db.pool is None, tools will be disabled.")
            return

        try:
            account = await resolve_steam_account(pool)
        except MissingSteamCredentialsError:
            logger.warning(
                "Steam module: no primary Steam account configured. "
                "Connect one via the dashboard to enable Steam tools."
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Steam module: failed to resolve primary account — %s", exc)
            return

        api_key = await _fetch_api_key(pool, account.entity_id)
        if not api_key:
            logger.warning(
                "Steam module: primary account (steam_id=%s) has no API key stored. "
                "Re-connect the account via the dashboard to store the API key.",
                account.steam_id,
            )
            return

        self._primary_steam_id = str(account.steam_id)
        client = SteamAPIClient(api_key=api_key)
        await client.open()
        self._client = client
        self._credentials_ok = True
        logger.info(
            "Steam module: connected (steam_id=%s, display_name=%r)",
            account.steam_id,
            account.display_name,
        )

    async def on_shutdown(self) -> None:
        """Close the ``SteamAPIClient`` HTTP client."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Tool metadata (all tools are read-only)
    # ------------------------------------------------------------------

    def tool_metadata(self) -> dict[str, ToolMeta]:
        read_tools = {
            "steam_get_player_summary",
            "steam_get_owned_games",
            "steam_get_recently_played",
            "steam_get_achievements",
            "steam_get_friend_list",
            "steam_get_game_news",
            "steam_get_player_level",
            "steam_get_current_players",
            "steam_resolve_vanity_url",
        }
        return {name: ToolMeta(arg_sensitivities={"_write": False}) for name in read_tools}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_steam_id(self, steam_id: str | None) -> str | None:
        """Return steam_id if given, else fall back to the primary account's SteamID."""
        return steam_id or self._primary_steam_id

    # ------------------------------------------------------------------
    # register_tools
    # ------------------------------------------------------------------

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all 9 Steam MCP tools on the FastMCP server."""
        module = self  # capture for closures

        # ----------------------------------------------------------------
        # Tool 1: steam_get_player_summary
        # ----------------------------------------------------------------

        async def steam_get_player_summary(steam_id: str | None = None) -> dict[str, Any]:
            """Get Steam player profile info (name, avatar, status, visibility).

            Args:
                steam_id: SteamID64 string. Defaults to the primary connected account.
            """
            if not module._credentials_ok or module._client is None:
                return _no_credentials_error()
            sid = module._resolve_steam_id(steam_id)
            if not sid:
                return _no_credentials_error()
            try:
                players = await module._client.get_player_summaries([sid])
                if not players:
                    return _privacy_error(f"No player data returned for SteamID {sid}.")
                return {"player": players[0]}
            except Exception as exc:  # noqa: BLE001
                return _handle_steam_error(exc)

        mcp.tool()(steam_get_player_summary)

        # ----------------------------------------------------------------
        # Tool 2: steam_get_owned_games
        # ----------------------------------------------------------------

        async def steam_get_owned_games(
            steam_id: str | None = None,
            include_appinfo: bool = True,
            include_free_games: bool = True,
        ) -> dict[str, Any]:
            """Get the game library for a Steam account, including playtime.

            Args:
                steam_id: SteamID64 string. Defaults to the primary connected account.
                include_appinfo: Include game name and icon URL (default true).
                include_free_games: Include free-to-play games in results (default true).
            """
            if not module._credentials_ok or module._client is None:
                return _no_credentials_error()
            sid = module._resolve_steam_id(steam_id)
            if not sid:
                return _no_credentials_error()
            try:
                result = await module._client.request(
                    "IPlayerService",
                    "GetOwnedGames",
                    params={
                        "steamid": sid,
                        "include_appinfo": int(include_appinfo),
                        "include_played_free_games": int(include_free_games),
                    },
                )
                if not result:
                    return _privacy_error(f"Game library is private for SteamID {sid}.")
                return result
            except Exception as exc:  # noqa: BLE001
                return _handle_steam_error(exc)

        mcp.tool()(steam_get_owned_games)

        # ----------------------------------------------------------------
        # Tool 3: steam_get_recently_played
        # ----------------------------------------------------------------

        async def steam_get_recently_played(
            steam_id: str | None = None,
            count: int = 10,
        ) -> dict[str, Any]:
            """Get games played in the last 2 weeks for a Steam account.

            Args:
                steam_id: SteamID64 string. Defaults to the primary connected account.
                count: Maximum number of games to return (default 10, max 50).
            """
            if not module._credentials_ok or module._client is None:
                return _no_credentials_error()
            sid = module._resolve_steam_id(steam_id)
            if not sid:
                return _no_credentials_error()
            try:
                result = await module._client.request(
                    "IPlayerService",
                    "GetRecentlyPlayedGames",
                    params={"steamid": sid, "count": min(count, 50)},
                )
                if result is None:
                    return _privacy_error(f"Recently played data is private for SteamID {sid}.")
                return result
            except Exception as exc:  # noqa: BLE001
                return _handle_steam_error(exc)

        mcp.tool()(steam_get_recently_played)

        # ----------------------------------------------------------------
        # Tool 4: steam_get_achievements
        # ----------------------------------------------------------------

        async def steam_get_achievements(
            app_id: int,
            steam_id: str | None = None,
        ) -> dict[str, Any]:
            """Get achievements for a specific game for a Steam account.

            Args:
                app_id: Steam AppID of the game (e.g. 730 for CS2).
                steam_id: SteamID64 string. Defaults to the primary connected account.
            """
            if not module._credentials_ok or module._client is None:
                return _no_credentials_error()
            sid = module._resolve_steam_id(steam_id)
            if not sid:
                return _no_credentials_error()
            try:
                result = await module._client.request(
                    "ISteamUserStats",
                    "GetPlayerAchievements",
                    params={"steamid": sid, "appid": app_id},
                )
                return result
            except SteamAPIError as exc:
                if exc.status_code == 400:
                    return _privacy_error(
                        f"Achievements are private or the game ({app_id}) has none."
                    )
                return _handle_steam_error(exc)
            except Exception as exc:  # noqa: BLE001
                return _handle_steam_error(exc)

        mcp.tool()(steam_get_achievements)

        # ----------------------------------------------------------------
        # Tool 5: steam_get_friend_list
        # ----------------------------------------------------------------

        async def steam_get_friend_list(
            steam_id: str | None = None,
            enrich: bool = False,
        ) -> dict[str, Any]:
            """Get the friend list for a Steam account.

            Args:
                steam_id: SteamID64 string. Defaults to the primary connected account.
                enrich: Batch-fetch player summaries for each friend (default false).
                        Batches at 100 SteamIDs per API call.
            """
            if not module._credentials_ok or module._client is None:
                return _no_credentials_error()
            sid = module._resolve_steam_id(steam_id)
            if not sid:
                return _no_credentials_error()
            try:
                result = await module._client.request(
                    "ISteamUser",
                    "GetFriendList",
                    params={"steamid": sid, "relationship": "friend"},
                )
                friends = result.get("friendslist", {}).get("friends", [])
                if enrich and friends:
                    friend_ids = [f["steamid"] for f in friends]
                    # Batch in chunks of _FRIEND_ENRICH_BATCH (100)
                    summaries: list[dict[str, Any]] = []
                    for i in range(0, len(friend_ids), _FRIEND_ENRICH_BATCH):
                        batch = friend_ids[i : i + _FRIEND_ENRICH_BATCH]
                        batch_result = await module._client.get_player_summaries(batch)
                        summaries.extend(batch_result)
                    summary_map = {p["steamid"]: p for p in summaries}
                    for friend in friends:
                        friend["summary"] = summary_map.get(friend["steamid"])
                return {"friends": friends, "count": len(friends)}
            except SteamAPIError as exc:
                if exc.status_code == 401:
                    return _privacy_error(f"Friend list is private for SteamID {sid}.")
                return _handle_steam_error(exc)
            except Exception as exc:  # noqa: BLE001
                return _handle_steam_error(exc)

        mcp.tool()(steam_get_friend_list)

        # ----------------------------------------------------------------
        # Tool 6: steam_get_game_news
        # ----------------------------------------------------------------

        async def steam_get_game_news(
            app_id: int,
            count: int = 5,
            max_length: int = 300,
        ) -> dict[str, Any]:
            """Get recent news for a Steam game (public endpoint, no auth required).

            Args:
                app_id: Steam AppID of the game.
                count: Number of news items to return (default 5, max 20).
                max_length: Maximum character length per article body (default 300).
            """
            if not module._credentials_ok or module._client is None:
                return _no_credentials_error()
            try:
                result = await module._client.request(
                    "ISteamNews",
                    "GetNewsForApp",
                    params={
                        "appid": app_id,
                        "count": min(count, 20),
                        "maxlength": max_length,
                    },
                )
                return result
            except Exception as exc:  # noqa: BLE001
                return _handle_steam_error(exc)

        mcp.tool()(steam_get_game_news)

        # ----------------------------------------------------------------
        # Tool 7: steam_get_player_level
        # ----------------------------------------------------------------

        async def steam_get_player_level(steam_id: str | None = None) -> dict[str, Any]:
            """Get the Steam Experience Level for an account.

            Args:
                steam_id: SteamID64 string. Defaults to the primary connected account.
            """
            if not module._credentials_ok or module._client is None:
                return _no_credentials_error()
            sid = module._resolve_steam_id(steam_id)
            if not sid:
                return _no_credentials_error()
            try:
                result = await module._client.request(
                    "IPlayerService",
                    "GetSteamLevel",
                    params={"steamid": sid},
                )
                return result
            except Exception as exc:  # noqa: BLE001
                return _handle_steam_error(exc)

        mcp.tool()(steam_get_player_level)

        # ----------------------------------------------------------------
        # Tool 8: steam_get_current_players
        # ----------------------------------------------------------------

        async def steam_get_current_players(app_id: int) -> dict[str, Any]:
            """Get the current number of players in a Steam game (public endpoint).

            Args:
                app_id: Steam AppID of the game.
            """
            if not module._credentials_ok or module._client is None:
                return _no_credentials_error()
            try:
                result = await module._client.request(
                    "ISteamUserStats",
                    "GetNumberOfCurrentPlayers",
                    params={"appid": app_id},
                )
                return result
            except Exception as exc:  # noqa: BLE001
                return _handle_steam_error(exc)

        mcp.tool()(steam_get_current_players)

        # ----------------------------------------------------------------
        # Tool 9: steam_resolve_vanity_url
        # ----------------------------------------------------------------

        async def steam_resolve_vanity_url(vanity_url: str) -> dict[str, Any]:
            """Resolve a Steam vanity URL to a SteamID64.

            Args:
                vanity_url: The custom Steam URL name (e.g. 'gaben' from
                            steamcommunity.com/id/gaben).
            """
            if not module._credentials_ok or module._client is None:
                return _no_credentials_error()
            try:
                result = await module._client.request(
                    "ISteamUser",
                    "ResolveVanityURL",
                    params={"vanityurl": vanity_url},
                )
                success = result.get("success")
                if success == 1:
                    return {"steamid": result.get("steamid"), "vanity_url": vanity_url}
                return {
                    "error": "steam_vanity_not_found",
                    "message": f"Vanity URL '{vanity_url}' could not be resolved.",
                    "hint": "Check the URL spelling. The profile may have been removed.",
                }
            except Exception as exc:  # noqa: BLE001
                return _handle_steam_error(exc)

        mcp.tool()(steam_resolve_vanity_url)
