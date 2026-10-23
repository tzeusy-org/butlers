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


async def test_published_reader_preserves_resource_dimensions_at_actual_admission():
    from butlers.core.goal_admission import (
        Evidence,
        EvidenceRefused,
        GoalAdmission,
        Grant,
        Obligation,
        derive_admission,
    )
    from tests.contracts.test_catalog_publication import Connection, complete_fleet

    # Real final registration -> publisher -> current reader -> production
    # admission derivation. The SQL collaborator is not PG or creator proof.
    store, publishers = await complete_fleet()
    registry = await _complete_core_catalog("health")
    notify = registry["notify"]
    assert notify.grantable and "recipient" in notify.resource_params
    await publishers["health"].publish(registry)
    connection = Connection(store, "switchboard")
    async with connection.transaction():
        view = await GoalAdmission(None, None).catalog.load(connection)
        grant = Grant("health__notify", (("recipient", ("fixture-recipient",)),))
        evidence = Evidence(
            "occurrence",
            "fixture-source",
            obligation=Obligation("health", "fixture-revision", (grant,), created_exposed=False),
        )
        plan = derive_admission(evidence, view)
        assert plan.ceiling == (grant,)
        assert not plan.exposed
        # Neither successful publication nor a valid capability can turn a
        # misspelled scope key into an unrestricted real resource grant.
        forged = replace(
            evidence,
            obligation=replace(
                evidence.obligation,
                capabilities=(Grant("health__notify", (("not_a_resource", ("fixture",)),)),),
            ),
        )
        with pytest.raises(EvidenceRefused, match="invalid_contract_resources"):
            derive_admission(forged, view)
        # The same key refusal must hold for the actual local diagnostic path.
        parsed = {
            "version": 1,
            "capabilities": [
                {"capability": "health__notify", "resources": {"not_a_resource": ["fixture"]}}
            ],
        }
        result = validate_declarations(
            {"fixture": parsed}, build_obligation_catalog({"health": registry}), after_switch=False
        )
        assert [issue.code for issue in result.issues] == ["unknown_resource"]
        assert result.declarations == ()


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
    from unittest.mock import AsyncMock, patch

    from butlers import lifecycle
    from butlers.config import load_config
    from butlers.daemon import ButlerDaemon
    from tests.daemon.test_daemon import _make_registry, _patch_infra

    _loaded_config(tmp_path)
    path = tmp_path / "butler.toml"
    path.write_text(
        path.read_text() + '\n[[butler.subscription]]\nname="fixture"\n'
        'kind="domain"\nsource="travel.trip_booked"\n'
        "[butler.subscription.obligation]\nversion=1\ncapabilities=[]\n"
    )
    config = load_config(tmp_path)
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
        assert daemon.obligation_schedule_runtime is None
        assert daemon.obligation_git_sources is not None
        assert (
            daemon.obligation_git_sources.definitions[0].declaration
            is schedules[0].obligation.declaration
        )
        result = actual(schedules, owner_domain=owner_domain, local_catalog=local_catalog)
        assert [i.code for i in result] == ["fleet_catalog_unavailable"]
        order.append("diagnostic")
        return result

    actual_subscriptions = lifecycle.diagnose_loaded_subscriptions

    def subscription_diagnostic(subscriptions, *, owner_domain, local_catalog):
        assert order == ["sync", "finalized", "diagnostic"]
        assert subscriptions[0] is config.subscriptions[0]
        assert local_catalog is registry
        result = actual_subscriptions(
            subscriptions, owner_domain=owner_domain, local_catalog=local_catalog
        )
        assert [i.code for i in result] == ["fleet_catalog_unavailable"]
        order.append("subscription_diagnostic")
        raise Observed

    monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "1")
    patches = _patch_infra()
    with ExitStack() as stack:
        for name, value in patches.items():
            if not name.startswith("mock_"):
                stack.enter_context(value)
        stack.enter_context(patch.object(ButlerDaemon, "_finalize_tool_catalog", finalized))
        from butlers.core.runtime_config import RuntimeConfig

        stack.enter_context(
            patch(
                "butlers.core.runtime_config.RuntimeConfigAccessor.seed_if_empty",
                new=AsyncMock(return_value=RuntimeConfig("health")),
            )
        )
        monkeypatch.setattr(lifecycle, "diagnose_loaded_schedules", diagnostic)
        monkeypatch.setattr(lifecycle, "diagnose_loaded_subscriptions", subscription_diagnostic)
        lifecycle.sync_schedules.side_effect = lambda *a, **k: order.append("sync")
        daemon = ButlerDaemon(tmp_path, registry=_make_registry())
        daemon.config = config
        with pytest.raises(Observed):
            await daemon.start()
    assert order == ["sync", "finalized", "diagnostic", "subscription_diagnostic"]
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


