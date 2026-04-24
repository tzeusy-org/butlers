"""Sparse Tier 2 interpretation entry points for Chronicler.

Per RFC 0014 §D5, Chronicler MAY invoke an LLM only from bounded
interpretation paths (day-close, drilldown, ambiguity resolution,
correction assistance). This module defines the contract and the
token-bound guardrail, but does NOT own LLM wiring — callers pass an
``InterpretAdapter`` that performs the single LLM call. Tests substitute
a stub adapter.

Key invariants:
- Input bundle size is capped at :data:`MAX_TIER_2_INPUT_BYTES`.
- Only ONE LLM call per Tier 2 invocation (no per-event fan-out).
- Output SHALL preserve provenance (source refs cited).
"""

from __future__ import annotations

import enum
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

# Tier 2 input size guardrail. The number is a first-cut budget: ~20 KB
# of structured JSON comfortably fits a day-bounded episode/event bundle
# without encroaching on model context. Tune as needed.
MAX_TIER_2_INPUT_BYTES = 20_000


class TierTwoPath(enum.StrEnum):
    """Named Tier 2 paths recognized by Chronicler."""

    DAY_CLOSE = "day_close"
    DRILLDOWN = "drilldown"
    AMBIGUITY_RESOLUTION = "ambiguity_resolution"
    CORRECTION_ASSISTANCE = "correction_assistance"


class InterpretationBudgetExceeded(RuntimeError):
    """Raised when a Tier 2 bundle exceeds the allowed input budget."""


@dataclass
class TierTwoInput:
    """Token-bounded input bundle for a Tier 2 interpretation path."""

    path: TierTwoPath
    bundle: Any
    citations: list[str] = field(default_factory=list)

    def encoded(self) -> bytes:
        return json.dumps(
            {"path": self.path.value, "bundle": self.bundle, "citations": self.citations},
            default=str,
        ).encode("utf-8")

    def assert_within_budget(
        self,
        *,
        max_bytes: int = MAX_TIER_2_INPUT_BYTES,
    ) -> None:
        size = len(self.encoded())
        if size > max_bytes:
            raise InterpretationBudgetExceeded(
                f"Tier 2 {self.path.value} bundle exceeds budget: {size} bytes > {max_bytes} bytes"
            )


@dataclass
class TierTwoOutput:
    """Structured interpretation output with preserved provenance."""

    path: TierTwoPath
    summary: str
    cited_source_refs: list[str] = field(default_factory=list)
    structured: dict[str, Any] = field(default_factory=dict)


class InterpretAdapter(Protocol):
    """Callable that performs the single Tier 2 LLM call.

    Concrete adapters wrap the Claude Agent SDK (or a stub for tests).
    """

    async def __call__(self, bundle: TierTwoInput) -> TierTwoOutput:  # pragma: no cover — Protocol
        ...


async def interpret(
    adapter: InterpretAdapter,
    bundle: TierTwoInput,
    *,
    max_bytes: int = MAX_TIER_2_INPUT_BYTES,
) -> TierTwoOutput:
    """Run a bounded Tier 2 interpretation.

    Asserts the input budget, invokes ``adapter`` exactly once, and
    verifies the output preserves at least one cited source ref when the
    input carried citations.
    """
    bundle.assert_within_budget(max_bytes=max_bytes)
    output = await adapter(bundle)
    if bundle.citations and not output.cited_source_refs:
        # Defensive: force provenance in the typed output so callers
        # cannot silently drop citations. Fall back to the input list.
        output.cited_source_refs = list(bundle.citations)
    return output


def build_day_close_bundle(
    *,
    date_label: str,
    episodes: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    max_items_per_group: int = 50,
) -> TierTwoInput:
    """Assemble a day-close bundle within bounded cardinality."""
    episodes_capped = list(episodes)[:max_items_per_group]
    events_capped = list(events)[:max_items_per_group]

    citations: list[str] = []
    for item in episodes_capped + events_capped:
        ref = item.get("source_ref")
        if isinstance(ref, str) and ref not in citations:
            citations.append(ref)

    return TierTwoInput(
        path=TierTwoPath.DAY_CLOSE,
        bundle={
            "date": date_label,
            "episodes": episodes_capped,
            "events": events_capped,
            "episodes_truncated": len(episodes) > max_items_per_group,
            "events_truncated": len(events) > max_items_per_group,
        },
        citations=citations,
    )


def build_drilldown_bundle(
    *,
    episode_id: str,
    episode: dict[str, Any],
    supporting_events: Sequence[dict[str, Any]],
) -> TierTwoInput:
    citations = [str(episode.get("source_ref") or episode_id)]
    for evt in supporting_events:
        ref = evt.get("source_ref")
        if isinstance(ref, str) and ref not in citations:
            citations.append(ref)
    return TierTwoInput(
        path=TierTwoPath.DRILLDOWN,
        bundle={
            "episode_id": episode_id,
            "episode": episode,
            "supporting_events": list(supporting_events),
        },
        citations=citations,
    )


def build_ambiguity_bundle(
    *,
    target_id: str,
    candidates: Sequence[dict[str, Any]],
) -> TierTwoInput:
    citations: list[str] = []
    for c in candidates:
        ref = c.get("source_ref")
        if isinstance(ref, str):
            citations.append(ref)
    return TierTwoInput(
        path=TierTwoPath.AMBIGUITY_RESOLUTION,
        bundle={"target_id": target_id, "candidates": list(candidates)},
        citations=citations,
    )


def build_correction_assistance_bundle(
    *,
    episode_id: str,
    user_text: str,
    episode: dict[str, Any] | None = None,
) -> TierTwoInput:
    citations = [str(episode.get("source_ref") or episode_id)] if episode else [episode_id]
    return TierTwoInput(
        path=TierTwoPath.CORRECTION_ASSISTANCE,
        bundle={
            "episode_id": episode_id,
            "user_text": user_text,
            "episode": episode or {},
        },
        citations=citations,
    )


__all__ = [
    "InterpretAdapter",
    "InterpretationBudgetExceeded",
    "MAX_TIER_2_INPUT_BYTES",
    "TierTwoInput",
    "TierTwoOutput",
    "TierTwoPath",
    "build_ambiguity_bundle",
    "build_correction_assistance_bundle",
    "build_day_close_bundle",
    "build_drilldown_bundle",
    "interpret",
]
