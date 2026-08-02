"""Executable-command contracts for direct non-Messenger approval producers.

Most pending actions originate from a gate-wrapped MCP tool, so the daemon has
already resolved a registered handler before it persists the invocation.  A
small set of direct producers create rows themselves (dashboard lifecycle
routes and deterministic jobs).  Those producers must not leave an owner with
an approval that can only fail after it is accepted.

This module records the explicit durable command for that small inventoried
set.  It deliberately does not provide aliases, retry-time argument repair, or
cross-butler fallback: a persisted action remains evidence of the exact command
the producer requested.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class ApprovalCommandContractError(ValueError):
    """Raised when a direct approval command is not safely executable."""


@dataclass(frozen=True)
class ExecutableApprovalCommand:
    """A direct producer's exact owning-daemon replay command."""

    name: str
    owner_butler: str
    argument_names: tuple[str, ...]
    producer_source: tuple[str, str]

    def materialize(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Return a stable JSON-ready argument dict after exact-key validation."""
        received = set(arguments)
        expected = set(self.argument_names)
        if received != expected:
            missing = sorted(expected - received)
            unexpected = sorted(received - expected)
            details: list[str] = []
            if missing:
                details.append(f"missing={missing}")
            if unexpected:
                details.append(f"unexpected={unexpected}")
            raise ApprovalCommandContractError(
                f"Approval command {self.name!r} arguments do not match its declared "
                f"contract ({', '.join(details)})"
            )
        return {name: arguments[name] for name in self.argument_names}


@dataclass(frozen=True)
class NonReplayableApprovalProducer:
    """An inventoried producer that must reject before queue insertion."""

    name: str
    owner_butler: str
    producer_source: tuple[str, str]
    rejection_reason: str


CONNECTOR_DISCONNECT_COMMAND = ExecutableApprovalCommand(
    name="connector_disconnect",
    owner_butler="switchboard",
    argument_names=("connector_type", "endpoint_identity"),
    producer_source=(
        "src/butlers/api/routers/ingestion_connectors.py",
        "disconnect_connector",
    ),
)

MEMORY_RECLASSIFY_COMMAND = ExecutableApprovalCommand(
    name="memory_reclassify",
    owner_butler="relationship",
    argument_names=("memory_type", "memory_id", "permanence_target"),
    producer_source=(
        "roster/relationship/jobs/relationship_jobs.py",
        "run_episodic_predicate_curation._ensure_pending_action",
    ),
)

CONNECTOR_ROTATE_TOKEN_PRODUCER = NonReplayableApprovalProducer(
    name="connector_rotate_token",
    owner_butler="switchboard",
    producer_source=(
        "src/butlers/api/routers/ingestion_connectors.py",
        "rotate_connector_token",
    ),
    rejection_reason=(
        "Token rotation cannot be queued because no safe replayable command is available."
    ),
)

EXECUTABLE_DIRECT_COMMANDS: tuple[ExecutableApprovalCommand, ...] = (
    CONNECTOR_DISCONNECT_COMMAND,
    MEMORY_RECLASSIFY_COMMAND,
)

NON_MESSENGER_PRODUCER_INVENTORY: tuple[
    ExecutableApprovalCommand | NonReplayableApprovalProducer, ...
] = (
    CONNECTOR_DISCONNECT_COMMAND,
    CONNECTOR_ROTATE_TOKEN_PRODUCER,
    MEMORY_RECLASSIFY_COMMAND,
)


async def _get_registered_tool(mcp: Any, tool_name: str) -> Any | None:
    get_tool = getattr(mcp, "get_tool", None)
    if not callable(get_tool):
        raise ApprovalCommandContractError(
            "FastMCP instance does not expose required get_tool(name) API"
        )
    try:
        tool = get_tool(tool_name)
        if inspect.isawaitable(tool):
            tool = await tool
    except KeyError:
        return None
    return tool


async def validate_owner_command_registry(mcp: Any, owner_butler: str) -> None:
    """Fail startup when a declared command drifts from its owner MCP surface."""
    for command in EXECUTABLE_DIRECT_COMMANDS:
        if command.owner_butler != owner_butler:
            continue

        tool = await _get_registered_tool(mcp, command.name)
        if tool is None:
            raise ApprovalCommandContractError(
                f"Declared approval command {command.name!r} is not registered on "
                f"owning butler {owner_butler!r}"
            )

        handler = getattr(tool, "fn", None)
        if not callable(handler):
            raise ApprovalCommandContractError(
                f"Declared approval command {command.name!r} has no callable registered handler "
                f"on owning butler {owner_butler!r}"
            )

        signature = inspect.signature(handler)
        parameters = tuple(signature.parameters.values())
        if any(
            parameter.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
            for parameter in parameters
        ):
            raise ApprovalCommandContractError(
                f"Declared approval command {command.name!r} on owning butler "
                f"{owner_butler!r} must accept explicit keyword arguments only"
            )

        handler_names = tuple(parameter.name for parameter in parameters)
        if handler_names != command.argument_names:
            raise ApprovalCommandContractError(
                f"Declared approval command {command.name!r} on owning butler "
                f"{owner_butler!r} expects {command.argument_names!r}, but registered "
                f"handler accepts {handler_names!r}"
            )


__all__ = [
    "ApprovalCommandContractError",
    "CONNECTOR_DISCONNECT_COMMAND",
    "CONNECTOR_ROTATE_TOKEN_PRODUCER",
    "EXECUTABLE_DIRECT_COMMANDS",
    "ExecutableApprovalCommand",
    "MEMORY_RECLASSIFY_COMMAND",
    "NON_MESSENGER_PRODUCER_INVENTORY",
    "NonReplayableApprovalProducer",
    "validate_owner_command_registry",
]
