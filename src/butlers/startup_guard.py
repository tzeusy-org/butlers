"""Startup guards for Google-dependent components.

Provides utilities to check whether Google OAuth credentials are available
before attempting to launch components that require them (Gmail connector,
Calendar module). When credentials are missing, these guards emit clear,
actionable developer prompts describing how to complete the OAuth bootstrap.

Typical usage in a connector entrypoint::

    from butlers.startup_guard import require_google_credentials_or_exit

    require_google_credentials_or_exit(
        caller="gmail-connector",
        dashboard_url="http://localhost:40200",
    )

Typical usage to get a status without exiting::

    from butlers.startup_guard import check_google_credentials

    status = check_google_credentials()
    if not status.ok:
        print(status.message)
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credential check result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoogleCredentialCheckResult:
    """Result of a Google credential availability check.

    Attributes
    ----------
    ok:
        True if credentials are present and appear usable.
    missing_vars:
        List of environment variable names that are missing (when ok=False).
    message:
        Human-readable status message. Safe to log and display.
    remediation:
        Actionable instructions for the developer on how to fix the issue.
    """

    ok: bool
    missing_vars: list[str]
    message: str
    remediation: str


# ---------------------------------------------------------------------------
# Credential check logic
# ---------------------------------------------------------------------------

# Variables checked in priority order (mirrors GoogleCredentials.from_env).
_CREDENTIAL_FIELD_ALIASES: list[tuple[str, list[str]]] = [
    ("client_id", ["GOOGLE_OAUTH_CLIENT_ID"]),
    ("client_secret", ["GOOGLE_OAUTH_CLIENT_SECRET"]),
    ("refresh_token", ["GOOGLE_REFRESH_TOKEN"]),
]


def check_google_credentials() -> GoogleCredentialCheckResult:
    """Check whether Google OAuth credentials are available from environment.

    This is an env-only check — it does NOT connect to the database or
    make any network calls. It mirrors the priority order used by
    ``GoogleCredentials.from_env()``.

    Returns
    -------
    GoogleCredentialCheckResult
        A result object describing credential availability and, if missing,
        actionable remediation guidance.
    """
    # Check individual env vars
    missing_vars: list[str] = []
    for field_name, aliases in _CREDENTIAL_FIELD_ALIASES:
        found = any(os.environ.get(v, "").strip() for v in aliases)
        if not found:
            # Report the canonical var name for clarity
            missing_vars.append(aliases[0])

    if not missing_vars:
        return GoogleCredentialCheckResult(
            ok=True,
            missing_vars=[],
            message="Google credentials are available from environment variables.",
            remediation="",
        )

    # Build helpful remediation message
    missing_list = ", ".join(missing_vars)
    remediation = (
        "Google OAuth credentials are required by:\n"
        "  - Gmail connector      (outbound email delivery)\n"
        "  - Calendar module      (calendar read/write for all butlers)\n"
        "\n"
        "To complete Google OAuth bootstrap:\n"
        "  1. Start the Butlers dashboard:  uv run butlers dashboard\n"
        "  2. Open http://localhost:40200 in your browser.\n"
        "  3. Click 'Connect Google' and follow the OAuth flow.\n"
        "  4. After successful authorization, the refresh token is stored in the DB.\n"
        "\n"
        "Alternatively, set these environment variables:\n"
        "  GOOGLE_OAUTH_CLIENT_ID=<your-client-id>\n"
        "  GOOGLE_OAUTH_CLIENT_SECRET=<your-client-secret>\n"
        "  GOOGLE_REFRESH_TOKEN=<your-refresh-token>\n"
        "\n"
        "Then restart this connector."
    )

    message = (
        f"Google OAuth credentials are not available. "
        f"Missing: {missing_list}. "
        f"Google-dependent services (Gmail connector, Calendar module) will not start."
    )

    return GoogleCredentialCheckResult(
        ok=False,
        missing_vars=missing_vars,
        message=message,
        remediation=remediation,
    )


# ---------------------------------------------------------------------------
# Async DB-aware credential check
# ---------------------------------------------------------------------------


async def check_google_credentials_with_db(
    conn: object,
    *,
    caller: str = "unknown",
) -> GoogleCredentialCheckResult:
    """Check whether Google OAuth credentials are available from DB or environment.

    This is an async check that queries the database first (DB-first resolution),
    then falls back to environment variables if the DB has no stored credentials.

    Parameters
    ----------
    conn:
        An asyncpg connection or pool to use for DB lookup.
    caller:
        Name of the calling component (used in log messages).

    Returns
    -------
    GoogleCredentialCheckResult
        A result object describing credential availability. When ok=True,
        credentials are present in either the DB or environment variables.
    """
    from butlers.google_credentials import (
        MissingGoogleCredentialsError,
        resolve_google_credentials,
    )

    try:
        await resolve_google_credentials(conn, caller=caller)
        return GoogleCredentialCheckResult(
            ok=True,
            missing_vars=[],
            message="Google credentials are available (DB or environment).",
            remediation="",
        )
    except MissingGoogleCredentialsError as exc:
        env_result = check_google_credentials()
        # Use the env check's missing_vars and remediation (they are always populated)
        return GoogleCredentialCheckResult(
            ok=False,
            missing_vars=env_result.missing_vars,
            message=str(exc),
            remediation=env_result.remediation,
        )


# ---------------------------------------------------------------------------
# Hard-exit guard (for connector entrypoints)
# ---------------------------------------------------------------------------


def require_google_credentials_or_exit(
    *,
    caller: str = "component",
    dashboard_url: str = "http://localhost:40200",
    exit_code: int = 1,
) -> None:
    """Check for Google credentials and exit with a clear message if missing.

    This is intended for use in connector entrypoints (e.g. the Gmail
    connector ``main()`` function) where there is no point continuing
    without credentials.

    Parameters
    ----------
    caller:
        Name of the calling component (used in log and error messages).
    dashboard_url:
        URL of the Butlers dashboard (used in remediation instructions).
    exit_code:
        Exit code to use when credentials are missing (default: 1).
    """
    result = check_google_credentials()
    if result.ok:
        logger.debug("[%s] Google credentials check: OK", caller)
        return

    logger.error("[%s] %s", caller, result.message)

    # Print formatted output directly to stderr for developer visibility
    _print_credential_error(caller=caller, result=result, dashboard_url=dashboard_url)
    sys.exit(exit_code)


def _print_credential_error(
    *,
    caller: str,
    result: GoogleCredentialCheckResult,
    dashboard_url: str,
) -> None:
    """Print a formatted credential error to stderr."""
    separator = "=" * 70
    print(f"\n{separator}", file=sys.stderr)
    print(f"  STARTUP BLOCKED: {caller}", file=sys.stderr)
    print(separator, file=sys.stderr)
    print(f"\n  {result.message}\n", file=sys.stderr)
    print("  Required by:", file=sys.stderr)
    print("    - Gmail connector      (outbound email delivery)", file=sys.stderr)
    print("    - Calendar module      (calendar read/write for all butlers)", file=sys.stderr)
    print("\n  How to fix:", file=sys.stderr)
    print(f"    1. Open the Butlers dashboard: {dashboard_url}", file=sys.stderr)
    print("    2. Click 'Connect Google' and complete the OAuth flow.", file=sys.stderr)
    print("    3. Once authorized, restart this connector.", file=sys.stderr)
    print("\n  Or set environment variables:", file=sys.stderr)
    print("    GOOGLE_OAUTH_CLIENT_ID=<your-client-id>", file=sys.stderr)
    print("    GOOGLE_OAUTH_CLIENT_SECRET=<your-client-secret>", file=sys.stderr)
    print("    GOOGLE_REFRESH_TOKEN=<your-refresh-token>", file=sys.stderr)
    print(f"\n{separator}\n", file=sys.stderr)
