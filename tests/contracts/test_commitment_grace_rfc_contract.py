"""Static contract: ``DEFAULT_INITIAL_GRACE_SECONDS`` is RFC 0026 §6's number.

``butlers.core.commitments`` cites "RFC 0026 §6" as the authority for its 24h
default escalation grace. That citation was never machine-checked, so nothing
stopped the constant, the docstring, and the RFC from drifting apart — and the
L0 window is what the REQ-commitment-lifecycle-005 escalation job schedules
against, so a wrong default silently shifts when every commitment first
surfaces.

This test reads the RFC and derives the expected constant from it, rather than
restating 24h a third time. It fails when the RFC changes its window, when the
constant changes, when the docstring's quoted window stops matching, or when
section renumbering moves the escalation schedule out from under the §6
citation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from butlers.core.commitments import DEFAULT_INITIAL_GRACE_SECONDS

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).parent.parent.parent
_RFC = _REPO_ROOT / "about/legends-and-lore/rfcs/0026-commitment-lifecycle.md"
_MODULE = _REPO_ROOT / "src/butlers/core/commitments.py"

# The exact heading the module's citation points at. Renumbering the RFC's
# sections invalidates the citation even if the window itself is unchanged.
_SECTION_HEADING = "### 6. Escalation Integration"


def _read(path: Path) -> str:
    assert path.is_file(), f"required artifact is missing: {path}"
    return path.read_text(encoding="utf-8")


def _section_six() -> str:
    """Return the body of RFC 0026 §6, without its heading."""
    text = _read(_RFC)
    heading_present = _SECTION_HEADING in text
    assert heading_present, (
        f"RFC 0026 no longer contains {_SECTION_HEADING!r}; the "
        "'RFC 0026 §6' citation in src/butlers/core/commitments.py points at a "
        "section that has been renamed or renumbered and must be repointed."
    )
    body = text.split(_SECTION_HEADING, 1)[1]
    return body.split("\n### ", 1)[0]


def test_default_initial_grace_seconds_matches_rfc_0026_section_6() -> None:
    """The constant is whatever §6 says the L0 grace period defaults to."""
    # RFC 0026 §6: "**L0** (grace period): commitment exists, no surfacing yet.
    # Grace period defaults to 24h or until `next_action_window`, whichever is
    # sooner."
    match = re.search(
        r"Grace period\s+defaults to\s+(\d+)h\b",
        " ".join(_section_six().split()),
    )
    assert match is not None, (
        "RFC 0026 §6 no longer states a default grace window in the form "
        "'Grace period defaults to <N>h'. The 'RFC 0026 §6' citation on "
        "DEFAULT_INITIAL_GRACE_SECONDS claims an authority the section does "
        "not carry: either restore the statement or repoint the citation."
    )

    rfc_hours = int(match.group(1))
    assert DEFAULT_INITIAL_GRACE_SECONDS == rfc_hours * 60 * 60.0, (
        f"RFC 0026 §6 defaults the L0 grace period to {rfc_hours}h "
        f"({rfc_hours * 3600}s), but DEFAULT_INITIAL_GRACE_SECONDS is "
        f"{DEFAULT_INITIAL_GRACE_SECONDS}s. The constant sets when every "
        "commitment first surfaces; reconcile it with the RFC."
    )


def test_rfc_0026_section_6_still_owns_the_deadline_shortening_clause() -> None:
    """The module cites §6 for the seam ``initial_grace_seconds`` exists to serve."""
    body = " ".join(_section_six().split())
    clause_present = "L0 is shortened to surface before the deadline" in body
    assert clause_present, (
        "RFC 0026 §6 no longer describes shortening L0 for a deadline inside "
        "the grace window. src/butlers/core/commitments.py cites §6 for that "
        "behaviour as the reason initial_grace_seconds is an override seam "
        "(REQ-commitment-lifecycle-005); repoint the citation."
    )


def test_commitments_docstring_quotes_the_rfc_window() -> None:
    """The prose window in the module docstring cannot drift from the constant."""
    hours = int(DEFAULT_INITIAL_GRACE_SECONDS // 3600)
    expected = f"``DEFAULT_INITIAL_GRACE_SECONDS`` ({hours}h, RFC 0026 §6"
    citation_present = expected in " ".join(_read(_MODULE).split())
    assert citation_present, (
        "The 'Escalation grace' section of src/butlers/core/commitments.py must "
        f"quote the window it actually ships ({hours}h) alongside its RFC 0026 "
        "§6 citation, so a reader is not told one number while callers get "
        "another."
    )
