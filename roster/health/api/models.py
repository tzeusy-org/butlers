"""Health butler API models — proof of concept.

Demonstrates co-locating Pydantic models alongside the router.
"""

from pydantic import BaseModel


class HealthCheck(BaseModel):
    """A single health check result."""

    name: str
    status: str  # "pass" | "fail" | "warn"
    message: str | None = None
    timestamp: str


class VitalsSnapshot(BaseModel):
    """Current vitals snapshot for the health butler."""

    status: str
    uptime_seconds: int
    checks_passed: int
    checks_failed: int
    last_check_at: str
