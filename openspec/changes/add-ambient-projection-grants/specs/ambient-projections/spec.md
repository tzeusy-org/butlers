## ADDED Requirements

### Requirement: Owner-minted projection grants

The owner SHALL mint, list and revoke ambient projection grants only through an owner-authenticated dashboard session (owner cookie or owner key): `POST /api/projections`, `GET /api/projections`, `POST /api/projections/{id}/rotate` and `DELETE /api/projections/{id}`. Each grant SHALL record a label, a surface kind (`wall_display`, `eink`, `home_assistant` or `other`), an audience class (`household` or `owner_private`), exactly one route scope (`glance`), an expected fetch interval between one minute and one day, its creation and revocation times, and its last fetch time and status. The token SHALL be generated server-side with at least 256 bits of entropy and returned only in the mint response and in a rotate response. Only the SHA-256 digest of the token SHALL be stored, and no list or revoke response SHALL carry the token or the digest. Grants SHALL live in `public.ambient_projections`, written only by the dashboard API's owning login: row-level security SHALL admit SELECT only, and runtime roles' INSERT, UPDATE, DELETE and TRUNCATE privileges SHALL be revoked. Residual risk, accepted by owner decision bu-xfhsrz (option B): every butler runtime role can read the token digests, which do not let it present a token. Rotation is one atomic verb, `POST /api/projections/{id}/rotate`: a single UPDATE replaces the stored digest of an active grant, so the old token stops resolving at commit, the new token is returned only in that response, and the grant keeps its id, label, scope and last-fetch history; a revoked or unknown grant answers 404. Rollback is revoking every grant.

ID: REQ-ambient-projections-001
Source: bu-q7vx1q.11; owner decision bu-xfhsrz option B (2026-10-04); owner decision 2026-11-01 option A
Scope: v1-mandatory

#### Scenario: Mint returns the token once and stores only its digest

- **WHEN** the owner mints a grant
- **THEN** the response SHALL carry the token and the glance path once
- **AND** the stored row SHALL hold the token's SHA-256 digest and not the token
- **AND** `GET /api/projections` SHALL list the grant without a token or digest

#### Scenario: Rotate swaps the token in one owner verb

- **WHEN** the owner calls `POST /api/projections/{id}/rotate` on an active grant
- **THEN** the response SHALL carry the new token and the unchanged glance path once
- **AND** the old token SHALL answer 404 on the glance route from that moment, and the new token SHALL answer 200
- **AND** the grant's id, label, scope and last-fetch history SHALL be unchanged
- **AND** a request without owner authentication, or bearing a grant token, SHALL be refused with 401, and a revoked or unknown grant SHALL answer 404

#### Scenario: Minting needs an owner session

- **WHEN** `POST /api/projections` is called without owner authentication
- **THEN** the response SHALL be 401 and no grant SHALL be created

#### Scenario: A runtime role cannot write grants

- **WHEN** a butler runtime role inserts or updates a row of `public.ambient_projections`, even after a bootstrap rerun re-grants it the DML privilege
- **THEN** the insert SHALL fail with `insufficient_privilege` and the update SHALL change no row


#### Scenario: Schema-local rollback preserves the shared grant boundary

- **WHEN** one schema leaves core291 while another schema still consumes the shared ambient projection grant store
- **THEN** the complete store, rows, owner, RLS, policies and privileges SHALL remain intact
- **AND** the final consumer SHALL remove the boundary only when the store is empty; any live or revoked grant row or unestablished consumer history SHALL refuse destructive rollback with roll-forward guidance
- **AND** an empty-store downgrade and ordinary-owner re-upgrade SHALL preserve grant authority and the served290 predecessor

### Requirement: Route-scoped grant boundary

`OwnerAuthMiddleware` SHALL accept a projection grant only on the exact route its scope names, `GET /api/projections/{id}/glance`, with the token in the `X-Projection-Token` header or a single `token` query parameter (never both). A matching grant SHALL be verified by a constant-time digest comparison, SHALL NOT create an owner principal or session, and SHALL be refused on every other route, which falls through to owner authentication. A non-GET request bearing a grant SHALL be refused with 405 and a request with a body with 400. An unknown, revoked or mismatched grant, including a grant for a different projection, SHALL answer 404 so that a grant's existence is not disclosed. A store failure SHALL fail closed. Each successful grant fetch SHALL stamp the grant's last fetch time and status (`ok`, or `degraded` when a state source failed). A token carried in the URL can appear in proxy access logs; surfaces SHOULD prefer the header, and the glance route SHALL be excluded from request tracing.

ID: REQ-ambient-projections-002
Source: bu-q7vx1q.11 acceptance criteria (grants 1 to 4); owner decision bu-xfhsrz option B
Scope: v1-mandatory

#### Scenario: The glance needs a grant

- **WHEN** `GET /api/projections/{id}/glance` is called with no credential
- **THEN** the response SHALL be 401
- **AND** with that projection's token the response SHALL be 200 and the grant's last fetch SHALL be stamped

#### Scenario: A grant is refused off its route

- **WHEN** a valid grant token is presented on `GET /api/approvals`
- **THEN** the response SHALL be 401, as for any unauthenticated request
- **AND** presented on another projection's glance route, the response SHALL be 404

#### Scenario: A grant cannot write

- **WHEN** a valid grant token is presented with `POST /api/projections/{id}/glance`
- **THEN** the response SHALL be 405 and no handler SHALL run

#### Scenario: A revoked grant is not found

- **WHEN** the owner revokes a grant and its token is presented on its glance route
- **THEN** the response SHALL be 404

