"""Cross-check that staleness thresholds are in sync between FE and BE.

FRESH_MAX_DAYS and AGING_MAX_DAYS are declared in two places:
  - Backend:  roster/relationship/tools/staleness.py
  - Frontend: frontend/src/components/ui/Provenance.tsx

This test parses the FE source and asserts both sides agree, preventing
silent drift that would cause the UI staleness colour to diverge from what
the API computes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from butlers.tools.relationship.staleness import AGING_MAX_DAYS, FRESH_MAX_DAYS

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[2]
_PROVENANCE_TSX = _REPO_ROOT / "frontend" / "src" / "components" / "ui" / "Provenance.tsx"


def _parse_fe_constant(source: str, name: str) -> int:
    """Extract ``export const NAME = <int>`` from TypeScript source."""
    pattern = rf"export\s+const\s+{re.escape(name)}\s*=\s*(\d+)"
    match = re.search(pattern, source)
    if match is None:
        raise ValueError(f"Could not find constant {name!r} in Provenance.tsx")
    return int(match.group(1))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStalenessThresholdParity:
    def test_fresh_max_days_matches(self):
        """FE FRESH_MAX_DAYS must equal the BE value."""
        source = _PROVENANCE_TSX.read_text()
        fe_value = _parse_fe_constant(source, "FRESH_MAX_DAYS")
        assert fe_value == FRESH_MAX_DAYS, (
            f"FE FRESH_MAX_DAYS={fe_value} diverged from BE FRESH_MAX_DAYS={FRESH_MAX_DAYS}. "
            "Update both files together."
        )

    def test_aging_max_days_matches(self):
        """FE AGING_MAX_DAYS must equal the BE value."""
        source = _PROVENANCE_TSX.read_text()
        fe_value = _parse_fe_constant(source, "AGING_MAX_DAYS")
        assert fe_value == AGING_MAX_DAYS, (
            f"FE AGING_MAX_DAYS={fe_value} diverged from BE AGING_MAX_DAYS={AGING_MAX_DAYS}. "
            "Update both files together."
        )
