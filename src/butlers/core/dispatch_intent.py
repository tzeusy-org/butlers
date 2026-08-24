"""DispatchIntent — what a dispatch actually needs, derived from its trigger.

Model routing has always answered "which enabled, verified, breaker-closed,
quota-ok entry ranks best" without ever asking "which entries can do this job at
all". The two questions have different answers: a ``cheap``-tier ``api`` catalog
row is enabled, verified and cheap, and ``ApiAdapter`` raises ``RuntimeError`` the
moment a butler session hands it the MCP wiring every non-``healing``/``qa``
trigger builds (``spawner._run``). Ranking cannot recover from that -- a
disqualified candidate must be excluded *before* it can win.

A :class:`DispatchIntent` is the requirement side of that question, derived
deterministically from the trigger class so the same trigger always produces the
same requirements. It is deliberately *not* inferred from prompt content: an
intent carries no prompt, no owner data and no provider response, which is what
lets a resolution receipt built from it be persisted and displayed safely.

Grounding for the per-trigger table below (``_TRIGGER_PROFILES``) is code, not
policy invention:

- ``spawner._run`` wires the butler's own MCP server for every trigger source
  except ``healing`` and ``qa``, which it gives ``mcp_servers = {}``. So
  ``tool_use`` is *required* for the rest and simply not required for those two.
- ``session_resume`` is *preferred* (never required) for the conversational
  triggers the spawner tries to resume on -- see ``preferred_features``.
- Consequence follows where the dispatch's own product goes: outward to a
  requester (``route``/``dashboard``/``external``/``trigger``), onto
  butler-owned state (``tick``/``schedule``/``deadline``/``healing``), or into an
  internal artifact nobody is waiting on (``classification``/``qa``).

Everything else -- context floor, deadline, per-call budget -- is left ``None``
unless a caller supplies it, because nothing in the trigger alone determines it
and guessing would be inventing owner policy.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
from collections.abc import Iterable
from typing import Any

from butlers.core.model_capabilities import (
    CapabilityDescriptor,
    ModelFeature,
    Support,
)

logger = logging.getLogger(__name__)

#: Bumped whenever the derivation table or the fit rules below change meaning.
#: Recorded alongside every resolution so a stored receipt can be read against
#: the policy that actually produced it rather than against today's.
DISPATCH_POLICY_VERSION = "1"


class Consequence(enum.StrEnum):
    """Where a dispatch's own product lands, in ascending order of exposure."""

    #: Produces an internal artifact nobody is waiting on (classification, QA).
    OBSERVE = "observe"
    #: Acts on butler-owned state that the butler itself can revise.
    REVERSIBLE = "reversible"
    #: Delivers a reply or effect visible outside the system.
    EXTERNAL = "external"


#: Trigger class used when ``trigger_source`` matches nothing known. Treated as
#: maximally consequential and tool-requiring so an unrecognized caller fails
#: closed rather than silently getting the loosest possible requirements.
UNKNOWN_TRIGGER_CLASS = "unknown"


@dataclasses.dataclass(frozen=True)
class _TriggerProfile:
    required: frozenset[ModelFeature]
    preferred: frozenset[ModelFeature]
    consequence: Consequence


_TOOLS = frozenset({ModelFeature.TOOL_USE})
_NO_FEATURES: frozenset[ModelFeature] = frozenset()
_RESUME = frozenset({ModelFeature.SESSION_RESUME})

