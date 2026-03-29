#!/usr/bin/env python3
"""OAuth gate: poll DB for Google refresh token, exit 0 when found.

Used as a Docker Compose init service to gate Layer 3 startup.

Environment variables:
  POSTGRES_HOST     (default: postgres)
  POSTGRES_PORT     (default: 5432)
  POSTGRES_USER     (default: butlers)
  POSTGRES_PASSWORD (default: butlers)
  POSTGRES_DB       (default: butlers)
  OAUTH_GATE_TIMEOUT  (default: 0 = infinite)
  OAUTH_POLL_INTERVAL (default: 5)
  SKIP_OAUTH_CHECK    (default: false)
"""

import os
import sys
import time

SKIP = os.environ.get("SKIP_OAUTH_CHECK", "false").lower() in ("true", "1", "yes")
TIMEOUT = int(os.environ.get("OAUTH_GATE_TIMEOUT", "0"))
INTERVAL = int(os.environ.get("OAUTH_POLL_INTERVAL", "5"))


def _check_token() -> bool:
    """Return True if a Google OAuth refresh token exists in public.entity_info."""
    import psycopg2

    dsn = (
        f"host={os.environ.get('POSTGRES_HOST', 'postgres')} "
        f"port={os.environ.get('POSTGRES_PORT', '5432')} "
        f"user={os.environ.get('POSTGRES_USER', 'butlers')} "
        f"password={os.environ.get('POSTGRES_PASSWORD', 'butlers')} "
        f"dbname={os.environ.get('POSTGRES_DB', 'butlers')}"
    )
    try:
        with psycopg2.connect(dsn) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM public.entity_info ei
                    JOIN public.entities e ON e.id = ei.entity_id
                    WHERE ei.type = 'google_oauth_refresh'
                      AND ei.value IS NOT NULL
                      AND length(ei.value) > 0
                    """
                )
                row = cur.fetchone()
                return bool(row and row[0] > 0)
    except Exception as exc:
        print(f"oauth-gate: DB check failed: {exc}", file=sys.stderr)
        return False


def main() -> None:
    if SKIP:
        print("oauth-gate: SKIP_OAUTH_CHECK=true, exiting immediately")
        sys.exit(0)

    elapsed = 0
    while True:
        if _check_token():
            print("oauth-gate: Google OAuth refresh token found")
            sys.exit(0)

        if TIMEOUT > 0 and elapsed >= TIMEOUT:
            print(
                f"oauth-gate: timed out after {TIMEOUT}s — continuing without Google credentials",
                file=sys.stderr,
            )
            # Exit 0 so dependent services still start (matches dev.sh behavior)
            sys.exit(0)

        if elapsed == 0:
            print(
                f"oauth-gate: waiting for Google OAuth credentials "
                f"(poll every {INTERVAL}s, timeout: {TIMEOUT or 'infinite'})"
            )

        time.sleep(INTERVAL)
        elapsed += INTERVAL
        if elapsed % 30 == 0:
            print(f"oauth-gate: still waiting... ({elapsed}s)")


if __name__ == "__main__":
    main()
