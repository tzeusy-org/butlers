"""Shared audit-error grouping logic for the dashboard.

Both the Issues router and the Briefing router aggregate audit-log errors by
normalized first-line message — except credential-target rows, which group on a
synthetic content-blind title instead (see below). This module owns the shared
CTE SQL and the row-to-domain projection helpers so the two consumers stay in
sync.

Key normalization rule
----------------------
Temporary-path prefixes like ``/tmp/tmpABC123/`` are collapsed to ``/tmp/.../``
before grouping. Without this step, the same underlying error produces a
distinct group for every ephemeral temp directory, inflating the issues count
and making the Issues page and the Briefing disagree on the number of distinct
problems.

Credential-target groups are identified without free text (bu-uqipv)
--------------------------------------------------------------------
A group's identity *is* its ``error_summary``, and for a credential-target row
that string was the provider's own failure text: ``_write_credential_audit``
hands the raw probe message to ``credential_lifecycle_outcome``, which stores it
in ``error``. So the text ``AuditLogEntry`` stopped publishing (bu-ove06) came
straight back out as a group title, in ``Issue.error_message``, in the composed
``description``, and in the briefing attention item.

It could not simply be blanked: a constant summary would collapse every
credential failure in the fleet into one group with one occurrence count and one
acknowledgement covering unrelated broken credentials. Instead, credential-target
rows group on a synthetic title built only from columns that cannot carry a
provider's words: ``action`` and ``target``, both of which bu-ove06 publishes
unchanged so the row stays identifiable and ``?key=``-filterable, plus
``failure_category``, which is CHECK-constrained at rest to a closed vocabulary
(see the next section). The result reads
``Credential failed: u:google [rejected] (diagnostic withheld)``: distinct per
credential and per cause, stable across windows, and derived from nothing a
provider wrote.

The identity is the credential AND its persisted cause (bu-vhie6)
-----------------------------------------------------------------
bu-uqipv shipped with the credential alone as the identity, so a 401 and a 429
on ``u:google`` folded into one group. That was not a preference for coarse
grouping: the cause simply was not a column. It survived only inside the
``note`` free text as ``probe_status=<token>``, the token
(``live_failed:403``) is not a vocabulary member anyway, and the category was
derived at *response* time from that token plus a provider HTTP code that was
never persisted. Recovering it here would have meant substring-parsing the very
text this rule withholds.

Migration ``core_202`` makes it a column. ``public.audit_log.failure_category``
holds a :data:`~butlers.api.models.audit.PROBE_FAILURE_VOCABULARY` member,
written at INSERT time and CHECK-constrained at rest, so the title below can
name the cause while reading nothing a provider wrote:

    ``Credential failed: u:google [rejected] (diagnostic withheld)``

Two decisions the column forces, both deliberate:

**Producers with no category write NULL, and that is complete rather than
partial.** Across the fleet, credential-target audit rows come from five writer
helpers (``_write_credential_audit`` / ``_write_system_audit`` /
``_write_cli_audit`` in ``routers/secrets_v2``, ``_emit_oauth_audit`` in
``routers/oauth``, and a direct ``audit.append`` in ``jobs/secrets_lifecycle``)
at 26 call sites. Only **nine** of those sites can write a ``result = 'error'``
row at all, and every one of the nine now names a category: the two probe
endpoints derive it from their own ``probe_status`` plus HTTP code, and the
seven OAuth callback sites select a literal, because a callback knows its own
cause without any token. The remaining sites write success rows
(``rotated``/``disconnected``/``attempted``/``set``/``overrode``/``revoked``/
``verified``) or a ``delivered`` debounce marker, and fail by raising before
they ever reach the audit write. This grouping CTE only ever sees
``result = 'error'``, so ``failure_category`` is populated on every row it can
reach. ``tests/api/test_audit_failure_category_producers.py`` enumerates all
five helpers and every call site to keep that true.

**Historic rows keep NULL and keep their current group.** They are not
backfilled: the only place their cause was recorded is the withheld free text,
and parsing it is the inversion this change exists to prevent. The ``COALESCE``
below therefore renders an uncategorised row with the *byte-identical* title
bu-uqipv gave it, so its ``group_key`` (a sha256 of the summary) is unchanged
and an existing acknowledgement still covers it. After the migration a
credential that keeps failing opens one new categorised group beside its legacy
uncategorised one; the legacy group stops growing and ages out of the window.
One transitional duplicate per credential is the price of never reading the old
text.

Per-occurrence detail remains readable at ``public.audit_log`` and
``public.secret_probe_log``: this is content blindness on the wire, not
destruction of evidence.

Alternatives rejected
~~~~~~~~~~~~~~~~~~~~~
- **Parse the ``probe_status=<token>`` out of ``note`` at read time.** The
  original bu-uqipv rejection, and still correct: the published value must be
  selected out of a closed vocabulary, never derived from an input string.
  Persisting the category at write time is what removed the question.
- **Backfill ``failure_category`` for existing rows.** Same objection with the
  parse moved into a migration. Rejected.
- **Blank the summary to a constant.** Rejected: it breaks a working surface,
  per above.
- **Hash the error text into an opaque identity.** Rejected: it withholds the
  text and keeps the groups distinct, but the feed then shows an operator a row
  they cannot act on — an unreadable token where a title belongs — and the
  briefing would repeat it. Content blindness should cost detail, not meaning.
- **Normalise the scope prefix (``user:`` -> ``u:``) before grouping.** The
  ``target`` column is never normalised on write, so in principle one credential
  could fork into two groups. Rejected as unnecessary: every live producer
  builds its target through ``normalize_credential_key``, so the long spellings
  are historical/defensive only — and the predicate still *matches* them, so
  the fork's worst case is a duplicate group, never a leak. Doing it would
  copy ``credential_keys._SCOPE_TO_PREFIX`` into SQL as a second source of
  truth, which is the drift this module just spent a shared pattern avoiding.

Because the rule lives in the shared CTE below, the Issues feed, the briefing,
the occurrences drill-down, and the audit-row resolver all inherit it by
construction and cannot disagree about a group's title.

Severity model
--------------
- ``critical`` — error originated from a **scheduled** session
  (trigger_source starts with ``schedule:``)
- ``warning`` — all other errors

Callers apply their own window/LIMIT constraints after the CTE.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlencode

from butlers.api.models import Issue
from butlers.api.models.audit import CREDENTIAL_TARGET_PATTERN

# ---------------------------------------------------------------------------
# SQL building-block
# ---------------------------------------------------------------------------

#: Shared CTE fragment carrying every raw ``public.audit_log`` column plus the
#: normalized grouping columns (``error_summary``/``is_schedule``/
#: ``schedule_name``). Two consumers build on top of this same fragment so the
#: grouping definition (what counts as "the same error") can never drift
#: between them:
#:   - ``build_audit_group_query`` aggregates it into ``grouped_errors`` (one
#:     row per distinct error, for the Issues/Briefing feeds).
#:   - ``build_audit_group_occurrences_query`` selects the raw per-row
#:     occurrences behind one already-identified group (for the Issues page's
#:     "Seen Nx" drill-down).
#:
#: Canonical source (bu-j26e8)
#: ---------------------------
#: All audit rows live in the canonical ``public.audit_log`` primitive (which
#: gained ``metadata``/``result``/``error`` columns in core_122).  The historical
#: Switchboard ``dashboard_audit_log`` rows were backfilled into the canonical
#: table by migration core_124, so the legacy UNION arm was removed and
#: ``audit_source`` reads ``public.audit_log`` alone.  It projects the canonical
#: columns onto the legacy column names the downstream grouping logic expects —
#: ``butler, created_at, error, operation, request_summary, result`` — so the
#: inner ``WHERE result = 'error'{where_extra}`` filter (callers reference
#: ``created_at``) and the trigger-source / operation semantics keep working
#: unchanged.
#:
#: Column mapping for the canonical side:
#:   actor    -> butler          action   -> operation
#:   ts       -> created_at      metadata -> request_summary
#:   error    -> error           result   -> result
_AUDIT_NORMALIZED_CTE_SRC = """
WITH audit_source AS (
    SELECT
        id,
        actor AS butler,
        ts AS created_at,
        target,
        note,
        ip,
        request_id,
        error,
        action AS operation,
        COALESCE(metadata, '{{}}'::jsonb) AS request_summary,
        result,
        failure_category
    FROM public.audit_log
),
normalized_errors AS (
    SELECT
        id,
        butler,
        created_at,
        target,
        note,
        ip,
        request_id,
        error,
        operation,
        request_summary,
        result,
        failure_category,
        CASE
            WHEN target ~ '__CREDENTIAL_TARGET_PATTERN__' THEN
                'Credential ' || operation || ': ' || target
                || COALESCE(' [' || failure_category || ']', '')
                || ' (diagnostic withheld)'
            ELSE COALESCE(
                NULLIF(BTRIM(
                    REGEXP_REPLACE(
                        SPLIT_PART(error, E'\\n', 1),
                        '/tmp/tmp[a-zA-Z0-9_]+/',
                        '/tmp/.../',
                        'g'
                    )
                ), ''),
                'Unknown error'
            )
        END AS error_summary,
        (
            operation = 'session'
            AND COALESCE(request_summary->>'trigger_source', '') LIKE 'schedule:%'
        ) AS is_schedule,
        NULLIF(
            SPLIT_PART(COALESCE(request_summary->>'trigger_source', ''), ':', 2),
            ''
        ) AS schedule_name
    FROM audit_source
    WHERE result = 'error'{where_extra}
)
"""

#: ``_AUDIT_NORMALIZED_CTE_SRC`` carries the credential predicate as a sentinel
#: rather than an inline literal so the pattern has exactly one definition
#: (:data:`~butlers.api.models.audit.CREDENTIAL_TARGET_PATTERN`).  A plain
#: ``.replace`` is used instead of ``.format`` because the template's remaining
#: braces belong to ``{where_extra}`` and to ``'{{}}'::jsonb``.
_AUDIT_NORMALIZED_CTE = _AUDIT_NORMALIZED_CTE_SRC.replace(
    "__CREDENTIAL_TARGET_PATTERN__", CREDENTIAL_TARGET_PATTERN
)

_GROUPED_CTE_TAIL = """,
grouped_errors AS (
    SELECT
        error_summary,
        MIN(created_at) AS first_seen_at,
        MAX(created_at) AS last_seen_at,
        COUNT(*)::int AS occurrences,
        ARRAY_AGG(DISTINCT butler ORDER BY butler) AS butlers,
        BOOL_OR(is_schedule) AS has_schedule,
        ARRAY_REMOVE(
            ARRAY_AGG(DISTINCT schedule_name ORDER BY schedule_name),
            NULL
        ) AS schedule_names
    FROM normalized_errors
    GROUP BY error_summary
)
SELECT * FROM grouped_errors
ORDER BY last_seen_at DESC{limit_clause}"""

_OCCURRENCES_SELECT = """
SELECT
    id,
    created_at AS ts,
    butler AS actor,
    operation AS action,
    target,
    note,
    ip,
    request_id,
    request_summary AS metadata,
    result,
    error