# Deterministic trigger-class -> requirements table. Keyed by trigger CLASS, not
# by raw trigger_source, so `schedule:nightly-digest` and `schedule:weekly-review`
# resolve identically (see `trigger_class_of`).
_TRIGGER_PROFILES: dict[str, _TriggerProfile] = {
    "tick": _TriggerProfile(_TOOLS, _NO_FEATURES, Consequence.REVERSIBLE),
    "schedule": _TriggerProfile(_TOOLS, _NO_FEATURES, Consequence.REVERSIBLE),
    "deadline": _TriggerProfile(_TOOLS, _NO_FEATURES, Consequence.REVERSIBLE),
    "classification": _TriggerProfile(_TOOLS, _NO_FEATURES, Consequence.OBSERVE),
    "external": _TriggerProfile(_TOOLS, _NO_FEATURES, Consequence.EXTERNAL),
    "trigger": _TriggerProfile(_TOOLS, _NO_FEATURES, Consequence.EXTERNAL),
    "route": _TriggerProfile(_TOOLS, _RESUME, Consequence.EXTERNAL),
    "dashboard": _TriggerProfile(_TOOLS, _RESUME, Consequence.EXTERNAL),
    # spawner._run gives healing and QA sessions `mcp_servers = {}`, so neither
    # needs tool use; QA additionally produces only an internal artifact.
    "healing": _TriggerProfile(_NO_FEATURES, _NO_FEATURES, Consequence.REVERSIBLE),
    "qa": _TriggerProfile(_NO_FEATURES, _NO_FEATURES, Consequence.OBSERVE),
    UNKNOWN_TRIGGER_CLASS: _TriggerProfile(_TOOLS, _NO_FEATURES, Consequence.EXTERNAL),
}


def trigger_class_of(trigger_source: str | None) -> str:
    """Reduce a ``trigger_source`` to its trigger class.

    ``schedule:<task>`` and ``deadline:<task>`` collapse to ``schedule`` /
    ``deadline`` -- the task name identifies *which* job, never what the job
    needs from a model. Anything unrecognized becomes
    :data:`UNKNOWN_TRIGGER_CLASS`, whose profile is the conservative one.
    """
    if not trigger_source:
        return UNKNOWN_TRIGGER_CLASS
    head = trigger_source.split(":", 1)[0]
    if head in _TRIGGER_PROFILES and head != UNKNOWN_TRIGGER_CLASS:
        return head
    return UNKNOWN_TRIGGER_CLASS


@dataclasses.dataclass(frozen=True)
class DispatchIntent:
    """The deterministic requirement envelope for one dispatch.

    Prompt-free by construction: every field is a trigger-derived or
    caller-supplied scalar, so an intent (and any receipt built from it) can be
    persisted and rendered without redaction.
    """

    trigger_class: str
    #: Canonical complexity tier string. ``Complexity`` is a ``StrEnum``, so its
    #: members are accepted directly; stored as ``str`` to keep this module free
    #: of an import cycle with ``model_routing``.
    complexity_tier: str
    consequence: Consequence
    #: Features a candidate MUST prove. Unproven (UNSUPPORTED, or UNKNOWN above
    #: ``OBSERVE``) disqualifies -- see :func:`evaluate_fit`.
    required_features: frozenset[ModelFeature] = _NO_FEATURES
    #: Features that would be better to have. Recorded on the receipt as unmet;
    #: they never exclude and, as of this policy version, never re-rank.
    preferred_features: frozenset[ModelFeature] = _NO_FEATURES
    #: Context floor in tokens. When set, a candidate with a smaller declared
    #: window is excluded and one with NO declared window is excluded too --
    #: an undeclared envelope is not proof of fit.
    min_context_tokens: int | None = None
    #: Wall-clock budget in seconds. Only a candidate whose recent evidence
    #: *proves* it overruns is excluded; absence of evidence never is.
    deadline_s: float | None = None
    #: Per-call USD cap. Only a candidate with a known reference cost above the
    #: cap is excluded; an unpriced candidate is never excluded on price.
    max_cost_usd_per_call: float | None = None

    def describe(self) -> dict[str, Any]:
        """Return a JSON-safe, prompt-free projection for a resolution receipt."""
        return {
            "trigger_class": self.trigger_class,
            "complexity_tier": self.complexity_tier,
            "consequence": str(self.consequence),
            "required_features": sorted(str(f) for f in self.required_features),
            "preferred_features": sorted(str(f) for f in self.preferred_features),
            "min_context_tokens": self.min_context_tokens,
            "deadline_s": self.deadline_s,
            "max_cost_usd_per_call": self.max_cost_usd_per_call,
        }


