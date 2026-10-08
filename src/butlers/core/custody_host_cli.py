"""Current-owner endpoint recovery from the existing trusted host entrypoint.

No command takes credentials, actor claims, a verifier URL or a role selector.
Git configuration chooses the owning database and Switchboard MCP endpoint.
Selection values only locate targets; the engine checks current authority and
exact durable generations before the receiving business transaction commits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import click
from fastmcp import Client

from butlers.config import load_config
from butlers.core.custody_bootstrap import CustodyRuntime
from butlers.core.custody_control import CustodyControlTransport, host_profile, prepare_host_source
from butlers.core.custody_source import (
    CustodyError,
    canonical_json,
    canonical_targets,
    canonical_uuid,
    closed_object,
    digest,
)
from butlers.core.mcp_urls import canonical_runtime_mcp_url, runtime_mcp_url
from butlers.db import Database


def read_selection(path: Path) -> dict:
    """Bounded exact JSON; duplicate keys never survive into a selector DTO."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CustodyError("invalid")
            result[key] = value
        return result

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise CustodyError("invalid")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(8193)
        finally:
            os.close(descriptor)
        if len(raw) > 8192:
            raise CustodyError("invalid")
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
        closed_object(
            result,
            required={"target_set", "target_set_version"},
            optional={"case_id", "reason", "observed_incident_at", "provider", "provider_version"},
        )
        if (
            type(result["target_set_version"]) is not int
            or not 1 <= result["target_set_version"] < 2**63
            or result["target_set"] != canonical_targets(result["target_set"])
        ):
            raise CustodyError("invalid")
        canonical_json(result)
        return result
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise CustodyError("invalid") from None


async def run_host_custody(
    config_path: Path,
    operation: str,
    selection: dict,
    *,
    on_prepared: Callable[[str], None] | None = None,
) -> dict:
    """Actual host lifecycle; no bootstrap/install or historical apply here."""
    runtime = None
    database = None
    try:
        config = load_config(config_path)
        if config.name != "switchboard":
            raise CustodyError("refused")
        endpoint = canonical_runtime_mcp_url(config.switchboard_url or runtime_mcp_url(config.port))
        address = urlsplit(endpoint)
        if (
            address.scheme not in {"http", "https"}
            or not address.hostname
            or address.username is not None
            or address.password is not None
            or address.query
            or address.fragment
            or address.path.rstrip("/") != "/mcp"
        ):
            raise CustodyError("invalid")
        # Preserve the shared URL privacy pins before creating the actual SDK
        # transport, including when the CLI is invoked without daemon logging.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        database = Database.from_env(config.db_name)
        database.set_schema(config.db_schema)
        database.role = f"butler_{config.db_schema or config.name}_rw"
        database.strict_role_enforcement = True
        await database.connect()
        runtime = CustodyRuntime(
            database,
            host_profile(
                digest(
                    {
                        "kind": "trusted-host-command.v1",
                        "schema": config.db_schema,
                        "role": database.role,
                    }
                ),
                role=database.role,
            ),
        )
        admission = await runtime.start()
        source = await prepare_host_source(runtime, operation, selection)
        # Expose the safe original locator after durable source preparation,
        # BEFORE any remote effect. A lost acknowledgement can still be read
        # using this exact ID; the private authorization ticket is not printed.
        if on_prepared is not None:
            on_prepared(str(source.result_command_id))
        result = await CustodyControlTransport(admission, Client(endpoint)).execute(
            source, read_only=operation == "eligibility"
        )
        return result
    except CustodyError:
        raise
    except Exception:
        # No raw driver/URL/file/config diagnostics appear in public output.
        raise CustodyError("unavailable") from None
    finally:
        try:
            try:
                if runtime is not None:
                    await runtime.stop()
            finally:
                if database is not None:
                    await database.close()
        except Exception:
            raise CustodyError("unknown") from None


def _execute(config_path: Path, operation: str, selection: dict) -> None:
    try:
        result = asyncio.run(
            run_host_custody(
                config_path,
                operation,
                selection,
                on_prepared=lambda identifier: click.echo(f"Command: {canonical_uuid(identifier)}"),
            )
        )
    except CustodyError as error:
        raise click.ClickException(f"Custody {error.code}.") from None
    # A result locator and fixed verdict suffice for recovery. Never print the
    # source/wire/challenge, selected endpoints, case evidence or credentials.
    status = result.get("status")
    if status not in {"committed", "unknown"}:
        raise click.ClickException("Custody unknown.")
    click.echo(f"Custody {status}.")


@click.group("custody")
def custody() -> None:
    """Hold or recover endpoints using current trusted host authority."""


@custody.command("control")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option("--operation", type=click.Choice(["hold", "release", "replaced"]), required=True)
@click.option(
    "--selection",
    "selection_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
def control(config_path: Path, operation: str, selection_path: Path) -> None:
    """Commit an exact current target/generation selection from a JSON file."""
    try:
        selection = read_selection(selection_path)
    except CustodyError:
        raise click.ClickException("Custody invalid.") from None
    _execute(config_path, operation, selection)


@custody.command("result")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
@click.option("--command", "command_id", required=True)
def result(config_path: Path, command_id: str) -> None:
    """Read the same durable command with fresh current host authorization."""
    try:
        canonical_uuid(command_id)
    except CustodyError:
        raise click.ClickException("Custody invalid.") from None
    _execute(
        config_path,
        "eligibility",
        {"target_set": [], "target_set_version": 1, "result_command_id": command_id},
    )
