"""Pydantic models for the audit log API.

``AuditEntry`` maps to the legacy ``dashboard_audit_log`` table in the
Switchboard database (used by ``log_audit_entry``).

``AuditLogEntry`` maps to the new ``public.audit_log`` primitive table
introduced in core_092.  This is the model returned by the
``GET /api/audit-log`` and ``GET /api/audit-log/{id}`` endpoints, and by the
Issues occurrences drill-down (``routers/issues.py``).  It is also the
enforcement point for the credential-target free-text rule below.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class AuditEntry(BaseModel):
    """Single audit log entry from the legacy dashboard_audit_log table."""

    id: UUID
    butler: str
    operation: str
    request_summary: dict = Field(default_factory=dict)
    result: str
    error: str | None = None
    user_context: dict = Field(default_factory=dict)
    created_at: datetime


#: Matches a ``public.audit_log.target`` that names a credential.  The scope
#: segment vocabulary is ``core-credentials`` §Credential-Key Normalisation
#: Function: canonical short prefixes ``u:``/``s:``/``c:`` plus the long-scope
#: spellings ``normalize_key_param`` accepts, since older writers used them and
#: the column itself is never normalised.
#:
#: Exported as a bare string because ``audit_grouping`` embeds this exact
#: pattern in the grouping CTE's ``target ~ '...'`` predicate (bu-uqipv): the
#: same rows whose free text this model withholds must also not have that text
#: become their audit-group *title*.  Two hand-copied spellings of "this target
#: names a credential" could drift, leaving a namespace blind on one surface
#: and loud on the other.  The syntax is a common subset of Python ``re`` and
#: Postgres ARE — anchors, alternation, a non-capturing group — so one literal
#: serves both.
CREDENTIAL_TARGET_PATTERN = r"^(?:u|s|c|user|system|cli):"

_CREDENTIAL_TARGET_RE = re.compile(CREDENTIAL_TARGET_PATTERN)


def is_credential_target(target: str | None) -> bool:
    """Return True when *target* names a credential rather than another resource.

    Non-credential audit targets in this table look like ``butler:qa``,
    ``rule:7``, or a request path — none of which match.
    """
    return target is not None and _CREDENTIAL_TARGET_RE.match(target) is not None


class AuditLogEntry(BaseModel):
    """Single entry from the ``public.audit_log`` primitive table (core_092).

    This is the canonical audit primitive used by every mutation endpoint
    that changes system state.

    Credential-target free text is withheld on read (bu-ove06)
    ---------------------------------------------------------
    Rows whose ``target`` names a credential (``u:``/``s:``/``c:`` and the
    long-scope spellings) carry the same provider free text the secrets
    surfaces stopped publishing: ``_write_credential_audit`` persists
    ``"Probe failed: <provider text>; probe_status=<token>"`` in ``note`` and
    the raw failure message in ``error``.  Those three free-text columns —
    ``note``, ``error``, ``metadata`` — are blanked here and the row is flagged
    ``redacted``.  Every other row keeps its free text verbatim: the general
    operator log has a real forensic claim on its own diagnostics, and this is
    a credential-namespace carve-out rather than a blanket gag.

    The rule lives on the model, not in the routes, because the credential
    audit namespaces have five known producers (``_write_credential_audit`` /
    ``_write_system_audit`` / ``_write_cli_audit`` in ``routers/secrets_v2.py``,
    ``_emit_oauth_audit`` in ``routers/oauth.py``, ``jobs/secrets_lifecycle``)
    and three readers (``GET /api/audit-log``, ``GET /api/audit-log/{id}``,
    ``GET /api/issues/{key}/occurrences``), and either set can grow at any
    time.  A per-route fix would have left the occurrences drill-down
    publishing the identical text — which is exactly how the leak this closes
    survived three sibling fixes.
    """

    id: int
    ts: datetime
    actor: str
    action: str
    target: str | None = None
    note: str | None = None
    ip: str | None = None
    request_id: UUID | None = None
    # Added in core_122 to support unifying the richer dashboard_audit_log
    # writers into public.audit_log.  All three are optional so existing rows
    # (and callers) that never populate them deserialise unchanged.
    metadata: dict[str, Any] | None = None
    result: str | None = None
    error: str | None = None
    #: True when this row's free text was actually withheld above.  A silently
    #: blank ``note`` would read as "nothing was recorded"; this says "withheld
    #: — read it at the database", the same honesty the fleet's
    #: ``<thing>_available`` degraded flags provide elsewhere.  False for every
    #: non-credential row, and for a credential row that carried no free text.
    redacted: bool = False

    @model_validator(mode="after")
    def _withhold_credential_free_text(self) -> AuditLogEntry:
        """Blank the free-text columns of a credential-target row.

        Runs on every construction, not just :meth:`from_record`, so a future
        reader that builds this model straight from its own query cannot
        reintroduce the leak.
        """
        if not is_credential_target(self.target):
            return self
        if self.note is None and self.error is None and self.metadata is None:
            return self
        self.note = None
        self.error = None
        self.metadata = None
        self.redacted = True
        return self

    @classmethod
    def from_record(cls, row: object) -> AuditLogEntry:
        """Build an AuditLogEntry from an asyncpg Record.

        Projects the ``metadata``/``result``/``error`` columns (core_122).
        Reads defensively via ``KeyError`` (rather than assuming the column is
        always present) so a caller whose query happens to omit one of these
        three columns still deserialises cleanly with ``None`` in its place,
        instead of raising.
        """
        raw_ip = row["ip"]  # type: ignore[index]
        ip_str = str(raw_ip) if raw_ip is not None else None

        def _optional(key: str) -> Any:
            try:
                return row[key]  # type: ignore[index]
            except KeyError:
                return None

        return cls(
            id=row["id"],  # type: ignore[index]
            ts=row["ts"],  # type: ignore[index]
            actor=row["actor"],  # type: ignore[index]
            action=row["action"],  # type: ignore[index]
            target=row["target"],  # type: ignore[index]
            note=row["note"],  # type: ignore[index]
            ip=ip_str,
            request_id=row["request_id"],  # type: ignore[index]
            metadata=_coerce_metadata(_optional("metadata")),
            result=_optional("result"),
            error=_optional("error"),
        )


def _coerce_metadata(value: Any) -> dict[str, Any] | None:
    """Tolerantly coerce the raw ``metadata`` column value into a ``dict``.

    ``public.audit_log.metadata`` is JSONB and is contractually an object, but
    a since-fixed write path (2026-06-14 -> 07-05, bu-hmdqz.4) double-JSON-
    encoded the value for ~349k rows -- ``jsonb_typeof(metadata) = 'string'``
    for that whole band. asyncpg decodes a JSONB *string* scalar as a Python
    ``str`` (not a ``dict``), so the strict ``dict[str, Any] | None`` field
    below rejected every one of those rows with a pydantic ``ValidationError``
    -- surfaced to callers as an HTTP 500 that took the entire audit page down
    with it (e.g. ``GET /api/audit-log?actor=memory``).

    Handles exactly the three ``jsonb_typeof`` cases actually observed:
      - ``object``  -> already a dict, pass through unchanged.
      - ``string``  -> the poisoned case. The string is itself JSON text (the
        original writer effectively called ``json.dumps(json.dumps(data))``);
        decode it. If that inner text happens to decode to a dict, use it. If
        it decodes to something else (or isn't valid JSON at all -- some
        strings genuinely are not double-encoded), wrap it losslessly under
        ``_raw`` rather than raising, so the row still renders.
      - ``null``    -> already ``None``, pass through unchanged.

    A batched repair migration (core_169) normalizes the *stored* rows using
    this exact same fallback shape, so a row's rendered metadata is identical
    whether or not the migration has run yet against that particular row.
    """
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {"_raw": value}
        if isinstance(parsed, dict):
            return parsed
        return {"_raw": value}
    # Any other shape (list/int/float/bool) is outside the three known
    # jsonb_typeof cases this repair covers -- let pydantic's normal
    # validation reject it loudly rather than silently reshaping an
    # unanticipated type.
    return value
