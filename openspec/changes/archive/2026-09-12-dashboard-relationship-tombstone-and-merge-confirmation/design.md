## Context

`EntityDetailPage` receives a loaded entity through the existing memory entity read and already has client-side navigation for related entity records. The API preserves a merge source as a tombstone by setting `metadata.merged_into`, but this page currently treats the source as an ordinary record. Separately, `MergeCompareDialog` enforces comparison before merge but calls the existing merge mutation as soon as the operator presses Merge.

The dashboard has a shell-level screen-reader announcer and a shared Radix-backed `AlertDialog` primitive. The change must stay entirely inside the frontend and must not alter the merge request, memory API, relationship router, persistence, or ACLs.

## Goals / Non-Goals

**Goals:**

- Redirect a loaded merged-away entity to a valid, distinct survivor without leaving source-record controls visible.
- Make the redirect and any metadata inconsistency perceivable to screen-reader users.
- Require an explicit final confirmation that names both merge participants before the current merge mutation runs.
- Keep cancellation, Escape, mutation payload construction, success handling, and error handling deterministic and testable.

**Non-Goals:**

- Following arbitrary chains of merge metadata, repairing inconsistent data, or adding a backend redirect endpoint.
- Changing the comparison evidence, selection model, merge API, cache invalidation, or generic confirmation infrastructure.
- Reworking other entity lifecycle actions.

## Decisions

### Classify merge metadata locally before rendering detail content

The page will treat `metadata.merged_into` as a redirect target only when it is a string whose trimmed value is nonempty and differs from the loaded entity ID. The target will be URI-encoded when constructing the existing canonical entity route. An absent field remains ordinary entity metadata. A present non-string, empty/whitespace value, or self-reference will not navigate; the loaded detail will instead show a named `role="alert"` inconsistency state.

This local validation keeps the navigation rule close to the route consumer and does not expand API or router scope. It also prevents the self-reference loop without trying to infer a multi-record merge chain that the loaded response cannot prove.

### Replace-navigate through the existing shell announcement path

When metadata is valid, an effect will call the shell announcer and `navigate("/entities/<survivor>", { replace: true })`. The page will render only a transient merged-record status while navigation is pending, not the source record's title, body, or actions.

Using the existing announcer keeps the transition audible across the SPA without adding a second global live-region mechanism. Replacing history means Back does not strand the operator on the known merged-away URL.

### Use the shared alert dialog as a local final commit gate

The comparison dialog's Merge button will open a controlled `AlertDialog` rather than call the mutation directly. Its title and description identify the survivor and absorbed entity computed from the already-rendered comparison result. Cancel, an Escape close, or closing the confirmation only resets local confirmation state. Confirm calls the existing `handleMerge` path exactly once, preserving its `keepAs` payload and toast-based error behavior.

This is preferable to a new generic confirmation abstraction because the confirmation is specific to the irreversible merge commit and the project already has an accessible alert-dialog primitive with modal semantics and focus management.

## Risks / Trade-offs

- [A malformed server value could otherwise create a bad route or loop] → Validate type, trimmed content, and self-reference before navigation; render an explicit inconsistency instead of guessing a recovery target.
- [A fast route replacement could be invisible to assistive technology] → Send a dedicated shell announcement and retain a local status in the redirect-only render path.
- [Nested modal state could make cancellation confusing] → Keep confirmation state local to `MergeCompareDialog`, clear it whenever the comparison dialog closes, and test both Cancel and Escape for zero mutation calls.
- [Confirmation could accidentally alter the established request] → Route Confirm through the existing merge handler and assert the exact existing payload in focused tests.

## Migration Plan

1. Deploy the frontend-only change with targeted route and dialog regression tests.
2. Existing tombstones begin redirecting on the next detail-page load; no data migration or cache backfill is required.
3. Rollback is a frontend revert. Existing API and persisted merge records remain unchanged.

## Open Questions

- None. The issue acceptance criteria define the validation boundary and explicitly exclude backend recovery or a generic confirmation framework.
