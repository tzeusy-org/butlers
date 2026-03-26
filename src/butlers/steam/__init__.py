"""Steam Web API integration package.

Provides the shared :class:`~butlers.steam.client.SteamAPIClient` used by both
the ``steam`` module (MCP tools) and the ``steam`` connector (background polling).
"""

from butlers.steam.client import (
    SteamAPIClient,
    SteamAPIError,
    SteamRateLimitError,
)

__all__ = [
    "SteamAPIClient",
    "SteamAPIError",
    "SteamRateLimitError",
]
