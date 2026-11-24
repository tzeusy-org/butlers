"""Every SQL statement that reads calendar events honors the quarantine, or says why it need not.

REQ-module-calendar-041. Each SQL string under ``src/`` or ``roster/`` that selects from or joins
``calendar_events`` or ``calendar_event_instances`` must carry ``QUARANTINED_INVITATION_SQL`` (or
``quarantined_invitation_sql``) or an inline ``-- quarantine-exempt: <reason>`` comment saying why
the statement never exposes third-party invitation text or time (for example, that the rows are
filtered in Python by ``is_owner_attending``). A new statement, even in a file that already has a
compliant one, fails here until it does one or the other. Files in ``EXEMPT`` are whole-file
exceptions (writers, butler-internal lanes, response models).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_READ = re.compile(r"\b(?:FROM|JOIN)\s+(?:[\w{}\"]+\.)?calendar_event(?:s|_instances)\b", re.I)
_AWARE = ("QUARANTINED_INVITATION_SQL", "quarantined_invitation_sql")
_MARKER = re.compile(r"--\s*quarantine-exempt:\s*\w")

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


def _scanned_files() -> list[Path]:
    paths = []
    for root in ("src", "roster"):
        for path in sorted((REPO / root).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or "/migrations/" in rel:
                continue
            paths.append(path)
    return paths


def statements(text: str) -> list[tuple[int, str]]:
    """(line, source) of each outermost string literal that reads a calendar events table."""
    if "calendar_event" not in text:
        return []
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)

    def source_of(node: ast.expr) -> str:
        assert node.end_lineno is not None and node.end_col_offset is not None
        chunk = lines[node.lineno - 1 : node.end_lineno]
        # col offsets are UTF-8 byte offsets
        chunk[-1] = chunk[-1].encode()[: node.end_col_offset].decode()
        chunk[0] = chunk[0].encode()[node.col_offset :].decode() if len(chunk) > 1 else chunk[0]
        if len(chunk) == 1:
            return chunk[0].encode()[node.col_offset :].decode()
        return "".join(chunk)

    found: list[tuple[int, str]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.JoinedStr):
            # Outermost literal only: its own pieces are not separate statements.
            if any(
                isinstance(v, ast.Constant) and "calendar_event" in str(v.value)
                for v in node.values
            ):
                segment = source_of(node)
                if _READ.search(segment):
                    found.append((node.lineno, segment))
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "calendar_event" in node.value:
                segment = source_of(node)
                if _READ.search(segment):
                    found.append((node.lineno, segment))
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def compliant(segment: str) -> bool:
    return any(name in segment for name in _AWARE) or bool(_MARKER.search(segment))


def _readers() -> list[str]:
    return [
        path.relative_to(REPO).as_posix()
        for path in _scanned_files()
        if statements(path.read_text(encoding="utf-8"))
    ]


def test_the_scan_finds_the_known_readers() -> None:
    readers = _readers()
    assert "src/butlers/jobs/meeting_card.py" in readers
    assert "src/butlers/chronicler/adapters/calendar.py" in readers
    assert len(readers) >= 15


def test_the_checker_is_statement_granular() -> None:
    clean = 'Q = f"""SELECT 1 FROM calendar_events e WHERE NOT {QUARANTINED_INVITATION_SQL}"""\n'
    bare = 'Q = """SELECT 1 FROM calendar_events e"""\n'
    marked = 'Q = """SELECT 1 FROM calendar_events e -- quarantine-exempt: why"""\n'
    empty_marker = 'Q = """SELECT 1 FROM calendar_events e -- quarantine-exempt:"""\n'
    both = clean + bare
    assert [compliant(seg) for _, seg in statements(clean)] == [True]
    assert [compliant(seg) for _, seg in statements(bare)] == [False]
    assert [compliant(seg) for _, seg in statements(marked)] == [True]
    assert [compliant(seg) for _, seg in statements(empty_marker)] == [False]
    assert [compliant(seg) for _, seg in statements(both)] == [True, False]


def test_every_calendar_events_statement_honors_the_quarantine_or_is_exempt() -> None:
    offenders = []
    for path in _scanned_files():
        rel = path.relative_to(REPO).as_posix()
        if rel in EXEMPT:
            assert EXEMPT[rel].strip(), "an exemption needs a reason"
            continue
        for line, segment in statements(path.read_text(encoding="utf-8")):
            if not compliant(segment):
                offenders.append(f"{rel}:{line}")
    assert not offenders, (
        "SQL reading calendar events without the quarantine predicate or an inline "
        "'-- quarantine-exempt: <reason>' (REQ-module-calendar-041): " + ", ".join(offenders)
    )


def test_every_exemption_still_names_a_real_reader() -> None:
    readers = set(_readers()) | {
        rel for rel in EXEMPT if _READ.search((REPO / rel).read_text(encoding="utf-8")) is not None
    }
    stale = [rel for rel in EXEMPT if rel not in readers]
    assert not stale, f"exemptions that no longer read calendar_events: {stale}"