async def test_education_roster_ceilings_cover_actual_registered_workflows(monkeypatch, caplog):
    from pathlib import Path
    from unittest.mock import AsyncMock

    from butlers.config import load_config
    from butlers.core.obligation_catalog import diagnose_loaded_schedules
    from tests.contracts.test_tool_authorization_catalog import _roster_catalog

    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "roster/education")
    prompts = {spec.name: spec for spec in config.schedules if spec.dispatch_mode == "prompt"}
    # These are the workflow branches, including the analytics fallback and
    # orphan/idempotent cleanup paths in the actual stale-flow skill. They must
    # resolve against real registered definitions, not a made-up tool catalog.
    required = {
        "weekly-progress-digest": {
            "analytics_get_cross_topic",
            "analytics_get_trend",
            "analytics_get_snapshot",
            "mastery_get_map_summary",
            "notify",
        },
        "weekly-stale-flow-check": {
            "mind_map_list",
            "mind_map_get",
            "teaching_flow_get",
            "teaching_flow_abandon",
            "mind_map_update_status",
            "spaced_repetition_schedule_cleanup",
            "memory_store_fact",
            "notify",
        },
        "daily-spaced-repetition-nudge": {
            "mind_map_list",
            "spaced_repetition_pending_reviews",
            "notify",
        },
    }
    assert prompts.keys() == required.keys()
    assert {name: spec.obligation.status for name, spec in prompts.items()} == {
        name: "valid" for name in required
    }
    # Registration and final approval wrapping execute; handler/provider/DB
    # bodies do not. The resulting local snapshot still is not a verified fleet.
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    registry = await _roster_catalog("education")
    for name, calls in required.items():
        ceiling = {g.capability: g for g in prompts[name].obligation.declaration.capabilities}
        assert ceiling.keys() == {f"education__{call}" for call in calls}
        for call in calls:
            descriptor = registry[call]
            grant = ceiling[f"education__{call}"]
            assert descriptor.grantable
            assert descriptor.owner_domain == "education"
            assert set(dict(grant.resources)) <= set(descriptor.resource_params)
        # notify cannot acquire an invented channel/owner resource or a policy
        # exemption through this source declaration. Actual delivery is gated.
        assert ceiling["education__notify"].resources == ()
    stale = prompts["weekly-stale-flow-check"].obligation.declaration
    memory = next(g for g in stale.capabilities if g.capability == "education__memory_store_fact")
    assert memory.resources == (("predicate", ("study_pattern",)),)
    assert registry["teaching_flow_abandon"].effect_class == "administer"
    assert registry["spaced_repetition_schedule_cleanup"].effect_class == "administer"
    # Dynamic map IDs stay a population, not guessed fixed owner selections.
    assert all(
        "mind_map_id" not in dict(grant.resources)
        for spec in prompts.values()
        for grant in spec.obligation.declaration.capabilities
    )
    result = diagnose_loaded_schedules(
        config.schedules, owner_domain=config.name, local_catalog=registry
    )
    assert len(result) == len(prompts)
    assert {issue.code for issue in result} == {"fleet_catalog_unavailable"}
    assert all(spec.prompt not in caplog.text for spec in prompts.values())


