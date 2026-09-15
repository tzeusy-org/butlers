## Context

See [proposal.md](proposal.md). The dashboard currently has two connector API
surfaces. The newer ingestion router owns role-aware roster, lifecycle, and
detail subresource behavior, while the auto-discovered Switchboard router still
owns a raw list plus detail, statistics, settings, and retired operations. The
two surfaces disagree about which registry rows have runtime authority.

## Goals / Non-Goals

**Goals:**

- Establish one explicit API namespace and one frontend query path for each
  dashboard connector use case.
- Preserve the detail-page contract (including content-blind auth/scopes,
  counters, filtered statistics, archived history, and settings) while moving
  it into the ingestion router.
- Make removed paths mechanically observable as absent from OpenAPI, not merely
  unused by the current frontend.

**Non-Goals:**

- Changing connector writers, heartbeat/cursor provenance, database schema,
  OAuth behavior, or live connector state.
- Re-introducing the retired fanout matrix, card-delete action, or raw cursor
  editing under a new name.
- Supporting out-of-repository callers through redirects, aliases, or a
  deprecation window; the owner explicitly selected a completed migration.

## Decisions

### One ingestion router owns all dashboard connector operations

`ingestion_connectors` will expose canonical detail, statistics, and settings
routes alongside its existing summaries, cross-summary, lifecycle, and scoped
detail subresources. The relevant legacy projection/query helpers move with the
behavior rather than importing the roster router into core. This keeps the
dashboard API boundary explicit and prevents a second data projection from
drifting back into service.

Alternative considered: keep the Switchboard handlers and proxy them from the
ingestion namespace. Rejected because the proxy would preserve two public paths
and two owners, directly contradicting the completed-migration requirement.

### Role-aware summaries are the only list source

The frontend will consolidate on the existing summaries query and filter
archived identities before Timeline attention, channel choices, and System
topology. It will not locally recreate liveness from raw heartbeats.

Alternative considered: add a Timeline-only lightweight list endpoint. Rejected
unless performance evidence proves the canonical summaries contract cannot
serve the surface; an additional list shape would recreate the split authority
this change removes.

### Retired operations are deleted, not re-homed

The unused legacy aggregate summary, destructive delete, raw cursor patch, and
fanout handlers are removed. The existing ingestion cross-summary and
approval-gated disconnect/archive operations remain their deliberately distinct
contracts. No route is added merely to preserve a former operation.

### Contract and OpenAPI enforce the removal

Focused behavior tests preserve the canonical response contracts. One OpenAPI
absence assertion sweeps the whole retired namespace, and the existing frontend
client/OpenAPI contract verifies every remaining client path resolves to a live
route. The duplicate ingestion-router include is removed and a warning-capture
test rejects duplicate operation IDs.

## Risks / Trade-offs

- [Tailnet caller outside the repository uses a removed path] → The owner
  explicitly authorized a breaking removal; no compatibility layer is added.
  A content-blind access-log check can inform deployment follow-up without
  changing this decision.
- [Moved detail projection omits a legacy field] → Pin auth/scopes, settings,
  counters, filtered statistics, archived rows, and deleted-row exclusion in
  canonical API/client tests before deleting the old handler.
- [Canonical summaries are temporarily unavailable or slow] → Preserve its
  explicit unavailable state and measure the canonical request after migration;
  do not silently fall back to raw registry data.
- [OpenSpec deltas race another active connector change] → Check same-named
  modified requirements before archive and rebuild against the refreshed
  baseline when required.

## Migration Plan

1. Add focused failing tests for canonical paths and duplicate OpenAPI IDs.
2. Move detail/stats/settings behavior to the ingestion router and migrate the
   typed frontend client/hooks and all list consumers.
3. Delete every legacy Switchboard connector handler and orphaned adapter/test
   path; remove the duplicate router mount.
4. Reconcile current specs/docs, validate the OpenSpec change, run source scans
   and contract tests, then deploy through the ordinary PR/merge-queue path.

Rollback is a code-only revert: no data migration or runtime mutation occurs.
