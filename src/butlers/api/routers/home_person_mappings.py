"""Owner-only, content-blind Home Assistant person mapping submission."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from starlette.responses import JSONResponse

from butlers.api.audit_emit import authenticated_principal
from butlers.api.models import ApiResponse, ErrorDetail, ErrorResponse
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.api.routers.audit import append as append_audit
from butlers.metrics_registry import get_or_create_counter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/home/person-mappings", tags=["home-person-mappings"])

_MAX_BODY_BYTES = 32_768
_MAX_MAPPINGS = 50
_LOCK_NAMESPACE = "butlers:dashboard:ha-person-mapping:v1"
_HA_PERSON_ID = re.compile(r"\Aperson\.[a-z0-9_]+\Z", re.ASCII)
_IDEMPOTENCY_KEY = re.compile(r"\A[A-Za-z0-9_-]{43}\Z", re.ASCII)

mapping_batch_total = get_or_create_counter(
    "dashboard_ha_person_mapping_batch_total",
    "Content-blind terminal outcomes for dashboard HA person mapping batches.",
    labelnames=["outcome", "failure_category"],
)


def _get_db_manager() -> Any:
    """Dependency stub replaced by the dashboard application wiring."""
    return None


class MappingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ha_person_id: str
    entity_id: str

    @field_validator("ha_person_id")
    @classmethod
    def _valid_ha_person_id(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 255 or _HA_PERSON_ID.fullmatch(value) is None:
            raise ValueError("invalid Home Assistant person identifier")
        return value

    @field_validator("entity_id")
    @classmethod
    def _canonical_entity_id(cls, value: str) -> str:
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError) as exc:
            raise ValueError("invalid entity identifier") from exc
        if str(parsed) != value:
            raise ValueError("entity identifier is not canonical")
        return value


class MappingBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mappings: list[MappingInput]

    @field_validator("mappings")
    @classmethod
    def _bounded_distinct_batch(cls, mappings: list[MappingInput]) -> list[MappingInput]:
        if not 1 <= len(mappings) <= _MAX_MAPPINGS:
            raise ValueError("mapping batch is outside the accepted bounds")
        ha_ids = [item.ha_person_id for item in mappings]
        entity_ids = [item.entity_id for item in mappings]
        if len(set(ha_ids)) != len(ha_ids) or len(set(entity_ids)) != len(entity_ids):
            raise ValueError("mapping batch contains a duplicate member")
        return mappings


@dataclass(frozen=True)
class MappingReceipt:
    receipt: str
    complete: bool
    received_count: int
    created_count: int
    unchanged_count: int
    conflict_count: int
    invalid_reference_count: int


@dataclass(frozen=True)
class MappingDecision:
    receipt: MappingReceipt
    outcome: Literal["success", "refused"]
    failure_category: (
        Literal["request_invalid", "reference_invalid", "mapping_conflict", "idempotency_conflict"]
        | None
    ) = None


def _error(
    status_code: int,
    code: str,
    message: str,
    receipt: MappingReceipt | None = None,
) -> JSONResponse:
    details = asdict(receipt) if receipt is not None else None
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def _bounded_body(request: Request) -> bytes | None:
    body = bytearray()
    async for chunk in request.stream():
        remaining = _MAX_BODY_BYTES + 1 - len(body)
        body.extend(chunk[:remaining])
        if len(body) > _MAX_BODY_BYTES:
            return None
    return bytes(body)


def _parse_batch(raw_body: bytes) -> MappingBatch:
    decoded = raw_body.decode("utf-8")
    value = json.loads(decoded)
    return MappingBatch.model_validate(value)


def _request_digest(batch: MappingBatch) -> bytes:
    pairs = sorted((item.ha_person_id, item.entity_id) for item in batch.mappings)
    canonical = json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).digest()


def _key_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii")).digest()


def _receipt_from_row(row: asyncpg.Record) -> MappingReceipt:
    return MappingReceipt(
        receipt=str(row["receipt"]),
        complete=row["complete"],
        received_count=row["received_count"],
        created_count=row["created_count"],
        unchanged_count=row["unchanged_count"],
        conflict_count=row["conflict_count"],
        invalid_reference_count=row["invalid_reference_count"],
    )


async def _write_audit(
    connection: asyncpg.Connection,
    actor: str,
    decision: MappingDecision,
) -> None:
    metadata = {
        "receipt": decision.receipt.receipt,
        "complete": decision.receipt.complete,
        "received_count": decision.receipt.received_count,
        "created_count": decision.receipt.created_count,
        "unchanged_count": decision.receipt.unchanged_count,
        "conflict_count": decision.receipt.conflict_count,
        "invalid_reference_count": decision.receipt.invalid_reference_count,
        "outcome": decision.outcome,
    }
    if decision.failure_category is not None:
        metadata["failure_category"] = decision.failure_category
    await append_audit(
        connection,
        actor,
        "home_assistant_person_mapping_batch",
        metadata=metadata,
        result=decision.outcome,
    )


async def _persist_decision(
    connection: asyncpg.Connection,
    *,
    key_digest: bytes,
    request_digest: bytes,
    decision: MappingDecision,
) -> None:
    receipt = decision.receipt
    await connection.execute(
        """
        INSERT INTO public.ha_person_mapping_receipts (
            key_digest, request_digest, receipt, complete, received_count,
            created_count, unchanged_count, conflict_count,
            invalid_reference_count, outcome, failure_category
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        key_digest,
        request_digest,
        uuid.UUID(receipt.receipt),
        receipt.complete,
        receipt.received_count,
        receipt.created_count,
        receipt.unchanged_count,
        receipt.conflict_count,
        receipt.invalid_reference_count,
        decision.outcome,
        decision.failure_category,
    )