def derive_dispatch_intent(
    trigger_source: str | None,
    complexity_tier: str,
    *,
    deadline_s: float | None = None,
    min_context_tokens: int | None = None,
    max_cost_usd_per_call: float | None = None,
    extra_required_features: Iterable[ModelFeature] = (),
) -> DispatchIntent:
    """Derive the intent for a dispatch from its trigger and caller-known bounds.

    Deterministic: the same arguments always produce an equal
    :class:`DispatchIntent`, with no clock, no randomness, and no read of prompt
    content. ``extra_required_features`` lets a specialized call site add a
    requirement the trigger class cannot know (e.g. a lane that parses a
    schema-constrained payload requiring
    :attr:`~butlers.core.model_capabilities.ModelFeature.STRUCTURED_OUTPUT`); it
    can only ever add requirements, never drop the trigger's own.
    """
    trigger_class = trigger_class_of(trigger_source)
    profile = _TRIGGER_PROFILES[trigger_class]
    extra = frozenset(extra_required_features)
    return DispatchIntent(
        trigger_class=trigger_class,
        complexity_tier=str(complexity_tier),
        consequence=profile.consequence,
        required_features=profile.required | extra,
        preferred_features=profile.preferred - (profile.required | extra),
        min_context_tokens=min_context_tokens,
        deadline_s=deadline_s,
        max_cost_usd_per_call=max_cost_usd_per_call,
    )


# ---------------------------------------------------------------------------
# Hard fit
# ---------------------------------------------------------------------------


class FitCode(enum.StrEnum):
    """Stable reason codes for a hard-fit finding.

    Stable because they are written into resolution receipts and read back by
    operators and by later analysis; renaming one silently reinterprets history.
    """

    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    CAPABILITY_UNKNOWN = "capability_unknown"
    CONTEXT_WINDOW_TOO_SMALL = "context_window_too_small"
    CONTEXT_WINDOW_UNKNOWN = "context_window_unknown"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    COST_EXCEEDS_BUDGET = "cost_exceeds_budget"
    CAPABILITY_DESCRIPTOR_INVALID = "capability_descriptor_invalid"


@dataclasses.dataclass(frozen=True)
class FitFinding:
    """One reason a candidate does or might not fit.

    ``detail`` carries only feature names and numbers the resolver itself
    computed -- never a stored string, a prompt, or a provider response.
    """

    code: FitCode
    detail: str | None = None

    def describe(self) -> dict[str, Any]:
        return {"code": str(self.code), "detail": self.detail}


@dataclasses.dataclass(frozen=True)
class FitVerdict:
    """Whether a candidate survives hard fit, and everything noted on the way.

    ``advisories`` are findings that did NOT exclude: an unknown required
    capability tolerated at :attr:`Consequence.OBSERVE`, and unmet preferred
    features. They exist so a receipt can show that a candidate won *despite* a
    gap, rather than presenting a clean win it did not have.
    """

    eligible: bool
    exclusions: tuple[FitFinding, ...] = ()
    advisories: tuple[FitFinding, ...] = ()

    @property
    def primary_exclusion(self) -> FitFinding | None:
        return self.exclusions[0] if self.exclusions else None


