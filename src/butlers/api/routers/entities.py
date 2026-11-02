"""Entity graph dossier: what the fleet knows about one entity (bu-8cdl1.8, RFC 0031 S5).

``GET /api/entities/{id}/dossier`` reads the shared ``public.entity_graph_edges`` projection on
the Switchboard pool (the same authority the memory catalog search uses) and returns the entity's
live relationships as receipts grouped by canonical source, plus true known and withheld counts.

Trust boundary (REQ-entity-graph-010): withheld stubs are a bare count; their predicate, object
and source identity are never selected.  The endpoint never reads a butler's private schema.
Unavailability is a 503, never an empty dossier; an unknown entity is a 404.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from butlers.api.deps import Db
from butlers.api.models import ApiResponse
from butlers.core.entity_graph_edges import (
    DOSSIER_RECEIPT_LIMIT,
    DOSSIER_RECEIPT_LIMIT_MAX,
    dossier_for_entity,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/entities", tags=["entities"])


class DossierReceipt(BaseModel):
    """One live relationship and where it came from."""

    edge_id: str
    source_id: str
    predicate: str
    direction: str
    counterpart_entity_id: str
    counterpart_name: str | None = None
    sensitivity: str
    updated_at: str


class DossierSource(BaseModel):
    """Live receipts for one canonical source (schema + table)."""

    source_schema: str
    source_table: str
    live_count: int
    receipts: list[DossierReceipt]


class EntityDossier(BaseModel):
    """Per-source receipts and the known/withheld coverage statement for one entity."""

    entity_id: str
    relationships_known: int
    relationships_withheld: int
    sources: list[DossierSource]
    receipts_limit: int
    receipts_truncated: bool


def _unavailable(exc: Exception) -> HTTPException:
    logger.warning("entity dossier: graph read unavailable: %s", exc, exc_info=True)
    return HTTPException(status_code=503, detail="Entity graph is unavailable")


@router.get("/{entity_id}/dossier", response_model=ApiResponse[EntityDossier])
async def get_entity_dossier(
    entity_id: uuid.UUID,
    limit: int = Query(
        DOSSIER_RECEIPT_LIMIT,
        ge=1,
        le=DOSSIER_RECEIPT_LIMIT_MAX,
        description="Max live receipts returned (counts are always the true totals)",
    ),
    *,
    db: Db,
) -> ApiResponse[EntityDossier]:
    """Return the entity's live projected relationships grouped by source, with coverage."""
    try:
        pool = db.pool("switchboard")
    except KeyError as exc:
        raise _unavailable(exc) from exc

    try:
        exists = await pool.fetchrow("SELECT id FROM public.entities WHERE id = $1", entity_id)
        if exists is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        read = await dossier_for_entity(pool, entity_id, limit=limit)
        counterpart_ids = list({r.counterpart_entity_id for r in read.receipts})
        names: dict[uuid.UUID, str] = {}
        if counterpart_ids:
            rows = await pool.fetch(
                "SELECT id, canonical_name FROM public.entities WHERE id = ANY($1::uuid[])",
                counterpart_ids,
            )
            names = {row["id"]: row["canonical_name"] for row in rows}
    except HTTPException:
        raise
    except Exception as exc:
        raise _unavailable(exc) from exc

    grouped: dict[tuple[str, str], list[DossierReceipt]] = {key: [] for key in read.live_by_source}
    for receipt in read.receipts:
        grouped.setdefault((receipt.source_schema, receipt.source_table), []).append(
            DossierReceipt(
                edge_id=str(receipt.edge_id),
                source_id=str(receipt.source_id),
                predicate=receipt.predicate,
                direction=receipt.direction,
                counterpart_entity_id=str(receipt.counterpart_entity_id),
                counterpart_name=names.get(receipt.counterpart_entity_id),
                sensitivity=receipt.sensitivity,
                updated_at=receipt.updated_at.isoformat(),
            )
        )
    known = sum(read.live_by_source.values())
    sources = [
        DossierSource(
            source_schema=schema,
            source_table=table,
            live_count=read.live_by_source.get((schema, table), len(items)),
            receipts=items,
        )
        for (schema, table), items in sorted(grouped.items())
    ]
    return ApiResponse[EntityDossier](
        data=EntityDossier(
            entity_id=str(entity_id),
            relationships_known=known,
            relationships_withheld=read.withheld,
            sources=sources,
            receipts_limit=limit,
            receipts_truncated=known > len(read.receipts),
        )
    )
