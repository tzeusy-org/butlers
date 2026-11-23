"""Every reader of calendar_events honors the invitation quarantine, or says why it need not.

REQ-module-calendar-041. A new SELECT/JOIN over ``calendar_events`` or ``calendar_event_instances``
under ``src/`` or ``roster/`` fails here until its file either uses a quarantine-aware predicate
(so unanswered invitations from untrusted organizers hold no time and reach no prompt) or is listed
in ``EXEMPT`` with the reason it never exposes third-party invitations.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_READ = re.compile(r"\b(?:FROM|JOIN)\s+(?:[\w{}\"]+\.)?calendar_event(?:s|_instances)\b", re.I)
_AWARE = (
    "QUARANTINED_INVITATION_SQL",
    "quarantined_invitation_sql",
    "is_quarantined_invitation",
    "is_owner_attending",
    "asserts_owner_time",
    "counts_as_owner_time",
    "counts_toward_owner_load",
)

#: file -> why it exposes no third-party invitation text or time.
EXEMPT: dict[str, str] = {
    "src/butlers/modules/calendar/recurrence.py": "resolves an event id from its origin ref",
    "src/butlers/modules/calendar/butler_events.py": "reads the butler lane's own events",
    "src/butlers/modules/calendar/__init__.py": "maps an origin ref to its calendar id",
    "src/butlers/modules/calendar/internal_projection.py": "projects butler-internal events",
    "src/butlers/modules/calendar/projection.py": "the writer: upserts, deletes, id lookups",
    "src/butlers/modules/calendar/reminders.py": "butler-internal reminder events only",
    "src/butlers/modules/calendar/organizer_trust.py": "reads metadata to stamp the verdict",
    "src/butlers/modules/calendar_response.py": (
        "the owner's own RSVP to one invitation they chose; shows the model no text"
    ),
    "src/butlers/api/models/calendar_workspace.py": "response models; a docstring names the tables",
    "src/butlers/api/routers/calendar_workspace.py": (
        "owner-facing dashboard joins (proposals, single-entry reads); no prompt"
    ),
    "src/butlers/jobs/health_visit_brief.py": (
        "only events Health itself booked (calendar_action_log), never an invitation"
    ),
    "roster/relationship/migrations/007_reminders_to_calendar_events.py": "a one-off delete",
}


def _readers() -> list[str]:
    found = []
    for root in ("src", "roster"):
        for path in sorted((REPO / root).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.startswith("roster/") and "/migrations/" in rel:
                if rel not in EXEMPT:
                    continue
            if _READ.search(path.read_text(encoding="utf-8")):
                found.append(rel)
    return found


def test_the_scan_finds_the_known_readers() -> None:
    readers = _readers()
    assert "src/butlers/jobs/meeting_card.py" in readers
    assert "src/butlers/chronicler/adapters/calendar.py" in readers
    assert len(readers) >= 15


@pytest.mark.parametrize("rel", _readers())
def test_a_calendar_events_reader_honors_the_quarantine_or_is_exempt(rel: str) -> None:
    if rel in EXEMPT:
        assert EXEMPT[rel].strip(), "an exemption needs a reason"
        return
    text = (REPO / rel).read_text(encoding="utf-8")
    assert any(name in text for name in _AWARE), (
        f"{rel} reads calendar events but uses no quarantine-aware predicate "
        "(REQ-module-calendar-041). Apply QUARANTINED_INVITATION_SQL or is_owner_attending / "
        "asserts_owner_time, or add it to EXEMPT with a reason."
    )


def test_every_exemption_still_names_a_real_reader() -> None:
    readers = set(_readers())
    stale = [rel for rel in EXEMPT if rel not in readers]
    assert not stale, f"exemptions that no longer read calendar_events: {stale}"
