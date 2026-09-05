> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Spotify-category lifecycle exclusion shipped directly in the secrets lifecycle job.
> **Successor:** `src/butlers/jobs/secrets_lifecycle.py`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Spotify lifecycle alert exclusion

## Problem

The generic secrets lifecycle job scans every `butler_secrets` row, including
Spotify OAuth artifacts. Spotify access tokens normally expire after one hour
and are refreshed by the provider-specific connector. Treating that routine
expiry as a generic credential failure sends false Telegram alerts even while
the dedicated Spotify status endpoint reports a healthy connection. The
Secrets frontend already hides these provider-managed rows for the same reason.

## Decision

Exclude system credentials with `category="spotify"` from lifecycle snapshot
collection in both per-butler and shared-public stores. This keeps the change at
the earliest common seam: excluded rows cannot enter attention-state filtering,
debouncing, delivery, or retry handling. Real Spotify authentication health
continues to come from the connector's refresh behavior and dedicated status
endpoint, where refresh rejection is already the defined error condition.

The exclusion intentionally applies only to proactive lifecycle notifications.
It does not migrate duplicate credentials, change staleness probing, reorder
background jobs, or alter other credential categories. Those are separate
concerns and unnecessary for stopping this incident.

## Verification

Add a collection-level regression test containing both a local Lifestyle
Spotify row and a shared-public Spotify row, and prove neither becomes a
lifecycle snapshot. Retain a normal system credential in the fixture to prove
the scan still collects actionable credentials. Run the lifecycle test module
plus scoped Ruff checks.
