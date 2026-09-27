"""Host helper confined to the disposable browser test database."""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

from butlers.api.owner_auth.host_cli import auth
from butlers.db import db_params_from_env


async def expire_sessions():
    connection = await asyncpg.connect(**db_params_from_env(), database="test_owner_auth_browser")
    try:
        await connection.execute(
            "UPDATE dashboard_auth.sessions SET expires_at=now()-interval '1 second'"
        )
    finally:
        await connection.close()


if __name__ == "__main__":
    if os.environ.get("POSTGRES_DB") != "test_owner_auth_browser" or os.environ.get("DATABASE_URL"):
        raise SystemExit("Refusing non-test database")
    if sys.argv[1:] == ["expire-sessions"]:
        asyncio.run(expire_sessions())
    else:
        auth.main(args=sys.argv[1:])
