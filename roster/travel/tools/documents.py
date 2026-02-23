"""Travel butler document tools — attach travel documents to trip containers."""

from __future__ import annotations

import json
from datetime import date, datetime
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
    meta_json = json.dumps(metadata or {})

    row = await pool.fetchrow(
        """
        INSERT INTO travel.documents (
            trip_id, type, blob_ref, expiry_date, metadata
        ) VALUES (
            $1::uuid, $2, $3, $4, $5::jsonb
        )
        RETURNING *
        """,
        trip_id,
        type,
        blob_ref,
        expiry,
        meta_json,
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
