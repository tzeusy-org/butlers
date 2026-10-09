"""Content-blind serving observations, with requested and reported identities separate."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ServedAttempt(BaseModel):
    attempt_id: int
    attempt_index: int
    outcome: str
    requested_model_id: str | None = None
    served_identity: dict[str, Any] | None = None


class ServedCostComparison(BaseModel):
    attempt_id: int
    execution_index: int
    model_id: str
    identity_authority: str
    provenance: str
    reported_cost_usd: float | None = None
    computed_served_cost_usd: float | None = None
    difference_usd: float | None = None
    difference_ratio: float | None = None
    comparison_state: Literal["comparable", "unpriced", "unknown"] = "unknown"
    cost_source: Literal["cli_estimate"] = "cli_estimate"
    finding: Literal["provider_vs_computed_cost"] | None = None
    reason: (
        Literal["cumulative_scope", "partial_coverage", "missing_reported_cost", "unpriced_model"]
        | None
    ) = None
    token_buckets: dict[str, int | None] = Field(default_factory=dict)