### Requirement: Deterministic expiring glance

`GET /api/projections/{id}/glance` SHALL return, in the standard `ApiResponse` envelope, `state_class`, `line` (at most 48 characters), `needs_you` (the attention item count), `top_item` (`kind`, `label`, `door_url`, or null), `as_of`, `valid_until` (`as_of` plus twice the grant's expected fetch interval), `coverage` (`ok`, `failed` and `stale` source names) and `audience_class`. It SHALL compose the dashboard state with the same reader and classify it with the same `classify` as `GET /api/dashboard/briefing`, so both return the same `state_class` for the same state, and it SHALL NOT call a model. A failed state source SHALL appear in `coverage.failed` and SHALL never compose `quiet`; a classifier exception SHALL compose `degraded`. For a `household` grant, `top_item` SHALL carry only its kind and a fixed label for that kind and no door, so no free text from an attention item (names, health facts or amounts) reaches a household surface. The owner MAY preview a grant's glance with an owner session; that preview SHALL NOT stamp the grant's last fetch.

ID: REQ-ambient-projections-003
Source: bu-q7vx1q.11 acceptance criteria (glance 1 to 5); owner decision 2026-11-01 option A
Scope: v1-mandatory

#### Scenario: A failed source with nothing waiting is degraded

- **WHEN** one state source fails and no attention item exists
- **THEN** `state_class` SHALL be `degraded` and the source SHALL be listed in `coverage.failed`

#### Scenario: The glance expires

- **WHEN** a glance is served for a grant with a five-minute fetch interval
- **THEN** `valid_until` SHALL be ten minutes after `as_of`

#### Scenario: A household glance carries no personal text

- **WHEN** the top attention item's description names a person, an amount and a health fact
- **THEN** a household glance SHALL contain none of them
- **AND** an owner-private glance of the same state SHALL contain them

#### Scenario: One attention model

- **WHEN** the glance and `GET /api/dashboard/briefing` are computed for the same state
- **THEN** both SHALL return the same `state_class`
- **AND** the glance SHALL make no model call

### Requirement: Glance formats for dumb surfaces

The glance route SHALL accept `format=json` (the default envelope above), `format=text` or `format=png`; any other format SHALL be refused with 422. The text and PNG formats SHALL render the same composed glance, one fact per line, and SHALL print `valid_until` (in UTC, to the minute) so a surface that cannot parse JSON still shows when its picture stops being true. The PNG SHALL be 1-bit, sized by `width` and `height` between 128 and 1600 pixels (default 400 by 300), and SHALL keep the `valid_until` line on its bottom edge however much text precedes it. A household grant's text and PNG SHALL carry no more than its JSON glance. Grant fetches of one projection within 30 seconds SHALL reuse one composed glance (a per-grant cache), and each such fetch SHALL still stamp the grant's last fetch; an owner preview SHALL always recompose and SHALL NOT fill the cache. A revoked grant never reaches the cache, because the grant boundary answers 404 first.

ID: REQ-ambient-projections-005
Source: bu-q7vx1q.11 acceptance criteria (glance 2, PNG); bu-q7vx1q.95
Scope: v1-mandatory

#### Scenario: Text and PNG print their expiry

- **WHEN** a household grant fetches `format=text` and `format=png`
- **THEN** the text SHALL end with `Valid until` and the glance's `valid_until`
- **AND** the PNG SHALL be a 1-bit image of the requested size whose bottom edge carries the expiry
- **AND** neither SHALL contain a name, amount or health fact from an attention item

#### Scenario: Format fetches share one composition

- **WHEN** a surface fetches its JSON glance and then its PNG within 30 seconds
- **THEN** the dashboard state SHALL be composed once and both fetches SHALL stamp the grant
- **AND** an owner preview in between SHALL compose afresh

### Requirement: Consumer liveness

A projection surface that stops fetching SHALL become visible to the owner. Each active grant whose last fetch time, or its creation time if it has never fetched, is older than twice its expected fetch interval (the moment that surface's last glance passed its `valid_until`) SHALL contribute exactly one low-severity attention item with `source = "projection"` to the dashboard attention model that `GET /api/dashboard/briefing` and the glance share. The item SHALL name the grant's label and how long ago the surface last fetched. The condition SHALL be derived when the attention model is read, from `public.ambient_projections` through a role that may only SELECT it: it SHALL write nothing, schedule nothing and call no model, and the item SHALL disappear on the surface's next successful grant fetch. A revoked grant SHALL contribute no item. The glance served to a surface under its own grant SHALL omit that surface's own item, because the surface is fetching. A household glance SHALL show the item only as its fixed kind label. A failure to read the grants SHALL record `projections` as a failed source rather than read as no lapsed surface.

ID: REQ-ambient-projections-004
Source: bu-q7vx1q.11 acceptance criteria (grants 5); bu-q7vx1q.95
Scope: v1-mandatory

#### Scenario: A lapsed surface opens one item until it fetches

- **WHEN** a grant with a five-minute interval has not fetched for eleven minutes
- **THEN** the attention model SHALL carry exactly one `projection` item for it, however often it is read
- **AND** after that surface fetches its glance under its grant, the item SHALL be gone

#### Scenario: A fresh or revoked grant raises nothing

- **WHEN** a grant fetched within twice its interval, or a grant is revoked
- **THEN** it SHALL contribute no attention item

#### Scenario: An unreadable grant table is not calm

- **WHEN** the grant table cannot be read
- **THEN** `projections` SHALL be listed as a failed source and the state SHALL NOT compose `quiet`
