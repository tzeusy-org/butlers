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


def check_google_credentials() -> GoogleCredentialCheckResult:
    """Return a DB-only remediation result for sync-only call sites.

    Returns
    -------
    GoogleCredentialCheckResult
        A remediation result directing callers to DB-backed OAuth bootstrap.
    """
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
        "Then restart this connector."
    )

    return GoogleCredentialCheckResult(
        ok=False,
        missing_vars=[],
        message=(
            "Google OAuth credential availability is DB-managed. "
            "Use check_google_credentials_with_db() to validate runtime readiness."
        ),
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
    """Check whether Google OAuth credentials are available from DB.

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
        credentials are present in DB-backed secret storage.
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
            message="Google credentials are available in DB-backed secret storage.",
            remediation="",
        )
    except MissingGoogleCredentialsError as exc:
        return GoogleCredentialCheckResult(
            ok=False,
            missing_vars=[],
            message=str(exc),
            remediation=check_google_credentials().remediation,
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
    print(f"\n{separator}\n", file=sys.stderr)
