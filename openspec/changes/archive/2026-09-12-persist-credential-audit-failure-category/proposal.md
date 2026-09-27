# Persist the credential failure category so audit groups are identified by cause

## Why

`project-issue-group-identity-credential-blind` made credential-target
audit-error groups content-blind: the group title is
`Credential <action>: <target> (diagnostic withheld)`, built only from columns
already published for that row. It bought that with a stated cost — *identity
is the credential, not the cause* — and recorded why the cost was unavoidable:

> the only per-cause signal on the row is the withheld text itself.

That was true of the row **as it was being written**, not of the failure. The
two probe endpoints already derive a cause: `_probe_category` maps a
`probe_status` token plus the provider's HTTP status code onto a member of
`PROBE_FAILURE_VOCABULARY`, and publishes it as `TestResult.message`. But the
audit row got only the free text. The category was computed at *response* time
and thrown away; the token survived solely inside the withheld `note` as
`probe_status=<token>`.

So the cause is unrecoverable at read time only because nobody stored it. The
consequence is real: a rate-limited probe and a rejected credential on
`u:google` share one group, one occurrence count, and one acknowledgement.
Dismissing "Google is throttling us" silently dismisses "Google no longer
accepts this credential".

Recovering it by parsing `note` at read time is the exact inversion the
credential-blind work exists to prevent. The fix is to stop discarding it.

## The decision

> The derived `PROBE_FAILURE_VOCABULARY` member is persisted at **write** time
> in its own column, `public.audit_log.failure_category`, and the credential
> group title is built from `action`, `target`, **and** that column. Nothing
> outside the vocabulary may be stored, and the column is not published on the
> wire.

Three properties make this safe rather than a reopening of the leak:

- **Selected, not derived.** The stored value is chosen out of a closed
  eight-member tuple. A new provider message, a new `probe_status` token, or a
  new HTTP status code cannot widen what escapes; an unclassifiable failure
  lands on `other`. This is owner Option C applied at rest.
- **Structurally enforced.** `core_202` adds a CHECK constraint over the same
  eight members, so a writer that bypasses `audit.append()` — a job, a psql
  session, a migration — still cannot put a raw token in the column.
  `clamp_failure_category` handles the application half, collapsing a stray
  value to `other` rather than raising, because an audit write is
  fire-and-forget and a producer bug must not delete the record of a failure.
- **Not published.** `AuditLogEntry` gains no field and the occurrences
  drill-down projects no new column. `models/audit.py` remains the single
  enforcement point for what a credential row discloses. Persisting the cause
  changes how rows *group*; it is not licence to widen what each row *says*.

## Two decisions this change writes down

**Producers with no category write `NULL`, and that is complete, not partial.**
The credential-audit surface has five writer helpers and roughly twenty-six call
sites, but only **nine** can write a `result = 'error'` row at all: the two
probe endpoints in `routers/secrets_v2.py` and seven `action='failed'` sites in
`routers/oauth.py`. Every other site writes a success action, or raises
`HTTPException` before reaching the audit write. The grouping CTE reads only
`result = 'error'` rows, so `failure_category` is populated on every row it can
ever see. All nine now name a category, and
`tests/api/test_audit_failure_category_producers.py` enumerates the whole set
statically so a tenth failure path cannot be added without one.

The two probe sites pass the category they already derived. The seven OAuth
sites pass a **literal**, because a callback knows its own cause without reading
anything a provider wrote: a refused authorization is `rejected`, a failed token
exchange or userinfo call is `provider_error`, a missing refresh token is
`not_set`, and an unavailable credential store is `other` — local infrastructure,
not a verdict on the credential.

**Pre-existing rows keep `NULL` and keep their current group. They are not
backfilled.** The only place a historic row's cause survives is the withheld
`note`, so backfilling means parsing exactly the text this capability withholds.
Instead the title concatenation is `COALESCE(' [' || failure_category || ']',
'')`: Postgres makes the inner expression `NULL` for an uncategorised row, so it
renders the **byte-identical** pre-change string, keeps its `error_summary`,
keeps its `group_key`, and keeps every acknowledgement already attached to it.

The accepted cost is a transitional duplicate: a credential that was failing
before the migration and keeps failing after it shows two groups for one window
— the frozen legacy group and the new categorised one. The legacy group stops
growing and ages out. One duplicate per still-failing credential, bounded by the
window, is cheaper than either parsing withheld text or orphaning every existing
acknowledgement.

## Rejected alternatives

- **Parse `probe_status=<token>` out of `note` at read time.** Recovers the
  cause for historic rows too, and is exactly the inversion the credential-blind
  requirement exists to prevent: the grouping CTE would be reading the column
  `AuditLogEntry` withholds, and one malformed note would put provider text in a
  group title.
- **Store the raw `probe_status` token or the provider's HTTP status code.**
  Both are derived from what the provider said, so the set of storable strings
  is defined by the provider rather than by this repository. Neither can be
  CHECK-constrained.
- **Backfill the new column from the old free text.** Same objection as parsing
  at read time, made permanent and unauditable.
- **Give the column a `NOT NULL DEFAULT 'other'`.** Would rewrite every historic
  row's group title, changing its `group_key` and orphaning every existing
  acknowledgement — to say nothing more than `NULL` already says.
- **Publish `failure_category` on `AuditLogEntry`.** Tempting, since the value
  is safe by construction. Out of scope here: widening the wire projection is a
  disclosure decision for `dashboard-audit-log`'s withholding requirement, not a
  side effect of a grouping change.

## Impact

- Migration `core_202`: `public.audit_log.failure_category TEXT NULL` plus the
  `audit_log_failure_category_vocabulary` CHECK constraint. Additive; no
  backfill; reversible.
- `PROBE_FAILURE_VOCABULARY` and the new `clamp_failure_category` move to
  `api/models/audit.py` so `routers/secrets_v2` and `routers/audit` share one
  definition without a router-imports-router edge. `secrets_v2` re-exports the
  tuple, so every existing reader keeps resolving.
- `audit.append()` accepts and clamps `failure_category`; the three
  `secrets_v2` writers and `_emit_oauth_audit` forward it.
- The shared `normalized_errors` CTE reads the column. Uncategorised rows'
  titles are unchanged byte-for-byte, so no existing group moves.
