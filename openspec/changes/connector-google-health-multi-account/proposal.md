# Proposal: Google Health connector polls all health-scoped accounts

## Why

The Google Health connector currently polls only the row in `public.google_accounts` where `is_primary = true`. In practice, owners often connect multiple Google accounts and the recent Fitbit→Google Health Connect sync targets whichever account the device is linked to — not necessarily the one tagged primary.

Concretely, in this environment:
- `uniquosity@gmail.com` (primary) has 245 historical sleep dataPoints with the most recent end_time of `2025-07-29`.
- `tzeuse@gmail.com` (secondary, also has all three `googlehealth.*` scopes) has a live stream through `2026-05-24`.

The connector polls primary only, so the live data never lands. The connector reports `healthy` because polls succeed — they just come back empty for the configured window.

The single-owner v1 safety invariant (current spec: "non-primary accounts are ignored") was a defensible default when the connector was scoped to one owner, one Google account. With multi-account Google OAuth (`google-multi-account-oauth`) now in production and owners legitimately splitting health data across personal vs. family-shared accounts, that invariant has become a silent-data-loss bug.

## What Changes

- Connector enumerates **every** row in `public.google_accounts` whose `granted_scopes` contains all three `googlehealth.*` URLs and whose `status = 'active'`, not just the `is_primary` row.
- One per-account poll set per discovered account (separate cursors keyed by account UUID + resource + endpoint).
- Heartbeat & metrics labelled per account (`endpoint_identity = google_health:user:<email>`) — already the convention; we just emit one per account.
- Dashboard `GET /api/connectors/google-health/status` returns a list of `{account_email, scopes_granted, last_ingest_at, …}` instead of one flat object. A `primary_account_email` field preserves the "default" account for UIs that only want one summary.
- Account changes (new connection, scope revoke, account deletion) are detected on the next `granted_scopes` re-check loop (already 300s); the connector spawns or tears down per-account poll sets accordingly.

## Out of Scope

- Per-account *override* of poll intervals — all accounts share the same `poll_intervals` config in v1.
- Cross-account deduplication of dataPoints — each account is its own owner identity; if the same physical Fitbit syncs to two accounts simultaneously, dedup is the source-account problem to solve, not ours.
- Multi-owner identity (multi-tenant). The connector still serves one Butlers owner whose Google account list happens to be > 1.

## Risks

- **Rate limits.** Each account counts separately against `health.googleapis.com` quotas. Per-resource intervals stay at the existing defaults (30 min for sleep/activity/HR, etc.); two accounts double API call volume but stay far below documented quotas.
- **Dashboard schema additive.** The status response gains a nested `accounts: list[AccountStatus]` field. Top-level summary fields stay (worst-of across accounts) — single-account installs remain visually identical. See `design.md` ADR-1.
- **Ingestion-event identity ambiguity.** `external_event_id` becomes `google_health:<email>:<resource>:<date>` to disambiguate per account. 3 historical rows are migrated by an Alembic step (see `design.md` ADR-2 and `tasks.md` §4).
- **Paired butler-health delta.** The current `butler-health` spec drops envelopes whose `sender.identity != primary google_user_id`. Without the paired MODIFY in `specs/butler-health/spec.md`, multi-account ingest would be silently rejected at the butler boundary. This change ships both deltas as one atomic OpenSpec change.

## Predecessor

This proposal exists because of the discovery captured in `openspec/changes/archive/2026-04-24-google-health-connector/research-notes.md` under "Data-availability discovery — multi-account topology (2026-05-25)". The scope-restricted-token bugfix (`_mint_access_token` now passes `scope=`) is independent and already landed.
