"""Credential-key normalisation utility (core-credentials §Credential-Key Normalisation Function).

The canonical credential-key format is ``<prefix>:<name>`` where ``<prefix>`` is
a single letter derived from the credential scope:

    ``u`` — user-scoped OAuth credential  (e.g. ``u:google``)
    ``s`` — system-scoped secret          (e.g. ``s:BUTLER_TELEGRAM_TOKEN``)
    ``c`` — CLI credential                (e.g. ``c:claude``)

``normalize_credential_key(scope, key)`` is the primary factory: it maps a
long-form scope name (``"user"``, ``"system"``, ``"cli"``) or its single-letter
alias to the canonical ``<prefix>:<name>`` string.

This module is imported by:
- ``src/butlers/api/routers/audit.py`` — ``GET /api/audit-log?key=`` filter
- Any future audit-write callsite that constructs credential-key targets
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Scope → prefix mapping
# ---------------------------------------------------------------------------

#: Maps every accepted scope name (long *and* short alias) to its canonical
#: single-letter prefix.  Long names are the authoritative vocabulary;
#: short aliases are accepted for convenience and round-trip symmetry.
_SCOPE_TO_PREFIX: dict[str, str] = {
    # long forms (authoritative)
    "user": "u",
    "system": "s",
    "cli": "c",
    # short aliases (accepted for convenience)
    "u": "u",
    "s": "s",
    "c": "c",
}


def normalize_credential_key(scope: str, key: str) -> str:
    """Return the canonical ``<prefix>:<key>`` credential-key string.

    Parameters
    ----------
    scope:
        Either the long-form scope name (``"user"``, ``"system"``, ``"cli"``)
        or the single-letter alias (``"u"``, ``"s"``, ``"c"``).
    key:
        The credential name within the scope (e.g. ``"google"``,
        ``"BUTLER_TELEGRAM_TOKEN"``).  The value is used verbatim — no
        case normalisation is applied, matching the spec requirement that
        ``s:BUTLER_TELEGRAM_TOKEN`` preserves capitalisation.

    Returns
    -------
    str
        The canonical key string, e.g. ``"u:google"``.

    Raises
    ------
    ValueError
        When *scope* is not a recognised scope name or alias.

    Examples
    --------
    >>> normalize_credential_key("user", "google")
    'u:google'
    >>> normalize_credential_key("system", "BUTLER_TELEGRAM_TOKEN")
    's:BUTLER_TELEGRAM_TOKEN'
    >>> normalize_credential_key("cli", "claude")
    'c:claude'
    """
    prefix = _SCOPE_TO_PREFIX.get(scope)
    if prefix is None:
        known = sorted(set(_SCOPE_TO_PREFIX.keys()))
        raise ValueError(f"Unknown credential scope {scope!r}. Expected one of: {known}")
    return f"{prefix}:{key}"


def normalize_key_param(raw_key: str) -> str:
    """Normalise a credential-key value received as a query-parameter.

    Accepts both the canonical short-prefix form (``u:google``) and the
    long-scope form (``user:google``), and returns the canonical form.

    This is the entry-point used by ``GET /api/audit-log?key=`` to ensure
    that callers can pass either format and still get consistent filtering.

    Parameters
    ----------
    raw_key:
        The raw ``?key=`` value from the query string, e.g. ``"u:google"``
        or ``"user:google"``.

    Returns
    -------
    str
        The canonical credential-key string.

    Raises
    ------
    ValueError
        When *raw_key* is not in ``<scope>:<name>`` format, or the scope
        portion is not a recognised name or alias.

    Examples
    --------
    >>> normalize_key_param("u:google")
    'u:google'
    >>> normalize_key_param("user:google")
    'u:google'
    >>> normalize_key_param("s:BUTLER_TELEGRAM_TOKEN")
    's:BUTLER_TELEGRAM_TOKEN'
    """
    if ":" not in raw_key:
        raise ValueError(
            f"Invalid credential-key format {raw_key!r}. "
            "Expected '<scope>:<name>', e.g. 'u:google' or 'user:google'."
        )
    scope, _, name = raw_key.partition(":")
    return normalize_credential_key(scope, name)


__all__ = ["normalize_credential_key", "normalize_key_param"]
