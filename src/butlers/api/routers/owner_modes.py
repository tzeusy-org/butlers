"""Owner mode controls: read, enter, exit and edit modes (bu-dhloi1.21 PR8).

Dark: with ``flags.MODE_CONTROLS_LIVE`` off every route answers 404 before it
touches the database or the owner-auth service. Live, every route needs the
owner's verified dashboard session (an API key, connector, runtime session or
model tool never carries one), then the switchboard pool, where the dashboard
login is the table owner of ``owner_mode_overlay`` and ``owner_mode_entries``.

A tightening is written directly. A loosening (an exit, an earlier end, a
dropped hold, an added break-through sender, a shorter duration) needs the
``mode_loosen`` dashboard step-up: ``step-up/options`` derives the exact
change from the ledger and asks for a passkey assertion bound to it
(``{"required": false}`` when the change is not a loosening), and the change
route spends it, sent as ``X-Dashboard-Step-Up``, in the write's transaction.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request

from butlers.api.db import DatabaseManager
from butlers.api.deps import Db
from butlers.api.models import ApiResponse
from butlers.api.models.owner_modes import (
    ModeId,
    OwnerModeEdit,
    OwnerModeEditOptions,
    OwnerModeEnter,
    OwnerModeEnterOptions,
    OwnerModeExit,
    OwnerModeOptions,
    OwnerModeReceipt,
    OwnerModes,
)
from butlers.api.owner_auth.service import AuthError
from butlers.core.dashboard_stepup import verified_dashboard_session
from butlers.perception import flags
from butlers.perception.mode_controls import loosen_binding, read_modes
from butlers.perception.mode_entries import ModeEntryWriter
from butlers.perception.mode_overlay import ModeConflict, ModeOverlayUnavailable, ModeOverlayWriter
from butlers.perception.modes import (
    EditBreakThrough,
    EditDuration,
    EditHolds,
    Enter,
    Exit,
    ModeChange,
    ModeControlsDark,
    ModeStepUpRequired,
)


def _live() -> None:
    if not flags.MODE_CONTROLS_LIVE:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(prefix="/api/owner-modes", tags=["owner-modes"], dependencies=[Depends(_live)])

_LEDGER = "switchboard"
Mode = Annotated[ModeId, Path(description="A wire mode ID; aliases are refused.")]


def _refuse(status: int, error: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": error})


def _owner_pool(db: DatabaseManager):
    if verified_dashboard_session.get() is None:
        raise HTTPException(
            status_code=403,
            detail={"error": "owner_dashboard_session_required", "open_dashboard": "/settings"},
        )
    try:
        return db.pool(_LEDGER)
    except KeyError as exc:
        raise _refuse(503, "owner_mode_unavailable") from exc


def _edit(mode: str, body: OwnerModeEdit) -> ModeChange:
    # The model admits exactly one field, and a named list is never null.
    if body.holds is not None:
        return EditHolds(mode, frozenset(body.holds))
    if body.break_through is not None:
        return EditBreakThrough(mode, frozenset(body.break_through))
    duration = None if body.duration_s is None else timedelta(seconds=body.duration_s)
    return EditDuration(mode, duration)


def _option_change(body: OwnerModeOptions) -> ModeChange:
    if isinstance(body, OwnerModeEnterOptions):
        return Enter(body.mode, body.until, body.cause)
    if isinstance(body, OwnerModeEditOptions):
        return _edit(body.mode, body)
    return Exit(body.mode)


def _refusal(exc: Exception) -> HTTPException:
    """The closed error for a writer or binding outcome; ModeConflict before ValueError."""
    if isinstance(exc, ModeControlsDark):
        return HTTPException(status_code=404, detail="Not Found")
    if isinstance(exc, ModeStepUpRequired):
        return _refuse(403, "dashboard_step_up_required")
    if isinstance(exc, ModeConflict):
        return _refuse(409, "owner_mode_conflict")
    if isinstance(exc, ValueError):
        return _refuse(422, "owner_mode_invalid")
    return _refuse(503, "owner_mode_unavailable")


_OUTCOMES = (ModeControlsDark, ModeStepUpRequired, ValueError, ModeOverlayUnavailable)


async def _change(
    db: DatabaseManager, change: ModeChange, body: OwnerModeEnter | OwnerModeExit | OwnerModeEdit
) -> ApiResponse[OwnerModeReceipt]:
    pool = _owner_pool(db)
    try:
        async with pool.acquire() as connection:
            if isinstance(change, Enter | Exit):
                receipt = await ModeEntryWriter(connection).change(
                    body.owner_action_id, change, body.expected_version
                )
            else:
                receipt = await ModeOverlayWriter(connection).edit(
                    body.owner_action_id, change, body.expected_version
                )
    except _OUTCOMES as exc:
        raise _refusal(exc) from exc
    return ApiResponse(data=OwnerModeReceipt(**receipt))


@router.get("", response_model=ApiResponse[OwnerModes])
async def get_owner_modes(*, db: Db) -> ApiResponse[OwnerModes]:
    """Every mode's profile and active entry, with the versions a change must name."""
    pool = _owner_pool(db)
    try:
        async with pool.acquire() as connection:
            modes = await read_modes(connection)
    except _OUTCOMES as exc:
        raise _refusal(exc) from exc
    return ApiResponse(data=OwnerModes.model_validate({"modes": modes}))


@router.post("/step-up/options", response_model=ApiResponse[dict])
async def owner_modes_stepup_options(
    body: Annotated[OwnerModeOptions, Body()], request: Request, *, db: Db
) -> ApiResponse[dict]:
    """Ask for a passkey assertion bound to one exact loosening; nothing is written."""
    pool = _owner_pool(db)
    session = verified_dashboard_session.get()
    change = _option_change(body)
    try:
        async with pool.acquire() as connection:
            # This read scope ends BEFORE the separate authentication call.
            binding = await loosen_binding(
                connection,
                change,
                owner_action_id=body.owner_action_id,
                expected_version=body.expected_version,
            )
    except _OUTCOMES as exc:
        raise _refusal(exc) from exc
    if binding is None:
        return ApiResponse(data={"required": False})
    service = getattr(request.app.state, "owner_auth_service", None)
    if service is None:
        raise _refuse(503, "owner_mode_unavailable")
    try:
        return ApiResponse(data=await service.stepup_options(session, binding))
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None


@router.post("/{mode}/enter", response_model=ApiResponse[OwnerModeReceipt])
async def enter_owner_mode(
    mode: Mode, body: OwnerModeEnter, *, db: Db
) -> ApiResponse[OwnerModeReceipt]:
    """Enter a mode, or move an active entry's end; an earlier end needs step-up."""
    return await _change(db, Enter(mode, body.until, body.cause), body)


@router.post("/{mode}/exit", response_model=ApiResponse[OwnerModeReceipt])
async def exit_owner_mode(
    mode: Mode, body: OwnerModeExit, *, db: Db
) -> ApiResponse[OwnerModeReceipt]:
    """Leave an active mode now, spending the request's step-up."""
    return await _change(db, Exit(mode), body)


@router.post("/{mode}/edit", response_model=ApiResponse[OwnerModeReceipt])
async def edit_owner_mode(
    mode: Mode, body: OwnerModeEdit, *, db: Db
) -> ApiResponse[OwnerModeReceipt]:
    """Edit one of a mode's holds, break-through senders or duration."""
    return await _change(db, _edit(mode, body), body)
