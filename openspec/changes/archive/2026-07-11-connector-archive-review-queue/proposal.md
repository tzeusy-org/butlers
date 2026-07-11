## Why

The soft-archive mechanism (bu-33dm2 / PR #3026) added an `archived_at` state and
audit-logged archive/unarchive endpoints, but archival stays a fully manual
action. The four dead identities from the 2026-07-05 audit were archived by a
one-off migration seed; there is no ongoing surface that points a human at the
*next* superseded identity to archive.

bu-33dm2 part 3 asked to *consider* auto-archiving endpoints offline >30d that
share a `connector_type` with a newer online identity. Auto-archiving is
deliberately rejected: it risks silencing a merely-quiet live connector, which
would violate the "archiving must never mask a failing live connector"
invariant. The safe middle ground is a **flag-only review queue** — surface the
candidates, let a human confirm each with the existing archive action.

## What Changes

- **`GET /api/ingestion/connectors/summaries`** gains a computed, read-only
  `archive_candidate` boolean per connector. It is `true` for an ACTIVE
  (non-archived) identity that BOTH (a) last heartbeated strictly more than 30
  days ago AND (b) has a newer, currently-`online` sibling identity of the same
  `connector_type`. It is derived from the same rows already fetched — no new
  query, no new storage, no migration.
- **Honesty**: `archive_candidate` is a SUGGESTION only. It never feeds the
  fleet-health rollups or alerting (those exclude `archived` only), never
  removes the identity from the active roster, and its degraded-mode envelope
  (`aggregates_available` / `device_liveness_available` /
  `hourly_events_available`) is unchanged. A genuinely-failing live connector is
  never a candidate (it is not offline for 30+ days) and is never filed as "just
  an archive candidate".
- **Dashboard**: candidates surface as a "review · suggested for archiving"
  queue below the active roster, each row linking to connector detail (history
  reachable) and offering a one-click archive that reuses the existing
  audit-logged `POST …/{type}/{identity}/archive` endpoint — no new archive
  mechanics, never automatic. Candidates also remain in the active roster with
  their true (offline) liveness.

## Impact

- Additive API field (`archive_candidate`); no migration, no schema change.
- Reuses the existing archive endpoint for the action — no new lifecycle
  mechanics.
