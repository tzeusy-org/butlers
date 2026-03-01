"""Health MCP tool registrations.

All ``@mcp.tool()`` closures extracted from ``HealthModule.register_tools``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def register_tools(mcp: Any, module: Any) -> None:  # noqa: C901
    """Register all health MCP tools on *mcp*, using *module* for pool access."""

    # Import sub-modules (deferred to avoid import-time side effects)
    from butlers.tools.health import conditions as _cond
    from butlers.tools.health import diet as _diet
    from butlers.tools.health import measurements as _meas
    from butlers.tools.health import medications as _meds
    from butlers.tools.health import reports as _reports
    from butlers.tools.health import research as _research

    # =================================================================
    # Measurement tools
    # =================================================================

    @mcp.tool()
    async def measurement_log(
        type: str,
        value: Any,
        notes: str | None = None,
        measured_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Log a health measurement. Value is stored as JSONB for compound values.

        Type must be one of: weight, blood_pressure, heart_rate,
        blood_sugar, temperature. For compound values like blood pressure,
        pass a dict: {"systolic": 120, "diastolic": 80}.
        """
        return await _meas.measurement_log(
            module._get_pool(), type, value, notes=notes, measured_at=measured_at
        )

    @mcp.tool()
    async def measurement_history(
        type: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Get measurement history for a type, optionally filtered by date range."""
        return await _meas.measurement_history(
            module._get_pool(), type, start_date=start_date, end_date=end_date
        )

    @mcp.tool()
    async def measurement_latest(type: str) -> dict[str, Any] | None:
        """Get the most recent measurement for a type."""
        return await _meas.measurement_latest(module._get_pool(), type)

    # =================================================================
    # Medication tools
    # =================================================================

    @mcp.tool()
    async def medication_add(
        name: str,
        dosage: str,
        frequency: str,
        schedule: list[str] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Add a medication with dosage, frequency, optional schedule and notes."""
        return await _meds.medication_add(
            module._get_pool(), name, dosage, frequency, schedule=schedule, notes=notes
        )

    @mcp.tool()
    async def medication_list(active_only: bool = True) -> list[dict[str, Any]]:
        """List medications, optionally only active ones."""
        return await _meds.medication_list(module._get_pool(), active_only=active_only)

    @mcp.tool()
    async def medication_log_dose(
        medication_id: str,
        taken_at: datetime | None = None,
        skipped: bool = False,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Log a medication dose. Use skipped=True to record a missed dose."""
        return await _meds.medication_log_dose(
            module._get_pool(), medication_id, taken_at=taken_at, skipped=skipped, notes=notes
        )

    @mcp.tool()
    async def medication_history(
        medication_id: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        """Get medication dose history with adherence rate."""
        return await _meds.medication_history(
            module._get_pool(), medication_id, start_date=start_date, end_date=end_date
        )

    # =================================================================
    # Condition tools
    # =================================================================

    @mcp.tool()
    async def condition_add(
        name: str,
        status: str = "active",
        diagnosed_at: datetime | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Add a health condition. Status must be one of: active, managed, resolved."""
        return await _cond.condition_add(
            module._get_pool(), name, status=status, diagnosed_at=diagnosed_at, notes=notes
        )

    @mcp.tool()
    async def condition_list(status: str | None = None) -> list[dict[str, Any]]:
        """List conditions, optionally filtered by status."""
        return await _cond.condition_list(module._get_pool(), status=status)

    @mcp.tool()
    async def condition_update(
        condition_id: str,
        name: str | None = None,
        status: str | None = None,
        diagnosed_at: datetime | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Update a condition. Allowed fields: name, status, diagnosed_at, notes.

        If status is provided, it must be one of: active, managed, resolved.
        """
        fields = {
            k: v
            for k, v in {
                "name": name,
                "status": status,
                "diagnosed_at": diagnosed_at,
                "notes": notes,
            }.items()
            if v is not None
        }
        return await _cond.condition_update(module._get_pool(), condition_id, **fields)

    # =================================================================
    # Symptom tools
    # =================================================================

    @mcp.tool()
    async def symptom_log(
        name: str,
        severity: int,
        condition_id: str | None = None,
        notes: str | None = None,
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Log a symptom with severity (1-10), optionally linked to a condition."""
        return await _cond.symptom_log(
            module._get_pool(),
            name,
            severity,
            condition_id=condition_id,
            notes=notes,
            occurred_at=occurred_at,
        )

    @mcp.tool()
    async def symptom_history(
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Get symptom history, optionally filtered by date range."""
        return await _cond.symptom_history(
            module._get_pool(), start_date=start_date, end_date=end_date
        )

    @mcp.tool()
    async def symptom_search(
        name: str | None = None,
        min_severity: int | None = None,
        max_severity: int | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Search symptoms by name, severity range, and date range."""
        return await _cond.symptom_search(
            module._get_pool(),
            name=name,
            min_severity=min_severity,
            max_severity=max_severity,
            start_date=start_date,
            end_date=end_date,
        )

    # =================================================================
    # Diet & Nutrition tools
    # =================================================================

    @mcp.tool()
    async def meal_log(
        type: str,
        description: str,
        nutrition: dict[str, Any] | None = None,
        eaten_at: datetime | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Log a meal. Type must be one of: breakfast, lunch, dinner, snack."""
        return await _diet.meal_log(
            module._get_pool(),
            type,
            description,
            nutrition=nutrition,
            eaten_at=eaten_at,
            notes=notes,
        )

    @mcp.tool()
    async def meal_history(
        type: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Get meal history, optionally filtered by type and date range."""
        return await _diet.meal_history(
            module._get_pool(), type=type, start_date=start_date, end_date=end_date
        )

    @mcp.tool()
    async def nutrition_summary(
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """Aggregate nutrition data over a date range.

        Returns total and daily average calories, protein, carbs, and fat.
        """
        return await _diet.nutrition_summary(module._get_pool(), start_date, end_date)

    # =================================================================
    # Report tools
    # =================================================================

    @mcp.tool()
    async def health_summary() -> dict[str, Any]:
        """Get a health overview: latest measurements, active medications, conditions."""
        return await _reports.health_summary(module._get_pool())

    @mcp.tool()
    async def trend_report(period: str = "week") -> dict[str, Any]:
        """Generate a trend report over a period (week=7d, month=30d).

        Returns measurement trends, medication adherence, symptom frequency
        and severity averages.
        """
        return await _reports.trend_report(module._get_pool(), period=period)

    # =================================================================
    # Research tools
    # =================================================================

    @mcp.tool()
    async def research_save(
        title: str,
        content: str,
        tags: list[str] | None = None,
        source_url: str | None = None,
        condition_id: str | None = None,
    ) -> dict[str, Any]:
        """Save a research note with optional tags, source URL, and condition link."""
        return await _research.research_save(
            module._get_pool(),
            title,
            content,
            tags=tags,
            source_url=source_url,
            condition_id=condition_id,
        )

    @mcp.tool()
    async def research_search(
        query: str | None = None,
        tags: list[str] | None = None,
        condition_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search research notes by text query, tags, and/or condition."""
        return await _research.research_search(
            module._get_pool(), query=query, tags=tags, condition_id=condition_id
        )

    @mcp.tool()
    async def research_summarize(
        condition_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Summarize research entries, optionally scoped by condition or tags."""
        return await _research.research_summarize(
            module._get_pool(), condition_id=condition_id, tags=tags
        )
