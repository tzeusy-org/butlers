"""Private browser command producer on the existing restricted auth pool.

HTTP middleware captures the actual cookie/CSRF proof before body handling.
The installed SQL engine selects current epochs, rechecks sessions, binds this
writer and derives the canonical owner. No DTO, cached principal or header key
can supply these inputs. Actual API lifecycle must install this fixed producer.
"""

from __future__ import annotations

from fastmcp import Client

from butlers.api.owner_auth.context import (
    OwnerCustodyProof,
    in_http_request,
    owner_custody_proof,
)
from butlers.api.owner_auth.service import OwnerAuthService
from butlers.core.custody_admission import CustodyAdmission
from butlers.core.custody_control import (
    CustodyControlTransport,
    PreparedCustodyCommand,
    register_prepared_command,
)
from butlers.core.custody_source import CustodyError, canonical_json


class CustodyBrowserProducer:
    """Constructor-only allocation, with no caller URL or auth proof argument."""

    def __init__(
        self, admission: CustodyAdmission, service: OwnerAuthService, switchboard: Client
    ) -> None:
        if (
            admission.profile.actor != "dashboard"
            or admission.profile.role != "dashboard_auth_api"
            or admission._pool is not service.pool
            or not admission._ready
        ):
            raise CustodyError("refused")
        self._admission, self._service = admission, service
        self._transport = CustodyControlTransport(admission, switchboard)

    async def prepare(self, operation: str, selection: dict) -> PreparedCustodyCommand:
        """Private request callback; SQL checks the actual proof independently.

        A later revoke wins at source registration and again at the destination
        domain COMMIT. The HTTP verdict is never reused as a command permission.
        """
        proof = owner_custody_proof.get()
        if (
            not in_http_request.get()
            or type(proof) is not OwnerCustodyProof
            or proof.origin != self._service.config.origin
            or proof.rp_id != self._service.config.rp_id
            or proof.key_generation != self._service.config.key_generation
        ):
            raise CustodyError("refused")
        if operation not in {"hold", "release", "replaced", "revoke_sessions", "eligibility"}:
            raise CustodyError("invalid")
        canonical_json(selection)
        async with self._admission.writer() as writer:
            prepared = await writer._call(
                "SELECT dashboard_auth.custody_prepare($1,$2::jsonb,$3::jsonb)",
                operation,
                proof.sql_values(),
                selection,
            )
        return await register_prepared_command(self._admission, prepared, operation, selection)

    async def execute(self, source: PreparedCustodyCommand) -> dict:
        """Uses only a privately prepared source after its known source COMMIT."""
        return await self._transport.execute(source, read_only=source.operation == "eligibility")
