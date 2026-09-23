# Design

## Decision

Use ledger session provenance rather than parsing attribution names. The aggregate query adds
`has_session = BOOL_OR(session_id IS NOT NULL)` for each existing group. The divergence detector
skips only rows whose `has_session` value is explicitly false. Missing or ambiguous provenance
continues through the roster comparison and therefore fails closed.

This covers connector identities such as `wa:*@lid` and `tg:*`, plus declared synthetic runtime
identities such as `__discretion__` and `__dashboard_briefing__`, without maintaining an incomplete
prefix allow-list. If an identity has any session-backed group, that group remains comparable and
an unknown identity still sets `source_error`.

## Boundaries

- The new column is diagnostic metadata on the existing read query; it does not participate in
  token totals, pricing, grouping keys, or response serialization.
- The response remains a boolean degraded state. Raw excluded identities are not projected to the
  browser or inserted into error copy.
- No connector producer, session writer, identity reconciliation, or WhatsApp behavior changes.

## Rollback

Revert the query column and explicit sessionless-row exclusion. No persisted state or migration is
involved.
