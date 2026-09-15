"""Request authority shared by HTTP enforcement and audit attribution.

Keep this module independent of HTTP handlers, services and database clients:
deterministic domain code also uses audit attribution.
"""

from contextvars import ContextVar

verified_http_principal: ContextVar[str | None] = ContextVar("owner_http_principal", default=None)
in_http_request: ContextVar[bool] = ContextVar("owner_http_request", default=False)
