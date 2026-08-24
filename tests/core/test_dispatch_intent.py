"""Tests for the inference contract: capability descriptors and dispatch intent.

Covers (bu-6jv4m.7):
- ``ModelFeature`` / ``CapabilityDescriptor``: three-valued support, layering,
  validation of stored envelopes, and the fail-closed default for an unregistered
  runtime type.
- Adapter baselines: every in-tree ``RuntimeAdapter`` declares a parseable
  ``declared_capabilities``, and ``session_resume`` tracks ``supports_resume``.
- ``derive_dispatch_intent`` / ``trigger_class_of``: deterministic, prompt-free,
  and grounded in what the spawner actually wires per trigger source.
- ``evaluate_fit``: each hard-fit rule, and the consequence-dependent handling of
  an UNKNOWN required capability.

Pure unit tests -- no database, no runtime, no network.
"""

from __future__ import annotations

import pytest

from butlers.core.dispatch_intent import (
    DISPATCH_POLICY_VERSION,
    UNKNOWN_TRIGGER_CLASS,
    Consequence,
    DispatchIntent,
    FitCode,
    derive_dispatch_intent,
    evaluate_fit,
    trigger_class_of,
)
from butlers.core.model_capabilities import (
    EMPTY_CAPABILITIES,
    CapabilityDescriptor,
    CapabilityDescriptorError,
    ModelFeature,
    Support,
    adapter_capability_baseline,
    capability_baseline_of_adapter,
    effective_capabilities,
    parse_capability_descriptor,
)

# ---------------------------------------------------------------------------
# Capability descriptors
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_support_is_three_valued() -> None:
    """Absence is UNKNOWN, not "no" -- the distinction the whole fail-closed rule rests on."""
    descriptor = CapabilityDescriptor(
        features={ModelFeature.TOOL_USE: True, ModelFeature.STRUCTURED_OUTPUT: False}
    )
    assert descriptor.support(ModelFeature.TOOL_USE) is Support.SUPPORTED
    assert descriptor.support(ModelFeature.STRUCTURED_OUTPUT) is Support.UNSUPPORTED
    assert descriptor.support(ModelFeature.SESSION_RESUME) is Support.UNKNOWN
    assert EMPTY_CAPABILITIES.support(ModelFeature.TOOL_USE) is Support.UNKNOWN


@pytest.mark.unit
def test_layered_over_merges_per_key_with_overlay_winning() -> None:
    """A per-entry envelope refines the adapter baseline; it does not replace it."""
    base = CapabilityDescriptor(
        features={ModelFeature.TOOL_USE: True, ModelFeature.SESSION_RESUME: False},
        max_context_tokens=100_000,
    )
    overlay = CapabilityDescriptor(
        features={ModelFeature.SESSION_RESUME: True}, max_output_tokens=8_000
    )
    merged = overlay.layered_over(base)

    # Untouched baseline key survives; overlapping key takes the overlay's value.
    assert merged.support(ModelFeature.TOOL_USE) is Support.SUPPORTED
    assert merged.support(ModelFeature.SESSION_RESUME) is Support.SUPPORTED
    assert merged.max_context_tokens == 100_000
    assert merged.max_output_tokens == 8_000
    # Inputs are frozen dataclasses and must not have been mutated.
    assert base.support(ModelFeature.SESSION_RESUME) is Support.UNSUPPORTED
    assert base.max_output_tokens is None


@pytest.mark.unit
def test_parse_capability_descriptor_accepts_none_dict_and_json_text() -> None:
    """asyncpg may hand back a dict or raw JSON text depending on codec registration."""
    assert parse_capability_descriptor(None) == EMPTY_CAPABILITIES
    assert parse_capability_descriptor({}) == EMPTY_CAPABILITIES

    from_dict = parse_capability_descriptor({"tool_use": True}, max_context_tokens=64_000)
    from_text = parse_capability_descriptor('{"tool_use": true}', max_context_tokens=64_000)
    assert from_dict == from_text
    assert from_dict.support(ModelFeature.TOOL_USE) is Support.SUPPORTED
    assert from_dict.max_context_tokens == 64_000


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "[]",
        '"a string"',
        {"tool_use": "yes"},
        {"no_such_feature": True},
        123,
    ],
)
def test_parse_capability_descriptor_rejects_unusable_envelopes(raw: object) -> None:
    """A malformed envelope raises rather than silently reading as "no requirements met"."""
    with pytest.raises(CapabilityDescriptorError):
        parse_capability_descriptor(raw)


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0, -1, "many", 1.5])
def test_parse_capability_descriptor_rejects_non_positive_context(bad: object) -> None:
    with pytest.raises(CapabilityDescriptorError):
        parse_capability_descriptor(None, max_context_tokens=bad)


