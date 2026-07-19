## Context

`public.approvals_policy` is already the physical global singleton for the
owner-default notification path, while the insight broker historically kept a
second quiet-window triplet in `public.insight_settings`. The two paths now
disagree on the end boundary: core treats it as inclusive and computes an
anchor one hour late, while the broker treats it as end-exclusive. Sleep
context duplicates timezone conversion and derives the same late anchor.

The durable owner-default queue deliberately has a narrow contract: a caller
chooses and stores an absolute `deliver_at`; the scheduler later dispatches the
stored envelope without re-evaluating policy. This change must preserve that
contract and RFC 0009's read-only context use, RFC 0011's urgent insight
bypass, RFC 0019's no-proactive-egress boundary, and RFC 0021's pending-action
expiry behavior.

## Goals / Non-Goals

**Goals:**

- Make `public.approvals_policy` the only owner-attention quiet-window
  authority, with `[start, end)` semantics in its IANA timezone.
- Give all listed direct readers one validated, timezone-aware predicate and
  one exact-end anchor implementation.
- Preserve valid user configuration during a rerunnable core migration and
  restore legacy shape on downgrade for an older binary.
- Keep the public policy endpoint/payload stable while rejecting incomplete
  pairs and unknown IANA timezones at the write boundary.
- Retain fail-open behavior for unavailable, incomplete, or invalid persisted
  policy data and make that condition observable through warning logs.

**Non-Goals:**

- No broker catch-up, wake signal, cron, morning digest, or new delivery path.
- No scheduler re-gating, retry redesign, queue schema change, or delivery
  preference change.
- No secrets lifecycle, retention, budget, dedup, or insight schedule change.
- No reinterpretation of invalid persisted timezone strings and no runtime
  dual-read compatibility fallback after migration.

## Decisions

### D1 — One policy helper owns both local-time conversion and interval math

`src/butlers/core/approvals_policy.py` exposes a pure hour predicate plus
timezone-aware policy helpers. The predicate evaluates a half-open interval:
for same-day windows, `start <= hour < end`; for overnight windows, `hour >=
start or hour < end`; equal endpoints disable the window. The timezone-aware
helper validates an aware `now`, converts through the stored IANA zone, and
returns either the exact next end anchor in UTC or no result. A boolean helper
uses the same parsed local state rather than having each reader construct a
`ZoneInfo` object.

This makes a local time exactly at the configured end non-quiet and makes the
previous hour's anchor that exact end. Missing hours, a partial policy, a bad
zone, or a policy read failure produce no suppression; bad persisted zones log
a warning rather than silently falling back to UTC.

**Alternative considered:** leave a lightweight private predicate in each
consumer. Rejected because it recreated the current semantic drift and made
invalid-zone behavior divergent.

### D2 — `approvals_policy` is canonical; insight settings retain only insight controls

The migration treats a canonical row with both hours as complete, and that
complete canonical pair wins all conflicts. Only when it is incomplete and the
legacy pair is complete does the migration copy the legacy hours/timezone into
the canonical row. A legacy blank timezone may use the canonical timezone or
UTC only as an explicit migration default; a nonblank invalid legacy zone is
copied verbatim rather than reinterpreted. A partial canonical policy with no
complete legacy source is normalized to disabled hours so runtime never sees a
hybrid pair. The three legacy columns are then dropped.

`insight_settings` continues to own verbosity and custom budget only. Runtime
does not read old columns even if a mixed deployment temporarily retains them.

**Alternative considered:** retain a runtime fallback to legacy fields until
all processes restart. Rejected because it leaves two authorities and violates
the requested no-compatibility-path boundary; migration order is the rollout
contract.

### D3 — Existing durable queue semantics remain a one-way decision

At notification creation, policy and active-context anchors are compared and
the later absolute UTC timestamp is persisted. The scheduler's due-row pass
does not call the policy helper and does not alter `deliver_at`. Approval push
uses the same policy anchor, but its pending action's expiry remains its
existing independent timestamp. The broker's regular cycle checks the shared
policy; its `urgent_only` path continues to skip policy and context reads
entirely.

**Alternative considered:** wake or rebuild queued rows when policy changes.
Rejected because it is a scheduler/broker catch-up redesign outside this slice
and would change durable-row expectations.

### D4 — API validation protects future persisted rows without changing the shape

`ApprovalsPolicy` keeps `quiet_start_hour`, `quiet_end_hour`, and `timezone`
payload fields. Both hours must be supplied together, and non-null policies
must use a valid IANA `ZoneInfo` key. `null` hours remain the disabled state.
The existing GET/PUT route and audit event remain unchanged; dashboard wording
changes from generic quiet hours to Owner Attention Policy.

**Alternative considered:** introduce a new owner-policy endpoint or rename
the public payload. Rejected because external API compatibility is explicitly
required and the existing table is already canonical.

## Risks / Trade-offs

- **Invalid historic timezone cannot safely be acted on** → copy it verbatim
  only where preservation is required, warn at runtime, and fail open rather
  than assigning a different jurisdiction.
- **A partial historic row has ambiguous intent** → never combine fields from
  different sources; normalize it to disabled if no complete legacy source
  exists.
- **Migration may run on a core-only database** → guard optional legacy table
  and column access with `to_regclass`/catalog checks, making upgrade and
  downgrade rerun-safe.
- **Multiple call sites can regress boundary behavior** → focused boundary,
  invalid-zone, API, broker, sleep, approval-push, and migration regression
  coverage uses the shared helper as the only expected authority.
- **An old binary after upgrade cannot see removed columns** → the documented
  rollback path is downgrade before rolling back the binary; downgrade recreates
  compatibility columns from canonical policy.

## Migration Plan

1. Add the guarded core migration after the current core head. It confirms
   canonical table/columns before reading them and treats the legacy table as
   optional.
2. Preserve a complete canonical policy. Otherwise copy only a complete legacy
   pair, using its timezone if nonblank and preserving invalid nonblank values.
   Normalize an unresolvable partial canonical pair to disabled hours.
3. Drop legacy quiet columns with `IF EXISTS`; preserve all non-quiet insight
   setting columns and existing grants.
4. Deploy code that uses only the shared helper and canonical table, then
   update dashboard/docs/tests in the same change.
5. On rollback, run the migration downgrade before restoring an older binary;
   it re-adds the legacy fields if absent and copies canonical values back
   without disturbing verbosity/budget fields.

## Open Questions

None. The parent planning packet fixes the single-writer scope, canonical
authority, migration precedence, and out-of-scope boundaries for this change.
