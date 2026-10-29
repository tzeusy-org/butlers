"""Trusted request projection for D13, independent of HTTP and domain writers.

Constructing this tuple grants nothing. The private store checks the opaque
nonce, authenticated session, current epochs and exact action in the writer's
transaction. Transport actors and model-supplied fields never populate it.
"""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class DashboardStepUpProof:
    session_digest: str
    nonce: str


dashboard_stepup_proof: ContextVar[DashboardStepUpProof | None] = ContextVar(
    "dashboard_stepup_proof", default=None
)

verified_dashboard_session: ContextVar[str | None] = ContextVar("dashboard_session", default=None)
