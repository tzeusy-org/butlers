"""The three delivered Health baseline declarations, shared by watch and reads."""

from butlers.core.baselines import BaselineMetric


def _metric(
    key: str,
    unit: str,
    concern: str,
    min_dispersion: float,
) -> BaselineMetric:
    return BaselineMetric(
        key=key,
        unit=unit,
        bucket="day",
        window_days=60,
        min_n=14,
        min_coverage=0.5,
        k_mad=2.6,
        k_consecutive=3,
        concern=concern,  # type: ignore[arg-type]
        min_dispersion=min_dispersion,
        expected_signal_key=f"health:baseline:{key}",
    )


METRICS: tuple[BaselineMetric, ...] = (
    _metric("resting_hr", "bpm", "above", 1.0),
    _metric("hrv", "ms", "below", 3.0),
    _metric("sleep_duration", "h", "both", 0.25),
)