@pytest.mark.unit
def test_capability_error_never_echoes_the_stored_value() -> None:
    """Error text names the key and the type, never the value it read.

    Catalog rows are operator-owned data; an exception that interpolates a stored
    value can carry it into logs and API errors, which is exactly the class of leak
    the repo's error-message discipline exists to prevent.
    """
    secret_shaped = "sk-do-not-echo-this-synthetic-value"
    with pytest.raises(CapabilityDescriptorError) as exc_info:
        parse_capability_descriptor({"tool_use": secret_shaped})
    message = str(exc_info.value)
    assert secret_shaped not in message
    assert "tool_use" in message
    assert "str" in message


# ---------------------------------------------------------------------------
# Adapter baselines
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_every_registered_adapter_declares_a_parseable_baseline() -> None:
    """Contract test: no adapter may ship a baseline the descriptor layer rejects."""
    from butlers.core.runtimes import get_adapter, list_registered_runtime_types

    runtime_types = list_registered_runtime_types()
    assert runtime_types, "runtime registry is empty; the rest of this test proves nothing"
    for runtime_type in runtime_types:
        adapter_cls = get_adapter(runtime_type)
        baseline = capability_baseline_of_adapter(adapter_cls)
        assert baseline == adapter_capability_baseline(runtime_type)
        expected_resume = Support.SUPPORTED if adapter_cls.supports_resume else Support.UNSUPPORTED
        assert baseline.support(ModelFeature.SESSION_RESUME) is expected_resume


@pytest.mark.unit
def test_api_adapter_baseline_reports_no_tool_use() -> None:
    """The concrete misfit this change exists to catch, asserted at its source.

    ``ApiAdapter.invoke`` raises for any non-empty ``mcp_servers``; the baseline must
    say so rather than leaving it UNKNOWN, because UNKNOWN is only fail-closed above
    OBSERVE consequence while an explicit ``false`` excludes everywhere.
    """
    baseline = adapter_capability_baseline("api")
    assert baseline.support(ModelFeature.TOOL_USE) is Support.UNSUPPORTED
    assert baseline.support(ModelFeature.STRUCTURED_OUTPUT) is Support.SUPPORTED

    claude = adapter_capability_baseline("claude")
    assert claude.support(ModelFeature.TOOL_USE) is Support.SUPPORTED
    assert claude.support(ModelFeature.SESSION_RESUME) is Support.SUPPORTED


@pytest.mark.unit
def test_unregistered_runtime_type_is_all_unknown() -> None:
    """An unknown runtime proves nothing, so it fails closed rather than open."""
    assert adapter_capability_baseline("no-such-runtime-type") == EMPTY_CAPABILITIES


@pytest.mark.unit
def test_effective_capabilities_layers_row_over_adapter() -> None:
    """A catalog row can contradict its adapter baseline; the row wins."""
    caps = effective_capabilities("api", {"tool_use": True}, max_context_tokens=200_000)
    assert caps.support(ModelFeature.TOOL_USE) is Support.SUPPORTED
    assert caps.support(ModelFeature.STRUCTURED_OUTPUT) is Support.SUPPORTED
    assert caps.max_context_tokens == 200_000

    # Empty envelope (the default for every pre-existing row) changes nothing.
    assert effective_capabilities("claude", {}) == adapter_capability_baseline("claude")
    assert effective_capabilities("claude", None) == adapter_capability_baseline("claude")


# ---------------------------------------------------------------------------
# Dispatch intent
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("trigger_source", "expected"),
    [
        ("tick", "tick"),
        ("schedule:daily_digest", "schedule"),
        ("deadline:rent_due", "deadline"),
        ("classification", "classification"),
        ("external", "external"),
        ("route", "route"),
        ("dashboard", "dashboard"),
        ("healing", "healing"),
        ("qa", "qa"),
        ("something_new", UNKNOWN_TRIGGER_CLASS),
        ("", UNKNOWN_TRIGGER_CLASS),
        (None, UNKNOWN_TRIGGER_CLASS),
    ],
)
def test_trigger_class_of(trigger_source: str | None, expected: str) -> None:
    """Task-qualified sources collapse to their class; anything unknown stays unknown."""
    assert trigger_class_of(trigger_source) == expected


