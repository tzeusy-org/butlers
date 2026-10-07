"""Private Relationship report admission under the trusted daemon/host boundary.

Neither runtime locators nor public tool arguments produce a report. Source
capabilities are ephemeral and verified at the configured source daemon; the
accepted inbox stores the frozen report, never the bearer capability. Identity
resolution attributes a sender, it does not authenticate external transport.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from starlette.responses import JSONResponse
from starlette.routing import Route

AUTHORITY_TOKENS = frozenset({"owner", "owner_device", "third_party", "system", "mixed"})
INVOCATION_HEADER = "X-Butlers-Fact-Invocation"
SOURCE_HEADER = "X-Butlers-Fact-Source"
VERIFIER_PATH = "/internal/fact-source/verify"
RECOVERY_PATH = "/internal/fact-source/recover"
RECEIVER_PATH = "/internal/fact-source/receiver"


@dataclass(frozen=True, slots=True)
class FactWriteContext:
    authority: str | None
    original_entity_id: uuid.UUID | None = None
    entity_created_at: datetime | None = None
    live_entity_id: uuid.UUID | None = None
    confirmation_entity_id: uuid.UUID | None = None
    confirmation_original_entity_id: uuid.UUID | None = None
    confirmed_at: datetime | None = None
    confirmation_source: str | None = None
    preserve: bool = False

    @property
    def owner_class(self) -> bool:
        return self.authority in {"owner", "owner_device"}

    @property
    def verified(self) -> bool:
        return self.owner_class or self.confirmed_at is not None

    def to_record(self) -> dict[str, Any]:
        return {
            key: str(value)
            if isinstance(value, uuid.UUID)
            else value.isoformat()
            if isinstance(value, datetime)
            else value
            for key, value in ((name, getattr(self, name)) for name in self.__slots__)
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> FactWriteContext:
        values = {name: record.get(name) for name in cls.__slots__}
        for key in (
            "original_entity_id",
            "live_entity_id",
            "confirmation_entity_id",
            "confirmation_original_entity_id",
        ):
            values[key] = uuid.UUID(values[key]) if values[key] else None
        for key in ("entity_created_at", "confirmed_at"):
            values[key] = datetime.fromisoformat(values[key]) if values[key] else None
        values["preserve"] = bool(values["preserve"])
        if values["authority"] not in AUTHORITY_TOKENS | {None}:
            raise ValueError("invalid stored content authority")
        return cls(**values)


def accepted_source_digest(row: Any) -> str:
    context = dict(row["request_context"] or {})
    context.pop("_fact_source_report", None)
    return hashlib.sha256(
        json.dumps(
            {"context": context, "payload": row["raw_payload"], "text": row["normalized_text"]},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def dashboard_origin_digest(envelope: dict[str, Any]) -> str | None:
    """Bind one admitted turn's canonical origin, body and destination.

    The transport observation time can change on a retry. All stable accepted
    event/source/thread/target and content fields remain bound. This digest is
    a comparison, never a credential or a public caller authority selector.
    """
    try:
        source, event, sender = envelope["source"], envelope["event"], envelope["sender"]
        payload, control = envelope["payload"], envelope["control"]
        raw = payload["raw"]
        conversation = str(uuid.UUID(raw["conversation_id"]))
        message = str(uuid.UUID(raw["message_id"]))
        if (
            source["channel"] != "dashboard"
            or source["provider"] != "internal"
            or source["endpoint_identity"] != f"dashboard:web:{conversation}"
            or event["external_event_id"] != message
            or event["external_thread_id"] != conversation
            or raw["source"] != "dashboard"
            or sender["identity"] != "dashboard:operator"
            or not isinstance(raw["message"], str)
            or not isinstance(payload["normalized_text"], str)
        ):
            return None
        binding = {
            "source": source,
            "event": {key: event.get(key) for key in ("external_event_id", "external_thread_id")},
            "sender": sender,
            "payload": payload,
            "control": {
                key: control.get(key)
                for key in ("policy_tier", "ingestion_tier", "pinned_target", "payload_type")
            },
        }
        return hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    except (KeyError, TypeError, ValueError):
        return None


@dataclass(slots=True)
class _SourceClaim:
    report: FactWriteContext
    request_id: uuid.UUID
    target: str
    expires: float
    source_digest: str
    invocation: tuple[str, str] | None = None


class FactSourceContextRegistry:
    """A source-owned issuer; callers cannot provide a replacement report."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.incarnation = secrets.token_urlsafe(24)
        self._claims: dict[str, _SourceClaim] = {}
        self._reports: dict[uuid.UUID, FactWriteContext] = {}
        self._source_digests: dict[uuid.UUID, str] = {}

    async def resolve_identities(self, channel_type: str, values: list[str]) -> dict[str, Any]:
        """Call the fixed owning-role MCP resolver at an admitted registry target.

        Switchboard never reads Relationship's facts. Neither a caller URL nor
        a caller identity result can select this transport or produce a claim.
        """
        if len(values) > 256:
            results = {}
            for start in range(0, len(values), 256):
                results.update(
                    await self.resolve_identities(channel_type, values[start : start + 256])
                )
            return results
        from fastmcp import Client

        from butlers.core.mcp_urls import canonical_runtime_mcp_url, resolve_cross_container_mcp_url
        from butlers.identity import IdentityResolutionQueryError, ResolvedContact
        from butlers.tools.switchboard.registry.registry import resolve_routing_target

        try:
            target, _ = await resolve_routing_target(self.pool, "relationship")
            if target is None:
                raise ValueError("owning identity resolver unavailable")
            endpoint = resolve_cross_container_mcp_url(
                canonical_runtime_mcp_url(target["endpoint_url"])
            )
            async with Client(endpoint, timeout=5) as client:
                result = await client.call_tool(
                    "identity_resolve_channels",
                    {"channel_type": channel_type, "channel_values": values},
                )
            data = result.data
            if not isinstance(data, dict) or set(data) != set(values):
                raise ValueError("invalid owning identity result")
            identities = {}
            for value, record in data.items():
                if record is None:
                    identities[value] = None
                    continue
                if not isinstance(record, dict) or not isinstance(record.get("roles"), list):
                    raise ValueError("invalid owning identity result")
                identities[value] = ResolvedContact(
                    entity_id=uuid.UUID(str(record["entity_id"])),
                    name=record.get("name"),
                    roles=record["roles"],
                    is_unidentified=record.get("is_unidentified") is True,
                )
            return identities
        except Exception as exc:
            # Provider/endpoint exception text never enters durable evidence.
            raise IdentityResolutionQueryError(type(exc).__name__) from None

    async def assert_sender_channel(
        self,
        entity_id: uuid.UUID,
        channel_type: str,
        channel_value: str,
    ) -> None:
        """Keep the deterministic ingress hook on Relationship's owning role."""
        from fastmcp import Client

        from butlers.core.mcp_urls import canonical_runtime_mcp_url, resolve_cross_container_mcp_url
        from butlers.tools.switchboard.registry.registry import resolve_routing_target

        try:
            target, _ = await resolve_routing_target(self.pool, "relationship")
            if target is None:
                raise ValueError("owning identity writer unavailable")
            endpoint = resolve_cross_container_mcp_url(
                canonical_runtime_mcp_url(target["endpoint_url"])
            )
            async with Client(endpoint, timeout=5) as client:
                await client.call_tool(
                    "identity_assert_sender_channel",
                    {
                        "entity_id": str(entity_id),
                        "channel_type": channel_type,
                        "channel_value": channel_value,
                    },
                )
        except Exception:
            # The already committed sender reservation still deduplicates.
            import logging

            logging.getLogger(__name__).warning("identity.sender_channel_fact_assertion_failed")

    async def resolve_identity(
        self, _executor: Any, channel_type: str, value: str, *, raise_on_error: bool = False
    ) -> Any:
        try:
            return (await self.resolve_identities(channel_type, [value]))[value]
        except Exception:
            if raise_on_error:
                raise
            return None

    async def capture_accepted_report(
        self,
        inbox_id: uuid.UUID,
        resolved_entity_id: uuid.UUID | None,
        *,
        mixed: bool = False,
    ) -> FactWriteContext:
        """Freeze the canonical resolution exactly once on its accepted inbox.

        This method is called only by the owning Pipeline after resolution, not
        exposed through MCP/HTTP. Recovery reuses the original private record.
        """
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT request_context,raw_payload,normalized_text,lifecycle_state "
                "FROM switchboard.message_inbox WHERE id=$1 FOR UPDATE",
                inbox_id,
            )
            if row is None:
                raise ValueError("accepted source is unavailable")
            context = row["request_context"] or {}
            stored = context.get("_fact_source_report")
            if stored is not None:
                report = FactWriteContext.from_record(stored)
            else:
                # Resolve from the source-owning durable accepted request, not
                # the public process argument's sender/entity fields. The
                # Pipeline result is only a consistency witness.
                sender = context.get("source_sender_identity")
                channel = context.get("source_channel")
                resolved = None
                if isinstance(sender, str) and isinstance(channel, str):
                    resolved = await self.resolve_identity(
                        conn, channel, sender, raise_on_error=False
                    )
                actual_entity_id = resolved.entity_id if resolved is not None else None
                if resolved_entity_id is not None and actual_entity_id != resolved_entity_id:
                    raise ValueError("canonical accepted sender resolution mismatch")
                entity = None
                if actual_entity_id is not None:
                    entity = await conn.fetchrow(
                        "SELECT id, created_at, roles FROM public.entities "
                        "WHERE id=$1 FOR KEY SHARE",
                        actual_entity_id,
                    )
                authority = "mixed" if mixed else "third_party"
                if not mixed and entity is not None and "owner" in (entity["roles"] or []):
                    authority = "owner"
                report = FactWriteContext(
                    authority,
                    entity["id"] if entity else None,
                    entity["created_at"] if entity else None,
                    entity["id"] if entity else None,
                )
                if channel == "dashboard" and not mixed:
                    payload = row["raw_payload"] or {}
                    metadata = (
                        payload.get("metadata") or (payload.get("payload") or {}).get("raw") or {}
                    )
                    source_metadata = metadata.get("source_metadata") or {}
                    message_id = source_metadata.get("dashboard_message_id") or metadata.get(
                        "message_id"
                    )
                    has_stamp = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE "
                        "table_schema='public' AND table_name='dashboard_messages' "
                        "AND column_name='fact_owner_admission')"
                    )
                    if message_id and has_stamp is True:
                        dashboard = await conn.fetchrow(
                            "SELECT content,role,fact_owner_admission "
                            "FROM public.dashboard_messages "
                            "WHERE id=$1",
                            uuid.UUID(str(message_id)),
                        )
                        if (
                            dashboard
                            and dashboard["role"] == "user"
                            and dashboard["content"] == metadata.get("message")
                            and dashboard["fact_owner_admission"] is not None
                            and dashboard["fact_owner_admission"].get("accepted_text_digest")
                            == hashlib.sha256(row["normalized_text"].encode()).hexdigest()
                            and (origin := dashboard_origin_digest(payload)) is not None
                            and dashboard["fact_owner_admission"].get("accepted_origin_digest")
                            == origin
                            and (payload.get("payload") or {}).get("normalized_text")
                            == row["normalized_text"]
                            and (payload.get("source") or {}).get("channel") == channel
                            and (payload.get("sender") or {}).get("identity") == sender
                            and (payload.get("source") or {}).get("endpoint_identity")
                            == context.get("source_endpoint_identity")
                            and (payload.get("event") or {}).get("external_thread_id")
                            == context.get("source_thread_identity")
                        ):
                            report = FactWriteContext.from_record(dashboard["fact_owner_admission"])
                await conn.execute(
                    "UPDATE switchboard.message_inbox SET request_context="
                    "COALESCE(request_context,'{}'::jsonb) || $2::jsonb WHERE id=$1",
                    inbox_id,
                    {"_fact_source_report": report.to_record()},
                )
        self._reports[inbox_id] = report
        self._source_digests[inbox_id] = accepted_source_digest(row)
        return report

    def issue(self, request_id: uuid.UUID, target: str) -> str:
        report = self._reports.get(request_id)
        if report is None:
            raise ValueError("no admitted source producer for this request")
        now = time.monotonic()
        self._claims = {key: claim for key, claim in self._claims.items() if claim.expires > now}
        token = secrets.token_urlsafe(32)
        self._claims[token] = _SourceClaim(
            report, request_id, target, now + 3600, self._source_digests[request_id]
        )
        return token

    async def verify(
        self, token: str, target: str, incarnation: str, invocation: str
    ) -> FactWriteContext:
        claim = self._claims.get(token)
        if claim is None or claim.expires <= time.monotonic() or claim.target != target:
            raise ValueError("source admission unavailable")
        binding = (incarnation, invocation)
        if claim.invocation is not None and claim.invocation != binding:
            raise ValueError("source admission already claimed")
        # Current eligibility comes from the source's own durable record. Do not
        # hold this source lock across the independent Relationship transaction.
        row = await self.pool.fetchrow(
            "SELECT request_context,raw_payload,normalized_text,lifecycle_state "
            "FROM switchboard.message_inbox WHERE id=$1",
            claim.request_id,
        )
        if (
            row is None
            or row["lifecycle_state"] in {"errored", "dead_letter"}
            or accepted_source_digest(row) != claim.source_digest
            or (row["request_context"] or {}).get("_fact_source_report") != claim.report.to_record()
        ):
            raise ValueError("accepted source no longer eligible")
        # No await between compare and first claim: one event-loop atomic claim.
        if claim.invocation is not None and claim.invocation != binding:
            raise ValueError("source admission already claimed")
        claim.invocation = binding
        return claim.report

    async def recover(self, target: str, receiver_capability: str) -> str:
        # Receiver addresses come from this source's actual registry, never the
        # request body. The ephemeral receiver claim proves possession of its
        # own durable, source-verified acceptance, rather than a copied UUID.
        from butlers.tools.switchboard.registry.registry import resolve_routing_target

        receiver, _ = await resolve_routing_target(self.pool, target)
        if receiver is None:
            raise ValueError("registered receiver is unavailable")
        parts = urlsplit(receiver["endpoint_url"])
        url = urlunsplit((parts.scheme, parts.netloc, RECEIVER_PATH, "", ""))
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            response = await client.post(url, json={"capability": receiver_capability})
            response.raise_for_status()
            receipt = response.json()["receipt"]
        request_id = uuid.UUID(receipt["request_id"])
        row = await self.pool.fetchrow(
            "SELECT request_context,raw_payload,normalized_text,lifecycle_state "
            "FROM switchboard.message_inbox WHERE id=$1",
            request_id,
        )
        if (
            row is None
            or row["lifecycle_state"] in {"errored", "dead_letter"}
            or accepted_source_digest(row) != receipt["digest"]
        ):
            raise ValueError("accepted source no longer eligible")
        frozen = (row["request_context"] or {}).get("_fact_source_report")
        if not frozen:
            raise ValueError("original source was not admitted")
        self._reports[request_id] = FactWriteContext.from_record(frozen)
        self._source_digests[request_id] = receipt["digest"]
        return self.issue(request_id, target)

    def recovery_route(self) -> Route:
        async def recover_request(request: Any) -> JSONResponse:
            try:
                body = await request.json()
                capability = await self.recover(body["target"], body["capability"])
                return JSONResponse({"capability": capability})
            except (ValueError, KeyError, TypeError, httpx.HTTPError):
                return JSONResponse({"error": "source recovery unavailable"}, status_code=403)

        return Route(RECOVERY_PATH, recover_request, methods=["POST"])

    def route(self) -> Route:
        async def verify_request(request: Any) -> JSONResponse:
            try:
                body = await request.json()
                report = await self.verify(
                    body["capability"], body["target"], body["incarnation"], body["invocation"]
                )
                claim = self._claims[body["capability"]]
                return JSONResponse(
                    {
                        "report": report.to_record(),
                        "receipt": {
                            "request_id": str(claim.request_id),
                            "digest": claim.source_digest,
                        },
                    }
                )
            except (ValueError, KeyError, TypeError):
                return JSONResponse({"error": "source admission unavailable"}, status_code=403)

        return Route(VERIFIER_PATH, verify_request, methods=["POST"])


