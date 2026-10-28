#!/usr/bin/env python3
"""OAuth gate: poll DB for Google refresh token, exit 0 when found.

Used as a Docker Compose init service to gate Layer 3 startup.

Environment variables:
  POSTGRES_HOST     (default: postgres)
  POSTGRES_PORT     (default: 5432)
  POSTGRES_USER     (required connector-purpose credential)
  POSTGRES_PASSWORD (required connector-purpose credential)
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

    from butlers.db_authority.catalog import setup_dbapi_purpose
    from butlers.db_authority.roles import purpose_profile

    conn = None
    available = False
    try:
        profile = purpose_profile(os.environ.get("POSTGRES_DB", "butlers"), "connector")
        user, password = os.environ.get("POSTGRES_USER"), os.environ.get("POSTGRES_PASSWORD")
        if not user or not password:
            raise ValueError("Connector-purpose credential is unavailable")
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "postgres"),
            port=os.environ.get("POSTGRES_PORT", "5432"),
            user=user,
            password=password,
            dbname=profile.database,
            sslmode=os.environ.get("POSTGRES_SSLMODE") or "prefer",
            connect_timeout=5,
            options="-c statement_timeout=5000",
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            setup_dbapi_purpose(cur, profile)
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
            available = bool(row and row[0] > 0)
    except Exception:
        print("oauth-gate: database authority or token availability unreadable", file=sys.stderr)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                print("oauth-gate: owned database connection cleanup incomplete", file=sys.stderr)
                available = False
    return available


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
            # Exit 0 so dependent services still start
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
