"""Execute the adopted connector heartbeat boundaries against a fixed server clock."""

from datetime import UTC, datetime, timedelta

import pytest

from butlers.api.routers.owntracks import _derive_connection_state
from butlers.core.liveness import derive_liveness

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("age_seconds", "expected"),
    [
        pytest.param(None, "offline", id="never-seen"),
        pytest.param(-300.000001, "offline", id="beyond-future-tolerance"),
        pytest.param(-300, "online", id="exact-future-tolerance"),
        pytest.param(-299.999999, "online", id="within-future-tolerance"),
        pytest.param(0, "online", id="received-now"),
        pytest.param(299.999999, "online", id="under-five-minutes"),
        pytest.param(300, "stale", id="exact-five-minutes"),
        pytest.param(300.000001, "stale", id="over-five-minutes"),
        pytest.param(899.999999, "stale", id="under-fifteen-minutes"),
        pytest.param(900, "stale", id="exact-fifteen-minutes"),
        pytest.param(900.000001, "offline", id="over-fifteen-minutes"),
    ],
)
def test_connector_liveness_threshold_contract(monkeypatch, age_seconds, expected):
    now = datetime(2026, 10, 21, 12, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is not None else now.replace(tzinfo=None)

    # derive_liveness imports the datetime module inside the call. Freeze that
    # server clock rather than constructing a timestamp relative to a moving now.
    monkeypatch.setattr("datetime.datetime", FrozenDatetime)
    heartbeat = None if age_seconds is None else now - timedelta(seconds=age_seconds)

    assert derive_liveness(heartbeat) == expected

    if age_seconds == 300:
        # Prove the changed boundary reaches the API's connection-state consumer;
        # its stale verdict must also stop advertising the connector as running.
        state, running, *_ = _derive_connection_state(
            token_configured=True, heartbeat_row={"last_heartbeat_at": heartbeat}
        )
        assert (state.value, running) == ("stale", False)
