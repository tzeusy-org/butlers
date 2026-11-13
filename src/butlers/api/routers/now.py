"""Dashboard ``GET /api/now``: the owner's deterministic situation card (bu-dhloi1.21).

Owner-authenticated only. Dark: answers 404 until ``perception.flags.NOW_LIVE``
is turned on at the switch. When live it reads every /now signal on a fresh
connection from ``BUTLERS_PERCEPTION_READER_DSN``, switching to
``perception_owner_reader`` with ``SET LOCAL ROLE`` inside one transaction, so
the reader role never outlives the request. No model is called.

Activation needs: the DSN's login role granted membership of
``perception_owner_reader`` (and not of ``perception_availability_reader``), and
exactly one owner entity in ``public.entities``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from butlers.api.deps import get_db_optional
from butlers.api.models import ApiResponse, ErrorDetail, ErrorResponse
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.db import register_jsonb_codec
from butlers.perception import flags
from butlers.perception.now import read_now, render_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/now", tags=["now"])

READER_DSN_ENV = "BUTLERS_PERCEPTION_READER_DSN"
_NO_STORE = {"Cache-Control": "no-store"}


class NowLineModel(BaseModel):
    kind: str
    label: str
    state: str
    text: str
    stale: bool
    source: str | None
    age_seconds: float | None


class NowCardModel(BaseModel):
    as_of: datetime
    headline: str
    all_current: bool
    lines: list[NowLineModel]
    text: str


def _now() -> datetime:
    return datetime.now(UTC)


async def _owner_subject() -> UUID | None:
    """The single owner entity, or ``None`` when there is not exactly one."""
    db = get_db_optional()
    if db is None:
        return None
    try:
        pool = db.credential_shared_pool()
    except KeyError:
        return None
    rows = await pool.fetch("SELECT id FROM public.entities WHERE 'owner' = ANY(roles)")
    return rows[0]["id"] if len(rows) == 1 else None


async def _open_reader() -> Any | None:
    """A fresh perception reader connection, or ``None`` when not activated."""
    dsn = (os.environ.get(READER_DSN_ENV) or "").strip()
    if not dsn:
        return None
    connection = await asyncpg.connect(dsn, command_timeout=15)
    await register_jsonb_codec(connection)
    return connection


def _unavailable() -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code="now_unavailable", message="The /now card is not available")
    )
    return JSONResponse(status_code=503, content=body.model_dump(mode="json"), headers=_NO_STORE)


@router.get(
    "",
    response_model=ApiResponse[NowCardModel],
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_now(
    _owner: str = Depends(require_dashboard_owner_control),
) -> ApiResponse[NowCardModel] | JSONResponse:
    if not flags.NOW_LIVE:
        raise HTTPException(status_code=404, detail="Not Found")
    as_of = _now()
    connection = None
    try:
        subject = await _owner_subject()
        if subject is None:
            return _unavailable()
        connection = await _open_reader()
        if connection is None:
            return _unavailable()
        async with connection.transaction():
            await connection.execute("SET LOCAL ROLE perception_owner_reader")
            card = await read_now(connection, subject=subject, as_of=as_of)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, PermissionError):
        logger.error("/now read failed: perception reader unavailable")
        return _unavailable()
    finally:
        if connection is not None:
            await connection.close()
    model = NowCardModel(
        as_of=card.as_of,
        headline=card.headline,
        all_current=card.all_current,
        lines=[NowLineModel(**asdict(line)) for line in card.lines],
        text=render_text(card),
    )
    return JSONResponse(
        content=ApiResponse[NowCardModel](data=model).model_dump(mode="json"), headers=_NO_STORE
    )