class FactReceiverContextRegistry:
    """Ephemeral proof that recovery owns an actually accepted local inbox."""

    def __init__(self, pool: Any, target: str) -> None:
        self.pool, self.target = pool, target
        self._claims: dict[str, tuple[uuid.UUID, uuid.UUID, float]] = {}

    def issue(self, row_id: uuid.UUID, processing_claim_id: uuid.UUID) -> str:
        now = time.monotonic()
        self._claims = {k: v for k, v in self._claims.items() if v[2] > now}
        token = secrets.token_urlsafe(32)
        self._claims[token] = (row_id, processing_claim_id, now + 30)
        return token

    async def verify(self, token: str) -> dict[str, str]:
        claim = self._claims.get(token)
        if not claim or claim[2] <= time.monotonic():
            raise ValueError("receiver recovery admission unavailable")
        row = await self.pool.fetchrow(
            "SELECT route_envelope FROM route_inbox WHERE id=$1 "
            "AND lifecycle_state='processing' AND processing_claim_id=$2",
            claim[0],
            claim[1],
        )
        if row is None:
            raise ValueError("receiver processing claim is unavailable")
        receipt = row["route_envelope"].get("_fact_source_receipt")
        if not receipt:
            raise ValueError("no original source-verified receipt")
        return receipt

    def route(self) -> Route:
        async def verify_request(request: Any) -> JSONResponse:
            try:
                body = await request.json()
                return JSONResponse({"receipt": await self.verify(body["capability"])})
            except (ValueError, KeyError, TypeError):
                return JSONResponse({"error": "receiver admission unavailable"}, status_code=403)

        return Route(RECEIVER_PATH, verify_request, methods=["POST"])

    async def recover_report(
        self,
        row_id: uuid.UUID,
        processing_claim_id: uuid.UUID,
        source_endpoint: str | None,
    ) -> FactWriteContext:
        if not source_endpoint:
            raise ValueError("configured source verifier is unavailable")
        token = self.issue(row_id, processing_claim_id)
        try:
            parts = urlsplit(source_endpoint)
            url = urlunsplit((parts.scheme, parts.netloc, RECOVERY_PATH, "", ""))
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                response = await client.post(url, json={"target": self.target, "capability": token})
                response.raise_for_status()
                fresh_source = response.json()["capability"]
            context_token = _incoming_source.set(fresh_source)
            try:
                return await admit_incoming_source(
                    self.target,
                    "recovery:" + str(processing_claim_id),
                    source_endpoint,
                )
            finally:
                _incoming_source.reset(context_token)
        finally:
            self._claims.pop(token, None)