def evaluate_fit(
    intent: DispatchIntent,
    capabilities: CapabilityDescriptor,
    *,
    observed_p95_ms: float | None = None,
    reference_cost_usd: float | None = None,
) -> FitVerdict:
    """Decide whether one candidate can do the job *intent* describes.

    Hard fit, evaluated before any ranking. The rules, and why each stops where
    it does:

    - **Required capability.** ``UNSUPPORTED`` always excludes -- that is proof of
      misfit. ``UNKNOWN`` excludes too (fail closed) at
      :attr:`Consequence.REVERSIBLE` and :attr:`Consequence.EXTERNAL`; at
      :attr:`Consequence.OBSERVE` it is tolerated and recorded as an advisory,
      because an internal artifact nobody is waiting on is the one place where
      "probably fine" costs nothing outside the system. Note the asymmetry is
      only ever about *absence of proof*: an explicit ``false`` excludes at every
      consequence level.
    - **Context floor.** A declared window below the floor excludes; an
      *undeclared* window also excludes, because ``min_context_tokens`` is a
      requirement and an undeclared envelope cannot satisfy it. Callers that do
      not know their context need leave the floor ``None``, which is the default
      and excludes nobody.
    - **Deadline.** Excludes only on evidence that the candidate overruns
      (``observed_p95_ms`` > the deadline). Missing evidence never excludes: a
      brand-new entry has no latency history, and disqualifying it for that would
      make the fleet permanently unable to try anything new -- the same doctrine
      ``compute_routing_score`` already follows for its evidence gate.
    - **Budget.** Excludes only a candidate whose reference cost is *known* to
      exceed the cap. An unpriced entry is not excluded, matching
      ``_reference_cost_usd``'s existing treatment of unpriced models as
      cost-neutral rather than disqualified (an operator may genuinely be running
      a free, local, or subscription-covered model).

    ``observed_p95_ms`` and ``reference_cost_usd`` are supplied by the resolver
    from data it already fetched; passing ``None`` for either simply skips that
    rule.
    """
    exclusions: list[FitFinding] = []
    advisories: list[FitFinding] = []

    tolerate_unknown = intent.consequence is Consequence.OBSERVE
    for feature in sorted(intent.required_features):
        support = capabilities.support(feature)
        if support is Support.SUPPORTED:
            continue
        if support is Support.UNSUPPORTED:
            exclusions.append(FitFinding(FitCode.CAPABILITY_UNSUPPORTED, str(feature)))
            continue
        finding = FitFinding(FitCode.CAPABILITY_UNKNOWN, str(feature))
        if tolerate_unknown:
            advisories.append(finding)
        else:
            exclusions.append(finding)

    for feature in sorted(intent.preferred_features):
        if capabilities.support(feature) is not Support.SUPPORTED:
            advisories.append(FitFinding(FitCode.CAPABILITY_UNKNOWN, str(feature)))

    if intent.min_context_tokens is not None:
        declared = capabilities.max_context_tokens
        if declared is None:
            exclusions.append(
                FitFinding(
                    FitCode.CONTEXT_WINDOW_UNKNOWN,
                    f"required >= {intent.min_context_tokens} tokens",
                )
            )
        elif declared < intent.min_context_tokens:
            exclusions.append(
                FitFinding(
                    FitCode.CONTEXT_WINDOW_TOO_SMALL,
                    f"{declared} < {intent.min_context_tokens} tokens",
                )
            )

    if intent.deadline_s is not None and observed_p95_ms is not None:
        deadline_ms = intent.deadline_s * 1000.0
        if observed_p95_ms > deadline_ms:
            exclusions.append(
                FitFinding(
                    FitCode.DEADLINE_EXCEEDED,
                    f"p95 {observed_p95_ms:.0f}ms > {deadline_ms:.0f}ms",
                )
            )

    if intent.max_cost_usd_per_call is not None and reference_cost_usd is not None:
        if reference_cost_usd > intent.max_cost_usd_per_call:
            exclusions.append(
                FitFinding(
                    FitCode.COST_EXCEEDS_BUDGET,
                    f"{reference_cost_usd:.6f} > {intent.max_cost_usd_per_call:.6f} USD/call",
                )
            )

    return FitVerdict(
        eligible=not exclusions,
        exclusions=tuple(exclusions),
        advisories=tuple(advisories),
    )