async def _decide_batch(
    pool: asyncpg.Pool,
    batch: MappingBatch,
    raw_key: str,
    actor: str,
) -> tuple[MappingDecision, bool]:
    key_hash = _key_digest(raw_key)
    request_hash = _request_digest(batch)
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))",
            _LOCK_NAMESPACE,
        )
        stored = await connection.fetchrow(
            "SELECT * FROM public.ha_person_mapping_receipts WHERE key_digest = $1",
            key_hash,
        )
        if stored is not None:
            if stored["request_digest"] == request_hash:
                decision = MappingDecision(
                    receipt=_receipt_from_row(stored),
                    outcome=stored["outcome"],
                    failure_category=stored["failure_category"],
                )
                await _write_audit(connection, actor, decision)
                return decision, True
            receipt = MappingReceipt(str(uuid.uuid4()), False, len(batch.mappings), 0, 0, 1, 0)
            decision = MappingDecision(receipt, "refused", "idempotency_conflict")
            await _write_audit(connection, actor, decision)
            return decision, False

        entity_ids = [uuid.UUID(item.entity_id) for item in batch.mappings]
        entity_rows = await connection.fetch(
            """
            SELECT id, entity_type, metadata
            FROM public.entities
            WHERE id = ANY($1::uuid[])
            ORDER BY id
            FOR UPDATE
            """,
            entity_ids,
        )
        live_ids = {
            row["id"]
            for row in entity_rows
            if row["entity_type"] == "person"
            and not (row["metadata"] or {}).get("merged_into")
            and not (row["metadata"] or {}).get("deleted_at")
        }
        invalid_count = len(set(entity_ids) - live_ids)
        if invalid_count:
            receipt = MappingReceipt(
                str(uuid.uuid4()), False, len(batch.mappings), 0, 0, 0, invalid_count
            )
            decision = MappingDecision(receipt, "refused", "reference_invalid")
            await _persist_decision(
                connection,
                key_digest=key_hash,
                request_digest=request_hash,
                decision=decision,
            )
            await _write_audit(connection, actor, decision)
            return decision, False

        ha_ids = [item.ha_person_id for item in batch.mappings]
        rows = await connection.fetch(
            """
            SELECT ha_entity_id, entity_id
            FROM connectors.home_assistant_persons
            WHERE ha_entity_id = ANY($1::text[]) OR entity_id = ANY($2::uuid[])
            """,
            ha_ids,
            entity_ids,
        )
        proposed = {(item.ha_person_id, uuid.UUID(item.entity_id)) for item in batch.mappings}
        existing = {(row["ha_entity_id"], row["entity_id"]) for row in rows}
        exact = proposed & existing
        conflicts = {
            pair
            for pair in proposed
            if any(
                row["ha_entity_id"] == pair[0]
                and row["entity_id"] != pair[1]
                or row["entity_id"] == pair[1]
                and row["ha_entity_id"] != pair[0]
                for row in rows
            )
        }
        if conflicts:
            receipt = MappingReceipt(
                str(uuid.uuid4()), False, len(batch.mappings), 0, len(exact), len(conflicts), 0
            )
            decision = MappingDecision(receipt, "refused", "mapping_conflict")
            await _persist_decision(
                connection,
                key_digest=key_hash,
                request_digest=request_hash,
                decision=decision,
            )
            await _write_audit(connection, actor, decision)
            return decision, False

        new_pairs = proposed - exact
        await connection.executemany(
            "INSERT INTO connectors.home_assistant_persons (ha_entity_id, entity_id) "
            "VALUES ($1, $2)",
            sorted(new_pairs),
        )
        receipt = MappingReceipt(
            str(uuid.uuid4()), True, len(batch.mappings), len(new_pairs), len(exact), 0, 0
        )
        decision = MappingDecision(receipt, "success")
        await _persist_decision(
            connection,
            key_digest=key_hash,
            request_digest=request_hash,
            decision=decision,
        )
        await _write_audit(connection, actor, decision)
        return decision, False