@dataclass(frozen=True, slots=True)
class _Invocation:
    target: str
    runtime_session: str
    report: FactWriteContext
    source_request: uuid.UUID | None
    deadline: float


# The registries hold private producer objects, not copies from public routing
# context. All processes in a trusted cohost share this in-memory registry.
_source_registry: FactSourceContextRegistry | None = None
_invocations: dict[str, _Invocation] = {}
_incarnation = secrets.token_urlsafe(24)
_current_report: contextvars.ContextVar[FactWriteContext | None] = contextvars.ContextVar(
    "admitted_fact_report", default=None
)
_incoming_source: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "incoming_fact_source_capability", default=None
)
_incoming_receipt: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "private_fact_source_receipt", default=None
)
_pipeline_source: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "canonical_fact_source_request", default=None
)


def register_source_registry(registry: FactSourceContextRegistry) -> None:
    global _source_registry
    _source_registry = registry


def source_registry() -> FactSourceContextRegistry | None:
    return _source_registry


def source_transport_headers(target: str) -> dict[str, str]:
    request_id = _pipeline_source.get()
    if request_id is None:
        # Classification tool requests get the original private accepted source
        # through their admitted invocation, never caller runtime query params.
        return {}
    if _source_registry is None:
        raise ValueError("registered source issuer is unavailable")
    return {SOURCE_HEADER: _source_registry.issue(request_id, target)}


