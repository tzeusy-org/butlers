# Identify credential audit-error groups without provider free text

## Why

`GET /api/issues` groups `public.audit_log` error rows by their normalized
error text, and that text **is** the group's identity: `error_summary` is the
only column `grouped_errors` is `GROUP BY`'d on, it ships as
`Issue.error_message`, folds into the composed `description`, hashes into
`issue_key`, and binds as `$1` when the occurrences drill-down re-derives the
same group. The briefing's attention items come off the same rows.

For a credential-target row that string is the provider's own failure text.
`_write_credential_audit` passes the raw probe message through
`credential_lifecycle_outcome`, which maps `action == 'failed'` to
`("error", error)`, so `"Probe failed: <provider text>"` lands in the `error`
column and becomes the group title.

bu-ove06 stopped `AuditLogEntry` publishing that column per row and
deliberately did **not** extend the fix here, because a group title cannot be
blanked the way a row's note can. This change closes the surface one door
further out.

## The decision

> A credential-target error row's group identity is a synthetic title composed
> only from columns that are already published for that row — its `action` and
> its `target` — rather than from its error text. Every other row keeps
> grouping on its normalized error verbatim.

Concretely, the shared `normalized_errors` CTE computes
`Credential <action>: <target> (diagnostic withheld)` for rows matching the
credential-target predicate, and the existing first-line/temp-path
normalization for everything else. The rule lives in the CTE, not in a router,
so all four consumers — the Issues feed, the briefing, the occurrences
drill-down, and the audit-row-to-group resolver — inherit one definition and
cannot disagree about a group's title.

Two properties follow, and both are load-bearing:

- **Distinguishable.** `u:google` and `u:notion` remain two groups with two
  occurrence counts and two acknowledgements.
- **Content-blind.** `action` and `target` are exactly the columns bu-ove06
  keeps publishing on a credential row, so the title re-publishes nothing that
  requirement withholds.

The accepted cost is that identity is the **credential, not the cause**: a 401
and a 429 on the same credential fold into one group. That is unavoidable under
a content-blind identity, because the only per-cause signal on the row is the
withheld text itself. The occurrence count stays truthful and the per-occurrence
detail is still readable at `public.audit_log` and `public.secret_probe_log`.

### Rejected alternatives

- **Group on a `PROBE_FAILURE_VOCABULARY` token plus the target namespace**
  (the direction the bead proposed). Rejected on evidence: the token is not a
  column. It exists only inside the `note` free text as `probe_status=<token>`,
  and only two of the ten endpoints that write credential audit rows put it
  there — `probe_user_credential` (via `_write_credential_audit`) and
  `probe_system_credential` (via `_write_system_audit`). The other eight
  (rotate, disconnect, reauthorize, set, delete, across the user, system and
  CLI namespaces) never emit a token at all, so a token-keyed identity would
  have nothing to key on for most credential failures. The token itself
  (`live_failed:403`) is also **not** a vocabulary member —
  `_probe_failure_category` derives the category at *response* time from that
  token plus the provider's HTTP status code, and the code is never persisted
  on the audit row. Reconstructing it on read would mean substring-parsing the
  very free text this rule withholds, which inverts owner Option C: the
  published value must be selected out of a closed vocabulary, never derived
  from an input string. Persisting the category at write time is a legitimate
  future move, but it is a five-producer change that still leaves every
  historic row uncategorised, so it is a follow-up rather than this fix.
- **Blank the summary to a constant.** Rejected: every credential failure in
  the fleet would collapse into one group, with one occurrence count and one
  acknowledgement covering unrelated broken credentials. That breaks a working
  surface to fix a leak, which is the trade this bead exists to avoid.
- **Hash the error text into an opaque identity.** Rejected: it withholds the
  text and keeps groups distinct, but the operator is then shown a row they
  cannot act on — an unreadable token where a title belongs — and the briefing
  repeats it. Content blindness should cost detail, not meaning.
- **Normalise the scope prefix (`user:` → `u:`) before grouping.** The `target`
  column is never normalised on write, so in principle one credential could
  fork into two groups. Rejected as unnecessary: every live producer builds its
  target through `normalize_credential_key`, so the long spellings are
  historical/defensive only — and the predicate still *matches* them, so the
  fork's worst case is a duplicate group, never a leak. Adopting it would copy
  `credential_keys._SCOPE_TO_PREFIX` into SQL as a second source of truth,
  which is the drift the shared predicate was introduced to prevent.

This is an engineering call, not an owner call. The owner decision it
implements already exists (Option C, 2026-08-13, carried by bu-nz4sn, bu-m9s61,
bu-rh8z5 and bu-ove06); this change only decides how a group is named once its
error text is off the wire.

## What Changes

- A new `dashboard-audit-log` requirement, **Credential-Target Audit Groups Are
  Identified Without Free Text**, states the rule, its two load-bearing
  properties, and the non-credential carve-out.
- `normalized_errors` in `src/butlers/api/audit_grouping.py` computes
  `error_summary` through a `CASE` on the credential-target predicate.
- `CREDENTIAL_TARGET_PATTERN` is exported from
  `src/butlers/api/models/audit.py` and embedded in the CTE, so the model's
  withholding rule and the grouping rule share one definition of "this target
  names a credential". The syntax is a common subset of Python `re` and
  Postgres ARE, so one literal serves both.
- Tests: DB-free shape tests pin the credential branch, the shared predicate,
  and that all three query builders emit the same CTE; a migrated-Postgres
  module writes a synthetic provider failure string to `public.audit_log` and
  asserts it appears nowhere in `GET /api/issues` or
  `GET /api/issues/{key}/occurrences`, for every credential-key spelling.

## Delta placement

The rule is an **ADDED** requirement on `dashboard-audit-log` rather than a
MODIFIED block on `dashboard-api` §`Issues Aggregation`, because
`durable-issue-condition-ledger` already holds an unarchived MODIFIED block on
that exact requirement:

```
rg -l '^### Requirement: Issues Aggregation$' openspec/changes/*/specs/*/spec.md
#   openspec/changes/durable-issue-condition-ledger/specs/dashboard-api/spec.md
```

Two unarchived deltas on one requirement clobber each other at archive time. It
lands on `dashboard-audit-log` because it is the direct continuation of that
capability's **Credential-Target Audit Free Text Is Withheld On Read**
(bu-ove06) — the same namespace rule, moved from a row's columns to a group's
identity — and because the grouping CTE it governs is shared by surfaces in two
other capabilities. An ADDED requirement deletes no live clause, so
`scripts/check_spec_overwrites.py` has nothing to flag and no baseline freeze is
needed.

## Impact

- Affected specs: `dashboard-audit-log`
- Affected code: `src/butlers/api/audit_grouping.py`,
  `src/butlers/api/models/audit.py`, `tests/api/test_audit_grouping.py`,
  `tests/api/test_audit_grouping_credential_blind_db.py`
- Behavioural change for existing credential groups: their `error_summary`
  changes, so their `issue_key` changes, so an existing acknowledgement of such
  a group in `public.dismissed_issues` stops matching and the group reappears
  once. That is correct — the acknowledgement was made against an identity that
  no longer exists — and it is a one-time effect confined to the credential
  namespace.
- No frontend change: the Issues page renders `error_message` as given.
