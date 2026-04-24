"""Unit tests for Chronicler sparse interpretation guardrails (RFC 0014 §D5).

The projection adapters MUST NOT invoke an LLM. Tier 2 interpretation
paths are bounded by :data:`MAX_TIER_2_INPUT_BYTES`. These tests enforce
both invariants without requiring a database or LLM backend.
"""

from __future__ import annotations

import inspect

import pytest

from butlers.chronicler.adapters import (
    CalendarCompletedAdapter,
    CoreSessionsAdapter,
)
from butlers.chronicler.interpretation import (
    MAX_TIER_2_INPUT_BYTES,
    InterpretationBudgetExceeded,
    TierTwoInput,
    TierTwoOutput,
    TierTwoPath,
    build_correction_assistance_bundle,
    build_day_close_bundle,
    build_drilldown_bundle,
    interpret,
)

# ── No-LLM invariant on projection adapters ────────────────────────────────


_FORBIDDEN_SUBSTRINGS = (
    # Any textual hint that an LLM client is being instantiated from a
    # projection adapter would flag here. This check is deliberately
    # coarse — stronger guardrails come from explicit type-checking and
    # runtime probes.
    "anthropic",
    "ClaudeClient",
    "ClaudeSDKClient",
    "openai",
)


@pytest.mark.parametrize(
    "adapter_cls",
    [CoreSessionsAdapter, CalendarCompletedAdapter],
)
def test_projection_adapter_source_does_not_reference_llm_clients(adapter_cls) -> None:
    src = inspect.getsource(inspect.getmodule(adapter_cls))
    for needle in _FORBIDDEN_SUBSTRINGS:
        assert needle not in src, (
            f"{adapter_cls.__name__} module references forbidden LLM symbol "
            f"{needle!r}; projection adapters MUST NOT invoke an LLM "
            "(RFC 0014 §D5)"
        )


@pytest.mark.parametrize(
    "adapter_cls",
    [CoreSessionsAdapter, CalendarCompletedAdapter],
)
def test_projection_adapter_llm_probe_hook_is_noop(adapter_cls) -> None:
    """The base class's ``_llm_probe`` hook is a no-op; guardrail tests
    may monkeypatch it to raise if called. Concrete adapters MUST NOT
    override it — otherwise the runtime probe becomes unreliable."""
    from butlers.chronicler.adapters.base import ProjectionAdapter

    adapter = adapter_cls(butler_schemas=())
    assert adapter_cls._llm_probe is ProjectionAdapter._llm_probe
    assert adapter._llm_probe() is None


# ── Tier 2 budget guardrail ────────────────────────────────────────────────


def test_tier_two_budget_enforced() -> None:
    huge = {"x": "y" * (MAX_TIER_2_INPUT_BYTES + 100)}
    bundle = TierTwoInput(path=TierTwoPath.DAY_CLOSE, bundle=huge)
    with pytest.raises(InterpretationBudgetExceeded):
        bundle.assert_within_budget()


def test_tier_two_budget_passes_for_small_bundle() -> None:
    bundle = TierTwoInput(
        path=TierTwoPath.DAY_CLOSE,
        bundle={"episodes": [], "events": []},
        citations=["core.sessions:abc"],
    )
    bundle.assert_within_budget()


def test_day_close_bundle_caps_inputs() -> None:
    many_episodes = [{"source_ref": f"core.sessions:ep-{i}", "title": f"e{i}"} for i in range(200)]
    bundle = build_day_close_bundle(
        date_label="2026-04-23",
        episodes=many_episodes,
        events=[],
        max_items_per_group=50,
    )
    assert bundle.bundle["episodes_truncated"] is True
    assert len(bundle.bundle["episodes"]) == 50
    bundle.assert_within_budget()


def test_drilldown_bundle_carries_citations() -> None:
    bundle = build_drilldown_bundle(
        episode_id="ep-1",
        episode={"source_ref": "core.sessions:ep-1", "title": "meeting"},
        supporting_events=[
            {"source_ref": "core.sessions:ev-1", "title": "started"},
            {"source_ref": "core.sessions:ev-2", "title": "completed"},
        ],
    )
    assert "core.sessions:ep-1" in bundle.citations
    assert "core.sessions:ev-1" in bundle.citations


async def test_interpret_enforces_citations_fallback() -> None:
    """If the adapter output has no citations but the input declared
    some, ``interpret`` must fill them back in."""

    async def adapter(bundle: TierTwoInput) -> TierTwoOutput:
        return TierTwoOutput(
            path=bundle.path,
            summary="short summary without citations",
            cited_source_refs=[],
        )

    inp = build_correction_assistance_bundle(
        episode_id="ep-1",
        user_text="fix start to 2:45",
        episode={"source_ref": "core.sessions:ep-1"},
    )
    out = await interpret(adapter, inp)
    assert out.cited_source_refs, "interpret() must restore citations"


async def test_interpret_rejects_oversized_bundle() -> None:
    async def adapter(bundle: TierTwoInput) -> TierTwoOutput:  # pragma: no cover
        return TierTwoOutput(path=bundle.path, summary="")

    inp = TierTwoInput(
        path=TierTwoPath.DAY_CLOSE,
        bundle={"x": "y" * (MAX_TIER_2_INPUT_BYTES + 100)},
    )
    with pytest.raises(InterpretationBudgetExceeded):
        await interpret(adapter, inp)
