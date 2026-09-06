## Why

The dashboard currently discards owner-authored text when an eligible chat composer or form/dialog unmounts, closes, or loses its route. That makes the owner repeat mental work and conflicts with the project's goal of reliably absorbing it.

This draft proposes a browser-local recovery contract with explicit eligibility, expiry, conflict, disclosure, and secret-surface boundaries. It is proposed future behavior only: implementation remains blocked until the owner approves this exact artifact.

## What Changes

- Persist drafts for both chat composers and an explicit allowlist of existing form/dialog families, using stable context keys rather than component-instance identity.
- Restore a same-context draft automatically with a quiet accessible status and a one-click discard action.
- Clear only the successfully submitted draft; retain it after validation, transport, or application failure.
- Store versioned records in the current browser origin for 24 hours after the last edit, isolate records by surface and domain identity, and resolve cross-tab writes without silently merging or overwriting focused local text.
- Keep typing available when browser storage is denied, full, corrupt, or unsupported, while visibly disclosing that recovery is unavailable.
- Forbid persistence on dedicated Secrets, OAuth, credential, token, password, recovery-code, and copy-once-value surfaces. Ordinary eligible free-text controls are not content-classified; if the owner enters secret material there, it is treated as ordinary draft text and can remain browser-local until discard or expiry.
- Preserve the existing same-thread history-read recovery and clear-on-send contracts while defining their missing success/failure timing and cross-unmount behavior.

### Proposed owner policy

Approve an explicit surface-and-field allowlist, `localStorage` scoped to the dashboard origin, a sliding 24-hour expiry, a 64 KiB serialized limit per draft, newest-write-wins storage ordering with an explicit conflict choice for an already-edited tab, and no content inspection. This favors recovery without creating a server-side content store or pretending arbitrary prose can be reliably recognized as a credential.

The residual privacy boundary is deliberate: any person or script with access to the same browser profile and dashboard origin can read eligible drafts. Existing localhost/Tailscale isolation and optional dashboard API-key authentication govern server access, but they do not encrypt browser storage or prove which human opened a shared browser profile.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-chat-ui`: add durable, per-conversation browser draft behavior for `ChatPanel` and `FloatingChatWidget`, including restore, discard, send outcome, and history-recovery reconciliation.
- `dashboard-shell`: add the shared browser draft contract for explicitly eligible form/dialog surfaces, including stable identity, record lifecycle, conflict handling, accessibility, degraded storage behavior, and the forbidden credential boundary.

## Impact

- Future implementation is confined to `frontend/`: a shared draft-store/hook seam, the explicitly listed chat and form/dialog consumers, focused component/hook tests, and frontend documentation.
- No backend route, database schema, server persistence, provider call, telemetry payload, URL state, event-bus payload, runtime action, or deployment changes.
- No new dependency is proposed; browser storage is an existing platform capability.
- No test files change in this spec-only draft (`Tests: +0 ~0 -0`).

## Out of Scope

- Server or cloud draft sync, cross-device recovery, account-level draft history, draft search, encryption-at-rest managed by Butlers, or recovery after browser data is cleared.
- Content inspection, secret detection, redaction, or a promise that arbitrary eligible prose cannot contain credentials.
- Draft persistence for any unlisted surface, including the entire `/secrets` subtree, provider configuration drawers, password controls, model prompt editors, MCP/config editors, ingestion rule editors, and inline calendar description autosave.
- Changes to the active history query's cached-message ownership, optimistic-message reconciliation, retry identity, SSE behavior, or terminal-action recovery.
- The sibling bus-coverage-token and unified-navigation-intent outcomes from `bu-2jtfw.15`.
- Timing-token ownership, elapsed route frames, universal never-blank or stale-while-revalidate conversion, Dispatch retokening, status registry, and Attention primitive work.
- Implementation, activation, baseline-spec sync, archive, downstream readiness, or merge.