def _record_outcome(decision: MappingDecision) -> None:
    category = decision.failure_category or "none"
    mapping_batch_total.labels(outcome=decision.outcome, failure_category=category).inc()
    logger.info(
        "HA person mapping batch outcome=%s failure_category=%s received_count=%d "
        "created_count=%d unchanged_count=%d conflict_count=%d invalid_reference_count=%d",
        decision.outcome,
        category,
        decision.receipt.received_count,
        decision.receipt.created_count,
        decision.receipt.unchanged_count,
        decision.receipt.conflict_count,
        decision.receipt.invalid_reference_count,
    )


async def _record_request_invalid(
    db_manager: Any,
    actor: str,
    receipt: MappingReceipt,
) -> bool:
    """Write the fixed structural-refusal audit; return false if audit is unavailable."""
    try:
        pool = db_manager.pool("switchboard")
        decision = MappingDecision(receipt, "refused", "request_invalid")
        async with pool.acquire() as connection, connection.transaction():
            await _write_audit(connection, actor, decision)
        _record_outcome(decision)
        return True
    except Exception:
        logger.error("HA person mapping batch outcome=error failure_category=database_unavailable")
        return False


async def _request_invalid_response(
    db_manager: Any,
    receipt: MappingReceipt,
) -> JSONResponse:
    actor = authenticated_principal()
    if not await _record_request_invalid(db_manager, actor, receipt):
        return _error(
            503,
            "MAPPING_DATABASE_UNAVAILABLE",
            "Mapping service is unavailable.",
            receipt,
        )
    return _error(422, "INVALID_REQUEST", "Mapping request is invalid.", receipt)


@router.post("", response_model=None)
async def submit_person_mappings(
    request: Request,
    _owner: Literal["owner"] = Depends(require_dashboard_owner_control),
    db_manager: Any = Depends(_get_db_manager),
) -> JSONResponse | ApiResponse[dict[str, Any]]:
    """Atomically submit exact private mappings and return aggregate evidence."""
    if request.url.query:
        receipt = MappingReceipt(str(uuid.uuid4()), False, 0, 0, 0, 0, 0)
        return await _request_invalid_response(db_manager, receipt)

    raw_body = await _bounded_body(request)
    if raw_body is None:
        return _error(413, "REQUEST_BODY_TOO_LARGE", "Request body exceeds 32 KiB.")

    receipt = MappingReceipt(str(uuid.uuid4()), False, 0, 0, 0, 0, 0)
    try:
        batch = _parse_batch(raw_body)
        receipt = MappingReceipt(str(uuid.uuid4()), False, len(batch.mappings), 0, 0, 0, 0)
    except (UnicodeError, json.JSONDecodeError, ValidationError, RecursionError):
        return await _request_invalid_response(db_manager, receipt)

    raw_key = request.headers.get("idempotency-key", "")
    if _IDEMPOTENCY_KEY.fullmatch(raw_key) is None:
        return await _request_invalid_response(db_manager, receipt)
    try:
        decoded_key = base64.urlsafe_b64decode(raw_key + "=")
    except ValueError:
        return await _request_invalid_response(db_manager, receipt)
    if len(decoded_key) != 32:
        return await _request_invalid_response(db_manager, receipt)

    actor = authenticated_principal()
    try:
        pool = db_manager.pool("switchboard")
    except Exception:
        return _error(
            503, "MAPPING_DATABASE_UNAVAILABLE", "Mapping service is unavailable.", receipt
        )

    try:
        decision, _replayed = await _decide_batch(pool, batch, raw_key, actor)
    except Exception:
        logger.error(
            "HA person mapping batch outcome=error failure_category=database_unavailable",
        )
        return _error(
            503, "MAPPING_DATABASE_UNAVAILABLE", "Mapping service is unavailable.", receipt
        )

    _record_outcome(decision)
    if decision.outcome == "success":
        return ApiResponse(data=asdict(decision.receipt))
    if decision.failure_category in {"mapping_conflict", "idempotency_conflict"}:
        code = (
            "IDEMPOTENCY_CONFLICT"
            if decision.failure_category == "idempotency_conflict"
            else "MAPPING_CONFLICT"
        )
        return _error(409, code, "Mapping request conflicts with existing state.", decision.receipt)
    return _error(422, "INVALID_REFERENCE", "One or more references are invalid.", decision.receipt)
