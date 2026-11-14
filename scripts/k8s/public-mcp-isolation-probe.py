"""Run inside the public MCP pod by public-mcp-live-checks.sh (task 1.8, public half).

Logs in as mcp_public_ro to each database and prints one verdict line per database:
`ok`, `FAIL` or `INCONCLUSIVE`. The password comes from the pod's mounted file and is never
printed. Only a pg_hba/auth refusal (SQLSTATE class 28) counts as a refusal: a timeout, DNS or
TLS error says nothing about the boundary. The caller requires exactly one verdict per database
and a zero exit, so a crash here can never read as a pass.
"""

import asyncio
import os

import asyncpg

PASSWORD_FILE = os.environ.get("PUBLIC_MCP_PASSWORD_FILE", "/etc/butlers-public-mcp/db/password")
DATABASES = (
    ("butlers_public", True),
    ("butlers", False),
    ("postgres", False),
    ("template1", False),
)


def read_password() -> str:
    with open(PASSWORD_FILE) as handle:
        return handle.read().strip()


async def main() -> None:
    password = read_password()
    for db, want in DATABASES:
        error = ""
        try:
            conn = await asyncpg.connect(
                host=os.environ["MCP_PUBLIC_DB_HOST"],
                port=int(os.environ["MCP_PUBLIC_DB_PORT"]),
                user=os.environ["MCP_PUBLIC_DB_USER"],
                password=password,
                database=db,
                ssl="require",
                timeout=8,
            )
            await conn.close()
            word = "allowed"
        except Exception as exc:
            # `exc` is unbound after this block in Python 3: keep what the line needs here.
            error = type(exc).__name__
            state = getattr(exc, "sqlstate", None) or ""
            word = "refused" if state.startswith("28") else "inconclusive"
        if word == "inconclusive":
            print(f"INCONCLUSIVE ISOLATION login to {db}: {error}")
        else:
            verdict = "ok   " if (word == "allowed") == want else "FAIL "
            print(f"{verdict}ISOLATION login to {db}: {word}")


if __name__ == "__main__":
    asyncio.run(main())
