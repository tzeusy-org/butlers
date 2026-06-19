"""Unit tests for roster/relationship/tools/facts.py (bu-2jlb2).

Covers:
  (a) fact_set raises ValueError when resolve_contact_entity_id returns None.
  (b) fact_list raises ValueError when resolve_contact_entity_id returns None.
  (c) facts.py does not contain live SQL against public.contacts or unqualified
      contacts (static source check — retirement enforcement).

All tests are pure unit tests (no Docker/Postgres).  The asyncpg pool is
mocked via unittest.mock.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.unit

_CONTACT_ID = uuid.uuid4()
_FACTS_MODULE = "butlers.tools.relationship.facts"
_RESOLVER_TARGET = f"{_FACTS_MODULE}.resolve_contact_entity_id"

_FACTS_PY = Path(__file__).resolve().parents[2] / "roster" / "relationship" / "tools" / "facts.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pool_with_entity(entity_id: uuid.UUID | None = None):
    """Return a mock asyncpg.Pool whose fetchrow returns a plausible facts row."""
    pool = MagicMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    facts_row = MagicMock()
    facts_row.__getitem__.side_effect = {
        "id": uuid.uuid4(),
        "predicate": "color",
        "content": "blue",
        "metadata": {},
        "created_at": None,
    }.__getitem__
    pool.fetchrow = AsyncMock(return_value=facts_row)
    pool.fetch = AsyncMock(return_value=[])
    return pool


# ===========================================================================
# (a) fact_set raises ValueError when entity resolution returns None
# ===========================================================================


class TestFactSetNoEntity:
    async def test_raises_value_error(self):
        """fact_set must raise ValueError when no entity is linked to the contact."""
        from butlers.tools.relationship.facts import fact_set

        pool = _pool_with_entity(None)
        with patch(_RESOLVER_TARGET, new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError, match="no linked entity"):
                await fact_set(pool, _CONTACT_ID, "color", "blue")

    async def test_error_includes_contact_id(self):
        """ValueError message must include the contact_id for diagnosability."""
        from butlers.tools.relationship.facts import fact_set

        pool = _pool_with_entity(None)
        with patch(_RESOLVER_TARGET, new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError, match=str(_CONTACT_ID)):
                await fact_set(pool, _CONTACT_ID, "color", "blue")

    async def test_no_sql_executed_on_missing_entity(self):
        """fact_set must NOT execute any SQL when entity resolution returns None."""
        from butlers.tools.relationship.facts import fact_set

        pool = _pool_with_entity(None)
        with patch(_RESOLVER_TARGET, new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError):
                await fact_set(pool, _CONTACT_ID, "color", "blue")

        pool.execute.assert_not_called()
        pool.fetchrow.assert_not_called()


# ===========================================================================
# (b) fact_list raises ValueError when entity resolution returns None
# ===========================================================================


class TestFactListNoEntity:
    async def test_raises_value_error(self):
        """fact_list must raise ValueError when no entity is linked to the contact."""
        from butlers.tools.relationship.facts import fact_list

        pool = _pool_with_entity(None)
        with patch(_RESOLVER_TARGET, new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError, match="no linked entity"):
                await fact_list(pool, _CONTACT_ID)

    async def test_error_includes_contact_id(self):
        """ValueError message must include the contact_id for diagnosability."""
        from butlers.tools.relationship.facts import fact_list

        pool = _pool_with_entity(None)
        with patch(_RESOLVER_TARGET, new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError, match=str(_CONTACT_ID)):
                await fact_list(pool, _CONTACT_ID)

    async def test_no_sql_executed_on_missing_entity(self):
        """fact_list must NOT execute any SQL when entity resolution returns None."""
        from butlers.tools.relationship.facts import fact_list

        pool = _pool_with_entity(None)
        with patch(_RESOLVER_TARGET, new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError):
                await fact_list(pool, _CONTACT_ID)

        pool.fetch.assert_not_called()


# ===========================================================================
# (c) Static retirement check — no live SQL against contacts in facts.py
# ===========================================================================

# Pattern matches FROM/JOIN/INTO/UPDATE against unqualified 'contacts' table or
# public.contacts, but ignores lines that are purely comments or docstring text.
_LIVE_SQL_CONTACTS_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(?:public\.)?contacts\b",
    re.IGNORECASE,
)


def _is_comment_or_docstring_line(line: str) -> bool:
    """Return True if the line is clearly a comment or in a docstring context."""
    stripped = line.strip()
    return stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''")


class TestFactsPyNoContactsSQL:
    def test_no_live_sql_against_contacts(self):
        """facts.py must contain zero live SQL that queries public.contacts.

        This is the static enforcement gate for contacts-schema retirement (bu-2jlb2,
        Phase 7).  Docstring mentions are allowed (they are documentation, not queries).
        """
        assert _FACTS_PY.exists(), f"facts.py not found at {_FACTS_PY}"
        source = _FACTS_PY.read_text(encoding="utf-8")
        violations = []
        for lineno, line in enumerate(source.splitlines(), start=1):
            if _LIVE_SQL_CONTACTS_RE.search(line):
                if not _is_comment_or_docstring_line(line):
                    violations.append(f"  line {lineno}: {line.rstrip()}")

        assert not violations, (
            "facts.py contains live SQL against contacts table "
            "(contacts-schema retirement violated):\n" + "\n".join(violations)
        )

    def test_uses_shared_resolver(self):
        """facts.py must import resolve_contact_entity_id from _entity_resolve.

        This confirms the shared resolver is wired in, not a private contacts query.
        """
        assert _FACTS_PY.exists()
        source = _FACTS_PY.read_text(encoding="utf-8")
        assert (
            "from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id"
            in source
        ), (
            "facts.py must import resolve_contact_entity_id from _entity_resolve "
            "(contacts-schema retirement, bu-2jlb2)"
        )

    def test_no_local_resolve_entity_id(self):
        """The private _resolve_entity_id helper must be gone after the retirement fix."""
        assert _FACTS_PY.exists()
        source = _FACTS_PY.read_text(encoding="utf-8")
        assert "def _resolve_entity_id" not in source, (
            "facts.py still defines a local _resolve_entity_id that queries public.contacts — "
            "remove it and use resolve_contact_entity_id from _entity_resolve instead "
            "(contacts-schema retirement, bu-2jlb2)"
        )
