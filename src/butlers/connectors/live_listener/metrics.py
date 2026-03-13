"""Voice-specific Prometheus metrics for the live-listener connector.

Exports:
- connector_live_listener_segments_total{mic, outcome}
- connector_live_listener_discretion_total{mic, verdict}
- connector_live_listener_transcription_failures_total{mic, error_type}
- connector_live_listener_discretion_failures_total{mic, error_type}
- connector_live_listener_transcription_discarded_total{mic, reason}
- connector_live_listener_stage_latency_seconds{mic, stage}
- connector_live_listener_e2e_latency_seconds{mic}
- connector_live_listener_segment_duration_seconds{mic}
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

segments_total = Counter(
    "connector_live_listener_segments_total",
    "Total speech segments processed, by outcome",
    labelnames=["mic", "outcome"],
)
"""outcome: transcribed | discarded_noise | discarded_silence | transcription_failed"""

discretion_total = Counter(
    "connector_live_listener_discretion_total",
    "Total discretion evaluations, by verdict",
    labelnames=["mic", "verdict"],
)
"""verdict: forward | ignore | error_forward"""

transcription_failures_total = Counter(
    "connector_live_listener_transcription_failures_total",
    "Total transcription service failures",
    labelnames=["mic", "error_type"],
)

discretion_failures_total = Counter(
    "connector_live_listener_discretion_failures_total",
    "Total discretion LLM call failures",
    labelnames=["mic", "error_type"],
)

transcription_discarded_total = Counter(
    "connector_live_listener_transcription_discarded_total",
    "Total transcriptions discarded after receipt (empty or low confidence)",
    labelnames=["mic", "reason"],
)
"""reason: empty | low_confidence"""

# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------

_LATENCY_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0)

stage_latency_seconds = Histogram(
    "connector_live_listener_stage_latency_seconds",
    "Per-stage pipeline latency in seconds",
    labelnames=["mic", "stage"],
    buckets=_LATENCY_BUCKETS,
)
"""stage: vad | transcription | discretion | submission"""

e2e_latency_seconds = Histogram(
    "connector_live_listener_e2e_latency_seconds",
    "End-to-end latency from speech offset to Switchboard submission",
    labelnames=["mic"],
    buckets=_LATENCY_BUCKETS,
)

segment_duration_seconds = Histogram(
    "connector_live_listener_segment_duration_seconds",
    "Duration of captured speech segments in seconds",
    labelnames=["mic"],
    buckets=(0.1, 0.3, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0),
)


class LiveListenerMetrics:
    """Per-mic metrics helper for the live-listener connector.

    Wraps module-level Prometheus objects with a fixed ``mic`` label so call
    sites don't have to repeat it.
    """

    def __init__(self, mic: str) -> None:
        self._mic = mic

    # --- Counters ---

    def inc_segments(self, outcome: str) -> None:
        """Increment segment counter.

        Args:
            outcome: One of ``transcribed``, ``discarded_noise``,
                     ``discarded_silence``, ``transcription_failed``.
        """
        segments_total.labels(mic=self._mic, outcome=outcome).inc()

    def inc_discretion(self, verdict: str) -> None:
        """Increment discretion verdict counter.

        Args:
            verdict: One of ``forward``, ``ignore``, ``error_forward``.
        """
        discretion_total.labels(mic=self._mic, verdict=verdict).inc()

    def inc_transcription_failure(self, error_type: str) -> None:
        """Increment transcription failure counter."""
        transcription_failures_total.labels(mic=self._mic, error_type=error_type).inc()

    def inc_discretion_failure(self, error_type: str) -> None:
        """Increment discretion failure counter."""
        discretion_failures_total.labels(mic=self._mic, error_type=error_type).inc()

    def inc_transcription_discarded(self, reason: str) -> None:
        """Increment transcription-discarded counter.

        Args:
            reason: One of ``empty``, ``low_confidence``.
        """
        transcription_discarded_total.labels(mic=self._mic, reason=reason).inc()

    # --- Histograms ---

    def observe_stage_latency(self, stage: str, latency_s: float) -> None:
        """Record per-stage latency.

        Args:
            stage: One of ``vad``, ``transcription``, ``discretion``, ``submission``.
            latency_s: Elapsed time in seconds.
        """
        stage_latency_seconds.labels(mic=self._mic, stage=stage).observe(latency_s)

    def observe_e2e_latency(self, latency_s: float) -> None:
        """Record end-to-end pipeline latency."""
        e2e_latency_seconds.labels(mic=self._mic).observe(latency_s)

    def observe_segment_duration(self, duration_s: float) -> None:
        """Record speech segment duration."""
        segment_duration_seconds.labels(mic=self._mic).observe(duration_s)
