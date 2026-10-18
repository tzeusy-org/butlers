"""The lane band's literal width and the coverage gate are pinned (bu-q7vx1q.80).

A lane's band is ``median +/- max(3 * 1.4826 * MAD, 3600 s)``: the floor decides the edge of
a flat lane, the MAD decides the edge of a dispersed one. Each edge is probed just inside and
just outside so a changed ``k_mad``, a changed floor or a changed coverage gate cannot pass.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from butlers.chronicler.balance import LaneBalance, compute_daily_balance

pytestmark = pytest.mark.unit

_DAY = date(2026, 7, 20)
_HOUR = 3600
_MAD_SCALE = 1.4826
_K_MAD = 3


def _history(values: list[int]) -> dict[date, int]:
    return {_DAY - timedelta(days=len(values) - i): v for i, v in enumerate(values)}


def _work(seconds: int, values: list[int], *, lookback_days: int = 28) -> LaneBalance:
    balances = compute_daily_balance(
        {"work": seconds}, {"work": _history(values)}, local_date=_DAY, lookback_days=lookback_days
    )
    return next(b for b in balances if b.lane == "work")


@pytest.mark.parametrize(
    ("offset", "state"),
    [
        (_HOUR - 1, "within"),
        (_HOUR, "above"),
        (_HOUR + 1, "above"),
        (-(_HOUR - 1), "within"),
        (-_HOUR, "below"),
        (-(_HOUR + 1), "below"),
    ],
)
def test_a_flat_lane_edge_is_one_hour_from_the_median(offset: int, state: str) -> None:
    work = _work(8 * _HOUR + offset, [8 * _HOUR] * 14)

    assert work.state == state
    assert work.band_low == pytest.approx(7 * _HOUR)
    assert work.band_high == pytest.approx(9 * _HOUR)


# 7 days at 6h and 7 at 10h: median 8h, MAD 2h, so the band is 8h +/- 3 * 1.4826 * 2h.
_DISPERSED = [6 * _HOUR, 10 * _HOUR] * 7
_MAD_HALF_WIDTH = _K_MAD * _MAD_SCALE * 2 * _HOUR  # 32024.16 s, well past the floor


@pytest.mark.parametrize(
    ("offset", "state"),
    [
        (int(_MAD_HALF_WIDTH), "within"),
        (int(_MAD_HALF_WIDTH) + 1, "above"),
        (-int(_MAD_HALF_WIDTH), "within"),
        (-int(_MAD_HALF_WIDTH) - 1, "below"),
    ],
)
def test_a_dispersed_lane_edge_is_three_scaled_mads_from_the_median(
    offset: int, state: str
) -> None:
    work = _work(8 * _HOUR + offset, _DISPERSED)

    assert work.state == state
    assert work.band_low == pytest.approx(8 * _HOUR - _MAD_HALF_WIDTH)
    assert work.band_high == pytest.approx(8 * _HOUR + _MAD_HALF_WIDTH)


def test_a_short_lane_is_not_hidden_by_a_wide_band() -> None:
    # A lane usually ~45 minutes: the one-hour minimum band still lets a 2.5h day read above.
    exercise = _work(150 * 60, [45 * 60] * 14)
    assert exercise.state == "above"
    assert exercise.band_low == pytest.approx(45 * 60 - _HOUR)


def test_coverage_gate_needs_a_quarter_of_a_long_lookback() -> None:
    # Lookback 60: min_n (7) is easily met, so only the 25% coverage gate (15 days) decides.
    assert _work(8 * _HOUR, [8 * _HOUR] * 10, lookback_days=60).state == "insufficient_history"
    assert _work(8 * _HOUR, [8 * _HOUR] * 14, lookback_days=60).state == "insufficient_history"
    assert _work(8 * _HOUR, [8 * _HOUR] * 15, lookback_days=60).state == "within"
