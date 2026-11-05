"""Shared broker test builders (no database)."""

from __future__ import annotations

from uuid import uuid4

from butlers.capability_broker.contracts import Capability, Principal
from butlers.core.goal_admission import Grant
from butlers.core.goal_labels import admission_label
from butlers.core.tool_catalog import OwnerNotifyBranch, RecipientParameters

NOTIFY_RECIPIENTS = RecipientParameters(
    argument_names=("recipient", "entity_id", "request_context"),
    undeterminable=True,
    owner_notify_branch=OwnerNotifyBranch.TELEGRAM_SEND_INSIGHT_WITHOUT_PARTY,
    default_owner_condition="telegram send/insight without party",
    undeterminable_condition="reply/react inherited thread",
)


def capability(name="search", effect_class="read", domain="general", **overrides) -> Capability:
    values = {
        "domain": domain,
        "name": name,
        "effect_class": effect_class,
        "output_trust": "trusted",
        "recipient_params": RecipientParameters(),
        "approvals_enabled": True,
    }
    values.update(overrides)
    return Capability(**values)


def notify(**overrides) -> Capability:
    return capability(
        "notify", "communicate", "messenger", recipient_params=NOTIFY_RECIPIENTS, **overrides
    )


def principal(*grants: Grant, depth=0, exposed=False, origin="owner") -> Principal:
    label = admission_label(exposed)
    return Principal(
        uuid4(), uuid4(), depth, origin, exposed, "catalog-digest", tuple(grants), label
    )
