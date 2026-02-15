"""Search vector generation helpers for Memory Butler.

Provides text preprocessing and SQL expression helpers for populating
``search_vector tsvector`` columns in episodes, facts, and rules tables.

Since the Memory Butler uses raw SQL via asyncpg (not SQLAlchemy ORM),
these helpers produce sanitized text and parameterized SQL fragments
suitable for direct use in INSERT/UPDATE statements.
"""

from __future__ import annotations

import re

# Maximum text length before truncation (1 MB).  PostgreSQL's to_tsvector
# has an internal limit, and extremely long documents would bloat the index
# without proportional retrieval benefit.
MAX_TEXT_BYTES: int = 1_048_576  # 1 MB

# PostgreSQL text-search configuration to use.
TS_CONFIG: str = "english"


def preprocess_text(text: str | None) -> str:
    """Sanitize and normalize *text* before passing it to ``to_tsvector``.

    Handles the following edge cases:
    * ``None`` or empty string -> returns ``""``
    * NUL bytes (``\\x00``) which asyncpg / PostgreSQL reject
    * Collapse consecutive whitespace into single spaces
    * Strip leading / trailing whitespace
    * Truncate to :data:`MAX_TEXT_BYTES` (measured in UTF-8 bytes) to avoid
      oversized tsvector entries.  Truncation is performed on a codepoint
      boundary so we never produce invalid UTF-8.

    Returns:
        The cleaned text ready for ``to_tsvector``.
    """
    if not text:
        return ""

    # Remove NUL bytes — PostgreSQL TEXT columns cannot store them and
    # asyncpg raises DataError on \x00.
    cleaned = text.replace("\x00", "")

    # Collapse whitespace (tabs, newlines, multiple spaces) into single spaces.
    cleaned = re.sub(r"\s+", " ", cleaned)

    # Strip leading/trailing whitespace.
    cleaned = cleaned.strip()

    # Truncate to MAX_TEXT_BYTES on a codepoint boundary.
    encoded = cleaned.encode("utf-8")
    if len(encoded) > MAX_TEXT_BYTES:
        # Decode back with error handling to avoid splitting a multi-byte char.
        cleaned = encoded[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
        # The ignore-mode decode may leave a trailing partial codepoint removed,
        # but the string is still valid.  Re-strip in case truncation lands on
        # whitespace.
        cleaned = cleaned.rstrip()

    return cleaned


def tsvector_sql(param: str = "$1") -> str:
    """Return a SQL expression that builds a ``tsvector`` from a text parameter.

    Usage with asyncpg::

        text = preprocess_text(raw_content)
        sql = f"INSERT INTO episodes (content, search_vector) VALUES ($1, {tsvector_sql('$1')})"
        await conn.execute(sql, text)

    The expression uses PostgreSQL's ``to_tsvector`` with the project-wide
    text-search configuration (:data:`TS_CONFIG`).

    Args:
        param: The positional parameter placeholder referencing the text
               value (e.g. ``"$1"``, ``"$2"``).

    Returns:
        A SQL fragment like ``to_tsvector('english', $1)``.
    """
    return f"to_tsvector('{TS_CONFIG}', {param})"


def tsquery_sql(param: str = "$1") -> str:
    """Return a SQL expression that builds a ``tsquery`` from user search input.

    Uses ``plainto_tsquery`` which is safer than ``to_tsquery`` for
    free-form user input — it does not require boolean operators and
    will not raise syntax errors on special characters.

    Args:
        param: The positional parameter placeholder referencing the
               search query text (e.g. ``"$1"``).

    Returns:
        A SQL fragment like ``plainto_tsquery('english', $1)``.
    """
    return f"plainto_tsquery('{TS_CONFIG}', {param})"


def websearch_tsquery_sql(param: str = "$1") -> str:
    """Return a SQL expression using ``websearch_to_tsquery``.

    ``websearch_to_tsquery`` (PostgreSQL 11+) supports a Google-like
    search syntax with quoted phrases, ``-exclusion``, and ``OR``.
    Suitable for power-user search boxes.

    Args:
        param: The positional parameter placeholder.

    Returns:
        A SQL fragment like ``websearch_to_tsquery('english', $1)``.
    """
    return f"websearch_to_tsquery('{TS_CONFIG}', {param})"


def preprocess_search_query(query: str | None) -> str:
    """Sanitize a user-supplied search query before passing it to a tsquery function.

    Applies the same NUL-byte removal and whitespace normalization as
    :func:`preprocess_text`, but does **not** truncate aggressively since
    search queries are typically short.

    Returns:
        The cleaned query string, or ``""`` if input is falsy.
    """
    if not query:
        return ""

    cleaned = query.replace("\x00", "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()