async def admit_incoming_source(
    target: str,
    invocation: str,
    source_endpoint: str | None,
) -> FactWriteContext:
    source = _incoming_source.get()
    if not source:
        return FactWriteContext("third_party")
    if not source_endpoint:
        raise ValueError("configured source verifier is unavailable")
    parts = urlsplit(source_endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("configured source verifier is unavailable")
    url = urlunsplit((parts.scheme, parts.netloc, VERIFIER_PATH, "", ""))
    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
        response = await client.post(
            url,
            json={
                "capability": source,
                "target": target,
                "incarnation": _incarnation,
                "invocation": invocation,
            },
        )
        response.raise_for_status()
        payload = response.json()
        _incoming_receipt.set(payload.get("receipt"))
        return FactWriteContext.from_record(payload["report"])


async def register_invocation(
    target: str,
    runtime_session: str,
    *,
    source_endpoint: str | None,
    routed: bool,
) -> str:
    report = _current_report.get()
    if _incoming_source.get():
        report = await admit_incoming_source(target, runtime_session, source_endpoint)
    elif _pipeline_source.get() is not None:
        if _source_registry is None:
            raise ValueError("source issuer is unavailable")
        report = _source_registry._reports.get(_pipeline_source.get())
    if report is None:
        report = FactWriteContext("third_party" if routed else "system")
    now = time.monotonic()
    for stale_token in [key for key, value in _invocations.items() if value.deadline <= now]:
        _invocations.pop(stale_token, None)
    token = secrets.token_urlsafe(32)
    _invocations[token] = _Invocation(
        target, runtime_session, report, _pipeline_source.get(), time.monotonic() + 3600
    )
    return token


def settle_invocation(token: str | None) -> None:
    if token:
        _invocations.pop(token, None)


def resolve_invocation(token: str | None, target: str) -> _Invocation | None:
    invocation = _invocations.get(token or "")
    if invocation and invocation.target == target and invocation.deadline > time.monotonic():
        return invocation
    return None


def accepted_source_scope(operation: Any) -> Any:
    @wraps(operation)
    async def isolated(*args: Any, **kwargs: Any) -> Any:
        token = _pipeline_source.set(None)
        try:
            return await operation(*args, **kwargs)
        finally:
            source_id = _pipeline_source.get()
            _pipeline_source.reset(token)
            if source_id is not None and _source_registry is not None:
                _source_registry._reports.pop(source_id, None)
                _source_registry._source_digests.pop(source_id, None)

    return isolated


def current_fact_write_context() -> FactWriteContext:
    """No HTTP/MCP admission means internal SYSTEM, never inferred owner."""
    context = _current_report.get()
    if context is not None:
        return context
    from butlers.api.owner_auth.context import in_http_request, verified_http_principal

    if in_http_request.get():
        if verified_http_principal.get() != "owner":
            raise PermissionError("authenticated owner admission is required")
        # Entity linkage is captured on the writing transaction, not here.
        return FactWriteContext("owner_device")
    from butlers.core.tool_call_capture import get_current_runtime_butler_name

    if get_current_runtime_butler_name() is not None:
        return FactWriteContext("third_party")
    return FactWriteContext("system")
