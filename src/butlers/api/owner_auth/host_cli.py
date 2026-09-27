"""Host-only owner authorization, using the administrative Tier 0 DB connection."""

from __future__ import annotations

import asyncio
import json

import asyncpg
import click
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.api.owner_auth.service import AuthError, _locator
from butlers.db import database_name_from_env, db_params_from_env


async def run_host_operation(action: str, config, *, request_id=None, confirm_revoke=False) -> dict:
    """No API-role escalation path: the host connects with its own DB authority."""
    if request_id is not None:
        _locator(request_id)
    connection = None
    try:
        with suppress_instrumentation():
            connection = await asyncpg.connect(
                **db_params_from_env(),
                database=database_name_from_env("butlers"),
                command_timeout=5,
                timeout=5,
            )
            raw = await connection.fetchval(
                "SELECT dashboard_auth.host($1,$2::jsonb)",
                action,
                json.dumps(
                    {
                        "origin": config.origin,
                        "rp_id": config.rp_id,
                        "key_generation": config.key_generation,
                        "request_id": request_id,
                        "confirm_revoke": confirm_revoke,
                    }
                ),
            )
        result = json.loads(raw)
        if "error" in result:
            raise AuthError(result["error"])
        return result
    except AuthError:
        raise
    except Exception:
        raise AuthError() from None
    finally:
        if connection is not None:
            await connection.close()


def _execute(action: str, request_id=None, confirm_revoke=False) -> None:
    from butlers.api.owner_auth.config import OwnerAuthConfig

    config = OwnerAuthConfig.from_env()
    click.echo(
        f"Operation: {action.replace('_', '-')}\nCanonical origin: {config.origin or 'unavailable'}"
    )
    if request_id is not None:
        click.echo("Use only the exact request copied from the browser you are enrolling.")
    try:
        result = asyncio.run(
            run_host_operation(
                action,
                config,
                request_id=request_id,
                confirm_revoke=confirm_revoke,
            )
        )
    except AuthError as exc:
        raise click.ClickException(exc.message) from None
    click.echo(
        "Authorization state committed."
        if result["changed"]
        else "Already current; deadline unchanged."
    )


@click.group("auth")
def auth() -> None:
    """Authorize owner enrollment and recovery from the trusted host only."""


@auth.command("authorize-registration")
@click.option("--request", "request_id", required=True, help="Exact request from your own browser.")
def authorize_registration(request_id: str) -> None:
    """Authorize one unexpired browser-bound first registration."""
    _execute("authorize_registration", request_id)


@auth.command("authorize-recovery")
@click.option("--request", "request_id", required=True, help="Exact request from your own browser.")
@click.option(
    "--confirm-revoke",
    is_flag=True,
    required=True,
    help="Immediately retire credentials and sessions.",
)
def authorize_recovery(request_id: str, confirm_revoke: bool) -> None:
    """Revoke old authority and authorize this exact replacement request."""
    _execute("authorize_recovery", request_id, confirm_revoke)


@auth.command("reconcile-mode")
@click.option(
    "--confirm-revoke", is_flag=True, required=True, help="Retire all historical browser authority."
)
def reconcile_mode(confirm_revoke: bool) -> None:
    """Bind configured mode; changing keys retires passkeys and sessions."""
    _execute("reconcile_mode", confirm_revoke=confirm_revoke)


@auth.command("rebind-origin")
@click.option(
    "--confirm-revoke",
    is_flag=True,
    required=True,
    help="Retire credentials and sessions before new-origin recovery.",
)
def rebind_origin(confirm_revoke: bool) -> None:
    """Bind one new HTTPS origin/RP and require host-authorized recovery."""
    _execute("rebind_origin", confirm_revoke=confirm_revoke)


@auth.command("revoke-sessions")
@click.option(
    "--confirm-revoke",
    is_flag=True,
    required=True,
    help="Revoke every browser session and pending login without retiring the passkey.",
)
def revoke_sessions(confirm_revoke: bool) -> None:
    """Advance the browser session epoch; ordinary passkey sign-in remains available."""
    _execute("revoke_sessions", confirm_revoke=confirm_revoke)


@auth.command("clear-pending")
@click.option(
    "--confirm-revoke",
    is_flag=True,
    required=True,
    help="Invalidate every pending browser context and host-approved ceremony.",
)
def clear_pending(confirm_revoke: bool) -> None:
    """Release pending capacity without exposing visitor identities."""
    _execute("clear_pending", confirm_revoke=confirm_revoke)
