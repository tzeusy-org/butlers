## MODIFIED Requirements

### Requirement: System Prompt Versioning API
The dashboard SHALL expose owner-only read, compare-and-swap update, and version-history operations
for a butler's owner-operations overlay at the existing prompt route paths. The surface SHALL
distinguish immutable git-roster identity from mutable overlay content and SHALL never label an
overlay or legacy full-replacement row as the complete current system prompt.
All prompt routes SHALL pass fail-closed dashboard owner control before body buffering, database-pool
acquisition, roster-content reads, or protected-state observation. Unconfigured owner control SHALL
return 503; a missing or mismatched credential SHALL return 401. `authenticated_principal()` SHALL
provide attribution only after admission. Unknown roster names SHALL return 404 before prompt-pool
acquisition.
The current response SHALL report resolved roster identity content and digest; overlay content,
active state, version, source `owner_operations`, trust `trusted_owner_input`, timestamp, and
server-derived actor; the selected composition mode and mode version; the ordered layer names; and
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
`roster_digest`. An exact no-op SHALL emit no audit event. The owner-only
`PUT /api/butlers/{name}/prompt/mode` route SHALL accept exactly `mode` (`roster_overlay` or
rollback-only `legacy_full_replacement`), `expected_mode_version`, and
`acknowledge_identity_replacement`. It SHALL use the same pre-body owner control, dedicated pool,
per-agent serialization, compare-and-swap conflict, content-blind privacy, and atomic audit rules as
overlay PUT. Selecting legacy mode SHALL require an open approved rollback window, an existing
legacy head, and `acknowledge_identity_replacement=true`. A committed transition SHALL emit
`butler.prompt_mode_changed` with target equal to the canonical agent name and metadata limited to
`mode_version`, `from_mode`, `to_mode`, `overlay_version`, and `roster_digest`. An exact no-op SHALL
emit no audit event.
Rollback-window authority SHALL come only from server-side deployment configuration
`BUTLERS_PROMPT_LEGACY_ROLLBACK_UNTIL`, parsed as an RFC 3339 UTC timestamp. A missing, malformed,
or expired value SHALL mean closed. No request field, prompt/mode row, MCP tool, runtime session, or
generic API route SHALL open or extend the window. The migration-seeded
`precutover_legacy_hold` SHALL be a distinct read-only compatibility state: it SHALL NOT require or
claim owner selection, SHALL NOT be accepted as a mode PUT target, and SHALL NOT be re-enterable
after an agent leaves it.

ID: REQ-dashboard-butler-management-001
Source: specify-roster-identity-owner-operations-overlay design D4 and D7; heart-and-soul/security.md Dashboard and API Authentication
Scope: v1-mandatory

#### Scenario: Read current prompt
- **WHEN** an authenticated owner calls `GET /api/butlers/{name}/prompt` for a known roster agent
- **THEN** the response separates resolved roster identity from the current owner-operations overlay
- **AND** it reports source/trust/version metadata, roster digest, closed layer order, and `semantic_enforcement=false`
- **AND** it reports the selected composition mode and mode version, labeling legacy mode as identity-replacing
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
- **WHEN** owner control is not configured and any prompt route is called
- **THEN** the API returns 503 before body buffering, pool acquisition, roster reads, or protected-state access
- **AND** no audit row claims an authenticated action

#### Scenario: Missing or wrong owner credential is denied
- **WHEN** owner control is configured but the caller omits or mismatches the credential
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
- **WHEN** an authenticated owner submits a valid divergent mode with the current `expected_mode_version`
- **THEN** the mode route appends exactly one mode-history version
- **AND** `butler.prompt_mode_changed` with the specified target and metadata commits in the same transaction
- **AND** no prompt text enters the mode response or audit event

#### Scenario: Concurrent mode writers produce one winner
- **WHEN** two authenticated requests submit different modes from the same observed mode version
- **THEN** per-agent serialization permits at most one transition to commit
- **AND** the other returns 409 with the current mode version and no prompt text

#### Scenario: Legacy rollback requires explicit acknowledgement
- **WHEN** an authenticated owner selects `legacy_full_replacement` during the approved rollback window
- **THEN** the change succeeds only when a legacy head exists and `acknowledge_identity_replacement=true`
- **AND** a missing acknowledgement, closed window, or absent legacy head returns a fixed content-blind conflict without a write

#### Scenario: Rollback window fails closed
- **WHEN** `BUTLERS_PROMPT_LEGACY_ROLLBACK_UNTIL` is absent, malformed, or not later than the current UTC time
- **THEN** a request for `legacy_full_replacement` returns the fixed content-blind conflict without a write
- **AND** no request or stored row can override that closed result

#### Scenario: Owner can leave expired rollback mode
- **WHEN** rollback configuration is closed while mode history still selects `legacy_full_replacement`
- **THEN** the owner projection reports the expired blocked state without prompt text in error metadata
- **AND** an authenticated compare-and-swap transition to `roster_overlay` remains available
- **AND** selecting or re-entering `legacy_full_replacement` remains denied

#### Scenario: Pre-cutover hold is not an owner rollback target
- **WHEN** an existing agent is still in migration-seeded `precutover_legacy_hold`
- **THEN** the owner projection labels it as unreviewed pre-cutover compatibility state
- **AND** the mode route accepts a transition out to `roster_overlay` but rejects `precutover_legacy_hold` as an input mode
- **AND** no audit evidence claims the owner selected the seeded hold

#### Scenario: Dashboard discloses the semantic limit
- **WHEN** the owner views or edits an overlay
- **THEN** the interface persistently explains that roster identity remains structurally present but natural-language conflicts are not mechanically prevented
- **AND** activation requires explicit owner confirmation of that warning

#### Scenario: Non-owner evidence is content-blind
- **WHEN** any prompt route succeeds, is denied, conflicts, or fails
- **THEN** audit, logs, metrics, traces, and error bodies outside the authenticated owner data response contain only allowlisted metadata
- **AND** roster identity and overlay text are absent