FROM normalized_errors
WHERE error_summary = $1
  AND butler = ANY($2::text[])
ORDER BY created_at DESC
LIMIT $3 OFFSET $4"""


def build_audit_group_query(
    *,
    where_extra: str = "",
    limit: int | None = None,
) -> str:
    """Return a complete SELECT query using the shared audit grouping CTE.

    Args:
        where_extra: Extra SQL appended to the inner WHERE clause after
            ``result = 'error'``.  Must start with a newline + whitespace and
            a SQL keyword, e.g. ``"\\n                  AND created_at >= ..."``.
        limit: If given, caps the newest grouped rows in the final result.

    Returns:
        A complete SQL string ready to be passed to ``pool.fetch()``.
    """
    limit_clause = f"\n    LIMIT {int(limit)}" if limit is not None else ""
    normalized_cte = _AUDIT_NORMALIZED_CTE.format(where_extra=where_extra)
    return normalized_cte + _GROUPED_CTE_TAIL.format(limit_clause=limit_clause)


def build_audit_group_occurrences_query() -> str:
    """Return SQL for the raw ``audit_log`` rows behind one already-identified group.

    Reuses the exact same ``normalized_errors`` CTE that :func:`build_audit_group_query`
    groups, so a group's occurrences can never disagree with its own definition
    (JARVIS audit move 6 — "Seen 47x" issue groups otherwise offer no drill-down
    path to their occurrences).

    ``grouped_errors`` groups by ``error_summary`` ALONE (``has_schedule`` and
    ``butlers`` are only aggregates *over* that group, not part of its
    identity) — so this query filters on ``error_summary`` alone too. It does
    NOT additionally filter on ``is_schedule``: a group can legitimately mix
    scheduled and non-scheduled rows behind the same normalized error message,
    and filtering occurrences on the group's aggregated ``has_schedule`` flag
    would silently drop the rows on the other side of that flag, while the
    group's reported ``occurrences`` count keeps including them (undercounting
    the drill-down page relative to the total it claims).

    Callers bind exactly four positional parameters, in order:
        1. exact-match normalized ``error_summary`` (the group's
           ``Issue.error_message``)
        2. ``text[]`` of butler names to restrict to (the group's
           ``Issue.butlers`` — a single-element array for a single-butler
           group, or the full list for a multi-butler group; this is a
           redundant-but-harmless restriction since every row grouped under
           this ``error_summary`` already comes from a butler in that list)
        3. ``LIMIT``
        4. ``OFFSET``

    Returns:
        A complete SQL string ready to be passed to ``pool.fetch()``. Each row
        carries every column of the ``AuditLogEntry`` model (id/ts/actor/
        action/target/note/ip/request_id/metadata/result/error).
    """
    normalized_cte = _AUDIT_NORMALIZED_CTE.format(where_extra="")
    return normalized_cte + _OCCURRENCES_SELECT


_GROUP_FOR_ROW_TAIL = """,
target_row AS (
    SELECT error_summary FROM normalized_errors WHERE id = $1
),
windowed AS (
    SELECT n.*
    FROM normalized_errors n
    JOIN target_row t ON t.error_summary = n.error_summary
    WHERE ($2::timestamptz IS NULL OR n.created_at >= $2)
)
SELECT
    error_summary,
    MIN(created_at) AS first_seen_at,
    MAX(created_at) AS last_seen_at,
    COUNT(*)::int AS occurrences,
    ARRAY_AGG(DISTINCT butler ORDER BY butler) AS butlers,
    BOOL_OR(is_schedule) AS has_schedule,
    ARRAY_REMOVE(
        ARRAY_AGG(DISTINCT schedule_name ORDER BY schedule_name),
        NULL
    ) AS schedule_names