async def test_roster_prompt_ceilings_resolve_complete_workflows_without_fleet_authority(
    monkeypatch, caplog
):
    from pathlib import Path
    from unittest.mock import AsyncMock

    from butlers.config import load_config
    from butlers.core.obligation_catalog import diagnose_loaded_schedules
    from tests.contracts.test_tool_authorization_catalog import _roster_catalog

    # Each set covers a real scheduled workflow, including skill branches and
    # existing-reminder lookup. Registration/approval wrapping executes, while
    # handlers, database work and provider delivery deliberately do not.
    required = {
        "chronicler": {"chronicler_day_close": {"chronicler_day_close_bundle", "notify"}},
        "general": {"eod-tomorrow-prep": {"state_get", "calendar_list_events", "notify"}},
        "health": {
            "weekly-health-summary": {
                "health_summary",
                "trend_report",
                "measurement_history",
                "medication_list",
                "medication_history",
                "symptom_history",
                "notify",
            }
        },
        "lifestyle": {"weekly-taste-digest": {"memory_search", "notify"}},
        "travel": {
            "upcoming-travel-check": {"upcoming_travel", "notify"},
            "trip-document-expiry": {
                "document_expiry_inventory",
                "memory_fact_inventory",
                "calendar_scan_reminder_events",
                "calendar_create_event",
                "notify",
            },
        },
    }
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    for domain, workflows in required.items():
        config = load_config(root / "roster" / domain)
        prompts = {spec.name: spec for spec in config.schedules if spec.dispatch_mode == "prompt"}
        assert {name: prompts[name].obligation.status for name in workflows} == {
            name: "valid" for name in workflows
        }
        registry = await _roster_catalog(domain)
        for name, calls in workflows.items():
            spec = prompts[name]
            grants = {g.capability: g for g in spec.obligation.declaration.capabilities}
            assert grants.keys() == {f"{domain}__{call}" for call in calls}
            for call in calls:
                descriptor = registry[call]
                assert descriptor.grantable and descriptor.owner_domain == domain
                assert descriptor.effect_class == (
                    "communicate" if call in {"notify", "calendar_create_event"} else "read"
                )
                # Dynamic dates/IDs/recipients are not fabricated static owner
                # selections. Empty restrictions never authenticate those values.
                assert grants[f"{domain}__{call}"].resources == ()
                if domain == "travel" and call in {
                    "document_expiry_inventory",
                    "memory_fact_inventory",
                    "calendar_scan_reminder_events",
                }:
                    # Explicit false keeps current dynamic authorization and
                    # never creates an owner-selected resource grant.
                    assert descriptor.selection_bound == {
                        parameter: False for parameter in descriptor.resource_params
                    }
            assert [
                i.code
                for i in diagnose_loaded_schedules(
                    [spec], owner_domain=domain, local_catalog=registry
                )
            ] == ["fleet_catalog_unavailable"]
            assert spec.prompt not in caplog.text

        if domain == "chronicler":
            # The delivered adjacent helper is a deterministic job. It must
            # retain that executing successor and cannot inherit notify grants.
            from butlers.background import _deterministic_jobs_for_butler
            from butlers.scheduled_jobs import _run_chronicler_gap_interview_job

            gap = next(s for s in config.schedules if s.name == "chronicler_gap_interview")
            assert gap.dispatch_mode == "job" and gap.job_name == "chronicler_gap_interview"
            assert gap.obligation.status == "missing" and gap.name not in prompts
            assert (
                _deterministic_jobs_for_butler(domain)[gap.job_name]
                is _run_chronicler_gap_interview_job
            )
            spec = prompts["chronicler_day_close"]

            def changed(raw):
                return type(spec).model_validate(
                    spec.model_dump(exclude={"obligation"}) | {"obligation": raw}
                )

            for raw, expected in [
                (None, "missing_declaration"),
                (
                    {"version": 1, "capabilities": [{"capability": "chronicler__absent"}]},
                    "unknown_capability",
                ),
                (
                    {"version": 1, "capabilities": [{"capability": "other__absent"}]},
                    "fleet_catalog_unavailable",
                ),
                (
                    {
                        "version": 1,
                        "capabilities": [
                            {
                                "capability": "chronicler__notify",
                                "resources": {"channel": ["fixture"]},
                            }
                        ],
                    },
                    "unknown_resource",
                ),
            ]:
                assert [
                    i.code
                    for i in diagnose_loaded_schedules(
                        [changed(raw)], owner_domain=domain, local_catalog=registry
                    )
                ] == [expected]
            missing_bundle = replace(
                registry,
                descriptors=tuple(
                    d
                    for d in registry.descriptors
                    if d.canonical_name != "chronicler_day_close_bundle"
                ),
            )
            assert [
                i.code
                for i in diagnose_loaded_schedules(
                    [spec], owner_domain=domain, local_catalog=missing_bundle
                )
            ] == ["unknown_capability"]
        if domain == "lifestyle":
            # Search filters are API inputs, not registered resource dimensions.
            assert "predicate" not in registry["memory_search"].resource_params
            assert "memory_store_fact" not in workflows["weekly-taste-digest"]


