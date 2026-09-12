## MODIFIED Requirements

### Requirement: Secrets Audit-History and Breaks-Catalogue Endpoints
The `/api/secrets/*` namespace SHALL expose two read-side endpoints supporting the StampRow audit display and the WhatBreaks affordance.

#### Scenario: Audit history endpoint
- **WHEN** `GET /api/secrets/audit/<scope>/<key>?limit=50` is called (where `scope ∈ {user, system, cli}`)
- **THEN** the response is `ApiResponse<AuditEvent[]>` with the most recent audit rows filtered to the credential
- **AND** each `AuditEvent` includes `ts` (server pre-formatted relative timestamp), `actor`, and `action` (a short machine-readable verb), and nothing else
- **AND** the payload SHALL NOT contain the stored audit `note` or any other free text carried on an audit row — including rows written by producers outside the secrets router, on exactly the terms `Secrets Inventory and Per-Credential Read Endpoints` already binds the inventory and per-credential reads to, because the note column carries provider and exception text verbatim (a failed probe persists `"Probe failed: <provider text>; probe_status=<token>"`)
- **AND** the field SHALL be absent rather than published as an always-null placeholder, so no client can read the absence as "this event had no note"
- **AND** the note SHALL NOT be read into the response path at all — the endpoint's query does not select it — so a projection change alone cannot reintroduce it
- **AND** the free text SHALL still be persisted for operators: withholding it from the wire does not stop `public.audit_log`, `public.secret_probe_log`, or the `last_test_message` cache from recording the diagnostic
- **AND** the default `limit` is 10; max is 50
- **AND** the response includes a `meta.deep_link` field pointing to `/audit-log?key=<canonical-key>` for the full reel

#### Scenario: Breaks-catalogue endpoint
- **WHEN** `GET /api/secrets/breaks-catalogue?provider=<p>` is called
- **THEN** the response is `ApiResponse<BreakEntry[]>` reading from `public.provider_feature_catalogue`
- **AND** each `BreakEntry` includes `butler`, `feature`, `severity` (one of `high` / `medium` / `low`), `required_scopes` (jsonb array)
- **AND** when `?provider=` is omitted, the endpoint returns the full catalogue keyed by provider in `meta.by_provider`

#### Scenario: Breaks-catalogue degraded source is flagged, never a false empty
- **WHEN** `GET /api/secrets/breaks-catalogue` is called and the shared credential pool is unreachable
- **THEN** the response is still HTTP 200 with `data: []` and `meta.catalogue_available: false`
- **BECAUSE** an empty catalogue must not read as "no breaks tracked for this provider" when the pool itself could not be queried -- mirrors the fleet-wide `meta.<flag>` degraded-envelope convention (see CLAUDE.md API Conventions). A legitimately-absent `provider_feature_catalogue` table (pre-migration) is a different case: it is NOT flagged and keeps `data: []` with `catalogue_available` absent (an honest empty, not a degraded source).
