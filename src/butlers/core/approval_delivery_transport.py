"""Trusted transport and Messenger ledger for approval-delivery recovery.

The public ``notify.v1`` recovery block carries correlation only.  Authority is
derived from FastMCP access-token claims at each server boundary and converted
to :class:`TrustedRecoveryContext`, which is never part of the caller model.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from fastmcp.server.dependencies import AccessToken

from butlers.core.approval_delivery_worker import DeliveryClaim, HandoffResult

RecoveryOperation = Literal["handoff", "reconcile"]
RecoveryMode = Literal["single", "burst_digest"]
RecoverySubjectKind = Literal["action", "cohort"]

_CANONICAL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_OPAQUE_REFERENCE = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")


class RecoveryAuthorityError(ValueError):
    """Recovery correlation failed a transport-derived authority check."""


@dataclass(frozen=True, slots=True)
class TrustedRecoveryContext:
    """Switchboard-derived authority forwarded only through internal context."""

    issuer: str
    owning_schema: str
    operation: RecoveryOperation
    subject_kind: RecoverySubjectKind
    subject_key: str
    presentation_key: str
    presentation_generation: int
    presentation_mode: RecoveryMode

    def validate(self) -> None:
        if not _CANONICAL_NAME.fullmatch(self.issuer):
            raise RecoveryAuthorityError("recovery issuer is invalid")
        if not _CANONICAL_NAME.fullmatch(self.owning_schema):
            raise RecoveryAuthorityError("recovery owning schema is invalid")
        if self.issuer != self.owning_schema:
            raise RecoveryAuthorityError("recovery issuer and owning schema do not match")
        if not 1 <= self.presentation_generation <= 1000:
            raise RecoveryAuthorityError("recovery presentation generation is invalid")
        expected_kind: RecoverySubjectKind = (
            "cohort" if self.subject_key.startswith("approval-cohort:") else "action"
        )
        expected_mode: RecoveryMode = "burst_digest" if expected_kind == "cohort" else "single"
        expected_prefix = (
            f"approval-cohort:{self.owning_schema}:"
            if expected_kind == "cohort"
            else f"approval:{self.owning_schema}:"
        )
        if self.subject_kind != expected_kind or self.presentation_mode != expected_mode:
            raise RecoveryAuthorityError("recovery subject kind and mode do not match")
        if not self.subject_key.startswith(expected_prefix):
            raise RecoveryAuthorityError("recovery subject does not belong to owning schema")
        raw_subject_id = self.subject_key.removeprefix(expected_prefix)
        try:
            subject_id = uuid.UUID(raw_subject_id)
        except ValueError as exc:
            raise RecoveryAuthorityError("recovery subject identity is invalid") from exc
        if str(subject_id) != raw_subject_id:
            raise RecoveryAuthorityError("recovery subject identity is not canonical")
        if self.presentation_key != (f"{self.subject_key}:p:{self.presentation_generation}"):
            raise RecoveryAuthorityError("recovery presentation key does not match subject")

    def as_internal_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "issuer": self.issuer,
            "owning_schema": self.owning_schema,
            "operation": self.operation,
            "subject_kind": self.subject_kind,
            "subject_key": self.subject_key,
            "presentation_key": self.presentation_key,
            "presentation_generation": self.presentation_generation,
            "presentation_mode": self.presentation_mode,
        }

    @classmethod
    def from_internal_dict(cls, value: Mapping[str, Any]) -> TrustedRecoveryContext:
        try:
            context = cls(
                issuer=str(value["issuer"]),
                owning_schema=str(value["owning_schema"]),
                operation=value["operation"],
                subject_kind=value["subject_kind"],
                subject_key=str(value["subject_key"]),
                presentation_key=str(value["presentation_key"]),
                presentation_generation=int(value["presentation_generation"]),
                presentation_mode=value["presentation_mode"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RecoveryAuthorityError("trusted recovery context is incomplete") from exc
        if context.operation not in {"handoff", "reconcile"}:
            raise RecoveryAuthorityError("trusted recovery operation is invalid")
        context.validate()
        return context


def authenticated_daemon_name(
    access_token: AccessToken | None,
    *,
    required_scope: str,
) -> str | None:
    """Return the daemon identity only from a tightly bound access token."""
    if access_token is None or required_scope not in access_token.scopes:
        return None
    claims = access_token.claims if isinstance(access_token.claims, Mapping) else {}
    if claims.get("actor_type") != "daemon":
        return None
    name = claims.get("butler_name")
    if not isinstance(name, str) or not _CANONICAL_NAME.fullmatch(name):
        return None
    if access_token.client_id != f"butler:{name}":
        return None
    return name


def recovery_context_from_request(
    *,
    issuer: str,
    recovery: Any,
) -> TrustedRecoveryContext:
    """Convert validated caller correlation into server-held trusted context."""
    context = TrustedRecoveryContext(
        issuer=issuer,
        owning_schema=issuer,
        operation=recovery.operation,
        subject_kind=recovery.subject_kind,
        subject_key=recovery.subject_key,
        presentation_key=recovery.presentation_key,
        presentation_generation=recovery.presentation_generation,
        presentation_mode=recovery.presentation_mode,
    )
    context.validate()
    return context


class ApprovalRecoveryRuntime:
    """Dormant source-side runtime for the fenced approval worker.

    Construction alone has no side effect.  A later rollout slice may attach an
    instance to a daemon after authenticated transport is operationally enabled.
    """

    def __init__(
        self,
        *,
        source_butler: str,
        owning_schema: str,
        dispatch: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        resolve_owner_recipient: Callable[[], Awaitable[str | None]],
        resolve_callback_secret: Callable[[], Awaitable[str | None]],
    ) -> None:
        if source_butler != owning_schema or not _CANONICAL_NAME.fullmatch(source_butler):
            raise ValueError("approval recovery source and owning schema must match")
        self._source_butler = source_butler
        self._owning_schema = owning_schema
        self._dispatch = dispatch
        self._resolve_owner_recipient = resolve_owner_recipient
        self._resolve_callback_secret = resolve_callback_secret

    async def resolve_verified_owner_recipient(self) -> str | None:
        return await self._resolve_owner_recipient()

    async def resolve_callback_secret(self) -> str | None:
        return await self._resolve_callback_secret()

    @staticmethod
    def _recovery_payload(claim: DeliveryClaim, operation: RecoveryOperation) -> dict[str, Any]:
        return {
            "operation": operation,
            "subject_kind": claim.subject_kind,
            "subject_key": claim.subject_key,
            "presentation_key": claim.presentation_key,
            "presentation_generation": claim.presentation_generation,
            "presentation_mode": claim.presentation_mode,
        }

    async def handoff(
        self,
        claim: DeliveryClaim,
        envelope: dict[str, Any],
    ) -> HandoffResult:
        payload = dict(envelope)
        payload["recovery"] = self._recovery_payload(claim, "handoff")
        return self._parse_result(await self._dispatch(payload))

    async def reconcile(self, claim: DeliveryClaim) -> HandoffResult:
        payload = {
            "schema_version": "notify.v1",
            "origin_butler": self._source_butler,
            "delivery": {
                "intent": "approval_request",
                "channel": "telegram",
                "message": "",
            },
            "recovery": self._recovery_payload(claim, "reconcile"),
        }
        return self._parse_result(await self._dispatch(payload))

    @staticmethod
    def _parse_result(result: Mapping[str, Any]) -> HandoffResult:
        raw = result.get("handoff")
        if not isinstance(raw, Mapping):
            raise RuntimeError("trusted approval recovery returned no handoff result")
        classification = raw.get("classification")
        if classification not in {"confirmed", "safe_retry", "ambiguous"}:
            raise RuntimeError("trusted approval recovery returned an invalid classification")
        reason = raw.get("reason_code")
        reference = raw.get("provider_reference")
        if reference is not None and (
            not isinstance(reference, str) or not _OPAQUE_REFERENCE.fullmatch(reference)
        ):
            raise RuntimeError("trusted approval recovery returned an invalid reference")
        return HandoffResult(
            classification=classification,
            reason_code=reason if isinstance(reason, str) else None,
            provider_reference=reference,
        )


class MessengerApprovalHandoffRepository:
    """Own the content-blind trusted tuple at Messenger's provider boundary."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def process(
        self,
        context: TrustedRecoveryContext,
        *,
        provider_call: Callable[[], Awaitable[Any]] | None,
        reconcile_call: Callable[[str], Awaitable[HandoffResult]] | None = None,
        preflight_reason: str = "transport_unavailable",
    ) -> HandoffResult:
        context.validate()
        if preflight_reason not in {"transport_unavailable", "owner_recipient_unavailable"}:
            raise ValueError("invalid approval recovery preflight reason")
        action = await self._admit_or_classify(
            context,
            provider_available=provider_call is not None,
            preflight_reason=preflight_reason,
        )
        if isinstance(action, HandoffResult):
            if context.operation == "reconcile" and action.classification == "ambiguous":
                return await self._reconcile(context, reconcile_call)
            return action
        if action == "reconcile":
            return await self._reconcile(context, reconcile_call)
        if provider_call is None:
            raise RuntimeError("provider preflight result was not persisted")
        try:
            provider_result = await provider_call()
        except Exception:
            result = HandoffResult("ambiguous", "provider_outcome_unknown")
        else:
            result = HandoffResult(
                "confirmed",
                provider_reference=_provider_reference(provider_result),
            )
        return await self._store_result(context, result)

    async def _admit_or_classify(
        self,
        context: TrustedRecoveryContext,
        *,
        provider_available: bool,
        preflight_reason: str,
    ) -> Literal["provider", "reconcile"] | HandoffResult:
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    INSERT INTO approval_delivery_handoffs (
                        issuer, owning_schema, subject_key, presentation_key,
                        presentation_generation, presentation_mode
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (issuer, owning_schema, presentation_key, presentation_mode)
                    DO NOTHING
                    RETURNING id
                    """,
                    context.issuer,
                    context.owning_schema,
                    context.subject_key,
                    context.presentation_key,
                    context.presentation_generation,
                    context.presentation_mode,
                )
                current = await connection.fetchrow(
                    """
                    SELECT handoff_class, reason_code, provider_reference,
                           provider_started_at
                    FROM approval_delivery_handoffs
                    WHERE issuer = $1 AND owning_schema = $2
                      AND presentation_key = $3 AND presentation_mode = $4
                    FOR UPDATE
                    """,
                    context.issuer,
                    context.owning_schema,
                    context.presentation_key,
                    context.presentation_mode,
                )
                if current is None:
                    raise RuntimeError("approval recovery handoff admission failed")
                classification = current["handoff_class"]
                if classification == "confirmed":
                    return _result_from_row(current)
                if context.operation == "reconcile":
                    if classification == "safe_retry":
                        return _result_from_row(current)
                    if current["provider_started_at"] is None:
                        return await self._write_result_on_connection(
                            connection,
                            context,
                            HandoffResult("safe_retry", "transport_unavailable"),
                        )
                    return "reconcile"
                if classification == "ambiguous":
                    return _result_from_row(current)
                if (
                    classification is None
                    and row is None
                    and current["provider_started_at"] is not None
                ):
                    return "reconcile"
                if not provider_available:
                    return await self._write_result_on_connection(
                        connection,
                        context,
                        HandoffResult("safe_retry", preflight_reason),
                    )
                await connection.execute(
                    """
                    UPDATE approval_delivery_handoffs
                    SET provider_started_at = clock_timestamp(), handoff_class = NULL,
                        reason_code = NULL, provider_reference = NULL,
                        completed_at = NULL, updated_at = clock_timestamp()
                    WHERE issuer = $1 AND owning_schema = $2
                      AND presentation_key = $3 AND presentation_mode = $4
                    """,
                    context.issuer,
                    context.owning_schema,
                    context.presentation_key,
                    context.presentation_mode,
                )
                return "provider"

    async def _reconcile(
        self,
        context: TrustedRecoveryContext,
        reconcile_call: Callable[[str], Awaitable[HandoffResult]] | None,
    ) -> HandoffResult:
        if reconcile_call is None:
            result = HandoffResult("ambiguous", "provider_outcome_unknown")
        else:
            try:
                result = await reconcile_call(context.presentation_key)
            except Exception:
                result = HandoffResult("ambiguous", "provider_outcome_unknown")
        return await self._store_result(context, result)

    async def _store_result(
        self, context: TrustedRecoveryContext, result: HandoffResult
    ) -> HandoffResult:
        _validate_handoff_result(result)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                return await self._write_result_on_connection(connection, context, result)

    @staticmethod
    async def _write_result_on_connection(
        connection: Any,
        context: TrustedRecoveryContext,
        result: HandoffResult,
    ) -> HandoffResult:
        row = await connection.fetchrow(
            """
            UPDATE approval_delivery_handoffs
            SET handoff_class = CASE
                    WHEN handoff_class = 'confirmed' THEN handoff_class
                    WHEN $5 = 'confirmed' THEN $5
                    WHEN handoff_class = 'ambiguous'
                         AND $8 = 'reconcile' AND $5 = 'safe_retry' THEN $5
                    WHEN handoff_class = 'ambiguous' THEN handoff_class
                    ELSE $5
                END,
                reason_code = CASE
                    WHEN handoff_class = 'confirmed' THEN reason_code
                    WHEN $5 = 'confirmed' THEN $6
                    WHEN handoff_class = 'ambiguous'
                         AND $8 = 'reconcile' AND $5 = 'safe_retry' THEN $6
                    WHEN handoff_class = 'ambiguous' THEN reason_code
                    ELSE $6
                END,
                provider_reference = CASE
                    WHEN handoff_class = 'confirmed' THEN provider_reference
                    WHEN $5 = 'confirmed' THEN $7
                    WHEN handoff_class = 'ambiguous'
                         AND $8 = 'reconcile' AND $5 = 'safe_retry' THEN $7
                    WHEN handoff_class = 'ambiguous' THEN provider_reference
                    ELSE $7
                END,
                completed_at = clock_timestamp(), updated_at = clock_timestamp()
            WHERE issuer = $1 AND owning_schema = $2
              AND presentation_key = $3 AND presentation_mode = $4
            RETURNING handoff_class, reason_code, provider_reference
            """,
            context.issuer,
            context.owning_schema,
            context.presentation_key,
            context.presentation_mode,
            result.classification,
            result.reason_code,
            result.provider_reference,
            context.operation,
        )
        if row is None:
            raise RuntimeError("approval recovery handoff result lost its trusted tuple")
        return _result_from_row(row)


def _validate_handoff_result(result: HandoffResult) -> None:
    if result.classification == "safe_retry" and result.reason_code not in {
        "transport_unavailable",
        "owner_recipient_unavailable",
        "provider_preflight_failed",
    }:
        raise ValueError("safe retry requires a pre-provider reason")
    if result.classification == "ambiguous" and result.reason_code != "provider_outcome_unknown":
        raise ValueError("ambiguous handoff requires provider_outcome_unknown")
    if result.classification == "confirmed" and result.reason_code is not None:
        raise ValueError("confirmed handoff cannot carry a reason")
    if result.provider_reference is not None and not _OPAQUE_REFERENCE.fullmatch(
        result.provider_reference
    ):
        raise ValueError("provider reference must be a bounded opaque identifier")


def _result_from_row(row: Mapping[str, Any]) -> HandoffResult:
    return HandoffResult(
        classification=row["handoff_class"],
        reason_code=row["reason_code"],
        provider_reference=row["provider_reference"],
    )


def _provider_reference(provider_result: Any) -> str | None:
    if not isinstance(provider_result, Mapping):
        return None
    candidates = [provider_result.get("message_id"), provider_result.get("delivery_id")]
    nested = provider_result.get("result")
    if isinstance(nested, Mapping):
        candidates.extend((nested.get("message_id"), nested.get("delivery_id")))
    for candidate in candidates:
        if candidate is None:
            continue
        normalized = str(candidate)
        if _OPAQUE_REFERENCE.fullmatch(normalized):
            return normalized
    return None


__all__ = [
    "ApprovalRecoveryRuntime",
    "MessengerApprovalHandoffRepository",
    "RecoveryAuthorityError",
    "TrustedRecoveryContext",
    "authenticated_daemon_name",
    "recovery_context_from_request",
]