async def test_home_feed_is_typed_and_checked_against_actual_registered_non_actuating_ceiling(
    monkeypatch, caplog
):
    from pathlib import Path
    from unittest.mock import AsyncMock

    from butlers.config import load_config
    from butlers.core import obligation_catalog
    from tests.contracts.test_tool_authorization_catalog import _roster_catalog

    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "roster" / "home")
    assert len(config.subscriptions) == 1
    subscription = config.subscriptions[0]
    assert (subscription.name, subscription.kind, subscription.source) == (
        "ha-event-response",
        "feed",
        "home_assistant",
    )
    assert subscription.obligation.status == "valid"
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    registry = await _roster_catalog("home")
    declaration = subscription.obligation.declaration
    assert {g.capability for g in declaration.capabilities} == {
        "home__ha_get_entity_state",
        "home__ha_list_entities",
        "home__ha_get_history",
        "home__memory_recall",
        "home__memory_entity_resolve",
        "home__memory_store_fact",
        "home__notify",
    }
    writer = next(g for g in declaration.capabilities if g.capability == "home__memory_store_fact")
    assert dict(writer.resources) == {
        "scope": ("home",),
        "predicate": ("comfort_deviation", "device_issue", "usage_pattern"),
    }
    for grant in declaration.capabilities:
        descriptor = registry[grant.capability.removeprefix("home__")]
        assert set(dict(grant.resources)) <= set(descriptor.resource_params)
        assert descriptor.grantable and descriptor.owner_domain == "home"
        assert descriptor.effect_class in {"read", "record", "communicate"}
        assert not descriptor.owner_authority
    assert [
        i.code
        for i in obligation_catalog.diagnose_loaded_subscriptions(
            config.subscriptions, owner_domain="home", local_catalog=registry
        )
    ] == ["fleet_catalog_unavailable"]
    assert "home_assistant" not in caplog.text


def test_subscription_identity_shape_and_declaration_have_distinct_failure_policies(tmp_path):
    from butlers.config import ConfigError, load_config

    path = tmp_path / "butler.toml"
    prefix = '[butler]\nname = "home"\nport = 9100\n'
    identity = '[[butler.subscription]]\nname="fixture"\nkind="feed"\nsource="home_assistant"\n'
    path.write_text(
        prefix + identity + "[butler.subscription.obligation]\nversion=1\ncapabilities=[]\n"
    )
    original = load_config(tmp_path).subscriptions[0]
    assert original.obligation.status == "valid"
    for declaration, status in [
        ("", "missing"),
        ("[butler.subscription.obligation]\nversion=true\ncapabilities=[]\n", "invalid"),
    ]:
        path.write_text(prefix + identity + declaration)
        assert load_config(tmp_path).subscriptions[0].obligation.status == status
    for bad in [
        identity.replace('kind="feed"', 'kind="owner"'),
        identity.replace('source="home_assistant"', 'source="*"'),
        identity + "unknown=true\n",
        identity + identity,
    ]:
        path.write_text(prefix + bad)
        with pytest.raises(ConfigError):
            load_config(tmp_path)
    path.write_text(
        prefix
        + identity.replace('kind="feed"', 'kind="domain"').replace(
            'source="home_assistant"', 'source="travel.trip_booked"'
        )
    )
    assert load_config(tmp_path).subscriptions[0].source == "travel.trip_booked"


