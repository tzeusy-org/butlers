"""Additional owner-only checks consume the central verified HTTP authority."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, Request


def require_dashboard_owner_control(request: Request) -> Literal["owner"]:
    """Reject a route invoked without the central authenticated owner context.

    This is additive to the middleware boundary: scoped connector callback
    authority never satisfies this dependency, and no header is reinterpreted.
    """
    allowed = getattr(request.state, "owner_authority", None) is not None
    if not allowed:
        raise HTTPException(status_code=401, detail="Dashboard owner authentication is required")
    return "owner"
