"""The request-meta key that carries an owner-creation claim.

A leaf on purpose: the dashboard's ``butlers.api.deps`` classifies calls by
this key, and deps sits inside the relationship Finder's no-LLM import walk
(``roster/relationship/tests/test_finder_no_llm_transitive.py``), which
``butlers.core.owner_creation`` does not pass. Import nothing here.
"""

from __future__ import annotations

META_KEY = "butlers/owner-creation-v1"
