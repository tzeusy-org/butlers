"""RFC0038 declaration parsing and actual-registry validation.

REQ-goal-orchestration-007, REQ-capability-authorization-013.
These controls exercise value boundaries; no startup or publication is wired.
"""

from dataclasses import replace

import pytest

from butlers.core.obligation_catalog import (
    DeclarationValidationError,
    build_obligation_catalog,
    validate_declarations,
)
from butlers.core.obligations import DeclarationError, parse_declaration, qualify_capability
from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog

pytestmark = pytest.mark.unit


def test_declarations_are_exact_immutable_sets_not_authority_envelopes():
    raw = {
        "version": 1,
        "capabilities": [
            {
                "capability": "health__read",
                "resources": {"entity_id": ["fixture-b", "fixture-a"]},
                "park_only": True,
            }
        ],
    }
    declaration = parse_declaration(raw)
    original_digest = declaration.digest
    raw["capabilities"][0]["resources"]["entity_id"].append("fixture-c")
    assert declaration.capabilities[0].resources == (("entity_id", ("fixture-a", "fixture-b")),)
    assert declaration.digest == original_digest
    assert declaration.capabilities[0].park_only
    assert parse_declaration({"version": 1, "capabilities": []}).capabilities == ()

    invalid = [
        {"version": 1, "capabilities": [{"capability": "health.read"}]},
        {"version": True, "capabilities": []},
        {"version": 2, "capabilities": []},
        {"version": 1},
        {"version": 1, "capabilities": [], "origin": "owner"},
        {"version": 1, "capabilities": [], "preconditions": {}},
        {"version": 1, "capabilities": [{"capability": "health__*"}]},
        {"version": 1, "capabilities": [{"capability": "health__read", "park_only": 1}]},
        {"version": 1, "capabilities": [{"capability": "health__read", "creator_invocation": "x"}]},
        {
            "version": 1,
            "capabilities": [{"capability": "health__read", "resources": {"entity_id": []}}],
        },
        {
            "version": 1,
            "capabilities": [{"capability": "health__read", "resources": {"entity_id": ["*"]}}],
        },
        {
            "version": 1,
            "capabilities": [
                {"capability": "health__read", "resources": {"entity_id": ["a", "a"]}}
            ],
        },
        {"version": 1, "capabilities": [{"capability": "health__read"}] * 2},
    ]
    for value in invalid:
        with pytest.raises(DeclarationError):
            parse_declaration(value)


async def test_actual_registered_catalog_validation_warns_dark_and_refuses_after_switch(caplog):
    # Actual public core tool registration and approval wrapping, not a list of
    # invented capability names. This does not publish a fleet catalog.
    registry = await _complete_core_catalog("health")
    catalog = build_obligation_catalog({"health": registry})
    # RFC0038 D7 uses one domain__tool identity. Native dotted suffixes stay
    # intact; accepting domain.tool here would disagree with the real broker.
    assert "route.execute" in registry
    native_route = registry["route.execute"]
    assert qualify_capability("health", native_route.canonical_name) == "health__route.execute"
    assert not native_route.grantable
    assert "health__route.execute" not in {c.id for c in catalog.view.capabilities}
    actual = next(d for d in registry.descriptors if d.grantable and d.effect_class == "read")
    capability = f"health__{actual.canonical_name}"
    valid = {"version": 1, "capabilities": [{"capability": capability}]}
    result = validate_declarations({"healthy": valid}, catalog, after_switch=True)
    assert not result.issues
    assert result.declarations[0][1].capabilities[0].capability == capability
    assert catalog.view.digest
    assert all(c.owner_default is False for c in catalog.view.capabilities)

    populations = {
        "missing": None,
        "malformed": {"version": 1, "capabilities": [], "owner": True},
        "unknown": {"version": 1, "capabilities": [{"capability": "health__not_registered"}]},
        "bad_scope": {
            "version": 1,
            "capabilities": [{"capability": capability, "resources": {"not_an_argument": ["a"]}}],
        },
    }
    private = next((d for d in registry.descriptors if not d.grantable), None)
    assert private is not None
    populations["not_grantable"] = {
        "version": 1,
        "capabilities": [{"capability": f"health__{private.canonical_name}"}],
    }
    result = validate_declarations(populations, catalog, after_switch=False)
    assert {issue.code for issue in result.issues} == {
        "missing_declaration",
        "invalid_declaration",
        "unknown_capability",
        "unknown_resource",
    }
    assert result.declarations == ()
    assert "invalid_declaration" in caplog.text
    assert "not_an_argument" not in caplog.text
    with pytest.raises(DeclarationValidationError):
        validate_declarations(populations, catalog, after_switch=True)
    assert (
        validate_declarations({"healthy": valid}, None, after_switch=False).issues[0].code
        == "catalog_unavailable"
    )
    with pytest.raises(DeclarationValidationError):
        validate_declarations({"healthy": valid}, None, after_switch=True)

    # Classification/authorization drift cannot silently become a new eligible
    # catalog. A copied source registration is only a mutation control here.
    broken = replace(actual, owner_domain=None)
    registry = replace(registry, descriptors=(broken,))
    with pytest.raises(DeclarationValidationError):
        build_obligation_catalog({"health": registry})
