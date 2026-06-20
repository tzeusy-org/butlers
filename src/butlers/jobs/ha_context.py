"""Shared Home Assistant REST client context for butler job handlers.

``HomeJobContext`` is a lightweight async context manager that resolves HA
credentials from owner contact info and provides a short-lived
``httpx.AsyncClient`` pre-configured with ``Authorization: Bearer``.

It was previously defined inside ``butlers.jobs.home`` alongside Home-butler-
specific job logic.  Moving it here removes the implicit naming coupling
between the health butler (which also needs HA REST access) and the home
butler module.

Usage::

    async with (await HomeJobContext.create(pool)) as ctx:
        resp = await ctx.client.get(f"{ctx.ha_url}/api/states")
"""

from __future__ import annotations

from typing import Any

import asyncpg
import httpx

from butlers.credential_store import resolve_owner_entity_info


class HomeJobContext:
    """Lightweight context object for butler job handlers that need HA REST access.

    Holds the HA base URL and token resolved from owner contact info and
    provides a short-lived ``httpx.AsyncClient`` pre-configured with the
    ``Authorization: Bearer`` header.  Must be used as an async context
    manager so the underlying HTTP client is properly closed after the job:

    .. code-block:: python

        async with (await HomeJobContext.create(pool)) as ctx:
            resp = await ctx.client.get(f"{ctx.ha_url}/api/states")

    If HA credentials are missing from contact info, ``ha_url`` and
    ``ha_token`` will be ``None``.  Callers should check before making
    requests.

    Attributes:
        ha_url: HA base URL (e.g. ``"http://homeassistant.local:8123"``), or
            ``None`` if not configured.
        ha_token: Long-lived access token, or ``None`` if not configured.
        client: An open ``httpx.AsyncClient`` with the Authorization header
            set (available only inside the ``async with`` block).
    """

    def __init__(self, ha_url: str | None, ha_token: str | None) -> None:
        self.ha_url = ha_url
        self.ha_token = ha_token
        self.client: httpx.AsyncClient | None = None

    @classmethod
    async def create(cls, pool: asyncpg.Pool) -> HomeJobContext:
        """Resolve HA credentials from the owner's contact info and return a new context.

        Args:
            pool: asyncpg connection pool for the butler's database.

        Returns:
            A ``HomeJobContext`` instance with ``ha_url`` and ``ha_token``
            populated from contact info (either may be ``None`` if not found).
        """
        ha_url = await resolve_owner_entity_info(pool, "home_assistant_url")
        ha_token = await resolve_owner_entity_info(pool, "home_assistant_token")
        return cls(ha_url=ha_url, ha_token=ha_token)

    async def __aenter__(self) -> HomeJobContext:
        headers: dict[str, str] = {}
        if self.ha_token:
            headers["Authorization"] = f"Bearer {self.ha_token}"
        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=10.0),
            verify=False,  # noqa: S501 — local HA instances often use self-signed certs
        )
        await self.client.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.client is not None:
            await self.client.__aexit__(exc_type, exc_val, exc_tb)
            self.client = None
