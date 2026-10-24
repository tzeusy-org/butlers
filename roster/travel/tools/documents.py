"""Travel butler document tools — attach travel documents to trip containers."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from typing import Any

import asyncpg

from ._helpers import _row_to_dict

_VALID_DOCUMENT_TYPES = ("boarding_pass", "visa", "insurance", "receipt")


def _normalize_date(value: str | date | None) -> date | None:
    """Normalize a string or date to a date object, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


async def add_document(
    pool: asyncpg.Pool,
    trip_id: str,
    type: str,
    blob_ref: str | None = None,
    expiry_date: str | date | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach a travel document to an existing trip.

    Validates that the trip exists and that the document type is one of the
    supported values. Persists the document reference in ``travel.documents``
    and returns the full document record.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    trip_id:
        UUID string of the trip to attach the document to.
    type:
        Document type. One of: ``boarding_pass``, ``visa``, ``insurance``,
        ``receipt``.
    blob_ref:
        Optional opaque reference to the stored document blob (e.g. a storage
        key, file path, or URL). May be ``None`` when the document's metadata
        is tracked but the binary blob is not yet available.
    expiry_date:
        Optional expiry date for the document (relevant for visas, insurance).
        Accepts ISO date strings (``YYYY-MM-DD``) or date objects.
    metadata:
        Optional free-form JSONB metadata dict for extended attributes (e.g.
        flight number, gate, page count).

    Returns
    -------
    dict
        AddDocumentResult with keys:
        ``document_id``, ``trip_id``, ``type``, ``blob_ref``,
        ``expiry_date``, ``created_at``, ``metadata``.

    Raises
    ------
    ValueError
        If the document type is invalid or the trip does not exist.
    """
    if type not in _VALID_DOCUMENT_TYPES:
        raise ValueError(f"Invalid document type {type!r}. Must be one of {_VALID_DOCUMENT_TYPES}")

    # Validate trip exists
    trip_row = await pool.fetchrow(
        "SELECT id FROM travel.trips WHERE id = $1::uuid",
        trip_id,
    )
    if trip_row is None:
        raise ValueError(f"add_document: trip {trip_id!r} not found")

    expiry = _normalize_date(expiry_date)

    row = await pool.fetchrow(
        """
        INSERT INTO travel.documents (
            trip_id, type, blob_ref, expiry_date, metadata
        ) VALUES (
            $1::uuid, $2, $3, $4, $5
        )
        RETURNING *
        """,
        trip_id,
        type,
        blob_ref,
        expiry,
        metadata or {},
    )

    result = _row_to_dict(row)
    return {
        "document_id": result["id"],
        "trip_id": result["trip_id"],
        "type": result["type"],
        "blob_ref": result.get("blob_ref"),
        "expiry_date": result.get("expiry_date"),
        "created_at": result["created_at"],
        "metadata": result.get("metadata", {}),
    }


async def document_expiry_inventory(pool: asyncpg.Pool | None) -> dict[str, Any]:
    """Read all stored document types/trip statuses in one bounded snapshot.

    A complete empty result attests only this owning store, never passports in
    Memory or provider reminders. Blob references and arbitrary metadata are
    deliberately absent. No document is inferred from a trip's lifecycle.
    """
    result: dict[str, Any] = {
        "status": "unavailable",
        "complete": False,
        "coverage": "owning_store",
        "as_of": datetime.now(UTC).isoformat(),
        "items": [],
        "unresolved_count": 0,
        "reason": "store_unavailable",
    }
    if pool is None:
        return result
    try:
        async with asyncio.timeout(5):
            async with pool.acquire() as connection:
                async with connection.transaction(isolation="repeatable_read", readonly=True):
                    await connection.execute("SELECT set_config('statement_timeout', '4000', true)")
                    snapshot_at = await connection.fetchval("SELECT transaction_timestamp()")
                    result["as_of"] = snapshot_at.isoformat()
                    rows = []
                    async for row in connection.cursor(
                        """SELECT d.id, d.trip_id, d.type, d.expiry_date
                           FROM travel.documents AS d
                           JOIN travel.trips AS t ON t.id = d.trip_id
                           ORDER BY d.id LIMIT 1001""",
                        prefetch=100,
                    ):
                        rows.append(dict(row))
                    result.update(status="complete", complete=True, reason="exhausted")
                    if len(rows) > 1000:
                        result.update(status="incomplete", complete=False, reason="row_limit")
                    for row in rows[:1000]:
                        expiry = row["expiry_date"]
                        if expiry is not None and (
                            not isinstance(expiry, date) or isinstance(expiry, datetime)
                        ):
                            result["unresolved_count"] += 1
                            continue
                        if row["type"] not in _VALID_DOCUMENT_TYPES:
                            result["unresolved_count"] += 1
                            continue
                        result["items"].append(
                            {
                                "document_id": str(row["id"]),
                                "trip_id": str(row["trip_id"]),
                                "type": row["type"],
                                "expiry_date": expiry.isoformat() if expiry else None,
                            }
                        )
                    if result["unresolved_count"]:
                        result.update(status="incomplete", complete=False, reason="unresolved")
                    # Serialization is part of the same aggregate I/O/CPU budget.
                    if len(json.dumps(result).encode()) > 262144:
                        result.update(
                            status="incomplete", complete=False, reason="output_limit", items=[]
                        )
    except TimeoutError:
        result.update(status="incomplete", complete=False, reason="timeout")
    except Exception:
        result.update(status="unavailable", complete=False, reason="store_unavailable", items=[])
    return result