@pytest.mark.unit
def test_derive_dispatch_intent_is_deterministic() -> None:
    """Same inputs, equal intents -- no clock, no randomness, nothing read from a prompt."""
    first = derive_dispatch_intent("route", "workhorse", deadline_s=120.0)
    second = derive_dispatch_intent("route", "workhorse", deadline_s=120.0)
    assert first == second
    assert first.describe() == second.describe()


@pytest.mark.unit
def test_tool_wired_triggers_require_tool_use() -> None:
    """Every trigger the spawner gives MCP servers must require tool use.

    Grounded in ``ButlerSpawner._run``: ``mcp_servers`` is empty only for ``healing``
    and ``qa``; every other source gets the butler's own MCP endpoint wired in.
    """
    for source in (
        "tick",
        "schedule:x",
        "deadline:x",
        "classification",
        "external",
        "trigger",
        "route",
        "dashboard",
        "unrecognized",
    ):
        intent = derive_dispatch_intent(source, "workhorse")
        assert ModelFeature.TOOL_USE in intent.required_features, source

    for source in ("healing", "qa"):
        intent = derive_dispatch_intent(source, "workhorse")
        assert intent.required_features == frozenset(), source


@pytest.mark.unit
def test_consequence_levels_match_who_sees_the_result() -> None:
    """OBSERVE only where nothing outside the system is waiting on the output."""
    assert derive_dispatch_intent("classification", "cheap").consequence is Consequence.OBSERVE
    assert derive_dispatch_intent("qa", "cheap").consequence is Consequence.OBSERVE
    assert derive_dispatch_intent("tick", "cheap").consequence is Consequence.REVERSIBLE
    assert derive_dispatch_intent("route", "cheap").consequence is Consequence.EXTERNAL
    # An unrecognized trigger is treated as the most consequential, not the least.
    assert derive_dispatch_intent("who_knows", "cheap").consequence is Consequence.EXTERNAL


@pytest.mark.unit
def test_extra_required_features_only_add() -> None:
    """A call site can add a requirement; it can never drop the trigger's own."""
    intent = derive_dispatch_intent(
        "external", "workhorse", extra_required_features=[ModelFeature.STRUCTURED_OUTPUT]
    )
    assert intent.required_features == frozenset(
        {ModelFeature.TOOL_USE, ModelFeature.STRUCTURED_OUTPUT}
    )
    # Anything newly required stops being merely preferred.
    assert not (intent.preferred_features & intent.required_features)


@pytest.mark.unit
def test_intent_describe_is_json_safe_and_prompt_free() -> None:
    """The receipt projection must survive ``json.dumps`` and carry no free text."""
    import json

    intent = derive_dispatch_intent(
        "dashboard", "reasoning", deadline_s=90.0, min_context_tokens=32_000
    )
    payload = intent.describe()
    assert json.loads(json.dumps(payload)) == payload
    assert set(payload) == {
        "trigger_class",
        "complexity_tier",
        "consequence",
        "required_features",
        "preferred_features",
        "min_context_tokens",
        "deadline_s",
        "max_cost_usd_per_call",
    }
    assert DISPATCH_POLICY_VERSION == "1"


# ---------------------------------------------------------------------------
# Hard fit
# ---------------------------------------------------------------------------


def _intent(**kwargs) -> DispatchIntent:
    base = {
        "trigger_class": "external",
        "complexity_tier": "workhorse",
        "consequence": Consequence.EXTERNAL,
    }
    base.update(kwargs)
    return DispatchIntent(**base)


@pytest.mark.unit
def test_fit_passes_when_nothing_is_required() -> None:
    """The migration property: an empty intent excludes nobody, not even an empty descriptor."""
    verdict = evaluate_fit(_intent(), EMPTY_CAPABILITIES)
    assert verdict.eligible
    assert verdict.exclusions == ()


@pytest.mark.unit
def test_required_unsupported_always_excludes() -> None:
    """Explicit ``false`` is proof of misfit at every consequence level."""
    caps = CapabilityDescriptor(features={ModelFeature.TOOL_USE: False})
    for consequence in Consequence:
        verdict = evaluate_fit(
            _intent(
                consequence=consequence,
                required_features=frozenset({ModelFeature.TOOL_USE}),
            ),
            caps,
        )
        assert not verdict.eligible, consequence
        assert verdict.primary_exclusion is not None
        assert verdict.primary_exclusion.code is FitCode.CAPABILITY_UNSUPPORTED


