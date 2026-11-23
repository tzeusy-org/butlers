"""The quarantine SQL fragment and its Python twin agree on a real Postgres (REQ-module-calendar-041)."""

from __future__ import annotations

import json
import shutil
from typing import Any

import pytest

from butlers.core.temporal.calendar_provenance import is_quarantined_invitation
from butlers.core.temporal.invitation_quarantine import quarantined_invitation_sql

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


def _att(status: str | None = "needsAction", **extra: Any) -> dict[str, Any]:
    attendee: dict[str, Any] = {"email": "me@example.com", "self": True, **extra}
    if status is not None:
        attendee["response_status"] = status
    return attendee


_ORGANIZER = {"email": "sam@example.org", "organizer": True, "response_status": "accepted"}

_CASES: list[dict[str, Any] | None] = [
    {"organizer": "sam@example.org", "attendees": [_att(), _ORGANIZER]},
    {"organizer": "sam@example.org", "attendees": [_att(None), _ORGANIZER]},
    {"organizer": "sam@example.org", "attendees": [_att("accepted"), _ORGANIZER]},
    {"organizer": "sam@example.org", "attendees": [_att("tentative"), _ORGANIZER]},
    {"organizer": "sam@example.org", "attendees": [_att("declined"), _ORGANIZER]},
    {"organizer": "sam@example.org", "attendees": [_att("NeedsAction"), _ORGANIZER]},
    {"organizer": "sam@example.org", "attendees": [{**_att(), "responseStatus": "needsAction"}]},
    {"organizer": "me@example.com", "attendees": [_att()]},
    {"organizer": "ME@example.com ", "attendees": [_att()]},
    {"organizer": "", "attendees": [_att()]},
    {"attendees": [_att()]},
    {"attendees": [_att(organizer=True)]},
    {"attendees": [{"email": "x@y.test"}]},
    {"attendees": []},
    {"attendees": "nope"},
    {"butler_generated": True, "attendees": [_att()]},
    {"butler_generated": "TRUE", "attendees": [_att()]},
    {"organizer": "sam@example.org", "attendees": [_att()], "organizer_verdict": "known_entity"},
    {"organizer": "sam@example.org", "attendees": [_att()], "organizer_verdict": "known_domain"},
    {"organizer": "sam@example.org", "attendees": [_att()], "organizer_verdict": "owner"},
    {"organizer": "sam@example.org", "attendees": [_att()], "organizer_verdict": "blocked"},
    {"organizer": "sam@example.org", "attendees": [_att()], "organizer_verdict": "first_contact"},
    {"organizer": "sam@example.org", "attendees": [_att()], "organizer_verdict": "vouched"},
    {"organizer": "sam@example.org", "attendees": [_att()], "organizer_verdict": "unknown"},
    {"organizer": "sam@example.org", "attendees": [_att()], "organizer_verdict": "bogus"},
    {},
    # String booleans: `->> = 'true'` accepts the string, so the Python twin must too.
    {"organizer": "sam@example.org", "attendees": [_att(**{"self": "true"}), _ORGANIZER]},
    {"organizer": "sam@example.org", "attendees": [_att(**{"self": "True"}), _ORGANIZER]},
    {"organizer": "sam@example.org", "attendees": [_att(organizer="true")]},
    {"organizer": "sam@example.org", "attendees": [_att(organizer="false")]},
    {"organizer": "sam@example.org", "attendees": [_att(organizer="True")]},
    # Untrimmed status: the Python twin strips; so must the SQL.
    {"organizer": "sam@example.org", "attendees": [_att(" accepted "), _ORGANIZER]},
    {"organizer": "sam@example.org", "attendees": [_att("\tdeclined\n"), _ORGANIZER]},
    {"organizer": "sam@example.org", "attendees": [_att("  "), _ORGANIZER]},
    # A null `response_status` falls through to the raw Google `responseStatus` in both.
    {"organizer": "sam@example.org", "attendees": [_att(None, response_status=None)]},
    {
        "organizer": "sam@example.org",
        "attendees": [_att(None, response_status=None, responseStatus="accepted")],
    },
    {
        "organizer": "sam@example.org",
        "attendees": [_att(None, response_status=None, responseStatus=" Declined ")],
    },
    {
        "organizer": "sam@example.org",
        "attendees": [_att("needsAction", responseStatus="accepted")],
    },
    {"organizer": "sam@example.org", "attendees": [_att(None, responseStatus=5)]},
]


@pytest.mark.parametrize("metadata", _CASES, ids=lambda m: json.dumps(m, default=str)[:80])
async def test_sql_and_python_quarantine_agree(db_pool, metadata) -> None:
    sql = f"SELECT {quarantined_invitation_sql('e')} AS q FROM (SELECT $1::jsonb AS metadata) e"
    in_sql = await db_pool.fetchval(sql, metadata)
    assert in_sql is is_quarantined_invitation(metadata)
