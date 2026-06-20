"""Health MCP tool registrations.

All ``@mcp.tool()`` closures extracted from ``HealthModule.register_tools``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from butlers.modules.base import group_enabled


def register_tools(mcp: Any, module: Any, config: Any = None) -> None:  # noqa: C901
    """Register all health MCP tools on *mcp*, using *module* for pool access."""

    # Import sub-modules (deferred to avoid import-time side effects)
    from butlers.tools.health import conditions as _cond
    from butlers.tools.health import diet as _diet
    from butlers.tools.health import measurements as _meas
    from butlers.tools.health import medications as _meds
    from butlers.tools.health import reports as _reports
    from butlers.tools.health import research as _research
    from butlers.tools.health import wellness_ingest as _wellness

    # Build a group-aware tool decorator: returns @mcp.tool() when the
    # group is enabled, or a no-op passthrough when disabled.
    def _tool(group: str):
        if group_enabled(config, group):
            return mcp.tool()
        return lambda fn: fn  # no-op — function defined but not registered

    # =================================================================
    # Measurement tools
    # =================================================================

    @_tool("measurements")
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

    @_tool("measurements")
    async def measurement_history(
        type: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Get measurement history for a type, optionally filtered by date range."""
        return await _meas.measurement_history(
            module._get_pool(), type, start_date=start_date, end_date=end_date
        )

    @_tool("measurements")
    async def measurement_latest(type: str) -> dict[str, Any] | None:
        """Get the most recent measurement for a type."""
        return await _meas.measurement_latest(module._get_pool(), type)

    @_tool("measurements")
    async def measurement_update(
        measurement_id: str,
        type: str | None = None,
        value: Any | None = None,
        notes: str | None = None,
        measured_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Update a logged measurement. Allowed fields: type, value, notes,
        measured_at.

        Measurements are temporal facts, so the edit updates the existing entry
        in place rather than superseding it. Changing type rewrites the
        predicate; type must be one of the recognized measurement types.
        """
        fields = {
            k: v
            for k, v in {
                "type": type,
                "value": value,
                "notes": notes,
                "measured_at": measured_at,
            }.items()
            if v is not None
        }
        return await _meas.measurement_update(module._get_pool(), measurement_id, **fields)

    @_tool("measurements")
    async def measurement_delete(measurement_id: str) -> bool:
        """Soft-delete a logged measurement (retracts the fact, audit-preserving)."""
        return await _meas.measurement_delete(module._get_pool(), measurement_id)

    # =================================================================
    # Medication tools
    # =================================================================

    @_tool("medications")
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

    @_tool("medications")
    async def medication_list(active_only: bool = True) -> list[dict[str, Any]]:
        """List medications, optionally only active ones."""
        return await _meds.medication_list(module._get_pool(), active_only=active_only)

    @_tool("medications")
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

    @_tool("medications")
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

    @_tool("conditions")
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

    @_tool("conditions")
    async def condition_list(status: str | None = None) -> list[dict[str, Any]]:
        """List conditions, optionally filtered by status."""
        return await _cond.condition_list(module._get_pool(), status=status)

    @_tool("conditions")
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

    @_tool("symptoms")
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

    @_tool("symptoms")
    async def symptom_history(
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Get symptom history, optionally filtered by date range."""
        return await _cond.symptom_history(
            module._get_pool(), start_date=start_date, end_date=end_date
        )

    @_tool("symptoms")
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

    @_tool("symptoms")
    async def symptom_update(
        symptom_id: str,
        name: str | None = None,
        severity: int | None = None,
        condition_id: str | None = None,
        notes: str | None = None,
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Update a logged symptom. Allowed fields: name, severity, condition_id,
        notes, occurred_at.

        Symptoms are temporal facts, so the edit updates the existing entry
        in place rather than superseding it. If severity is provided it must be
        between 1 and 10.
        """
        fields = {
            k: v
            for k, v in {
                "name": name,
                "severity": severity,
                "condition_id": condition_id,
                "notes": notes,
                "occurred_at": occurred_at,
            }.items()
            if v is not None
        }
        return await _cond.symptom_update(module._get_pool(), symptom_id, **fields)

    @_tool("symptoms")
    async def symptom_delete(symptom_id: str) -> bool:
        """Soft-delete a logged symptom (retracts the fact, audit-preserving)."""
        return await _cond.symptom_delete(module._get_pool(), symptom_id)

    # =================================================================
    # Diet & Nutrition tools
    # =================================================================

    @_tool("nutrition")
    async def meal_log(
        type: str,
        description: str,
        eaten_at: datetime,
        nutrition: dict[str, Any] | None = None,
        notes: str | None = None,
        mood_before: int | None = None,
        satisfaction: int | None = None,
        symptom_notes: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Log a meal. Type must be one of: breakfast, lunch, dinner, snack.

        REQUIRED:
        - eaten_at: when the meal was (or will be) eaten. An estimate is fine
          (e.g. "today at noon"). Future times are accepted for planned meals.

        Optional contextual metadata:
        - mood_before: mood rating before the meal (1-10)
        - satisfaction: meal satisfaction rating (1-10)
        - symptom_notes: any symptoms experienced around the meal
        - tags: dietary markers (e.g. "low-carb", "vegetarian", "spicy")
        """
        return await _diet.meal_log(
            module._get_pool(),
            type,
            description,
            eaten_at=eaten_at,
            nutrition=nutrition,
            notes=notes,
            mood_before=mood_before,
            satisfaction=satisfaction,
            symptom_notes=symptom_notes,
            tags=tags,
            create_calendar_event_fn=module._make_calendar_event_fn(),
        )

    @_tool("nutrition")
    async def meal_history(
        type: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Get meal history, optionally filtered by type and date range."""
        return await _diet.meal_history(
            module._get_pool(), type=type, start_date=start_date, end_date=end_date
        )

    @_tool("nutrition")
    async def meal_update(
        meal_id: str,
        type: str | None = None,
        description: str | None = None,
        eaten_at: datetime | None = None,
        nutrition: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Update a logged meal. Allowed fields: type, description, eaten_at,
        nutrition, notes.

        Meals are temporal facts, so the edit updates the existing entry in
        place rather than superseding it. If type is provided it must be one of:
        breakfast, lunch, dinner, snack.
        """
        fields = {
            k: v
            for k, v in {
                "type": type,
                "description": description,
                "eaten_at": eaten_at,
                "nutrition": nutrition,
                "notes": notes,
            }.items()
            if v is not None
        }
        return await _diet.meal_update(module._get_pool(), meal_id, **fields)

    @_tool("nutrition")
    async def meal_delete(meal_id: str) -> bool:
        """Soft-delete a logged meal (retracts the fact, audit-preserving)."""
        return await _diet.meal_delete(module._get_pool(), meal_id)

    @_tool("nutrition")
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

    @_tool("reports")
    async def health_summary() -> dict[str, Any]:
        """Get a health overview: latest measurements, active medications, conditions."""
        return await _reports.health_summary(module._get_pool())

    @_tool("reports")
    async def trend_report(period: str = "week") -> dict[str, Any]:
        """Generate a trend report over a period (week=7d, month=30d).

        Returns measurement trends, medication adherence, symptom frequency
        and severity averages.
        """
        return await _reports.trend_report(module._get_pool(), period=period)

    # =================================================================
    # Research tools
    # =================================================================

    @_tool("research")
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

    @_tool("research")
    async def research_search(
        query: str | None = None,
        tags: list[str] | None = None,
        condition_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search research notes by text query, tags, and/or condition."""
        return await _research.research_search(
            module._get_pool(), query=query, tags=tags, condition_id=condition_id
        )

    @_tool("research")
    async def research_update(
        research_id: str,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        source_url: str | None = None,
        condition_id: str | None = None,
    ) -> dict[str, Any]:
        """Update a research note. Allowed fields: title, content, tags,
        source_url, condition_id.

        Research notes are property facts, so the edit supersedes the prior note
        keyed on its (subject, predicate) pair. If condition_id is provided it
        must reference an existing condition.
        """
        fields = {
            k: v
            for k, v in {
                "title": title,
                "content": content,
                "tags": tags,
                "source_url": source_url,
                "condition_id": condition_id,
            }.items()
            if v is not None
        }
        return await _research.research_update(module._get_pool(), research_id, **fields)

    @_tool("research")
    async def research_delete(research_id: str) -> bool:
        """Soft-delete a research note (retracts the fact, audit-preserving)."""
        return await _research.research_delete(module._get_pool(), research_id)

    @_tool("research")
    async def research_summarize(
        condition_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Summarize research entries, optionally scoped by condition or tags."""
        return await _research.research_summarize(
            module._get_pool(), condition_id=condition_id, tags=tags
        )

    # =================================================================
    # Wellness ingest tool
    # =================================================================

    @mcp.tool()
    async def wellness_ingest_envelope(context: dict[str, Any]) -> dict[str, Any]:
        """Ingest a structured wellness envelope and persist it as a health fact.

        Called exactly once when input.context carries a source.channel='wellness'
        envelope from the google_health or home_assistant connector. Translates the
        ingest.v1 envelope into a temporal fact stored in the health butler's memory
        store.

        Returns a dict with 'status' (ok | rejected_non_owner_sender |
        skipped_unknown_predicate | skipped_malformed_payload | error), and on
        success, 'fact_id' and 'predicate'.
        """
        from butlers.modules.memory.tools import get_embedding_engine

        embedding_engine = get_embedding_engine()
        return await _wellness.translate_wellness_envelope(
            module._get_pool(), embedding_engine, context
        )