@pytest.mark.unit
def test_required_unknown_fails_closed_except_at_observe() -> None:
    """Absence of proof disqualifies once anything outside the system depends on the run."""
    required = frozenset({ModelFeature.TOOL_USE})

    for consequence in (Consequence.REVERSIBLE, Consequence.EXTERNAL):
        verdict = evaluate_fit(
            _intent(consequence=consequence, required_features=required), EMPTY_CAPABILITIES
        )
        assert not verdict.eligible, consequence
        assert verdict.primary_exclusion is not None
        assert verdict.primary_exclusion.code is FitCode.CAPABILITY_UNKNOWN

    observe = evaluate_fit(
        _intent(consequence=Consequence.OBSERVE, required_features=required), EMPTY_CAPABILITIES
    )
    assert observe.eligible
    assert observe.exclusions == ()
    assert [f.code for f in observe.advisories] == [FitCode.CAPABILITY_UNKNOWN]


@pytest.mark.unit
def test_unmet_preferred_feature_is_advisory_only() -> None:
    """Preferences are recorded on the receipt; they never disqualify and never re-rank."""
    verdict = evaluate_fit(
        _intent(preferred_features=frozenset({ModelFeature.SESSION_RESUME})),
        CapabilityDescriptor(features={ModelFeature.SESSION_RESUME: False}),
    )
    assert verdict.eligible
    assert verdict.exclusions == ()
    assert verdict.advisories


@pytest.mark.unit
def test_context_floor_excludes_too_small_and_undeclared() -> None:
    """An undeclared window cannot satisfy a floor, so it is excluded rather than assumed."""
    intent = _intent(min_context_tokens=100_000)

    too_small = evaluate_fit(intent, CapabilityDescriptor(max_context_tokens=8_000))
    assert not too_small.eligible
    assert too_small.primary_exclusion is not None
    assert too_small.primary_exclusion.code is FitCode.CONTEXT_WINDOW_TOO_SMALL

    undeclared = evaluate_fit(intent, EMPTY_CAPABILITIES)
    assert not undeclared.eligible
    assert undeclared.primary_exclusion is not None
    assert undeclared.primary_exclusion.code is FitCode.CONTEXT_WINDOW_UNKNOWN

    assert evaluate_fit(intent, CapabilityDescriptor(max_context_tokens=200_000)).eligible
    # Exactly at the floor is a fit, not a miss.
    assert evaluate_fit(intent, CapabilityDescriptor(max_context_tokens=100_000)).eligible


@pytest.mark.unit
def test_deadline_excludes_only_on_proof_of_overrun() -> None:
    """No latency history must never disqualify -- otherwise nothing new can ever be tried."""
    intent = _intent(deadline_s=10.0)

    assert evaluate_fit(intent, EMPTY_CAPABILITIES, observed_p95_ms=None).eligible
    assert evaluate_fit(intent, EMPTY_CAPABILITIES, observed_p95_ms=9_000.0).eligible

    over = evaluate_fit(intent, EMPTY_CAPABILITIES, observed_p95_ms=11_000.0)
    assert not over.eligible
    assert over.primary_exclusion is not None
    assert over.primary_exclusion.code is FitCode.DEADLINE_EXCEEDED


@pytest.mark.unit
def test_budget_excludes_only_a_known_over_budget_cost() -> None:
    """An unpriced entry may be free, local, or subscription-covered; it is not excluded."""
    intent = _intent(max_cost_usd_per_call=0.01)

    assert evaluate_fit(intent, EMPTY_CAPABILITIES, reference_cost_usd=None).eligible
    assert evaluate_fit(intent, EMPTY_CAPABILITIES, reference_cost_usd=0.001).eligible

    over = evaluate_fit(intent, EMPTY_CAPABILITIES, reference_cost_usd=0.5)
    assert not over.eligible
    assert over.primary_exclusion is not None
    assert over.primary_exclusion.code is FitCode.COST_EXCEEDS_BUDGET


@pytest.mark.unit
def test_all_exclusions_are_reported_not_just_the_first() -> None:
    """The receipt should say everything that was wrong, not stop at the first problem."""
    verdict = evaluate_fit(
        _intent(
            required_features=frozenset({ModelFeature.TOOL_USE}),
            min_context_tokens=100_000,
            deadline_s=1.0,
        ),
        CapabilityDescriptor(features={ModelFeature.TOOL_USE: False}, max_context_tokens=1_000),
        observed_p95_ms=60_000.0,
    )
    assert not verdict.eligible
    assert {f.code for f in verdict.exclusions} == {
        FitCode.CAPABILITY_UNSUPPORTED,
        FitCode.CONTEXT_WINDOW_TOO_SMALL,
        FitCode.DEADLINE_EXCEEDED,
    }
