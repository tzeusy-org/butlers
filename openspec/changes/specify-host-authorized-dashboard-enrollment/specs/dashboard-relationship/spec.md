## MODIFIED Requirements

### Requirement: Owner-only authorization for entity endpoints

The entity endpoints under `/api/relationship/entities/*` MUST enforce owner-only authorization.
They expose both mutation surfaces that mint, merge, archive, or forget entities AND read
surfaces that return raw contact-fact `object` values (emails, phone numbers, social handles,
addresses) -- which are PII. The owner-only authorization gate from
`about/heart-and-soul/security.md:18-22` and `rfcs/0007:309` (`'owner' = ANY(e.roles)`) MUST
apply to both write and PII-bearing read surfaces; one without the other leaves a leak hole.

Before this relationship-domain assertion runs, the centralized
`dashboard-owner-auth` boundary MUST authenticate the HTTP caller. The existence
of an owner-role entity is not caller authentication. A missing, expired,
revoked, unconfigured, corrupt, or unavailable transport authority MUST expose
no entity data and apply no mutation before the relationship pool or owner row
is read.

**Clause 12a — Writes (mutations).** Every `POST/PATCH/DELETE` under
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

**Clause 12b — Reads (PII-bearing).** The same owner-only gate MUST apply to the following
GET endpoints because they return raw contact-fact `object` values (emails / phones /
handles / addresses) or aliased identity links whose exposure through the shared
`DASHBOARD_API_KEY` would leak PII to any caller reaching the API surface:

- `GET /api/relationship/entities/queue`
- `GET /api/relationship/entities/search`
- `GET /api/relationship/entities/{id}/contacts`
- `GET /api/relationship/entities/{id}/neighbours`
- `GET /api/relationship/entities/{id}/activity`
- `GET /api/relationship/plex/halo`

The list-only `GET /api/relationship/entities` and per-entity timeline / notes /
interactions / gifts / loans endpoints (which do NOT surface raw contact-fact `object`
values) inherit the existing dashboard session boundary and are not within scope of this
gate. Any future change that adds raw contact-fact values to those responses MUST extend
the gate to the affected endpoint.

**Clause 12c — Deploy gate.** In any non-`dev` environment, daemon startup MUST fail with
a fatal error if `DASHBOARD_API_KEY` is unset. The dev-time "no API key → auth disabled"
shortcut at `src/butlers/api/app.py:246` is incompatible with shipping the entity endpoints.
A guardrail test (tasks.md §12.8) MUST exercise this invariant.

For the adopted target, Clause 12c's fatal-startup behavior is narrowed to an
image that lacks the selected E1/E2 implementation or cannot establish readable,
consistent owner-auth state. Once the separately adopted implementation can
enter `keyless_unenrolled`, a non-`dev` process with an intentionally absent key
MAY start in that state, but every dashboard API remains behind the central
session boundary except health/readiness, content-blind auth status, and the
selected minimal enrollment surface. No entity endpoint becomes reachable
until host-authorized enrollment issues a valid session. Unknown, corrupt, or
unavailable auth state still fails closed and MUST NOT use absent-key startup as
a pass-through.

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
- **WHEN** the daemon starts with `BUTLERS_ENV != 'dev'` and `DASHBOARD_API_KEY` unset and the adopted keyless owner-auth implementation is absent, disabled, corrupt, or unavailable
- **THEN** startup MUST fail with a fatal error referencing the missing key or unavailable owner-auth state
- **AND** no entity endpoint MUST become reachable

#### Scenario: Adopted keyless production starts unenrolled and protected

- **WHEN** the selected E1/E2 implementation is active, auth state is readable and consistent, and a non-`dev` process starts with `DASHBOARD_API_KEY` intentionally unset
- **THEN** the process MAY start in `keyless_unenrolled` without admitting an entity API request
- **AND** no entity endpoint SHALL become reachable until host-authorized enrollment issues a valid owner session and the relationship owner-role assertion also succeeds
- **AND** an arbitrary first or same-origin visitor SHALL receive no entity data or mutation authority

## Source References

- Non-Negotiable Rule 1 (`about/heart-and-soul/vision.md`): one user, one instance, full sovereignty.
- `about/heart-and-soul/security.md`: owner/host trust boundary and credential non-disclosure.
- RFC 0007 (`about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md`): dashboard session and response contracts.
- `dashboard-owner-auth` REQ-dashboard-owner-auth-002, REQ-dashboard-owner-auth-004, and REQ-dashboard-owner-auth-005.
