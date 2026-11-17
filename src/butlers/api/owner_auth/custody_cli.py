"""Host-only endpoint custody commands (core_347), on the administrative Tier 0 DB connection.

``butlers custody hold|release|replace`` is the trusted host's protected act: it connects with the
host's own database authority, never the dashboard login, and calls ``public.custody_host``,
which no runtime or dashboard role may execute. Each mutation needs ``--confirm`` and prints its
command id first, so a lost acknowledgement can be read back with ``butlers custody status``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import asyncpg
import click
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.db import database_name_from_env, db_params_from_env

REASONS = ("lost", "stolen", "replaced", "disowned_access")


class CustodyHostError(Exception):
    """The command could not reach the database or did not answer; its outcome is unknown."""


async def run_custody_host(action: str, payload: dict[str, Any]) -> dict[str, Any]:
    connection = None
    try:
        with suppress_instrumentation():
            connection = await asyncpg.connect(
                **db_params_from_env(),
                database=database_name_from_env("butlers"),
                command_timeout=10,
                timeout=5,
            )
            raw = await connection.fetchval(
                "SELECT public.custody_host($1, $2::jsonb)", action, json.dumps(payload)
            )
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        raise CustodyHostError from None
    finally:
        if connection is not None:
            await connection.close()


def _execute(action: str, payload: dict[str, Any]) -> None:
    command_id = payload.get("command_id")
    if command_id is not None and action != "status":
        click.echo(f"Command: {command_id}")
    try:
        result = asyncio.run(run_custody_host(action, payload))
    except CustodyHostError:
        hint = (
            f" Read it back with: butlers custody status --command {command_id}"
            if (command_id and action != "status")
            else ""
        )
        raise click.ClickException("Custody outcome unknown." + hint) from None
    if "error" in result:
        raise click.ClickException(f"Custody command not authorized: {result['error']}")
    if result.get("outcome") == "refused":
        raise click.ClickException(f"Custody command refused: {result.get('reason')}")
    click.echo(json.dumps(result, indent=2, sort_keys=True))


@click.group("custody")
def custody() -> None:
    """Hold, release or replace owner endpoints from the trusted host."""


@custody.command("hold")
@click.option(
    "--fact",
    "fact_ids",
    multiple=True,
    required=True,
    type=click.UUID,
    help="Owner channel fact id to hold (repeatable).",
)
@click.option("--reason", type=click.Choice(REASONS), required=True)
@click.option("--confirm", is_flag=True, required=True, help="Commit the hold now.")
def hold(fact_ids: tuple[uuid.UUID, ...], reason: str, confirm: bool) -> None:
    """Hold the selected owner endpoints; they stop carrying owner authority."""
    _execute(
        "hold",
        {
            "command_id": str(uuid.uuid4()),
            "fact_ids": [str(f) for f in fact_ids],
            "reason": reason,
            "confirm": confirm,
        },
    )


def _disposition(action: str, hold_id: uuid.UUID, generation: int, confirm: bool) -> None:
    _execute(
        action,
        {
            "command_id": str(uuid.uuid4()),
            "hold_id": str(hold_id),
            "expected_generation": generation,
            "confirm": confirm,
        },
    )


@custody.command("release")
@click.option("--hold", "hold_id", required=True, type=click.UUID)
@click.option("--generation", required=True, type=click.IntRange(min=1))
@click.option("--confirm", is_flag=True, required=True, help="Commit the release now.")
def release(hold_id: uuid.UUID, generation: int, confirm: bool) -> None:
    """Release one hold generation; the endpoint may carry owner authority again."""
    _disposition("release", hold_id, generation, confirm)


@custody.command("replace")
@click.option("--hold", "hold_id", required=True, type=click.UUID)
@click.option("--generation", required=True, type=click.IntRange(min=1))
@click.option("--confirm", is_flag=True, required=True, help="Commit the replacement now.")
def replace(hold_id: uuid.UUID, generation: int, confirm: bool) -> None:
    """End a hold whose binding the owner has already retired."""
    _disposition("replace", hold_id, generation, confirm)


@custody.command("status")
@click.option("--command", "command_id", required=True, type=click.UUID)
def status(command_id: uuid.UUID) -> None:
    """Read back one command's committed result."""
    _execute("status", {"command_id": str(command_id)})


@custody.command("list")
def list_holds() -> None:
    """List active holds, without identifiers."""
    _execute("list", {})
