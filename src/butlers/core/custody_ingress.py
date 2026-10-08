"""Switchboard-owned accepted-row reader and bounded cheap-LOCK parser.

An accepted row, locator or parsed request is not authentication. The registered
source adapter must compose this read with genuine current owning resolution,
issuer/target binding, immutable registration and the committing engine. These
private callbacks are not model tools and introduce no cross-schema reader.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import asyncpg
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.core.custody_admission import CustodyAdmission, CustodyWriter
from butlers.core.custody_bindings import channel_origin_digest
from butlers.core.custody_source import (
    CustodyError,
    canonical_json,
    canonical_targets,
    canonical_uuid,
    closed_object,
    digest,
    utc_timestamp,
)

if TYPE_CHECKING:
    from butlers.core.custody_control import PreparedCustodyCommand


@dataclass(frozen=True, repr=False)
class AcceptedCustodyReport:
    """Private frozen actual-row observation, never an owner/source grant."""

    record_id: uuid.UUID
    received_at: datetime
    channel_type: str
    sender_identity: str
    origin_digest: str
    row_digest: str
    normalized_text: str

    def __reduce__(self):
        raise TypeError("custody accepted report cannot be serialized")


@dataclass(frozen=True)
class ExplicitLockRequest:
    reason: str
    exact_target_ids: tuple[uuid.UUID, ...] = ()
    device_label: str | None = None


def parse_explicit_lock(report: AcceptedCustodyReport) -> ExplicitLockRequest | None:
    """Parse the actual frozen source text, never a model-generated LOCK flag.

    Exact IDs are locators requiring server-owned resolution/current selection.
    Natural-language loss/theft entries need explicit unambiguous selection or
    a selection confirmation bound to this same genuine accepted source; this
    parser cannot infer a receiving bot, mailbox, device or credential account.
    """
    text = report.normalized_text.strip()
    if text.upper() == "LOCK":
        return ExplicitLockRequest("lost")  # Information/selection is still required.
    exact = re.fullmatch(r"LOCK ([0-9a-f -]+)", text, flags=re.ASCII | re.I)
    if exact:
        values = exact[1].split()
        if not values or len(values) > 64:
            raise CustodyError("invalid")
        selected = tuple(uuid.UUID(canonical_uuid(value.lower())) for value in values)
        if len(set(selected)) != len(selected):
            raise CustodyError("invalid")
        return ExplicitLockRequest("lost", tuple(sorted(selected)))
    lost = re.fullmatch(
        r"(?:I (?:have )?lost (?:my|the)|my) (phone|laptop|device)(?: is lost)?[.!]?",
        text,
        flags=re.ASCII | re.I,
    )
    if lost:
        # A bare "my phone" describes an object but supplies no loss/LOCK act.
        if text.lower().startswith("my ") and " is lost" not in text.lower():
            return None
        return ExplicitLockRequest("lost", device_label=lost[1].lower())
    stolen = re.fullmatch(
        r"my (phone|laptop|device) (?:was|has been) stolen[.!]?", text, flags=re.ASCII | re.I
    )
    if stolen:
        return ExplicitLockRequest("stolen", device_label=stolen[1].lower())
    return None


class CustodyAcceptedIngress:
    """Fixed source-owning canonical reader on the actual bound writer."""

    def __init__(self, admission: CustodyAdmission) -> None:
        if admission.profile.actor != "switchboard":
            raise CustodyError("refused")
        self._admission = admission

    async def read(self, writer: CustodyWriter, record_id: uuid.UUID) -> AcceptedCustodyReport:
        connection = writer._connection
        if type(record_id) is not uuid.UUID or not self._admission.owns_writer(writer):
            raise CustodyError("refused")
        try:
            with suppress_instrumentation():
                rows = await connection.fetch(
                    "SELECT id,received_at,request_context,raw_payload,normalized_text,"
                    "schema_version,direction,request_context::text AS context_bytes,"
                    "raw_payload::text AS payload_bytes FROM switchboard.message_inbox "
                    "WHERE id=$1 ORDER BY received_at FOR UPDATE",
                    record_id,
                )
                # The actual partition key is (received_at,id). Never choose
                # one of two ambiguous physical rows by LIMIT or caller time.
                if len(rows) != 1:
                    raise CustodyError("unavailable")
                row = rows[0]
                context = row["request_context"]
                payload, text = row["raw_payload"], row["normalized_text"]
                if (
                    row["schema_version"] != "message_inbox.v2"
                    or row["direction"] != "inbound"
                    or type(context) is not dict
                    or type(payload) is not dict
                    or type(text) is not str
                    or context.get("request_id") != str(record_id)
                    or context.get("payload_type") == "conversation_history"
                    or context.get("source_sender_identities")
                ):
                    raise CustodyError("unavailable")
                channel = context.get("source_channel")
                sender = context.get("source_sender_identity")
                if type(channel) is not str or type(sender) is not str:
                    raise CustodyError("unavailable")
                # The receiving endpoint stays in the original row digest; it
                # never stands in for the sending owner endpoint.
                content: dict[str, Any] = {
                    "record_id": str(record_id),
                    "received_at": utc_timestamp(row["received_at"]),
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
                # Hash PostgreSQL's actual stored JSONB text, not a second
                # serialization which may change numeric rendering. The fixed
                # engine can independently reproduce this exact own-row digest.
                for name in ("context", "payload"):
                    content[name + "_sha256"] = hashlib.sha256(
                        row[name + "_bytes"].encode("utf-8")
                    ).hexdigest()
                return AcceptedCustodyReport(
                    record_id,
                    row["received_at"],
                    channel,
                    sender,
                    channel_origin_digest(channel, sender),
                    digest(content),
                    text,
                )
        except asyncpg.PostgresError as exc:
            code = "unknown" if exc.sqlstate and exc.sqlstate.startswith("08") else "unavailable"
            raise CustodyError(code) from None
        except (OSError, asyncpg.InterfaceError):
            raise CustodyError("unknown") from None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise CustodyError("unavailable") from None

    async def assert_unchanged(
        self, writer: CustodyWriter, original: AcceptedCustodyReport
    ) -> AcceptedCustodyReport:
        current = await self.read(writer, original.record_id)
        if current != original:
            raise CustodyError("refused")
        return current

    async def capture_lock(
        self, record_id: uuid.UUID, *, original: AcceptedCustodyReport | None = None
    ) -> PreparedCustodyCommand:
        """Capture exact LOCK IDs from the own accepted row after resolution.

        The native producer first invokes the fixed owning Relationship MCP
        resolver outside this transaction. Its DTO is deliberately absent from
        this interface. SQL compiles the owner/issuer/current target selection
        from the protected current projection and independently reads the own
        accepted row. A caller cannot supply that association or a generation.
        Natural-language/bare LOCK needs the separate selection-confirmation
        flow; this method never guesses the device mentioned by a report.
        """
        from butlers.core.custody_control import PreparedCustodyCommand

        async with self._admission.writer() as writer:
            report = await self.read(writer, record_id)
            if original is not None and report != original:
                raise CustodyError("refused")
            request = parse_explicit_lock(report)
            if request is None or not request.exact_target_ids:
                raise CustodyError("unavailable")
            requested = [str(value) for value in request.exact_target_ids]
            source = await writer.register_source(
                "accepted_ingress",
                {
                    "locator": "inbox:" + str(record_id),
                    "revision": 1,
                    "content_digest": report.row_digest,
                    "origin_digest": report.origin_digest,
                    "owner_entity_id": None,
                    "issuer_target": None,
                    "target_set": [],
                    "target_set_version": 1,
                    "expires_at": utc_timestamp(report.received_at + timedelta(minutes=5)),
                    "intent": "LOCK",
                    "selected_target_ids": requested,
                },
            )
            closed_object(source, required={"source_ref", "source_digest", "projection"})
            projection = closed_object(
                source["projection"],
                required={
                    "locator",
                    "revision",
                    "content_digest",
                    "origin_digest",
                    "owner_entity_id",
                    "issuer_target",
                    "issuer_binding",
                    "target_set",
                    "target_set_version",
                    "expires_at",
                    "intent",
                    "selected_target_ids",
                },
            )
            if (
                source["source_digest"] != digest(projection)
                or projection["locator"] != "inbox:" + str(record_id)
                or type(projection["revision"]) is not int
                or projection["revision"] != 1
                or projection["content_digest"] != report.row_digest
                or projection["origin_digest"] != report.origin_digest
                or projection["intent"] != "LOCK"
                or projection["selected_target_ids"] != requested
            ):
                raise CustodyError("refused")
            canonical_uuid(projection["owner_entity_id"])
            canonical_uuid(projection["issuer_target"])
            canonical_targets([projection["issuer_binding"]])
            targets = canonical_targets(projection["target_set"])
            if (
                targets != projection["target_set"]
                or sorted(value["target_id"] for value in targets) != requested
                or projection["issuer_binding"]["target_id"] != projection["issuer_target"]
                or projection["issuer_binding"]["binding_digest"] != report.origin_digest
                or type(projection["target_set_version"]) is not int
                or projection["target_set_version"] < 1
            ):
                raise CustodyError("refused")
            source_ref = uuid.UUID(canonical_uuid(source["source_ref"]))
            selection = canonical_json(
                {"target_set": targets, "target_set_version": projection["target_set_version"]}
            )
        # Do not hand out the private source until registration COMMIT is known.
        return PreparedCustodyCommand(
            source_ref, source_ref, source_ref, source["source_digest"], "hold", selection
        )
