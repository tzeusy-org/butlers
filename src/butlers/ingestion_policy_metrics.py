"""Unified ingestion policy telemetry metrics (OTel).

Implements D11 from the unified-ingestion-policy design:

  butlers.ingestion.rule_matched       Counter
      Labels: scope_type, rule_type, action, source_channel
      Replaces: triage.rule_matched + connector_source_filter_total{action=blocked}

  butlers.ingestion.rule_pass_through  Counter
      Labels: scope_type, source_channel, reason
      Replaces: triage.pass_through + connector_source_filter_total{action=allowed}

  butlers.ingestion.evaluation_latency_ms  Histogram
      Labels: scope_type, result
      New: covers both global and connector scopes.

Cardinality controls (D11):
  - ``scope_type`` is bounded to ``global`` or ``connector:<type>`` (endpoint
    identity stripped from the full scope string).
  - ``action`` strips the target butler name: ``route_to:finance`` -> ``route_to``.

Issue: bu-r55.4
"""

from __future__ import annotations

import logging

from opentelemetry import metrics

logger = logging.getLogger(__name__)

_METER_NAME = "butlers"


# ---------------------------------------------------------------------------
# Lazy instrument accessors
# ---------------------------------------------------------------------------


def _get_meter() -> metrics.Meter:
    """Return the shared butlers meter (no-op if provider not initialized)."""
    return metrics.get_meter(_METER_NAME)


def _rule_matched_counter() -> metrics.Counter:
    """Counter: ingestion rule matched."""
    return _get_meter().create_counter(
        name="butlers.ingestion.rule_matched",
        description="Ingestion policy rule matched (first-match-wins)",
        unit="matches",
    )


def _rule_pass_through_counter() -> metrics.Counter:
    """Counter: no rule matched, pass-through."""
    return _get_meter().create_counter(
        name="butlers.ingestion.rule_pass_through",
        description="Ingestion policy evaluation with no rule match (pass-through)",
        unit="evaluations",
    )


def _evaluation_latency_histogram() -> metrics.Histogram:
    """Histogram: evaluation latency in milliseconds."""
    return _get_meter().create_histogram(
        name="butlers.ingestion.evaluation_latency_ms",
        description="Ingestion policy evaluation latency in milliseconds",
        unit="ms",
    )


# ---------------------------------------------------------------------------
# Cardinality-safe label helpers
# ---------------------------------------------------------------------------


def _scope_type(scope: str) -> str:
    """Extract cardinality-safe scope_type label from a full scope string.

    ``'global'``                                   -> ``'global'``
    ``'connector:gmail:gmail:user:dev'``            -> ``'connector:gmail'``
    ``'connector:telegram-bot:telegram-bot:my-bot'``-> ``'connector:telegram-bot'``
    """
    if scope == "global":
        return "global"
    # scope format: connector:<type>:<identity>...
    parts = scope.split(":", 2)
    if len(parts) >= 2:
        return f"connector:{parts[1]}"
    return scope  # fallback: return as-is


def _safe_action(action: str) -> str:
    """Strip butler name from route_to actions for cardinality safety.

    ``'route_to:finance'`` -> ``'route_to'``
    ``'skip'``             -> ``'skip'``
    """
    if action.startswith("route_to"):
        return "route_to"
    return action


# ---------------------------------------------------------------------------
# IngestionPolicyMetrics — per-evaluator metrics recorder
# ---------------------------------------------------------------------------


class IngestionPolicyMetrics:
    """Records OTel metrics for ingestion policy evaluation.

    Create one instance per ``IngestionPolicyEvaluator`` (one per scope).
    Instruments are lazily created from the global MeterProvider on first use,
    so it is safe to construct this object before ``init_metrics`` is called.

    Usage::

        _metrics = IngestionPolicyMetrics(scope="global")

        # After evaluation:
        _metrics.record_match(
            rule_type="sender_domain",
            action="skip",
            source_channel="email",
            latency_ms=0.42,
        )
        # Or for pass-through:
        _metrics.record_pass_through(
            source_channel="email",
            reason="no rule matched",
            latency_ms=0.15,
        )
    """

    def __init__(self, scope: str) -> None:
        self._scope_type = _scope_type(scope)

        # Lazy instrument cache
        self.__rule_matched: metrics.Counter | None = None
        self.__rule_pass_through: metrics.Counter | None = None
        self.__evaluation_latency: metrics.Histogram | None = None

    # -- lazy instrument accessors ------------------------------------------

    @property
    def _rule_matched(self) -> metrics.Counter:
        if self.__rule_matched is None:
            self.__rule_matched = _rule_matched_counter()
        return self.__rule_matched

    @property
    def _rule_pass_through(self) -> metrics.Counter:
        if self.__rule_pass_through is None:
            self.__rule_pass_through = _rule_pass_through_counter()
        return self.__rule_pass_through

    @property
    def _evaluation_latency(self) -> metrics.Histogram:
        if self.__evaluation_latency is None:
            self.__evaluation_latency = _evaluation_latency_histogram()
        return self.__evaluation_latency

    # -- recording helpers --------------------------------------------------

    def record_match(
        self,
        *,
        rule_type: str,
        action: str,
        source_channel: str,
        latency_ms: float,
    ) -> None:
        """Record a rule match event.

        Args:
            rule_type: The matched rule's type (e.g. sender_domain).
            action: The raw action string (route_to:finance is sanitized).
            source_channel: Envelope source channel (email, telegram, etc.).
            latency_ms: Evaluation duration in milliseconds.
        """
        self._rule_matched.add(
            1,
            {
                "scope_type": self._scope_type,
                "rule_type": rule_type,
                "action": _safe_action(action),
                "source_channel": source_channel,
            },
        )
        self._evaluation_latency.record(
            latency_ms,
            {
                "scope_type": self._scope_type,
                "result": "matched",
            },
        )

    def record_pass_through(
        self,
        *,
        source_channel: str,
        reason: str,
        latency_ms: float,
    ) -> None:
        """Record a pass-through (no rule matched) event.

        Args:
            source_channel: Envelope source channel.
            reason: Human-readable reason (e.g. "no rule matched").
            latency_ms: Evaluation duration in milliseconds.
        """
        self._rule_pass_through.add(
            1,
            {
                "scope_type": self._scope_type,
                "source_channel": source_channel,
                "reason": reason,
            },
        )
        self._evaluation_latency.record(
            latency_ms,
            {
                "scope_type": self._scope_type,
                "result": "pass_through",
            },
        )


__all__ = [
    "IngestionPolicyMetrics",
]
