"""Home Assistant connector — three-layer filtering pipeline.

Implements tasks 5.1–5.5 from openspec/changes/connector-home-assistant/tasks.md:

5.1 Layer 1 — Domain allowlist filter (configurable domain list)
5.2 Layer 2 — Significance filter with per-device-class thresholds for numeric sensors
5.3 Significance filter bypass for binary entities and unavailable/unknown transitions
5.4 Layer 3 — DiscretionEvaluator/DiscretionDispatcher integration
5.5 Filter pipeline metrics

Pipeline execution order per design decision D2:

    Layer 1: Domain allowlist  (zero-cost string comparison)
        ↓ pass
    Layer 2: Significance filter  (cheap numeric comparison against previous state)
        ↓ pass
    Layer 3: Discretion evaluator  (LLM-based semantic filter)
        ↓ pass
    Submit to Switchboard

Each rejected event is tagged with a filter reason and recorded by the
:class:`~butlers.connectors.home_assistant_filter.HAFilterPersistence` helper.

Binary entities (on/off, open/closed, locked/unlocked) always pass Layer 2.
Transitions to/from ``unavailable`` or ``unknown`` always pass Layer 2.

Usage::

    pipeline = HAFilterPipeline(
        domain_allowlist=frozenset({"light", "sensor", "lock"}),
        discretion_evaluator=DiscretionEvaluator(
            source_name="home_assistant:ha.local:8123",
            dispatcher=DiscretionDispatcher(pool=db_pool),
        ),
        metrics=HAConnectorMetrics("home_assistant:ha.local:8123"),
    )

    result = await pipeline.run(
        entity_id="sensor.living_room_temperature",
        domain="sensor",
        device_class="temperature",
        old_state_str="21.9",
        new_state_str="22.0",
        ha_event=raw_ha_event,
        time_fired="2026-03-26T12:00:00+00:00",
    )

    if result.verdict == "pass":
        # Submit to Switchboard
        ...
    elif result.verdict == "filtered":
        # Record filtered event
        ...
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from butlers.connectors.discretion import DiscretionEvaluator
    from butlers.connectors.home_assistant import HAConnectorMetrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default significance thresholds (task 5.2)
# ---------------------------------------------------------------------------

#: Default per-device-class numeric significance thresholds.
#: Only ``state_changed`` events whose absolute value delta *exceeds* the
#: threshold for their device class are considered significant.
#: A delta equal to the threshold is NOT significant (strict greater-than).
DEFAULT_SIGNIFICANCE_THRESHOLDS: dict[str, float] = {
    "temperature": 0.5,
    "humidity": 2.0,
    "energy": 0.1,
    "illuminance": 50.0,
    "power": 10.0,
    "voltage": 1.0,
    "current": 0.1,
    "pressure": 1.0,
    "carbon_dioxide": 25.0,
    "carbon_monoxide": 5.0,
    "pm25": 5.0,
    "pm10": 5.0,
    "volatile_organic_compounds": 10.0,
    "battery": 5.0,
}

#: State values that always bypass significance filtering (task 5.3).
#: Any transition to or from these values should always pass Layer 2.
_BYPASS_SIGNIFICANCE_STATES: frozenset[str] = frozenset({"unavailable", "unknown"})

#: Binary state value sets — entities whose state is always one of these
#: string pairs bypass significance filtering (task 5.3).
_BINARY_STATE_VALUES: frozenset[str] = frozenset(
    {
        "on",
        "off",
        "open",
        "closed",
        "locked",
        "unlocked",
        "home",
        "away",
        "true",
        "false",
        "playing",
        "paused",
        "idle",
    }
)


# ---------------------------------------------------------------------------
# Filter result types
# ---------------------------------------------------------------------------

FilterVerdict = Literal["pass", "filtered"]

#: Which pipeline layer produced the filter verdict.
FilterStage = Literal["domain_filter", "significance_filter", "discretion", "passed"]


@dataclass(frozen=True)
class PipelineResult:
    """Result of running the three-layer filter pipeline for a single event.

    Attributes:
        verdict: ``"pass"`` → event should be submitted to the Switchboard;
            ``"filtered"`` → event should be recorded as filtered.
        stage: The pipeline layer that determined the verdict.
            ``"passed"`` for events that cleared all three layers.
        filter_reason: HA filter-reason string for persistence (e.g.
            ``"domain_excluded:media_player"``).  Empty string when verdict
            is ``"pass"``.
        discretion_reason: One-line rationale from the discretion evaluator
            (only populated when stage is ``"discretion"`` or ``"passed"``).
    """

    verdict: FilterVerdict
    stage: FilterStage
    filter_reason: str = ""
    discretion_reason: str = ""


# ---------------------------------------------------------------------------
# Per-entity significance state cache (task 5.2)
# ---------------------------------------------------------------------------


class SignificanceStateCache:
    """In-memory cache of last known numeric state values per entity.

    Used by Layer 2 to compute the absolute delta between the previous and
    current state values.  Cache entries never expire — the cache grows
    proportionally to the number of distinct entities seen, which is bounded
    by the HA installation size.

    Thread safety: not required — the HA connector processes events
    sequentially in the asyncio event loop.
    """

    def __init__(self) -> None:
        self._cache: dict[str, float] = {}

    def get(self, entity_id: str) -> float | None:
        """Return the last known numeric state value, or ``None`` if unseen."""
        return self._cache.get(entity_id)

    def set(self, entity_id: str, value: float) -> None:
        """Store the current numeric state value for *entity_id*."""
        self._cache[entity_id] = value

    def __len__(self) -> int:
        return len(self._cache)


# ---------------------------------------------------------------------------
# Layer 1 — Domain allowlist filter (task 5.1)
# ---------------------------------------------------------------------------


def filter_layer1_domain(
    entity_id: str,
    domain: str,
    allowlist: frozenset[str],
) -> PipelineResult | None:
    """Apply the domain allowlist filter (Layer 1).

    Returns ``None`` if the event passes (domain is in the allowlist),
    or a :class:`PipelineResult` with ``verdict="filtered"`` if the domain
    is excluded.

    Args:
        entity_id: HA entity ID (used only for logging).
        domain: Entity domain extracted from the entity ID
            (e.g. ``"sensor"``, ``"light"``).
        allowlist: Set of allowed domain strings.

    Returns:
        ``None`` on pass; :class:`PipelineResult` on filter.
    """
    if domain in allowlist:
        return None

    logger.debug(
        "HA filter Layer 1: excluded domain=%s entity_id=%s",
        domain,
        entity_id,
    )
    return PipelineResult(
        verdict="filtered",
        stage="domain_filter",
        filter_reason=f"domain_excluded:{domain}",
    )


# ---------------------------------------------------------------------------
# Layer 2 — Significance filter (tasks 5.2–5.3)
# ---------------------------------------------------------------------------


def _is_binary_state(state_str: str) -> bool:
    """Return True if *state_str* is a recognised binary entity value."""
    return state_str.lower() in _BINARY_STATE_VALUES


def _should_bypass_significance(old_state_str: str | None, new_state_str: str | None) -> bool:
    """Return True if significance filtering should be bypassed.

    Bypass conditions (task 5.3):
    1. Either state is ``None`` (unavailable data) — pass to preserve safety.
    2. Either state is in ``_BYPASS_SIGNIFICANCE_STATES`` (``"unavailable"`` or
       ``"unknown"``).
    3. Either state is a binary entity value (on/off, open/closed, etc.).
    """
    # Null states: pass
    if old_state_str is None or new_state_str is None:
        return True

    # Unavailable/unknown transitions: always pass
    if (
        old_state_str.lower() in _BYPASS_SIGNIFICANCE_STATES
        or new_state_str.lower() in _BYPASS_SIGNIFICANCE_STATES
    ):
        return True

    # Binary states: always pass
    if _is_binary_state(old_state_str) or _is_binary_state(new_state_str):
        return True

    return False


def filter_layer2_significance(
    entity_id: str,
    device_class: str | None,
    old_state_str: str | None,
    new_state_str: str | None,
    state_cache: SignificanceStateCache,
    thresholds: dict[str, float] | None = None,
) -> PipelineResult | None:
    """Apply the significance filter (Layer 2).

    Returns ``None`` if the event passes, or a :class:`PipelineResult` with
    ``verdict="filtered"`` if the state change is below the significance
    threshold.

    Bypass rules (task 5.3):
    - Binary entities (on/off, open/closed, locked/unlocked) always pass.
    - Transitions to/from ``"unavailable"`` or ``"unknown"`` always pass.
    - Entities with no device class always pass (no threshold to apply).
    - Entities whose new state cannot be parsed as a float always pass
      (treat non-numeric as significant).

    Args:
        entity_id: HA entity ID.
        device_class: Entity device class from HA attributes (e.g.
            ``"temperature"``).  If ``None``, the event always passes.
        old_state_str: Previous entity state string (may be ``None``).
        new_state_str: Current entity state string (may be ``None``).
        state_cache: Per-entity numeric state value cache.  Updated with the
            new value when the event passes (or when the delta is computed).
        thresholds: Per-device-class significance thresholds.  Defaults to
            :data:`DEFAULT_SIGNIFICANCE_THRESHOLDS`.

    Returns:
        ``None`` on pass; :class:`PipelineResult` on filter.
    """
    effective_thresholds = thresholds if thresholds is not None else DEFAULT_SIGNIFICANCE_THRESHOLDS

    # Resolve the threshold for this device class (None when no class or unknown class).
    threshold = effective_thresholds.get(device_class) if device_class else None

    # Bypass if: binary/unavailable/unknown transition, no device class, or no known threshold.
    # In all these cases update the cache with the new numeric value if parseable and pass.
    if _should_bypass_significance(old_state_str, new_state_str) or threshold is None:
        if new_state_str is not None:
            try:
                new_val = float(new_state_str)
                state_cache.set(entity_id, new_val)
            except ValueError:
                pass
        return None

    try:
        new_val = float(new_state_str)
    except ValueError:
        # Non-numeric state — treat as significant, pass
        return None

    # Compute delta from cache (fall back to old_state_str if no cached value)
    cached_val = state_cache.get(entity_id)
    if cached_val is None and old_state_str is not None:
        try:
            cached_val = float(old_state_str)
        except ValueError:
            cached_val = None

    if cached_val is None:
        # No previous value → can't compute delta → pass
        state_cache.set(entity_id, new_val)
        return None

    delta = abs(new_val - cached_val)

    # Update cache regardless of whether the event passes or is filtered
    state_cache.set(entity_id, new_val)

    if delta <= threshold:
        logger.debug(
            "HA filter Layer 2: insignificant delta=%s (threshold=%s) device_class=%s entity_id=%s",
            delta,
            threshold,
            device_class,
            entity_id,
        )
        # Format delta for filter reason
        delta_str = f"{delta:.6f}".rstrip("0").rstrip(".")
        return PipelineResult(
            verdict="filtered",
            stage="significance_filter",
            filter_reason=f"insignificant_delta:{device_class}:{delta_str}",
        )

    return None


# ---------------------------------------------------------------------------
# Layer 3 — Discretion filter (task 5.4)
# ---------------------------------------------------------------------------


async def filter_layer3_discretion(
    entity_id: str,
    normalized_text: str,
    evaluator: DiscretionEvaluator,
    time_fired_ts: float | None = None,
) -> PipelineResult | None:
    """Apply the discretion evaluator (Layer 3).

    Returns ``None`` if the event passes discretion (FORWARD verdict), or a
    :class:`PipelineResult` with ``verdict="filtered"`` if the evaluator
    returns IGNORE.

    Per design decision D7: all events that pass Layers 1 and 2 go through
    discretion evaluation.  There is no bypass for specific event types.
    HA events use ``weight=1.0`` (owner-equivalent) per design decision D4,
    which means the weight-bypass path is taken and the LLM is NOT called —
    the event is always forwarded.  This ensures HA events never hit the LLM
    discretion cost.

    Args:
        entity_id: HA entity ID (used as source label in discretion context).
        normalized_text: Human-readable event summary for the discretion LLM.
        evaluator: Per-domain :class:`~butlers.connectors.discretion.DiscretionEvaluator`
            instance.
        time_fired_ts: Unix timestamp for context window ordering.

    Returns:
        ``None`` on FORWARD; :class:`PipelineResult` on IGNORE.
    """
    result = await evaluator.evaluate(
        text=normalized_text,
        timestamp=time_fired_ts,
        weight=1.0,  # HA events are owner-equivalent (design decision D4)
    )

    if result.verdict == "FORWARD":
        return None

    logger.debug(
        "HA filter Layer 3: discretion IGNORE entity_id=%s reason=%s",
        entity_id,
        result.reason,
    )
    return PipelineResult(
        verdict="filtered",
        stage="discretion",
        filter_reason="discretion_ignore",
        discretion_reason=result.reason,
    )


# ---------------------------------------------------------------------------
# Filter pipeline metrics helper (task 5.5)
# ---------------------------------------------------------------------------


def _record_pipeline_metrics(
    stage: FilterStage,
    verdict: FilterVerdict,
    metrics: HAConnectorMetrics | None,
) -> None:
    """Increment the appropriate Prometheus counter for a pipeline outcome.

    Args:
        stage: The pipeline layer that produced the verdict.
        verdict: ``"pass"`` or ``"filtered"``.
        metrics: :class:`~butlers.connectors.home_assistant.HAConnectorMetrics`
            instance, or ``None`` (no-op).
    """
    if metrics is None:
        return

    outcome = "passed" if verdict == "pass" else "filtered"

    if stage == "domain_filter":
        metrics.inc_events("domain_filter", outcome)
    elif stage == "significance_filter":
        # Domain filter passed, significance filter determined outcome
        metrics.inc_events("domain_filter", "passed")
        metrics.inc_events("significance_filter", outcome)
    elif stage == "discretion":
        # Layers 1+2 passed, discretion determined outcome
        metrics.inc_events("domain_filter", "passed")
        metrics.inc_events("significance_filter", "passed")
        metrics.inc_events("discretion", outcome)
    elif stage == "passed":
        # All three layers passed
        metrics.inc_events("domain_filter", "passed")
        metrics.inc_events("significance_filter", "passed")
        metrics.inc_events("discretion", "passed")


# ---------------------------------------------------------------------------
# HAFilterPipeline — composable three-layer pipeline (tasks 5.1–5.5)
# ---------------------------------------------------------------------------


@dataclass
class HAFilterPipelineConfig:
    """Configuration for the three-layer HA filter pipeline.

    Attributes:
        domain_allowlist: Set of HA entity domains that are allowed through
            Layer 1.  Events from any other domain are dropped immediately.
        significance_thresholds: Per-device-class numeric significance
            thresholds for Layer 2.  Defaults to
            :data:`DEFAULT_SIGNIFICANCE_THRESHOLDS`.
    """

    domain_allowlist: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "light",
                "switch",
                "sensor",
                "climate",
                "lock",
                "cover",
                "binary_sensor",
                "automation",
                "script",
            }
        )
    )
    significance_thresholds: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SIGNIFICANCE_THRESHOLDS)
    )


class HAFilterPipeline:
    """Three-layer event filter pipeline for the Home Assistant connector.

    Applies the sequential filter stages in order:

    1. **Domain allowlist** — drops events from non-allowed entity domains.
    2. **Significance filter** — drops numeric sensor updates below per-class
       thresholds; always passes binary entities and unavailable/unknown
       transitions.
    3. **Discretion evaluator** — semantic LLM-based filter; with HA's
       owner-equivalent weight (1.0), always passes via the weight-bypass path.

    Filter pipeline timing is measured and reported to
    :class:`~butlers.connectors.home_assistant.HAConnectorMetrics` when
    provided.

    Args:
        config: Pipeline configuration (allowlist, thresholds).
        evaluator: Shared :class:`~butlers.connectors.discretion.DiscretionEvaluator`
            instance for Layer 3.  May be ``None`` — if so, Layer 3 is skipped
            and all events that pass Layers 1 and 2 are forwarded.
        metrics: Per-endpoint Prometheus metrics helper.  May be ``None``
            (no metrics recorded).
    """

    def __init__(
        self,
        config: HAFilterPipelineConfig | None = None,
        evaluator: DiscretionEvaluator | None = None,
        metrics: HAConnectorMetrics | None = None,
    ) -> None:
        self._config = config or HAFilterPipelineConfig()
        self._evaluator = evaluator
        self._metrics = metrics
        self._state_cache = SignificanceStateCache()

    @property
    def state_cache(self) -> SignificanceStateCache:
        """Access to the per-entity significance state cache (for testing)."""
        return self._state_cache

    async def run(
        self,
        *,
        entity_id: str,
        domain: str | None = None,
        device_class: str | None = None,
        old_state_str: str | None = None,
        new_state_str: str | None = None,
        normalized_text: str = "",
        ha_event: dict[str, Any] | None = None,
        time_fired_ts: float | None = None,
    ) -> PipelineResult:
        """Run the three-layer filter pipeline for a single HA event.

        Args:
            entity_id: HA entity ID (e.g. ``"sensor.living_room_temperature"``).
            domain: Entity domain.  Derived from ``entity_id`` if not provided.
            device_class: Entity device class from HA attributes (optional).
            old_state_str: Previous entity state string (optional).
            new_state_str: Current entity state string (optional).
            normalized_text: Human-readable event summary for discretion
                (e.g. ``"Living Room Temperature: 21.9 -> 22.0 °C"``).
            ha_event: Raw HA event dict (unused in filter; for context only).
            time_fired_ts: Unix timestamp of the HA event (for discretion
                context window ordering).

        Returns:
            :class:`PipelineResult` with ``verdict="pass"`` if the event
            should be submitted to the Switchboard, or ``verdict="filtered"``
            if it was dropped at one of the three layers.
        """
        pipeline_start = time.monotonic()

        # Derive domain if not provided
        if domain is None and "." in entity_id:
            domain = entity_id.split(".")[0]

        effective_domain = domain or ""

        # ----------------------------------------------------------------
        # Layer 1: Domain allowlist
        # ----------------------------------------------------------------
        layer1_result = filter_layer1_domain(
            entity_id=entity_id,
            domain=effective_domain,
            allowlist=self._config.domain_allowlist,
        )
        if layer1_result is not None:
            _record_pipeline_metrics("domain_filter", "filtered", self._metrics)
            self._observe_pipeline_timing(pipeline_start)
            return layer1_result

        # ----------------------------------------------------------------
        # Layer 2: Significance filter
        # ----------------------------------------------------------------
        layer2_result = filter_layer2_significance(
            entity_id=entity_id,
            device_class=device_class,
            old_state_str=old_state_str,
            new_state_str=new_state_str,
            state_cache=self._state_cache,
            thresholds=self._config.significance_thresholds,
        )
        if layer2_result is not None:
            _record_pipeline_metrics("significance_filter", "filtered", self._metrics)
            self._observe_pipeline_timing(pipeline_start)
            return layer2_result

        # ----------------------------------------------------------------
        # Layer 3: Discretion evaluator
        # ----------------------------------------------------------------
        if self._evaluator is not None:
            layer3_result = await filter_layer3_discretion(
                entity_id=entity_id,
                normalized_text=normalized_text or entity_id,
                evaluator=self._evaluator,
                time_fired_ts=time_fired_ts,
            )
            if layer3_result is not None:
                # Record discretion verdict metric
                if self._metrics is not None:
                    self._metrics.inc_discretion(verdict="ignore")
                _record_pipeline_metrics("discretion", "filtered", self._metrics)
                self._observe_pipeline_timing(pipeline_start)
                return layer3_result

            # Discretion forwarded
            if self._metrics is not None:
                self._metrics.inc_discretion(verdict="forward")

        # All layers passed
        _record_pipeline_metrics("passed", "pass", self._metrics)
        self._observe_pipeline_timing(pipeline_start)

        return PipelineResult(
            verdict="pass",
            stage="passed",
            filter_reason="",
            discretion_reason="",
        )

    def _observe_pipeline_timing(self, start: float) -> None:
        """Record filter pipeline timing to Prometheus histogram.

        Args:
            start: Monotonic start time from ``time.monotonic()``.
        """
        if self._metrics is not None:
            elapsed = time.monotonic() - start
            self._metrics.observe_filter_pipeline(elapsed)
