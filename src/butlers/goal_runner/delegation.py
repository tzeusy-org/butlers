"""D9 delegation: the closed ``goal.delegate`` request and the child grant.

Pure and deterministic. The request is session-supplied, so it is parsed
against a closed shape: it names a brief, capabilities, a model tier and a
budget share, and nothing else. In particular it cannot assert a parent, a
root or a depth; the runner derives those from the calling invocation's row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from butlers.capability_broker.contracts import BrokerRefused, Catalog, Reason
from butlers.capability_broker.typed import TypedRefused, TypedSchema, parse_schema
from butlers.core.goal_admission import Grant, intersect

from .contracts import Brief

_TEXT_MAX = 4000
_ITEM_MAX = 1000
_ITEMS_MAX = 32
_CAPABILITIES_MAX = 64
# Effect classes no child may hold: administer by D9, transact by owner decision 8.
_NEVER_DELEGATED = frozenset({"administer", "transact"})


@dataclass(frozen=True, slots=True)
class BudgetShare:
    """Calls, tokens and wall time drawn from the goal budget (D9, D10)."""

    calls: int
    tokens: int
    wall_seconds: int


@dataclass(frozen=True, slots=True)
class DelegationRequest:
    brief: Brief
    result_schema: TypedSchema
    capabilities: tuple[Grant, ...]
    model_tier: str
    share: BudgetShare


def _refuse() -> BrokerRefused:
    return BrokerRefused(Reason.POLICY)


def _exact(node: Any, keys: set[str], required: set[str]) -> dict[str, Any]:
    if type(node) is not dict or not required <= set(node) or not set(node) <= keys:
        raise _refuse()
    return node


def _text(value: Any, limit: int) -> str:
    if type(value) is not str or not value.strip() or len(value) > limit or "\0" in value:
        raise _refuse()
    return value


def _texts(value: Any) -> tuple[str, ...]:
    if type(value) is not list or len(value) > _ITEMS_MAX:
        raise _refuse()
    return tuple(_text(item, _ITEM_MAX) for item in value)


def _count(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise _refuse()
    return value


def _grant(node: Any) -> Grant:
    entry = _exact(node, {"capability", "resources", "park_only"}, {"capability"})
    resources = entry.get("resources", {})
    park_only = entry.get("park_only", False)
    if type(entry["capability"]) is not str or type(resources) is not dict:
        raise _refuse()
    if type(park_only) is not bool:
        raise _refuse()
    try:
        return Grant(
            entry["capability"],
            tuple(
                (key, tuple(ids) if type(ids) is list else ids)
                for key, ids in sorted(resources.items())
            ),
            park_only,
        )
    except (ValueError, TypeError):
        raise _refuse() from None


def parse_delegation(arguments: Any) -> DelegationRequest:
    """The closed ``goal.delegate`` request; anything else refuses whole."""
    request = _exact(
        arguments,
        {"brief", "capabilities", "model_tier", "budget_share"},
        {"brief", "capabilities", "model_tier", "budget_share"},
    )
    brief = _exact(
        request["brief"],
        {"objective", "success_conditions", "context_refs", "guidance_refs", "result_schema"},
        {"objective", "result_schema"},
    )
    try:
        schema = parse_schema(brief["result_schema"])
    except TypedRefused:
        raise _refuse() from None
    share = _exact(
        request["budget_share"],
        {"calls", "tokens", "wall_seconds"},
        {"calls", "tokens", "wall_seconds"},
    )
    capabilities = request["capabilities"]
    if type(capabilities) is not list or len(capabilities) > _CAPABILITIES_MAX:
        raise _refuse()
    grants = tuple(_grant(item) for item in capabilities)
    if len({g.capability for g in grants}) != len(grants):
        raise _refuse()
    tier = request["model_tier"]
    if type(tier) is not str:
        raise _refuse()
    return DelegationRequest(
        brief=Brief(
            objective=_text(brief["objective"], _TEXT_MAX),
            success_conditions=_texts(brief.get("success_conditions", [])),
            context_refs=_texts(brief.get("context_refs", [])),
            guidance_refs=_texts(brief.get("guidance_refs", [])),
        ),
        result_schema=schema,
        capabilities=grants,
        model_tier=tier,
        share=BudgetShare(
            calls=_count(share["calls"]),
            tokens=_count(share["tokens"]),
            wall_seconds=_count(share["wall_seconds"]),
        ),
    )


def child_grant(
    requested: tuple[Grant, ...], lead: tuple[Grant, ...], catalog: Catalog
) -> tuple[Grant, ...]:
    """The request intersected with the lead's grant, minus administer (D9).

    An empty request, or one that is not a subset of the lead's grant (a
    capability the lead lacks, or a resource scope wider than the lead's), is
    refused rather than narrowed. A capability missing from the published
    catalog is refused too. Park-only travels down: the child's grant is
    park-only wherever the request or the lead's grant is.
    """
    if not requested:
        raise BrokerRefused(Reason.CAPABILITY)
    narrowed = {g.capability: g for g in intersect(requested, lead)}
    for grant in requested:
        within = narrowed.get(grant.capability)
        if within is None or within.resources != grant.resources:
            raise BrokerRefused(Reason.CAPABILITY)
        if catalog.get(grant.capability) is None:
            raise BrokerRefused(Reason.CAPABILITY)
    child = tuple(
        grant
        for name, grant in sorted(narrowed.items())
        if catalog.get(name).effect_class not in _NEVER_DELEGATED  # type: ignore[union-attr]
    )
    if not child:
        raise BrokerRefused(Reason.CAPABILITY)
    return child
