"""Health butler API router — proof of concept.

Demonstrates that a FastAPI router can be loaded from roster/ via importlib
and that it can import shared models from src/butlers/api/models.
"""

from fastapi import APIRouter

from butlers.api.models import ApiResponse

router = APIRouter(prefix="/api/health-butler", tags=["health-butler"])


@router.get("/vitals", response_model=ApiResponse[dict])
async def get_vitals() -> ApiResponse[dict]:
    """Example endpoint returning health vitals."""
    return ApiResponse[dict](
        data={
            "status": "healthy",
            "uptime_seconds": 12345,
            "checks_passed": 42,
        }
    )


@router.get("/checks", response_model=ApiResponse[list[str]])
async def list_checks() -> ApiResponse[list[str]]:
    """Example endpoint listing available health checks."""
    return ApiResponse[list[str]](
        data=[
            "database_connectivity",
            "mcp_server_status",
            "task_scheduler_active",
        ]
    )
