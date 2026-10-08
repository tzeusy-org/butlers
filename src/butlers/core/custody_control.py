"""Private current-owner command sources and fixed registered MCP transport.

Only actual server preparation and its acquired source writer can produce this
source. Selectors and stored command UUIDs alone confer no privilege. Network
challenge/response occurs after source COMMIT and outside every business writer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from fastmcp import Client
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.core.custody_admission import CustodyAdmission, CustodyProfile
from butlers.core.custody_bootstrap import CustodyRuntime
from butlers.core.custody_source import (
    CustodyError,
    canonical_json,
    canonical_targets,
    canonical_uuid,
    closed_object,
    digest,
    utc_timestamp,
)


def host_profile(config_digest: str, *, role: str = "butler_switchboard_rw") -> CustodyProfile:
    """Distinct source incarnation under the existing Switchboard runtime role."""
    return CustodyProfile(
        "host-switchboard",
        role,
        ("host_command",),
        ("eligibility", "hold", "release", "replaced", "revoke_sessions"),
        ("switchboard",),
        config_digest,
    )


@dataclass(frozen=True, repr=False)
class PreparedCustodyCommand:
    """Process-private frozen values; the engine authenticates them again."""

    command_id: uuid.UUID
    result_command_id: uuid.UUID
    source_ref: uuid.UUID
    source_digest: str
    operation: str
    selection_bytes: bytes

    def __reduce__(self):
        raise TypeError("private custody source cannot be serialized")


async def register_prepared_command(
    admission: CustodyAdmission, prepared: dict, operation: str, selection: dict
) -> PreparedCustodyCommand:
    """Server-only callback after host/browser SQL preparation, not a tool.

    The prepared response is not trusted proof. Registration checks its actual
    immutable command, current auth, exact selection/digest/expiry and owning
    process in the SAME transaction. Canonical owner lookup is server-derived.
    Browser startup must supply its own restricted admitted process; it cannot
    reuse this host runtime or obtain its connecting authority.
    """
    family = {"dashboard": "owner_command", "host-switchboard": "host_command"}.get(
        admission.profile.actor
    )
    if family is None:
        raise CustodyError("refused")
    closed_object(prepared, required={"command_id", "expires_at", "selection_digest"})
    command_id = uuid.UUID(canonical_uuid(prepared["command_id"]))
    if prepared["selection_digest"] != digest(selection):
        raise CustodyError("refused")
    selection_bytes = canonical_json(selection)
    targets = canonical_targets(selection.get("target_set"))
    try:
        deadline = prepared["expires_at"]
        if isinstance(deadline, str):
            deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        expiry = utc_timestamp(deadline)
        result_id = (
            uuid.UUID(canonical_uuid(selection["result_command_id"]))
            if operation == "eligibility"
            else command_id
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise CustodyError("invalid") from None
    async with admission.writer() as writer:
        source = await writer.register_source(
            family,
            {
                "locator": f"command:{command_id}",
                "revision": 1,
                "content_digest": prepared["selection_digest"],
                "origin_digest": digest({"kind": family, "command_id": str(command_id)}),
                "owner_entity_id": None,
                "issuer_target": None,
                "target_set": targets,
                "target_set_version": selection["target_set_version"],
                "expires_at": expiry,
            },
        )
        projection = source["projection"]
        closed_object(
            projection,
            required={
                "locator",
                "revision",
                "content_digest",
                "origin_digest",
                "owner_entity_id",
                "issuer_target",
                "target_set",
                "target_set_version",
                "expires_at",
            },
        )
        canonical_uuid(projection["owner_entity_id"])
        expected = {
            "locator": f"command:{command_id}",
            "revision": 1,
            "content_digest": prepared["selection_digest"],
            "origin_digest": digest({"kind": family, "command_id": str(command_id)}),
            "owner_entity_id": projection["owner_entity_id"],
            "issuer_target": None,
            "target_set": targets,
            "target_set_version": selection["target_set_version"],
            "expires_at": expiry,
        }
        if projection != expected:
            raise CustodyError("refused")
    return PreparedCustodyCommand(
        command_id,
        result_id,
        uuid.UUID(canonical_uuid(source["source_ref"])),
        source["source_digest"],
        operation,
        selection_bytes,
    )


async def prepare_host_source(
    runtime: CustodyRuntime, operation: str, selection: dict
) -> PreparedCustodyCommand:
    if runtime.admission is None:
        raise CustodyError("unavailable")
    prepared = await runtime.prepare_host_command(operation, selection)
    return await register_prepared_command(runtime.admission, prepared, operation, selection)


class CustodyControlTransport:
    """Constructor-fixed real MCP client for one configured Switchboard target.

    No verifier URL, audience or callback may arrive in a command/model body.
    A failed/lost apply response is UNKNOWN; callers retain the original ID and
    acquire a fresh current read ticket instead of resending the old mutation.
    """

    def __init__(self, admission: CustodyAdmission, client: Client) -> None:
        self._admission, self._client = admission, client

    async def execute(self, command: PreparedCustodyCommand, *, read_only: bool = False) -> dict:
        import json

        if type(command) is not PreparedCustodyCommand:
            raise CustodyError("refused")
        if command.operation == "eligibility" and not read_only:
            raise CustodyError("refused")
        method = "custody.result" if read_only else "custody.commit"
        selection = json.loads(command.selection_bytes)
        arguments = {"command_id": str(command.result_command_id)}
        operation = {
            "mint_request_id": str(uuid.uuid4()),
            "operation": command.operation,
            "method": method,
            "arguments": arguments,
            "command_id": str(command.result_command_id),
            "target_set": selection["target_set"],
            "target_set_version": selection["target_set_version"],
        }
        minted = await self._admission.mint(
            command.source_ref, operation, "switchboard", source_digest=command.source_digest
        )
        call_ref = uuid.UUID(canonical_uuid(minted["call_ref"]))
        try:
            async with self._client:
                with suppress_instrumentation():
                    challenge = await self._client.call_tool(
                        "custody.challenge",
                        {"call_ref": str(call_ref), "operation_digest": minted["operation_digest"]},
                    )
                challenge_data = challenge.structured_content
                closed_object(challenge_data, required={"challenge_ref"})
                await self._admission.respond(call_ref, challenge_data["challenge_ref"])
                wire = canonical_json(
                    {
                        "version": "custody-wire.v1",
                        "call_ref": str(call_ref),
                        "challenge_ref": challenge_data["challenge_ref"],
                        "operation_digest": minted["operation_digest"],
                        "method": method,
                        "arguments": arguments,
                    }
                ).decode("utf-8")
                with suppress_instrumentation():
                    result = await self._client.call_tool("custody.apply", {"wire": wire})
                if type(result.structured_content) is not dict:
                    raise CustodyError("unknown")
                return result.structured_content
        except CustodyError:
            raise
        except Exception:
            # Includes a transport-disconnected response after possible COMMIT.
            # Never retry a side effect or expose raw provider/wire diagnostics.
            raise CustodyError("unknown") from None
