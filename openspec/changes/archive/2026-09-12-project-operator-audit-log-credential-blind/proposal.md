# Withhold credential-target free text from the operator audit log

## Why

`GET /api/audit-log` and `GET /api/audit-log/{id}` select
`id, ts, actor, action, target, note, ip, request_id, metadata, result, error`
from `public.audit_log` with no projection, no redaction, and no filtering by
`target`. Credential rows live in that table: `_write_credential_audit` writes
`target = u:<provider>` together with a `note` of
`"Probe failed: <provider text>; probe_status=<token>"` and the raw failure
message in `error`. So a probe failure tail is readable through this surface.

The siblings closed the secrets surfaces around it. bu-nz4sn made the probe
responses content-blind, bu-m9s61 and `project-secret-read-endpoints-content-blind`
did the per-credential reads, and bu-rh8z5 did the secrets audit-history
endpoint. Each of those narrowed the leak; none closed it, because the identical
text stayed reachable one router over. Worse, the secrets audit endpoint's own
`meta.deep_link` points the operator at `/audit-log?key=<canonical-key>` — the
fix ships with a signpost to the way around itself.

## The decision

**This is not a copy of the bu-rh8z5 direction, and it is deliberately not a
blanket one.** The general operator audit log has a real forensic claim on its
free text that a credential passport does not, so the rule is scoped to the
credential target namespaces:

> Credential-target rows (`u:`/`s:`/`c:` and their long-scope spellings) are
> published without `note`, `error`, or `metadata`. Every other row keeps its
> free text verbatim.

Three alternatives were considered and rejected:

- **Blanket content-blindness on the whole audit log** (the literal bu-rh8z5
  direction). Rejected: it would gut the log's purpose. The Issues feed is a
  live grouping of exactly the `result = 'error'` rows in this table, and its
  occurrences drill-down publishes `AuditLogEntry` — an operator log that cannot
  say what failed is not an operator log. Nothing about the credential leak
  requires taking `error` off a session crash or a webhook failure.
- **Gate the free text behind a stronger operator role.** Rejected as not
  implementable rather than as a bad idea: the dashboard API has no role model
  at all. `src/butlers/api/security.py` is one regex helper, no route depends on
  an authenticated principal, and the product is explicitly single-owner. A
  "stronger role" here would be a control that does not exist.
- **Keep it, and state that the operator audit log is allowed to carry
  diagnostics.** Rejected because it re-creates the exact defect this class of
  bead exists to remove: two requirements giving opposite rules for the same
  stored text. `dashboard-api` §`Secrets Inventory and Per-Credential Read
  Endpoints` already says the audit note SHALL NOT reach the wire "including
  audit rows written by producers outside the secrets router, because the
  projection is enforced on read". A general-log exemption would make that
  sentence false for the credential namespace it names.

The chosen rule is the narrowest one under which both requirements are true at
once. It is an engineering call, not an owner call: the owner decision it
implements already exists (recorded in `UserSecretDetail` as "owner decision,
2026-08-13", and carried by the three sibling changes), and this bead only
decides where its enforcement boundary sits. The direction that *would* need an
owner is the opposite one — deciding that credential probe tails may be visible
in the dashboard after all would be a reversal of that decision, not a scoping
choice, and nothing here assumes it.

## What Changes

- A new `dashboard-audit-log` requirement, **Credential-Target Audit Free Text
  Is Withheld On Read**, states the rule, its scope, and why the non-credential
  case is deliberately untouched.
- `AuditLogEntry` withholds `note`/`error`/`metadata` for credential targets and
  gains `redacted: bool`. Enforcement lives on the **model**, not in the two
  routes, because the credential audit namespaces have five known producers and
  three readers (`routers/audit.py` list, `routers/audit.py` detail,
  `routers/issues.py` occurrences). A per-route fix would have left the
  occurrences endpoint publishing the same text — which is how this bug got here
  in the first place.
- `metadata` is withheld alongside `note`/`error` even though no credential
  producer populates it today. It is unbounded operator-written JSONB on a row
  the wire is not allowed to describe; leaving the one unwithheld free-text
  column open is how the next instance of this bead gets filed.
- `redacted: true` marks a row whose text was actually withheld. A silently
  blank Note would read as "nothing was recorded", and this codebase already
  refuses that shape elsewhere (`entries_source_available`,
  `catalogue_available`, "a short grid MUST NOT read as an honest 'nothing
  scheduled'"). The frontend uses the same flag to keep the Audit → Issues door
  mounted on a credential failure row whose `error` is now withheld.

## Delta placement

The rule is an **ADDED** requirement, not a MODIFIED block on `Audit Log Read
API`, because `close-audit-failure-spine` already holds an unarchived MODIFIED
block on that exact requirement:

```
rg -l '^### Requirement: Audit Log Read API$' openspec/changes/*/specs/*/spec.md
#   openspec/changes/close-audit-failure-spine/specs/dashboard-audit-log/spec.md
```

Two unarchived deltas on the same requirement clobber each other at archive
time, so this change states its rule as its own requirement and says in the
requirement text that it narrows the `Audit Log Read API` projection clause.
No baseline freeze is needed: an ADDED requirement deletes no live clause, and
`scripts/check_spec_overwrites.py` has nothing to flag.

`dashboard-api` §`Secrets Audit-History and Breaks-Catalogue Endpoints` is
**not** touched here — bu-rh8z5 holds the unarchived block on it. This change
only relies on the `meta.deep_link` clause that requirement already carries.

## Impact

- Affected specs: `dashboard-audit-log`
- Affected code: `src/butlers/api/models/audit.py`,
  `tests/api/test_audit_log.py`, `frontend/src/api/types.ts`,
  `frontend/src/components/audit/AuditLogTable.tsx`
- Breaking for any client reading `note`/`error`/`metadata` off a
  credential-target audit row. In-repo the only consumers are the Audit Log
  table and the Issues occurrences list, both updated here.
- `GET /api/issues` itself still publishes `error_summary` as an issue group's
  title, and a credential probe failure does reach that feed
  (`action='failed'`, `result='error'`). That is a different surface with a
  different problem — a group's identity *is* its normalized error text, so it
  cannot simply be blanked — and it is out of scope here rather than resolved
  by reflex. Reported as a follow-up.
