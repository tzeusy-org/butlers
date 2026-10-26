"""MCP tool for reading the caller's own personal baselines (bu-q7vx1q.77)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import Field

from butlers.core import baselines
from butlers.core.health_baseline_metrics import METRICS
from butlers.core_tools._base import ToolContext


def register_baseline_tools(ctx: ToolContext, mcp: Any, _core_tool: Any) -> None:
    @_core_tool("infra", annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def metric_baseline_get(
        metric_key: Annotated[
            str, Field(min_length=1, description="Metric key, e.g. resting_hr, hrv.")
        ],
    ) -> dict[str, Any]:
        """Read this butler's stored usual range ("baseline") for one metric.

        Returns state (ready, insufficient_history, unmeasurable, stale or
        no_baseline), the band only when state is ready, n_observed of n_expected
        days, coverage, computed_at and stale. Read-only; reads this butler's own
        schema. An unknown key or a job that has not run reads no_baseline.
        """
        if ctx.pool is None:
            raise RuntimeError("Database pool is not available")
        metric = next((metric for metric in METRICS if metric.key == metric_key), None)
        reading = await baselines.read_baseline(
            ctx.pool,
            metric_key,
            now=datetime.now(UTC),
            **({"refresh_interval": metric.refresh_interval} if metric else {}),
        )
        return (reading or baselines.missing_baseline(metric_key)).as_dict()
