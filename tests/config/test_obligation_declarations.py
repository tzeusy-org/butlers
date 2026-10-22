"""RFC0038 declaration parsing and actual-registry validation.

REQ-goal-orchestration-007, REQ-capability-authorization-013.
These controls exercise value boundaries and actual dark startup diagnostics;
no authenticated fleet publication or admission switch is wired.
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


def _loaded_config(tmp_path, declaration="version = 1\ncapabilities = []"):
    from butlers.config import load_config

    (tmp_path / "butler.toml").write_text(
        '[butler]\nname = "health"\nport = 9100\n'
        '[[butler.schedule]]\nname = "fixture"\ncron = "0 9 * * *"\n'
        'prompt = "Inspect the fixture"\n'
        "[butler.schedule.obligation]\n" + declaration + "\n"
    )
    return load_config(tmp_path)


def test_real_loaded_declaration_is_single_parse_immutable_and_precisely_refused(tmp_path):
    import subprocess
    import sys
    from dataclasses import FrozenInstanceError

    from butlers.config import ConfigError, ScheduleSpec

    loaded = _loaded_config(tmp_path).schedules[0]
    carrier = loaded.obligation
    assert carrier.status == "valid"
    assert carrier.declaration.capabilities == ()
    with pytest.raises(FrozenInstanceError):
        carrier.status = "missing"
    assert ScheduleSpec(name="plain", cron="0 9 * * *", prompt="x").obligation.status == "missing"
    for malformed in [
        "version = true\ncapabilities = []",
        "version = 1\ncapabilities = []\npreconditions = {}",
    ]:
        assert _loaded_config(tmp_path, malformed).schedules[0].obligation.status == "invalid"
    # Only the declaration has a dark warning policy. Ordinary configuration
    # errors retain their strict, startup-blocking contract.
    for changes in [{"unknown": True}, {"cron": "bad"}, {"job_name": "wrong-mode"}]:
        with pytest.raises(ValueError):
            ScheduleSpec(**({"name": "plain", "cron": "0 9 * * *", "prompt": "x"} | changes))
    with pytest.raises(ConfigError):
        _loaded_config(
            tmp_path, "version = 1\ncapabilities = []\n[butler.schedule.misspelled]\nx = 1"
        )
    for first in ["butlers.config", "butlers.core.obligations"]:
        code = (
            f"import {first}; from butlers.config import ScheduleSpec; "
            's=ScheduleSpec(name="x",cron="0 1 * * *",prompt="x",'
            'obligation={"version":1,"capabilities":[]}); '
            'assert s.obligation.status == "valid"'
        )
        subprocess.run([sys.executable, "-c", code], check=True, timeout=20)


async def test_actual_startup_diagnostics_follow_final_approval_catalog(
    tmp_path, monkeypatch, caplog
):
    from contextlib import ExitStack
    from unittest.mock import patch

    from butlers import lifecycle
    from butlers.daemon import ButlerDaemon
    from tests.daemon.test_daemon import _make_registry, _patch_infra

    config = _loaded_config(tmp_path)
    registry = await _complete_core_catalog("health")
    order = []

    async def finalized(self):
        order.append("finalized")
        self._tool_catalog = registry
        return registry

    class Observed(Exception):
        pass

    actual = lifecycle.diagnose_loaded_schedules

    def diagnostic(schedules, *, owner_domain, local_catalog):
        assert order == ["sync", "finalized"]
        assert schedules[0] is config.schedules[0]
        assert local_catalog is registry
        result = actual(schedules, owner_domain=owner_domain, local_catalog=local_catalog)
        assert [i.code for i in result] == ["fleet_catalog_unavailable"]
        order.append("diagnostic")
        raise Observed

    monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "1")
    patches = _patch_infra()
    with ExitStack() as stack:
        for name, value in patches.items():
            if not name.startswith("mock_"):
                stack.enter_context(value)
        stack.enter_context(patch.object(ButlerDaemon, "_finalize_tool_catalog", finalized))
        monkeypatch.setattr(lifecycle, "diagnose_loaded_schedules", diagnostic)
        lifecycle.sync_schedules.side_effect = lambda *a, **k: order.append("sync")
        daemon = ButlerDaemon(tmp_path, registry=_make_registry())
        daemon.config = config
        with pytest.raises(Observed):
            await daemon.start()
    assert order == ["sync", "finalized", "diagnostic"]
    assert "Inspect the fixture" not in caplog.text

    # A final local registration can disprove a local request. It must not
    # pretend that syntax/local success is a verified fleet catalog.
    actual_tool = next(d for d in registry.descriptors if d.grantable)
    local_name = f"health__{actual_tool.canonical_name}"
    for name, expected in [
        (local_name, "fleet_catalog_unavailable"),
        ("health__route.execute", "unknown_capability"),
        ("other__not_locally_registered", "fleet_catalog_unavailable"),
    ]:
        loaded = _loaded_config(
            tmp_path, f'version = 1\ncapabilities = [{{capability = "{name}"}}]'
        )
        result = actual(loaded.schedules, owner_domain="health", local_catalog=registry)
        assert [i.code for i in result] == [expected]
    for value, expected in [("version = true\ncapabilities = []", "invalid_declaration")]:
        result = actual(
            _loaded_config(tmp_path, value).schedules,
            owner_domain="health",
            local_catalog=registry,
        )
        assert [i.code for i in result] == [expected]
    assert "other__not_locally_registered" not in caplog.text
