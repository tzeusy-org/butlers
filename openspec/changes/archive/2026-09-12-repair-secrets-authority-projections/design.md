## Context

The passport adapts multiple compatibility-shaped sources into one conceptual
credential spine. For CLI credentials, the shared `cli[]` inventory family is
the Tier 1 system-global authority, while same-key `system[]` rows categorized
as `cli-auth` are legacy per-butler mirrors. Treating every mirror as another
health vote allows stale evidence to override a freshly persisted canonical
probe result and inflates failing or unverified counts.

Spotify is governed by a different boundary. Its OAuth tokens and lifecycle
remain connector-owned Tier 2 state and are intentionally excluded from generic
Secrets reads and probes. The passport's synthetic `u:spotify` row therefore
has to project the existing closed connector-status response rather than infer
generic credential health.

## Goals / Non-Goals

**Goals:**

- Make canonical CLI state win consistently in backend counts and frontend
  presentation without deleting raw compatibility evidence.
- Keep legacy mirror-only CLI keys visible with most-severe fallback behavior.
- Make a completed CLI Test display its persisted healthy or failed result
  without requiring a page reload.
- Map every closed Spotify connector status, plus loading and query failure, to
  an honest passport presentation state and recovery placement.
- Preserve content-blind responses and existing authority boundaries.

**Non-Goals:**

- Deleting, migrating, or rewriting per-butler CLI mirror rows.
- Changing daemon reconciliation, routed-session health, breaker state,
  credential values, token storage, or connector lifecycle behavior.
- Adding Spotify to generic Secrets or OAuth authorities, or adding a generic
  Spotify probe endpoint.
- Returning provider error text, account identifiers, raw scopes, probe
  messages, or credential material to the browser.

## Decisions

### Canonical CLI rows take precedence by credential key

Backend aggregation and frontend inventory adaptation will first build the set
of keys present in `cli[]`. A per-butler `system[]` row with category
`cli-auth` is relocated into the conceptual CLI display family only when its
key is absent from that set. Canonical and fallback rows continue to use the
existing family shapes.

This preserves raw per-source System evidence for detail and audit consumers
without allowing compatibility mirrors to override the canonical state or
inflate CLI-family failing and unverified counts. When no canonical row exists,
same-key mirrors retain most-severe aggregation so older deployments do not
lose visibility.

### A completed CLI Test refreshes persisted evidence

After `POST /api/cli-auth/{provider}/test` completes with an HTTP success
response, the dashboard invalidates both the Secrets inventory query prefix and
the CLI-provider status query. This applies to healthy and failed test results
because both are persisted evidence. Transport or API failures retain the
existing inline error behavior.

The test remains a dashboard-side credential probe. It does not establish that
a daemon-routed session succeeded and does not modify model breaker history.

### Spotify passport state comes from connector status

`DirectionPassport` consumes the existing Spotify status query while building
the synthetic `u:spotify` row. The connector drawer may consume the same query;
the shared query cache deduplicates the request.

The presentation mapping is:

| Connector status | Passport state | Placement / action |
|---|---|---|
| loading | `checking` | integrations; no stale alarm |
| `connected` | `ok` | healthy integration |
| `unconfigured` | `never_set` | not set; Configure |
| `authorization_needed` | `authorization_needed` | needs hand; Connect |
| `needs_reauth` | `authorization_needed` | needs hand; Re-authorize |
| `error` or query failure | `failed` | needs hand; connector recovery |

`checking` and `authorization_needed` are presentation states with explicit
labels and severity ranks. Neither is the generic `warn`/unverified state,
`checking` never enters `stale`, and the connector drawer remains the only
interactive authority. The passport does not add a generic probe action.

## Error Handling

- A CLI test whose request succeeds but whose persisted outcome is failed still
  invalidates the inventory and provider queries.
- A CLI test transport or API failure retains current inline error behavior and
  does not claim persisted evidence changed.
- A Spotify status query failure maps to content-blind `failed`; provider error
  text is not surfaced in the spine.
- Spotify loading maps to `checking`, never to healthy, stale, or needs hand.
- Missing canonical CLI state falls back to mirrors and never fabricates an
  empty or healthy canonical row.

## Risks / Trade-offs

- [Backend and frontend precedence drift] -> Bind both to the same canonical-key
  rule and cover canonical-plus-mirror and mirror-only cases at each seam.
- [A failed CLI test remains visually stale] -> Invalidate after every HTTP
  success response rather than only after a healthy result.
- [Spotify query failure exposes diagnostics] -> Collapse the presentation to
  `failed` and retain provider details behind the connector boundary.
- [A future archive overwrites a broad existing requirement] -> Add uniquely
  named requirements instead of modifying the current inventory, passport IA,
  or Spotify-exclusion blocks.

## Migration Plan

No database migration or data reconciliation is required. Implement the
backend and frontend precedence rules with focused regressions, then add query
invalidation and Spotify state mapping. Verify the existing content-blind and
generic-Spotify-prohibition tests remain green. Rollback is a normal code
rollback because no stored credential or schema state changes.

## Open Questions

None. The authority sources, fallback rule, refresh trigger, and Spotify state
mapping are fixed by the approved design.
