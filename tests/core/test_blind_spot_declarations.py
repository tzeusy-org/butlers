"""bu-2jtfw.13: declared-dependency scoping for the blind-spot preamble.

The mechanism is generic (union of module -> LIKE-pattern registry entries
for a butler's *enabled* modules); only ``health`` is wired to a real
connector-backed signal today (see ``blind_spot_declarations.py`` module
docstring). These tests prove the scoping logic itself with a synthetic
registry entry rather than waiting on a second real connector-backed signal
to exist.
"""

from __future__ import annotations

import pytest

from butlers.core.blind_spot_declarations import (
    MODULE_BLIND_SPOT_SIGNAL_PATTERNS,
    declared_signal_patterns,
)

pytestmark = pytest.mark.unit

_SYNTHETIC_REGISTRY = {
    "health": ("health:measurement-gap:%",),
    "email": ("email:sync-gap:%",),
}


def test_health_module_declares_its_pattern() -> None:
    patterns = declared_signal_patterns({"health": {}}, registry=_SYNTHETIC_REGISTRY)
    assert patterns == ("health:measurement-gap:%",)


def test_butler_without_email_module_declares_no_email_pattern() -> None:
    """AC3: no [modules.email] section -> no email-namespaced pattern, ever."""
    patterns = declared_signal_patterns({"health": {}}, registry=_SYNTHETIC_REGISTRY)
    assert "email:sync-gap:%" not in patterns


def test_butler_with_email_module_declares_its_pattern() -> None:
    patterns = declared_signal_patterns({"email": {}}, registry=_SYNTHETIC_REGISTRY)
    assert patterns == ("email:sync-gap:%",)


def test_unregistered_module_contributes_no_pattern() -> None:
    patterns = declared_signal_patterns(
        {"calendar": {}, "contacts": {}}, registry=_SYNTHETIC_REGISTRY
    )
    assert patterns == ()


def test_patterns_are_deduplicated_and_order_preserving() -> None:
    registry = {"a": ("x:%", "y:%"), "b": ("y:%", "z:%")}
    patterns = declared_signal_patterns({"a": {}, "b": {}}, registry=registry)
    assert patterns == ("x:%", "y:%", "z:%")


def test_default_registry_only_wires_health_today() -> None:
    """Documents the current honest scope: only health has a real signal wired."""
    assert set(MODULE_BLIND_SPOT_SIGNAL_PATTERNS.keys()) == {"health"}
