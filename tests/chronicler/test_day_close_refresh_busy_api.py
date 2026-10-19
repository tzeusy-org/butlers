"""A busy Chronicler refresh reaches the dashboard as 409 scheduler_busy (bu-kcgfqb).

The ``chronicler_day_close_refresh`` tool answers ``scheduler_busy`` while a tick or another
manual dispatch holds the daemon's tick guard. The dashboard route must report that as a
handled refusal (409 with the code), not as a bad gateway for an "invalid response".
"""

from __future__ import annotations

import pytest

from tests.chronicler.test_day_close_refresh_api import _make_app, _post_refresh

pytestmark = pytest.mark.unit

_DAEMON_MARKER = "private daemon payload marker"


async def test_a_busy_refresh_is_a_409_scheduler_busy_not_a_bad_gateway() -> None:
    app, _get_client, call_tool = _make_app(
        {"status": "error", "code": "scheduler_busy", "message": _DAEMON_MARKER}
    )

    response = await _post_refresh(app)

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "scheduler_busy"
    assert body["butler"] == "chronicler"
    assert "try again" in body["message"].lower()
    assert _DAEMON_MARKER not in response.text
    call_tool.assert_awaited_once()