async def test_actual_startup_diagnoses_registered_model_jobs_after_final_catalog(
    tmp_path, monkeypatch, caplog
):
    """Job mode must not hide model-backed work from the real startup consumer."""
    from contextlib import ExitStack
    from unittest.mock import patch

    from butlers import lifecycle
    from butlers.config import load_config
    from butlers.daemon import ButlerDaemon
    from tests.daemon.test_daemon import _make_registry, _patch_infra

    (tmp_path / "butler.toml").write_text(
        '[butler]\nname="switchboard"\nport=9100\n'
        '[[butler.schedule]]\nname="private-daily"\ncron="0 9 * * *"\n'
        'dispatch_mode="job"\njob_name="insight_delivery_cycle"\n'
        '[[butler.schedule]]\nname="private-urgent"\ncron="0 * * * *"\n'
        'dispatch_mode="job"\njob_name="insight_urgent_subcycle"\n'
        "[butler.schedule.obligation]\nversion=1\ncapabilities=[]\n"
        '[[butler.schedule]]\nname="private-untraced"\ncron="0 1 * * *"\n'
        'dispatch_mode="job"\njob_name="memory_episode_cleanup"\n'
    )
    config = load_config(tmp_path)
    registry = await _complete_core_catalog("switchboard")
    order = []

    async def finalized(self):
        order.append("finalized")
        self._tool_catalog = registry
        return registry

    class Observed(Exception):
        pass

    actual = lifecycle.diagnose_loaded_job_schedules

    def diagnostic(schedules, *, owner_domain, local_catalog, job_handlers):
        assert order == ["finalized"]
        assert all(a is b for a, b in zip(schedules, config.schedules, strict=True))
        assert local_catalog is registry
        # These are the exact handlers the background dispatcher will select,
        # not a catalog assembled by this test from desired capability names.
        from butlers.background import _deterministic_jobs_for_butler

        assert job_handlers is _deterministic_jobs_for_butler("switchboard")
        result = actual(
            schedules,
            owner_domain=owner_domain,
            local_catalog=local_catalog,
            job_handlers=job_handlers,
        )
        assert [(i.index, i.code) for i in result] == [
            (0, "missing_declaration"),
            (1, "fleet_catalog_unavailable"),
            (2, "job_semantics_unclassified"),
        ]
        order.append("job_diagnostic")
        raise Observed

    monkeypatch.setenv("BUTLERS_DISABLE_FILE_LOGGING", "1")
    with ExitStack() as stack:
        for name, value in _patch_infra().items():
            if not name.startswith("mock_"):
                stack.enter_context(value)
        stack.enter_context(patch.object(ButlerDaemon, "_finalize_tool_catalog", finalized))
        monkeypatch.setattr(lifecycle, "diagnose_loaded_job_schedules", diagnostic)
        daemon = ButlerDaemon(tmp_path, registry=_make_registry())
        daemon.config = config
        with pytest.raises(Observed):
            await daemon.start()
    assert order == ["finalized", "job_diagnostic"]
    assert "private-" not in caplog.text
    assert "insight_delivery_cycle" not in caplog.text


