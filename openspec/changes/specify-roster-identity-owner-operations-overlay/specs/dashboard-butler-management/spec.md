## MODIFIED Requirements

### Requirement: System Prompt Versioning API

The dashboard SHALL expose owner-only read, compare-and-swap update, and version-history operations
for a butler's owner-operations content at the existing prompt route paths; these SHALL NOT
select or activate a goal system prompt. The surface SHALL
distinguish versioned git-roster guidance from mutable owner operations and SHALL never label an
overlay or legacy full-replacement row as the complete current goal system prompt. Leads SHALL use lead policy and owner profile and
children their briefs under RFC0038 D15; no route SHALL enable domain-daemon prompt composition.
All prompt routes SHALL pass fail-closed dashboard owner control before body buffering, database-pool
acquisition, roster-content reads, or protected-state observation. Unavailable authoritative owner-auth state SHALL
return 503; a missing or mismatched credential SHALL return 401. `authenticated_principal()` SHALL
provide attribution only after admission. Unknown roster names SHALL return 404 before prompt-pool
acquisition.
The current owner response SHALL report resolved versioned roster guidance content and digest; overlay content,
active state, version, source `owner_operations`, trust `trusted_owner_input`, timestamp, and
server-derived actor; historical mode and mode-version provenance labeled non-authorizing for goals; applicable guidance source names; and
`semantic_enforcement=false`. The update body
SHALL contain exactly `overlay`, `active`, and `expected_version`. Overlay text SHALL be at most
65,536 UTF-8 bytes and stored byte-for-byte. NUL, invalid UTF-8, reserved overlay delimiters, an
active blank overlay, a negative/non-integer version, or an unknown field SHALL return a fixed 422
without echoing content. The API SHALL permit a disabled state to carry an empty overlay.
Valid updates SHALL serialize per agent in one transaction. An exact state match SHALL be a 200
no-op at the current version. A divergent update with matching `expected_version` SHALL append one
version and atomic metadata-only audit evidence. A divergent stale update SHALL return 409 with the
current version and no prompt text. Persistence or audit failure SHALL commit neither. Read,
history, errors, audit, logs, metrics, and traces outside the owner response SHALL be content-blind.
A committed divergent overlay update SHALL emit `butler.prompt_overlay_set` with target equal to the
canonical agent name and metadata limited to `version`, `active`, `byte_count`, `digest`, and
`roster_digest`. An exact no-op SHALL emit no audit event. The historical `PUT /api/butlers/{name}/prompt/mode` contract SHALL NOT act as a goal prompt
selector. Its integrity duties SHALL remain explicit: exact `mode`, `expected_mode_version` and
`acknowledge_identity_replacement` shape; pre-body owner control; dedicated pool; per-agent CAS;
identical-state no-op; at most one divergent winner; immutable history; and atomic content-blind
`butler.prompt_mode_changed` evidence limited to canonical target, `mode_version`, `from_mode`,
`to_mode`, `overlay_version` and `roster_digest`. They SHALL NOT authorize an append that selects an
obsolete goal mode. No replacement mode wire operation is selected here.
Historical legacy selection required explicit acknowledgement, an existing legacy head and the
approved deployment-owned `BUTLERS_PROMPT_LEGACY_ROLLBACK_UNTIL` RFC3339 UTC window, closed when
missing, malformed or expired. Requests, stored rows, MCP tools, runtimes and generic API paths
SHALL NOT create or extend operational rollback authority. An acknowledged/open-window request
SHALL still fail closed without a write if it attempts to select a legacy goal prompt.
`precutover_legacy_hold` SHALL remain truthful historical seeded state, never fabricated owner
selection, a new-agent default, an input target or a re-enterable live goal mode. Owner projections
SHALL disclose obsolete/expired state without content in error metadata. A separately authorized
safe exit SHALL retain owner control, CAS and atomic audit but SHALL NOT reopen goal legacy
selection, rewrite history or activate an overlay automatically.
The central `dashboard-owner-auth` boundary SHALL admit a valid configured
`X-API-Key` or a valid server-managed owner session before protected body reads,
domain-pool acquisition, caches or handlers. Passkey verification issues a session;
it is not a new per-route credential. Cookie-backed unsafe actions additionally
require independent synchronizer CSRF and exact Origin validation. Unavailable
authoritative auth state returns safe `503`; missing, expired, revoked or invalid
caller authority returns `401`. An absent API key alone is not unavailability when
healthy keyless session authority exists. Domain checks remain mandatory after
central authentication; auth-store reads necessary for verification are distinct
from forbidden pre-authentication domain access.

ID: REQ-dashboard-butler-management-001
Source: specify-roster-identity-owner-operations-overlay design D4 and D7; heart-and-soul/security.md Dashboard and API Authentication
Scope: v1-mandatory

#### Scenario: Read current prompt
- **WHEN** an authenticated owner calls `GET /api/butlers/{name}/prompt` for a known roster agent
- **THEN** the response separates versioned roster guidance from owner-operations content and reports source/trust/version, digest, applicable guidance sources and `semantic_enforcement=false`
- **AND** historical mode/version provenance is labeled identity-replacing history with no goal-selection authority, not the complete goal system prompt
- **AND** it sets `Cache-Control: no-store`

#### Scenario: Update prompt snapshots history
- **WHEN** an authenticated owner submits a valid divergent overlay state with the current `expected_version`
- **THEN** `PUT /api/butlers/{name}/prompt` appends exactly one `owner_operations_overlay` version
- **AND** `butler.prompt_overlay_set` with the specified target and metadata commits in the same transaction
- **AND** the response reports the new version without presenting the overlay as roster identity