FROM windowed
GROUP BY error_summary"""


def build_audit_group_for_row_query() -> str:
    """Return SQL resolving ONE ``public.audit_log`` row id to its current group.

    The exact Audit -> Issues evidence door (bu-6jv4m.3). The Audit Log
    previously linked a failure row to ``/issues?q=<first line of the error>``,
    which reconstructed the grouping key client-side (approximately) and then
    substring-matched a feed already bounded by its own default window. This
    resolves the same question the way the feed itself does -- through the
    shared ``normalized_errors`` CTE -- so the answer can never disagree with
    the group the Issues page would show.

    Two parameters, in order:
        1. the ``public.audit_log`` row id to resolve
        2. window lower bound as ``timestamptz``, or ``NULL`` for all history

    ``target_row`` finds the row's normalized ``error_summary`` over ALL
    history (a row outside the requested window still has an identity), then
    ``windowed`` re-derives that one group under the caller's bound. The result
    is at most one row, shaped exactly like ``build_audit_group_query``'s so
    :func:`issue_from_audit_group_row` projects it unchanged. **Zero rows is a
    real answer** -- the row is not a failure, or its group has no occurrences
    inside the window -- and the caller must report which, not render the
    emptiness as calm.
    """
    normalized_cte = _AUDIT_NORMALIZED_CTE.format(where_extra="")
    return normalized_cte + _GROUP_FOR_ROW_TAIL


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------

_ISSUE_TYPE_MAX_LEN = 80


def _slug(value: str) -> str:
    """Build a short, deterministic slug suitable for the *display* ``type`` field.

    NOT used for the group's identity key -- see :func:`audit_group_key`. The
    80-char truncation here is purely cosmetic (keeps ``Issue.type`` short and
    readable); two different error messages that happen to share the same
    first 80 characters after slugifying are a fine collision for a label,
    but were an active bug when this slug was *also* the group's key (bu-hmdqz.4).
    """
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized:
        return "unknown"
    return normalized[:_ISSUE_TYPE_MAX_LEN]


def audit_group_key(error_summary: str) -> str:
    """Return the stable, collision-resistant identity key for an audit-error group.

    bu-hmdqz.4 re-key: a group's true identity is the full, untruncated,
    normalized ``error_summary`` alone -- that's the exact (and only) column
    ``grouped_errors`` is ``GROUP BY``'d on (see the module docstring). This
    hashes that full string, so:

    - Two distinct long error messages that happen to share an identical
      first-80-character slug (previously collapsed onto the same
      ``issue_key`` via :func:`_slug`'s truncation -- observed live: two
      unrelated ``RuntimeError`` groups with 166 vs 2,860 occurrences sharing
      one key, so acking one silently acked the other) now always produce
      different keys.
    - The key excludes the butler-set (``ARRAY_AGG(DISTINCT butler)``) and
      schedule-name aggregates entirely. Both are aggregates *over* the
      error_summary group, not part of its identity, and both are
      window-dependent: the same group can be single-butler in a narrow
      window and multi-butler in a wider one (observed live: a feed query
      computing ``butler="switchboard"`` while the occurrences drill-down's
      all-time re-derivation computed ``butler="multiple"`` for the *same*
      group -- the keys disagreed and the drill-down 404'd on a group the
      feed had just shown).

    Stable across windows, aggregation runs, and query re-derivations, since
    it depends on nothing but the group's own normalized error text.
    """
    digest = hashlib.sha256(error_summary.encode("utf-8")).hexdigest()[:16]
    return f"audit_error_group:{digest}"


def issue_from_audit_group_row(row: object) -> Issue:
    """Map one grouped audit row into an :class:`~butlers.api.models.Issue`.

    This is the authoritative severity model for audit-derived issues:
    - ``critical`` for scheduled-task failures
    - ``warning`` for ad-hoc errors
    """
    error_message = str(row["error_summary"])  # type: ignore[index]
    butlers = [str(b) for b in (row["butlers"] or [])]  # type: ignore[index]
    if not butlers:
        butlers = ["unknown"]

    schedule_names = [str(name) for name in (row["schedule_names"] or [])]  # type: ignore[index]
    has_schedule = bool(row["has_schedule"])  # type: ignore[index]

    if has_schedule:
        severity = "critical"
        issue_type = (
            f"scheduled_task_failure:{_slug(schedule_names[0])}"
            if len(schedule_names) == 1
            else "scheduled_task_failure:multiple"
        )
        if len(schedule_names) == 1 and len(butlers) == 1:
            description = (
                f"Scheduled task '{schedule_names[0]}' failure on '{butlers[0]}': {error_message}"
            )
        elif len(schedule_names) == 1:
            description = (
                f"Scheduled task '{schedule_names[0]}' failures across "
                f"{len(butlers)} butlers: {error_message}"
            )
        elif len(butlers) == 1:
            description = f"Scheduled task failures on '{butlers[0]}': {error_message}"
        else:
            description = f"Scheduled task failures across {len(butlers)} butlers: {error_message}"
    else:
        severity = "warning"
        issue_type = f"audit_error_group:{_slug(error_message)}"
        if len(butlers) == 1:
            description = f"{error_message} ({butlers[0]})"
        else:
            description = f"{error_message} ({len(butlers)} butlers)"

    butler = butlers[0] if len(butlers) == 1 else "multiple"

    # Param names must match what GET /api/audit-log and AuditLogPage's filter
    # bar actually read (`actor`, `action`, `result`) — not `butler`/
    # `operation`, which nothing on the consuming end recognizes (the link
    # would silently land on an unfiltered audit log). AuditLogPage hydrates
    # its initial filter state directly from these query-string keys.
    #
    # `result=error` is always present (JARVIS audit move 6): every row this
    # function projects came from a `result = 'error'` group, so a
    # multi-butler, non-scheduled group — the one case with no other
    # disambiguating param — previously emitted a bare `/audit-log` with no
    # predicate at all. That link now still narrows to error rows even when
    # actor/action cannot be pinned to a single value.
    link_params: dict[str, str] = {"result": "error"}
    if len(butlers) == 1:
        link_params["actor"] = butlers[0]
    if has_schedule:
        link_params["action"] = "session"
    link = f"/audit-log?{urlencode(link_params)}"

    return Issue(
        severity=severity,
        type=issue_type,
        butler=butler,
        description=description,
        link=link,
        error_message=error_message,
        occurrences=int(row["occurrences"] or 1),  # type: ignore[index]
        first_seen_at=row["first_seen_at"],  # type: ignore[index]
        last_seen_at=row["last_seen_at"],  # type: ignore[index]
        # For this lane the recurrence epoch IS the newest occurrence: a newer
        # error under the same normalized message is, by definition, a new
        # occurrence that should lapse an acknowledgement (bu-6jv4m.3 made the
        # epoch explicit; the behaviour here is core_152's, unchanged).
        recurrence_at=row["last_seen_at"],  # type: ignore[index]
        butlers=butlers,
        # bu-hmdqz.4: the group's identity key is the hash of the full
        # error_summary alone -- NOT compute_issue_key(type, butler), which
        # would re-introduce the 80-char slug truncation collision and the
        # window-dependent butler-set drift. See audit_group_key's docstring.
        group_key=audit_group_key(error_message),
    )


def attention_item_from_audit_group_row(row: object) -> dict:
    """Map one grouped audit row into a briefing attention-item dict.

    Uses the same severity model as :func:`issue_from_audit_group_row` —
    scheduled-task errors become ``"high"`` (briefing maps ``"critical"`` to
    ``"high"`` for display), ad-hoc errors become ``"medium"``.

    The briefing attention-item shape is intentionally a flat dict (not the
    :class:`~butlers.api.models.Issue` Pydantic model) so the briefing router
    can extend it with ``source`` and ``link`` without coupling the Issue model
    to briefing-specific fields.
    """
    error_summary = str(row["error_summary"])  # type: ignore[index]
    butlers_raw = [str(b) for b in (row["butlers"] or [])]  # type: ignore[index]
    butlers = butlers_raw or ["unknown"]
    has_schedule = bool(row["has_schedule"])  # type: ignore[index]

    # Map to briefing severity scale: critical -> high, warning -> medium.
    severity = "high" if has_schedule else "medium"
    issue_type = "scheduled_task_failure" if has_schedule else "audit_error_group"

    if len(butlers) == 1:
        description = f"{error_summary} ({butlers[0]})"
        butler = butlers[0]
    else:
        description = f"{error_summary} ({len(butlers)} butlers)"
        butler = "multiple"

    first_seen_at = row["first_seen_at"]  # type: ignore[index]
    last_seen_at = row["last_seen_at"]  # type: ignore[index]

    return {
        "severity": severity,
        "type": issue_type,
        "butler": butler,
        "description": description,
        "link": "/audit-log",
        "error_message": error_summary,
        "occurrences": int(row["occurrences"] or 1),  # type: ignore[index]
        "first_seen_at": (
            first_seen_at.isoformat()
            if hasattr(first_seen_at, "isoformat")
            else (str(first_seen_at) if first_seen_at is not None else None)
        ),
        "last_seen_at": (
            last_seen_at.isoformat()
            if hasattr(last_seen_at, "isoformat")
            else (str(last_seen_at) if last_seen_at is not None else None)
        ),
        "butlers": butlers,
        "source": "audit_log",
    }
