"""Fixed Switchboard command receiver for the registered custody tool guard.

Commands and current owner/source/target checks belong to the SQL transaction,
not this DTO parser. The private call/writer are installed by the receiving
guard; caller IDs only locate the exact immutable command bound by the source.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping

from butlers.core.custody_admission import CustodyAdmission, CustodyWriter
from butlers.core.custody_source import (
    CustodyError,
    VerifiedCustodyCall,
    canonical_uuid,
    closed_object,
    current_custody_call,
)


class CustodyCommandReceiver:
    """Constructor-owned handlers, with no arbitrary SQL or callback selector."""

    def __init__(self, admission: CustodyAdmission) -> None:
        if admission.profile.actor != "switchboard":
            raise CustodyError("refused")
        self._admission = admission

    def handlers(
        self,
    ) -> Mapping[str, Callable[[CustodyWriter, VerifiedCustodyCall], Awaitable[dict]]]:
        return {"custody.commit": self.commit, "custody.result": self.result}

    def _command(self, writer: CustodyWriter, call: VerifiedCustodyCall, method: str) -> uuid.UUID:
        if (
            current_custody_call() is not call
            or self._admission.current_verified_writer() is not writer
            or call.method != method
        ):
            raise CustodyError("refused")
        closed_object(call.arguments, required={"command_id"})
        return uuid.UUID(canonical_uuid(call.arguments["command_id"]))

    async def commit(self, writer: CustodyWriter, call: VerifiedCustodyCall) -> dict:
        command_id = self._command(writer, call, "custody.commit")
        return await writer.commit_command(command_id, call.call_ref)

    async def result(self, writer: CustodyWriter, call: VerifiedCustodyCall) -> dict:
        command_id = self._command(writer, call, "custody.result")
        return await writer.result_read(command_id, call.call_ref)
