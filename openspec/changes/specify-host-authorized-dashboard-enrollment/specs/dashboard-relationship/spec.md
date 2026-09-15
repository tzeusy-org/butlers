## MODIFIED Requirements

### Requirement: Owner-only authorization for entity endpoints

- The entity endpoints under `/api/relationship/entities/*` MUST enforce owner-only authorization.
They expose both mutation surfaces that mint, merge, archive, or forget entities AND read
surfaces that return raw contact-fact `object` values (emails, phone numbers, social handles,
addresses) -- which are PII. The owner-only authorization gate from
`about/heart-and-soul/security.md:18-22` and `rfcs/0007:309` (`'owner' = ANY(e.roles)`) MUST
apply to both write and PII-bearing read surfaces; one without the other leaves a leak hole.
- Before this relationship-domain assertion runs, the centralized
`dashboard-owner-auth` boundary MUST authenticate the HTTP caller. The existence
of an owner-role entity is not caller authentication. A missing, expired,
revoked, unconfigured, corrupt, or unavailable transport authority MUST expose
no entity data and apply no mutation before the relationship pool or owner row
is read.
- **Clause 12a — Writes (mutations).** Every `POST/PATCH/DELETE` under
`/api/relationship/entities/*` MUST resolve the caller to an owner-role entity
per the `'owner' = ANY(e.roles)` pattern and return HTTP 403 with the envelope
`{ code: 'owner_required' }` otherwise. The gate applies to the exact endpoint set:
- `POST /api/relationship/entities`
- `POST /api/relationship/entities/{id}/merge`
- `POST /api/relationship/entities/{id}/archive`
- `POST /api/relationship/entities/{id}/promote-tier`
- `DELETE /api/relationship/entities/{id}`
- `POST /api/relationship/entities/queue/dismiss`
- `POST /api/relationship/entities/{id}/contacts`
- `DELETE /api/relationship/entities/{id}/contacts/{pred}/{valueHash}`
- `POST /api/relationship/entities/{id}/notes`
- `POST /api/relationship/entities/{id}/interactions`
- `POST /api/relationship/entities/{id}/gifts`
- `POST /api/relationship/entities/{id}/reach-out-drafts`
- **Clause 12b — Reads (PII-bearing).** The same owner-only gate MUST apply to the following
GET endpoints because they return raw contact-fact `object` values (emails / phones /
handles / addresses) or aliased identity links whose exposure through the shared
`DASHBOARD_API_KEY` would leak PII to any caller reaching the API surface:
- `GET /api/relationship/entities/queue`
- `GET /api/relationship/entities/search`
- `GET /api/relationship/entities/{id}/contacts`
- `GET /api/relationship/entities/{id}/neighbours`
- `GET /api/relationship/entities/{id}/activity`
- `GET /api/relationship/plex/halo`
- The list-only `GET /api/relationship/entities` and per-entity timeline / notes /
interactions / gifts / loans endpoints (which do NOT surface raw contact-fact `object`
values) inherit the existing dashboard session boundary and are not within scope of this
gate. Any future change that adds raw contact-fact values to those responses MUST extend
the gate to the affected endpoint.
- **Clause 12c — Deploy gate.** In any non-`dev` environment, daemon startup MUST fail with
a fatal error if `DASHBOARD_API_KEY` is unset. The dev-time "no API key → auth disabled"
shortcut at `src/butlers/api/app.py:246` is incompatible with shipping the entity endpoints.
A guardrail test (tasks.md §12.8) MUST exercise this invariant.
- After the adopted successor cutover, Clause 12c's legacy missing-key fatal
startup requirement above applies only to images lacking the central owner-auth
implementation. Healthy keyless startup is permitted and all relationship routes
remain protected. Corrupt/unavailable auth state denies protected requests with
503 while exact public health probes remain available; no absent-key pass-through
or first-visitor enrollment occurs. Normal keyless_enrolled restart preserves
credentials and unexpired sessions rather than resetting enrollment.
- The central `dashboard-owner-auth` boundary SHALL admit a valid configured
`X-API-Key` or a valid server-managed owner session before protected body reads,
domain-pool acquisition, caches or handlers. Passkey verification issues a session;
it is not a new per-route credential. Cookie-backed unsafe actions additionally
require independent synchronizer CSRF and exact Origin validation. Unavailable
authoritative auth state returns safe `503`; missing, expired, revoked or invalid
caller authority returns `401`. An absent API key alone is not unavailability when
healthy keyless session authority exists. Domain checks remain mandatory after
central authentication; auth-store reads necessary for verification are distinct
from forbidden pre-authentication domain access.

ID: REQ-dashboard-relationship-001
Source: dashboard-owner-auth successor design D1-D9; existing dashboard-relationship behavior preserved except explicit owner-auth supersession
Scope: v1-mandatory

#### Scenario: Owner request to mutate entity succeeds
- **WHEN** an authenticated request resolves to an entity with `'owner' = ANY(e.roles)` and
  calls `POST /api/relationship/entities/{id}/promote-tier`
- **THEN** the response status MUST be 2xx (per the endpoint's own contract)
- **AND** the gate MUST NOT reject the request

#### Scenario: Non-owner request is rejected with `owner_required`
- **WHEN** an authenticated request resolves to an entity whose `roles` does NOT contain
  `'owner'` and calls any endpoint in clause 12a or 12b
- **THEN** the response status MUST be 403
- **AND** the response body MUST contain `{ code: 'owner_required' }` (envelope form per
  `rfcs/0007:75-87` or unwrapped per relationship-domain convention; the `code` string is
  binding)
- **AND** no mutation MUST be applied
- **AND** no PII MUST be returned

#### Scenario: Missing `DASHBOARD_API_KEY` in production refuses startup
- **WHEN** the daemon starts with `BUTLERS_ENV != 'dev'` and `DASHBOARD_API_KEY` unset and the adopted keyless owner-auth implementation is absent or disabled
- **THEN** startup MUST fail with a fatal error referencing the missing key or unavailable owner-auth state
- **AND** no entity endpoint MUST become reachable

#### Scenario: Adopted keyless production starts unenrolled and protected

- **WHEN** the adopted central owner-auth implementation is active, auth state is readable and consistent, and a non-`dev` process starts with `DASHBOARD_API_KEY` intentionally unset
- **THEN** a fresh initialized process MAY start in `keyless_unenrolled`; an enrolled restart retains `keyless_enrolled` without admitting an entity API request
- **AND** no entity endpoint SHALL become reachable until host-authorized enrollment issues a valid owner session and the relationship owner-role assertion also succeeds
- **AND** an arbitrary first or same-origin visitor SHALL receive no entity data or mutation authority
