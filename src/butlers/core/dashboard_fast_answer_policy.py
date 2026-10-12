"""Side-effect-free admission and exact V1 grants for dashboard fast answers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from butlers.core.dashboard_fast_answer_catalog import CatalogOutcome

_V1_NAMES = (
    "dashboard_read_fleet_status",
    "dashboard_read_butler_detail",
    "dashboard_read_sessions_recent",
    "dashboard_read_session_detail",
    "dashboard_read_sessions_aggregate",
    "dashboard_read_sessions_trigger_breakdown",
    "dashboard_read_fleet_errors_recent",
    "dashboard_read_fleet_search",
    "dashboard_read_timeline_recent",
    "dashboard_read_butler_activity",
    "dashboard_read_spend_summary",
    "dashboard_read_spend_daily",
    "dashboard_read_spend_top_sessions",
    "dashboard_read_spend_breakdown_by_butler",
    "dashboard_read_spend_breakdown_by_model",
    "dashboard_read_insight_delivery_state",
)


@dataclass(frozen=True)
class ReadGrant:
    target: Literal["concierge"] = "concierge"
    module: Literal["dashboard_read"] = "dashboard_read"
    effect: Literal["read"] = "read"


FAST_ANSWER_CONCIERGE_TOOLS_V1: Mapping[str, ReadGrant] = MappingProxyType(
    {name: ReadGrant() for name in _V1_NAMES}
)


def valid_grants(grants: Mapping[str, ReadGrant]) -> bool:
    """Discovery cannot broaden or repair a malformed checked-in grant map."""
    return (
        isinstance(grants, Mapping)
        and len(grants) == 16
        and set(grants) == set(_V1_NAMES)
        and all(
            isinstance(grant, ReadGrant)
            and (grant.target, grant.module, grant.effect)
            == ("concierge", "dashboard_read", "read")
            for grant in grants.values()
        )
    )


class PlannedRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class FastAnswerAdmission(BaseModel):
    """Provider output is data; this schema offers no executable/terminal tools."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    lane: Literal["A", "B", "C", "D", "ambiguous"]
    scope: Literal["system", "domain", "none"]
    selected_owner: str | None
    question: str
    reads: tuple[PlannedRead, ...] = Field(max_length=3)


@dataclass(frozen=True)
class AdmissionDecision:
    state: Literal["eligible", "fallback", "no_match"]
    reason: str


def admit_fast_answer(
    admission: FastAnswerAdmission,
    catalog: CatalogOutcome,
    *,
    registered_tools: frozenset[str],
    module_enabled: bool,
    grants: Mapping[str, ReadGrant] = FAST_ANSWER_CONCIERGE_TOOLS_V1,
) -> AdmissionDecision:
    """Validate the whole plan before any read or terminal action begins."""
    if not valid_grants(grants):
        return AdmissionDecision("fallback", "invalid_grants")
    if admission.lane != "D":
        return AdmissionDecision("fallback", "not_question_lane")
    if admission.scope != "system":
        return AdmissionDecision("fallback", "not_system_question")
    if catalog.state == "unavailable":
        return AdmissionDecision("fallback", "catalog_unavailable")
    if catalog.state == "no_match":
        return AdmissionDecision("no_match", "authoritative_no_match")
    if catalog.selected_owner != "concierge" or admission.selected_owner != "concierge":
        return AdmissionDecision("fallback", "owner_not_selected")
    if not module_enabled:
        return AdmissionDecision("fallback", "module_disabled")
    if any(
        read.name not in grants or read.name not in registered_tools for read in admission.reads
    ):
        return AdmissionDecision("fallback", "read_not_authorized")
    return AdmissionDecision("eligible", "eligible")


def reply_observation_seconds(intent_lane: str | None) -> int:
    """An observation window never becomes an execution or replay authority."""
    return 45 if intent_lane == "answer" else 300
