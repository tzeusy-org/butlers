"""Test support utilities for the butlers package.

This sub-package exports helpers that are useful across multiple test trees
(``tests/``, ``roster/*/tests/``).  All public symbols are purely functional
and have no hard dependency on pytest itself so they can be imported safely in
any test context.
"""

from __future__ import annotations