#### Scenario: Prompt history list
- **WHEN** an authenticated owner calls `GET /api/butlers/{name}/prompt/history?limit=20`
- **THEN** the response is ordered by version descending and uses bounded pagination
- **AND** each row reports its exact provenance kind, including `legacy_full_replacement` for migrated historical rows
- **AND** no legacy row is relabeled as an owner-operations overlay

#### Scenario: Owner control is unconfigured
- **WHEN** authoritative owner-auth state is unavailable and any prompt route is called
- **THEN** the API returns 503 before body buffering, pool acquisition, roster reads, or protected-state access
- **AND** no audit row claims an authenticated action

#### Scenario: Missing or wrong owner credential is denied
- **WHEN** authoritative auth state is healthy but the caller lacks valid central key-or-session authority
- **THEN** the API returns 401 before body buffering, pool acquisition, roster reads, or protected-state access
- **AND** the error contains no roster, overlay, digest, or history content

#### Scenario: Unknown roster agent is rejected before prompt access
- **WHEN** an authenticated owner names an agent absent from the current roster
- **THEN** the API returns 404 before acquiring the dedicated prompt pool
- **AND** no row is read or appended for that name

#### Scenario: Invalid overlay is rejected content-blind
- **WHEN** an update violates the body shape, size, active-content, encoding, or reserved-delimiter rules
- **THEN** the API returns one fixed 422 category without echoing submitted content
- **AND** no version or audit row is written

#### Scenario: Exact retry is a no-op
- **WHEN** a valid submitted overlay and active state equal the current state
- **THEN** the API returns 200 with the unchanged version even if `expected_version` is stale
- **AND** it performs no prompt-history or audit write

#### Scenario: Concurrent divergent writers produce one winner
- **WHEN** two authenticated updates submit different valid states from the same observed version
- **THEN** per-agent serialization permits at most one changed state to commit
- **AND** the other returns 409 with the current version and no prompt text

#### Scenario: Concurrent identical writers converge
- **WHEN** two authenticated updates submit the same valid state concurrently
- **THEN** exactly one version and audit record commit
- **AND** the other request returns the resulting version as a 200 no-op

#### Scenario: Persistence and audit are atomic
- **WHEN** overlay persistence or audit append fails before commit
- **THEN** neither row commits and no response claims success
- **AND** the prior active state and version remain unchanged

#### Scenario: Owner changes composition mode with compare-and-swap
- **WHEN** an authenticated owner submits a divergent historical mode request with a current `expected_mode_version`
- **THEN** CAS, per-agent serialization, immutable history and atomic metadata-only `butler.prompt_mode_changed` remain required integrity duties for any separately authorized history transition
- **AND** a request that would select an obsolete goal prompt is refused without a write even with a matching version
- **AND** no response or audit contains prompt text, and this amendment supplies no replacement mode operation

#### Scenario: Concurrent mode writers produce one winner
- **WHEN** concurrent requests attempt a separately authorized mode-history transition from the same observed version
- **THEN** the retained CAS integrity boundary permits at most one divergent commit and returns content-blind 409 for a stale divergent version, with identical-state retries a no-op
- **AND** requests to activate an obsolete goal selector are all denied without mode or audit writes

#### Scenario: Legacy rollback requires explicit acknowledgement
- **WHEN** an authenticated owner requests `legacy_full_replacement`, with or without acknowledgement, a legacy head or an open old window
- **THEN** the route grants no goal-prompt exception and the obsolete goal selection fails content-blind without a write
- **AND** historical/operational review retains the old explicit acknowledgement, existing-head and approved-window duties without treating them as sufficient D15 authority

#### Scenario: Rollback window fails closed
- **WHEN** `BUTLERS_PROMPT_LEGACY_ROLLBACK_UNTIL` is absent, malformed or not later than current UTC time
- **THEN** legacy selection remains closed with a fixed content-blind conflict and no write
- **AND** no request or stored row can override closure; even an open historical value cannot authorize a legacy goal mode

#### Scenario: Owner can leave expired rollback mode
- **WHEN** historical mode evidence still records `legacy_full_replacement` after its rollback authority has closed
- **THEN** the owner projection reports obsolete/expired blocked state without prompt text in error metadata
- **AND** a separately authorized safe exit retains owner authentication, compare-and-swap, atomic audit and original provenance without automatically activating an overlay
- **AND** selecting or re-entering legacy goal composition remains denied

#### Scenario: Pre-cutover hold is not an owner rollback target
- **WHEN** historical evidence records migration-seeded `precutover_legacy_hold`
- **THEN** the owner projection labels it as unreviewed historical state, not owner selection or live goal composition
- **AND** no mode route accepts it as a target or permits re-entry or a new-agent assignment
- **AND** any authorized exit preserves history and does not silently activate owner operations

#### Scenario: Dashboard discloses the semantic limit
- **WHEN** the owner views or edits operations content
- **THEN** the interface persistently distinguishes versioned guidance, mutable operations and the separately governed goal prompt, and explains that natural-language conflicts are not mechanically prevented
- **AND** an owner-management change requires explicit confirmation of that warning, which grants no runtime activation or goal authority

#### Scenario: Non-owner evidence is content-blind
- **WHEN** any prompt route succeeds, is denied, conflicts, or fails
- **THEN** audit, logs, metrics, traces, and error bodies outside the authenticated owner data response contain only allowlisted metadata
- **AND** roster identity and overlay text are absent
