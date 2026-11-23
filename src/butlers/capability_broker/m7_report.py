"""The M7 typed-boundary gate report (bu-4c7nvp.1.2): evidence, not a release.

Content-blind counts over the owner-only S1 measurements view and the sw_058
stores, plus the exact owner decisions that still hold M7 activation. The
report can never declare the M7 release gate satisfied: that needs the owner's
typed-value decision (``bu-4c7nvp.1.1``) adopted as exact text and wired in
place of ``typed.TYPED_VALUE_RELAXATION``, which this code cannot do.
"""

from __future__ import annotations

from typing import Any

from .typed import TYPED_VALUE_RELAXATION

#: The decision M7 activation needs, stated exactly so the owner answers it once.
TYPED_VALUE_DECISION = {
    "bead": "bu-4c7nvp.1.1",
    "status": "held",
    "question": (
        "May a value that passed the closed typed shape (boolean, bounded number, "
        "enum token, date, UTC instant), when extracted from or returned by content "
        "whose label is not owner-authoritative, enter an effect argument without "
        "carrying that content's D6 exposure? If yes: which typed slots, for which "
        "effect classes, and under which recipient/selector rules."
    ),
    "current_behavior": (
        "No relaxation: a typed value carries its source's whole label, so effects "
        "after an extraction or child return keep the exposed (under-exposure) policy."
    ),
}

CITED_LABEL_DECISION = {
    "bead": "bu-7ry1sy",
    "status": "held",
    "current_behavior": "Cited evidence keeps its source label; no steering exemption.",
}


def release_gate_satisfied() -> bool:
    """False until an adopted typed-value decision replaces the held constant."""
    return TYPED_VALUE_RELAXATION is not None


async def m7_report(connection: Any) -> dict[str, Any]:
    """Build the report on an owner connection (the measurements view is owner-only)."""
    exposure = await connection.fetch(
        "SELECT role, exposure_cause, goal_union_exposed, invocation_exposed, invocations, "
        "effects, approval_routed_effects FROM switchboard.invocation_exposure_measurements "
        "ORDER BY role, exposure_cause NULLS FIRST, goal_union_exposed, invocation_exposed"
    )
    stores = await connection.fetchrow(
        "SELECT (SELECT count(*) FROM switchboard.goal_handles) AS handles, "
        "(SELECT count(*) FROM switchboard.extraction_cache) AS cached_extractions, "
        "(SELECT count(*) FROM switchboard.goal_child_results WHERE result IS NOT NULL) "
        "AS typed_returns, "
        "(SELECT count(*) FROM switchboard.source_containments) AS containments, "
        "(SELECT count(*) FROM switchboard.admission_decisions "
        " WHERE reason = 'recorded_without_goal') AS record_only_admissions"
    )
    return {
        "exposure_and_approval_causes": [dict(row) for row in exposure],
        "typed_boundaries": dict(stores),
        "typed_value_decision": dict(TYPED_VALUE_DECISION),
        "cited_label_decision": dict(CITED_LABEL_DECISION),
        "release_gate_satisfied": release_gate_satisfied(),
    }