async def test_job_diagnostics_use_actual_handler_semantics_and_refuse_false_coverage(caplog):
    """Known model paths need declarations; names/untraced jobs prove nothing."""
    from butlers.config import ScheduleSpec
    from butlers.core.obligation_catalog import diagnose_loaded_job_schedules
    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    registry = get_deterministic_schedule_job_registry()
    catalog = await _complete_core_catalog("switchboard")

    def spec(job, declaration=None):
        return ScheduleSpec(
            name="private-source",
            cron="0 1 * * *",
            dispatch_mode="job",
            job_name=job,
            obligation=declaration,
        )

    for domain, job in [
        ("switchboard", "insight_delivery_cycle"),
        ("switchboard", "insight_urgent_subcycle"),
        ("chronicler", "chronicler_narrate_daily"),
        ("health", "memory_consolidation"),
    ]:
        assert [
            i.code
            for i in diagnose_loaded_job_schedules(
                [spec(job)],
                owner_domain=domain,
                local_catalog=catalog,
                job_handlers=registry[domain],
            )
        ] == ["missing_declaration"]

    job = "insight_delivery_cycle"
    for declaration, expected in [
        ({"version": True, "capabilities": []}, "invalid_declaration"),
        (
            {"version": 1, "capabilities": [{"capability": "switchboard__nonexistent"}]},
            "unknown_capability",
        ),
        (
            {"version": 1, "capabilities": [{"capability": "other__not_proven_here"}]},
            "fleet_catalog_unavailable",
        ),
    ]:
        assert [
            i.code
            for i in diagnose_loaded_job_schedules(
                [spec(job, declaration)],
                owner_domain="switchboard",
                local_catalog=catalog,
                job_handlers=registry["switchboard"],
            )
        ] == [expected]

    # The same public names pointing at different functions lose their semantic
    # claim. No default of "job means deterministic" and no forged model grant.
    assert [
        i.code
        for i in diagnose_loaded_job_schedules(
            [spec(job), spec("missing")],
            owner_domain="switchboard",
            local_catalog=catalog,
            job_handlers={job: registry["switchboard"]["memory_episode_cleanup"]},
        )
    ] == ["job_semantics_unclassified", "unregistered_job"]
    for domain, job in [
        ("home", "home_safety_sentinel"),
        ("home", "context_producer_home_presence"),
        ("travel", "context_producer_commuting_eta"),
    ]:
        assert (
            diagnose_loaded_job_schedules(
                [spec(job)],
                owner_domain=domain,
                local_catalog=catalog,
                job_handlers=registry[domain],
            )
            == ()
        )
        assert [
            i.code
            for i in diagnose_loaded_job_schedules(
                [spec(job)],
                owner_domain=domain,
                local_catalog=catalog,
                job_handlers={job: registry["switchboard"]["insight_delivery_cycle"]},
            )
        ] == ["missing_declaration"]
    assert "private-source" not in caplog.text
    assert "nonexistent" not in caplog.text
    assert "other__not_proven_here" not in caplog.text


async def test_loaded_source_snapshot_binds_real_loader_registry_and_held_policy(tmp_path):
    import hashlib
    import json

    from butlers.core.obligation_git_sources import capture_loaded_git_sources
    from butlers.core.runtime_config import RuntimeConfig

    config = _loaded_config(tmp_path)
    registry = await _complete_core_catalog("health")
    runtime = RuntimeConfig("health", catalog_read_sensitivity="sensitive")
    snapshot = capture_loaded_git_sources(config, runtime, registry)
    assert (
        snapshot.source_sha256
        == hashlib.sha256((tmp_path / "butler.toml").read_bytes()).hexdigest()
    )
    context = json.loads(snapshot.context_json)
    assert context["catalog_scope"] == "local_registration"
    assert context["registry_generation"] == registry.generation_digest
    assert context["runtime_policy_scope"] == "held_effective"
    assert context["runtime_policy"]["catalog_read_sensitivity"] == "sensitive"
    assert snapshot.definitions[0].declaration is config.schedules[0].obligation.declaration
    original = snapshot.digest
    runtime2 = replace(runtime, catalog_read_sensitivity="normal")
    assert capture_loaded_git_sources(config, runtime2, registry).digest != original
    config.schedules[0].prompt = "changed after the strict loader"
    with pytest.raises(DeclarationError, match="loaded_source_changed"):
        capture_loaded_git_sources(config, runtime, registry)
    assert snapshot.digest == original
    with pytest.raises(DeclarationError, match="source_owner_mismatch"):
        capture_loaded_git_sources(
            _loaded_config(tmp_path), replace(runtime, butler_name="other"), registry
        )
