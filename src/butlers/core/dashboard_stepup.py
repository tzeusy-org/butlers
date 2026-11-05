"""Trusted request projection for D13, independent of HTTP and domain writers.

Constructing this tuple grants nothing. The private store checks the opaque
nonce, authenticated session, current epochs and exact action in the writer's
transaction. Transport actors and model-supplied fields never populate it.

``consume_stepup`` is the one call a core consumer (Contain, owner-policy
loosening) makes into that store, inside its own owning transaction.
"""

import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from opentelemetry.instrumentation.utils import suppress_instrumentation


@dataclass(frozen=True, repr=False)
class DashboardStepUpProof:
    session_digest: str
    nonce: str


@dataclass(frozen=True, repr=False)
class StepUpBinding:
    """One server-derived action: what the owner confirms and the writer spends."""

    kind: str
    target: str
    request_digest: str
    action_digest: str
    namespace: str


dashboard_stepup_proof: ContextVar[DashboardStepUpProof | None] = ContextVar(
    "dashboard_stepup_proof", default=None
)

verified_dashboard_session: ContextVar[str | None] = ContextVar("dashboard_session", default=None)


async def consume_stepup(
    connection: Any, phase: str, binding: StepUpBinding, result: dict | None = None
) -> dict:
    """``begin`` admits the current proof once for ``binding``; ``finish`` retains ``result``.

    Without a proof this refuses without touching the store. Missing-interface
    and privilege errors propagate so the caller refuses (never falls back).
    """
    proof = dashboard_stepup_proof.get()
    if proof is None:
        return {"state": "refused"}
    # No SQL span may export the nonce or session digest.
    with suppress_instrumentation():
        raw = await connection.fetchval(
            "SELECT dashboard_stepup.consume($1, $2, $3, $4, $5, $6, $7, ($8::text)::jsonb)",
            phase,
            proof.nonce,
            proof.session_digest,
            binding.kind,
            binding.target,
            binding.request_digest,
            binding.action_digest,
            json.dumps(result) if result is not None else None,
        )
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict):
        raise RuntimeError("Step-up storage unavailable")
    return value
