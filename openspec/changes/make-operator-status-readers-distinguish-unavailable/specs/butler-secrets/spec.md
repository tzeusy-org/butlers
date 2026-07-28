## MODIFIED Requirements

### Requirement: One Row Template Across All Three Families
The User-tab's six bespoke provider Setup cards SHALL be replaced by one row template applicable to System, User (oauth / token / apikey / webhook variants), and CLI families. Per-provider oddities (OwnTracks webhook URL, Steam ID format, WhatsApp QR-link affordance) SHALL live in a provider-specific drawer opened from the row, not in divergent row chrome.

#### Scenario: Row-template uniformity
- **WHEN** the spine renders any credential from any family
- **THEN** the row has the same vertical rhythm (10px vertical padding), same column layout (sliver | dot | label | subline | right-aligned glyph), and same hairline separators
- **AND** the rendered HTML structure of a System row is identical (modulo `data-*` attributes and content) to the rendered HTML structure of a User row or CLI row

#### Scenario: One spine row per credential concept
- **WHEN** the inventory contains multiple raw rows that resolve to the same credential concept, such as multiple `entity_info.type` rows for one User provider or the same System `key` across butler schemas
- **THEN** the spine renders exactly one row for that credential concept
- **AND** the row preserves the highest-severity state and the relevant shared/local source-target provenance

#### Scenario: Provider drawer for oddities
- **WHEN** a User OAuth row for `owntracks` is expanded
- **THEN** the drawer renders the webhook URL and the regen-secret affordance; no other provider's row renders these fields
- **AND** the drawer is implemented as a per-provider component dispatched by `provider` slug, not by branching the row template

#### Scenario: Guided Telegram user-session setup
- **WHEN** the owner chooses `set up Telegram` from the Passport User credential flow
- **THEN** Passport opens a labelled Telegram setup region with API ID, password-masked API hash, and phone-number fields, plus a link to `my.telegram.org/apps`
- **AND** the region clearly discloses that it enables ingestion of new account-visible direct messages, groups, supergroups, and channels; that no per-chat or per-sender exclusion exists yet; that messages flow through normal Switchboard classification and routing; and that any history is only the separately configured optional backfill
- **AND** the `send code` control remains disabled until the owner acknowledges that account-wide scope, and the server rejects a bypassed request without that acknowledgement
- **AND** the API hash is sent only to the Telegram session-auth flow; the generic raw-credential mutation MUST NOT receive it
- **AND** the authenticated session flow persists a versioned non-secret consent grant and the API ID, API hash, and user session only after successful verification
- **AND** dismissing the inline setup region returns keyboard focus to its `set up Telegram` trigger

#### Scenario: Telegram session status loading is distinct from setup state
- **WHEN** the Passport Telegram region is waiting for its session-status probe
- **THEN** it SHALL render a loading skeleton and SHALL NOT infer that credentials are absent, that setup is required, or that a session is ready
- **AND** it SHALL NOT expose credential inputs or session-auth actions until a successful status response selects the existing setup path.

#### Scenario: Telegram session status is unavailable
- **WHEN** the Passport Telegram session-status probe fails
- **THEN** the region SHALL render a named unavailable state with a retry action that re-queries only the status probe
- **AND** it MUST NOT render the normal setup flow, infer missing credentials or an unready session, expose credential inputs, or initiate session authentication
- **AND** it MUST NOT disclose an API ID, API hash, user session, or any other credential value.

#### Scenario: Successful unready Telegram status keeps the guided setup path
- **WHEN** the Passport Telegram session-status probe succeeds and reports that the session is not ready
- **THEN** the region SHALL render the existing guided setup trigger and credential-status indicators
- **AND** it SHALL NOT render the unavailable state or retry action unless a later status probe fails.

## Source References
- Non-Negotiable Rule 1 (user-federated owner sovereignty)
- Non-Negotiable Rule 4 (deterministic, debuggable infrastructure)
- RFC 0004 (Identity and Contact Resolution)
- RFC 0007 (Dashboard and API Surface)
