"""Snapshot-only, allowlisted dashboard Bead detail endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from butlers.api.models import ApiMeta, ApiResponse, ErrorDetail, ErrorResponse
from butlers.api.models.bead import BeadDependencySummary, BeadDetail
from butlers.beads_snapshot import BeadSnapshotReader, SnapshotAvailability

router = APIRouter(prefix="/api/beads", tags=["beads"])


def _snapshot_reader() -> BeadSnapshotReader:
    """Construct the sole file-backed reader; deliberately no live fallback."""

    return BeadSnapshotReader()


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _unavailable_response(availability: SnapshotAvailability) -> JSONResponse:
    """Return only safe source health, never exception or snapshot contents."""

    return _error_response(
        status_code=503,
        code="BEAD_SNAPSHOT_UNAVAILABLE",
        message="Bead snapshot is unavailable.",
        details={
            "reason": availability.reason,
            "export_as_of": availability.export_as_of,
        },
    )


@router.get("/{bead_id}", response_model=ApiResponse[BeadDetail])
async def get_bead_detail(bead_id: str) -> ApiResponse[BeadDetail] | JSONResponse:
    """Return one safe Bead detail only when the mounted snapshot is trusted."""

    result = _snapshot_reader().read(bead_id)
    if not result.availability.available:
        return _unavailable_response(result.availability)
    if result.detail is None:
        return _error_response(
            status_code=404,
            code="BEAD_NOT_FOUND",
            message="Bead not found in the current snapshot.",
        )

    detail = result.detail
    return ApiResponse(
        data=BeadDetail(
            id=detail.id,
            title=detail.title,
            status=detail.status,
            priority=detail.priority,
            type=detail.type,
            description=detail.description,
            design=detail.design,
            acceptance_criteria=detail.acceptance_criteria,
            labels=list(detail.labels),
            created_at=detail.created_at,
            updated_at=detail.updated_at,
            started_at=detail.started_at,
            closed_at=detail.closed_at,
            due_at=detail.due_at,
            dependencies=[
                BeadDependencySummary(
                    id=dependency.id,
                    title=dependency.title,
                    status=dependency.status,
                    priority=dependency.priority,
                    type=dependency.type,
                )
                for dependency in detail.dependencies
            ],
            external_ref=detail.external_ref,
        ),
        meta=ApiMeta(export_as_of=result.availability.export_as_of),
    )
