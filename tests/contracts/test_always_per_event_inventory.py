"""The checked-in always_per_event set equals what every module declares (RFC 0038 D13).

The dashboard process holds no module instance, so rule writes read the per-event mode from the
checked-in inventory (``ALWAYS_PER_EVENT_TOOLS`` and the presentation inventory). A tool a module
declares per-event but the inventory forgets would be a standing-rule hole, so this sweeps every
registered module's ``tool_metadata()`` with its enabling flags on and pins the two sets equal.
"""

from __future__ import annotations

from butlers.core.tool_presentation_inventory import (
    ALWAYS_PER_EVENT_TOOLS,
    TOOL_PRESENTATION_INVENTORY,
)
from butlers.modules.approvals.per_event import is_always_per_event
from butlers.modules.registry import default_registry

_ENABLED_MODULE_STATE = {
    # The response tool registers only for a response owner with responses enabled.
    "calendar": lambda module: (
        setattr(module, "_butler_name", "messenger"),
        setattr(module, "_config", module.config_schema(provider="google", response_enabled=True)),
    ),
}


def _declared_per_event_by_modules() -> tuple[set[str], list[str]]:
    declared: set[str] = set()
    swept: list[str] = []
    for name, module_cls in default_registry()._modules.items():
        try:
            module = module_cls()
        except Exception:  # noqa: BLE001 - a module needing runtime wiring declares no tools here
            continue
        enable = _ENABLED_MODULE_STATE.get(name)
        if enable is not None:
            enable(module)
        try:
            metadata = module.tool_metadata()
        except Exception:  # noqa: BLE001
            continue
        swept.append(name)
        for tool_name, meta in metadata.items():
            policy = getattr(meta, "approval_policy", None) or {}
            if policy.get("mode") == "always_per_event":
                declared.add(tool_name)
    return declared, swept


def test_every_module_declared_per_event_tool_is_in_the_inventory_and_vice_versa() -> None:
    declared, swept = _declared_per_event_by_modules()
    inventoried = set(ALWAYS_PER_EVENT_TOOLS) | {
        item.canonical_name
        for item in TOOL_PRESENTATION_INVENTORY
        if (item.approval_policy or {}).get("mode") == "always_per_event"
    }

    assert "calendar" in swept, "the sweep must reach the calendar module"
    assert "calendar_respond" in declared, "enabling flags must surface the response tool"
    assert declared == inventoried


def test_the_rule_gate_reads_the_inventory_without_a_module() -> None:
    assert is_always_per_event("calendar_respond")
    assert not is_always_per_event("calendar_create_event")
    assert not is_always_per_event(None)
