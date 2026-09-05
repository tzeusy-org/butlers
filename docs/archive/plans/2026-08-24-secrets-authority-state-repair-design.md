> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Codex CLI authority / Spotify projection repair shipped via the secrets-authority-projections change.
> **Successor:** `openspec/changes/repair-secrets-authority-projections`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Secrets Authority and Projection State Repair

## Problem

The Secrets passport currently presents two misleading states:

1. A successful Codex CLI probe updates the canonical shared
   `cli-auth/codex` credential, but the passport can remain `stale`. The
   inventory merges per-butler legacy mirrors into the same conceptual CLI
   row and lets a stale mirror override the healthy authority. The CLI Test
   mutation also does not invalidate the inventory query, so the page does not
   immediately request the persisted result.
2. `u:spotify` is a connector-owned projection with no generic probe by
   design, but its spine entry is synthesized with a hard-coded `warn` state.
   It therefore appears under `stale · unverified` even though its actual
   evidence comes from the connector status endpoint and its corrective action
   is Connect or Re-authorize.

The repair must make each surface reflect its real authority without creating
a second credential or probe path.

## Authority Boundaries

- `cli-auth/*` is Tier 1 system-global credential state. When the shared CLI
  inventory contains a key, that row is authoritative for the `c:` passport
  entry and CLI-family KPI counts. Same-key per-butler `butler_secrets` rows
  are compatibility mirrors, not independent health votes.
- If no canonical shared CLI row exists, a legacy per-butler `cli-auth/*` row
  may remain a display fallback so older deployments do not lose visibility.
- Spotify OAuth tokens remain connector-owned Tier 2 state. Generic Secrets
  inventory, detail, mutation, and probe routes continue to reject or omit
  Spotify token material. The projection reads only the connector's closed,
  content-blind status response.
- No credential values, provider error text, account identifiers, raw scopes,
  or probe messages are added to browser-visible responses.

## Codex CLI Repair

### Inventory reconciliation

Both backend KPI aggregation and frontend inventory adaptation will apply the
same precedence rule:

1. Build the set of keys present in the canonical `cli[]` family.
2. Relocate per-butler `system[]` rows with `category = "cli-auth"` into the
   CLI family only when their key is absent from that canonical set.
3. Continue most-severe deduplication among fallback mirrors for a key that has
   no canonical row.

The raw `system[]` response may continue carrying per-source rows for existing
detail/audit behavior. The precedence rule affects the conceptual CLI display
family and its aggregate counts, not database contents. No legacy credential
rows are deleted or rewritten.

### Probe refresh

`useTestCLIAuthApiKey` will invalidate both the Secrets inventory prefix and
the CLI-provider status query after any successful HTTP test response. This
applies whether the provider test result is healthy or failed, because either
outcome is persisted and must replace the displayed evidence immediately.

The probe remains an isolated dashboard-side credential test. It does not
claim a daemon-routed session succeeded and does not alter breaker history.

## Spotify Projection Repair

`DirectionPassport` will consume the existing `useSpotifyStatus` query when it
constructs the synthetic `u:spotify` spine entry. The embedded Spotify drawer
may use the same query; React Query supplies one shared cache and deduplicates
the request.

The projection maps the closed connector status to explicit UI states:

| Connector status | Passport state | Placement/action |
|---|---|---|
| loading | `checking` | normal integrations group; no false stale alarm |
| `connected` | `ok` | healthy integration |
| `unconfigured` | `never_set` | not set; Configure |
| `authorization_needed` | `authorization_needed` | Needs hand; Connect |
| `needs_reauth` | `authorization_needed` | Needs hand; Re-authorize |
| `error` or query failure | `failed` | Needs hand; connector recovery action |

`checking` and `authorization_needed` are presentation states. They receive
explicit catalog labels and severity ranks; neither is treated as the generic
credential `warn`/unverified state. The generic Spotify probe button remains
absent, and the existing connector drawer remains the only interactive
authority.

## Error Handling

- A failed Codex test still refreshes inventory when the HTTP request itself
  succeeds, allowing the persisted failed state to appear. Transport/API
  failures retain the current inline error behavior.
- A Spotify status request failure produces a content-blind `failed` state; it
  does not surface provider error text in the spine.
- Status loading is represented honestly as `checking`, not `healthy`,
  `stale`, or `needs hand`.
- Missing canonical CLI state falls back to legacy mirrors; the repair does not
  fabricate an empty or healthy row.

## Specification Changes

Create a focused OpenSpec change with additive requirements covering:

- canonical shared CLI authority precedence over per-butler compatibility
  mirrors for passport state and KPI aggregation;
- immediate inventory refresh after a CLI test persists an outcome;
- connector-status-derived Spotify projection states while preserving the
  generic Spotify probe prohibition and content-blind contract.

Additive requirements avoid rewriting the existing broad credential
aggregation and Spotify authority requirements.

## Test Strategy

The change will use focused red-green regressions at the owning seams:

1. Backend inventory tests: canonical healthy CLI plus stale mirrors yields a
   healthy conceptual CLI result and correct unverified/failing counts; a
   mirror-only key still uses most-severe fallback behavior.
2. Frontend inventory-adapter tests: canonical CLI wins over stale relocated
   system mirrors; legacy-only rows still render.
3. CLI hook tests: a completed Codex Test invalidates Secrets inventory and CLI
   provider status.
4. Spotify projection tests: every closed connector status maps to the expected
   spine state/group, loading does not enter `stale`, and the projection still
   renders no generic probe/audit surface.
5. Focused backend pytest plus frontend Vitest, lint, TypeScript build, and
   Knip gates. Expand to the repository's final merge-readiness gate before
   landing if the branch remains otherwise isolated and CI resources permit.

## Non-Goals

- Deleting or migrating legacy per-butler CLI credential rows.
- Changing Codex daemon reconciliation, runtime volumes, model breaker state,
  or routed-session health semantics.
- Adding Spotify to generic Secrets or generic OAuth authorities.
- Adding a Spotify probe endpoint or exposing provider-derived details.
- Changing credential values, token storage, or connector lifecycle behavior.
