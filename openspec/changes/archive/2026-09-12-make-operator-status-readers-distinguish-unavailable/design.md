## Context

The Permissions audit reel and the Passport Telegram setup region are
operator-read surfaces whose query state has security meaning. A successful
empty audit response is a legitimate absence of history, while a failed query
is unknown. Likewise, Telegram session status must be known before the UI can
decide whether to show setup, and a failed probe cannot safely be interpreted
as missing credentials or an unready session.

Both surfaces already use TanStack Query and have local, focused rendering
boundaries. No API response shape, persisted state, or mutation endpoint needs
to change.

## Goals / Non-Goals

**Goals:**

- Render successful-empty, loading, and unavailable query states distinctly
  where they carry different operator meaning.
- Keep a retry action local to the failed status reader.
- Retain cached audit rows when a refresh fails, with a clear degraded label.
- Keep the successful Telegram unready path unchanged and avoid rendering or
  inferring credential state on status failure.

**Non-Goals:**

- Changing Telegram consent, session authentication, secret persistence,
  credential display, authorization, or account-wide ingestion semantics.
- Changing the audit API, adding global error handling, or hiding valid cached
  audit data during a transient refresh error.

## Decisions

### Model availability separately from returned data

Each surface SHALL consume the query error flag in addition to returned data.
The audit reel renders its calm no-history copy only when the query is not in
an error state and the successful payload has no entries. If the query is
errored, it renders a named degraded note and retry; cached data, when present,
continues to render alongside that note. This preserves useful evidence while
making its freshness uncertainty explicit.

Treating errors as an empty array was rejected because it falsely reports a
known empty history. Suppressing cached entries was rejected because it removes
still-useful, clearly labelled security evidence.

### Gate Telegram setup on successful status resolution

The Telegram setup region SHALL show a loading skeleton while status is
pending. On a status error it SHALL render only a named unavailable state and
retry action before any status-dependent normal setup UI. A retry invokes the
same status query rather than attempting session authentication or a credential
mutation. Only a successful status response may select the existing unready
setup flow.

Synthesizing an unready status after an error was rejected because it implies
missing configuration and could cause the UI to expose or solicit secrets under
false premises. A page-level error boundary was rejected because the retry and
operator explanation belong to this narrowly scoped probe.

### Prove rendered states with focused query mocks

Focused component/page tests SHALL exercise the rendered loading, failure, and
successful-unready states. Audit coverage SHALL include the cached-data plus
error case; Telegram coverage SHALL assert that failure neither reveals setup
inputs nor replaces the normal successful-unready path.

## Risks / Trade-offs

- [Cached audit data may be stale] → Label the source degraded and retain a
  retry affordance next to the visible rows.
- [A retry could fail repeatedly] → Keep the failure UI non-mutating and do
  not infer configuration or session state between attempts.
- [Status paths can regress independently] → Use direct rendered-state tests
  rather than only hook-level assertions.

## Migration Plan

1. Ship the scoped frontend state handling and focused tests together.
2. No database, API, or rollout migration is required.
3. Rollback is a normal frontend revert; it does not affect stored secrets,
   consent, sessions, or audit records.

## Open Questions

None.
