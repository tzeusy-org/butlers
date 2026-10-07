## MODIFIED Requirements

### Requirement: Connectors Roster

The `/ingestion/connectors` route SHALL render every listening channel as a
dense roster, not as a card grid.

It SHALL include:

- attention strip when any connector has auth issues, health issues, or an
  additive operational warning;
- rows with health dot, channel glyph/name/kind, function gloss, last-event
  meta, 24h sparkline, auth pill, event/session/cost totals, and disclosure;
- dormant or available connector section with connect actions;
- footer KPI band and add-connector action.

The whole row SHALL be the navigation target to connector detail (click or
keyboard Enter/Space while the row has focus), with the disclosure chevron
kept as a visual cue rather than a separate click target. When a row's auth
pill reads `reauth`, the pill itself SHALL use the typed connector recovery
resolver (see "Ingestion-Originated OAuth page_of_origin Contract"), and it
remains independently clickable above the row's navigation target.

The roster SHALL render structured timestamp-keyed count buckets and independent listening evidence under Per-bucket listening state. Summary-only database polling and existing operational-role, checkpoint, archive, auth and warning authority SHALL remain unchanged; no sibling endpoint or successful count query SHALL establish listening.

ID: REQ-dashboard-ingestion-dispatch-console-005
Source: bu-s11n0s.5 complete-protocol D1-D6; preserved governing requirement at a6f342bd54c207b9e672d7d4e51be6af6d4f2876
Scope: v1-mandatory

#### Scenario: Connector with auth issue appears in attention strip

- **WHEN** at least one connector has `auth.status` requiring action or
  degraded health
- **THEN** the roster renders a compact attention strip above the table
- **AND** each attention item links to the affected connector detail route
- **AND** the connector row displays the same auth state consistently
- **AND** if the row's auth status is `needs_reauth`, its auth pill is itself
  a reauth action rather than a static label

#### Scenario: Dormant connectors are discoverable

- **WHEN** the connector discovery endpoint reports available but unconnected
  connectors
- **THEN** the roster renders an `available` or `dormant` section
- **AND** each dormant row includes the connector's display name, its channel,
  and a connect action
- **AND** the connect action links to `/secrets?focus=u:<provider>` (the
  catalog's own `provider` field), deep-linking straight to that provider's
  credential entry instead of the bare `/secrets` page
- **AND** no per-row description line restates the section eyebrow (the
  discovery catalog carries no per-connector one-liner to show instead)

#### Scenario: Operational warning does not rewrite connector health

- **WHEN** a connector summary carries an `operational_warnings` entry while
  its transport state and liveness remain healthy/online
- **THEN** the connector appears in the attention strip with an operational
  warning label
- **AND** the full warning is readable on the roster row
- **AND** the row's health verdict remains online rather than being rewritten
  as degraded or error
- **AND** if the diagnostic source's additive availability flag is explicitly
  `false`, the roster names that degraded source instead of treating missing
  warnings as an all-clear

#### Scenario: Roster and detail share exact bucket positions

- **WHEN** the same endpoint summary and detail are read with the same canonical window
- **THEN** their count bucket starts and ingested/filtered positions SHALL agree
- **AND** the roster SHALL issue one bounded summary request, never a detail query for each row
- **AND** unavailable listening SHALL remain UNKNOWN independently of readable counts


### Requirement: Connector Detail

The connector detail route SHALL render a two-zone operational detail page for
one connector endpoint.

It SHALL include:

- header band with large channel glyph, display headline, mono meta line, and
  purpose paragraph;
- reauth callout when the connector requires reauthorization;
- KPI strip, 24h histogram, recent events, and incident list;
- OAuth scope list when the connector supports OAuth scope introspection;
- schedule, routing rules, config fields, and safe action controls.

The detail SHALL use actual bucket_start keys and relative range labels under Count-bucket truth. Count and listening availability SHALL remain independent. No unreadable history or legacy cached DTO SHALL be converted into measured zero or LIVE.

ID: REQ-dashboard-ingestion-dispatch-console-006
Source: bu-s11n0s.5 complete-protocol D1-D6; preserved governing requirement at a6f342bd54c207b9e672d7d4e51be6af6d4f2876
Scope: v1-mandatory

#### Scenario: Reauth callout follows connector auth state

- **WHEN** the connector detail response says auth requires reauthorization
- **THEN** the detail page renders a bordered reauth callout with explanatory
  copy and a reauthorize action
- **AND** successful reauthorization updates the auth state and clears the
  callout on refresh
- **AND** unsupported or unavailable OAuth scope state is rendered explicitly
  rather than hidden

#### Scenario: Scope list consumes the OAuth scope capability

- **WHEN** `connector-oauth-scope-surface` fields are available on the connector
  detail response
- **THEN** the detail page renders per-scope status, scope name, verdict, and
  explanatory note
- **AND** no access token, refresh token, or credential secret appears in the
  response or UI

#### Scenario: Sparse observations retain their actual hours

- **WHEN** events exist only at canonical hours 2 and 20
- **THEN** the 24h histogram SHALL retain 24 keyed slots with positives at 2 and 20
- **AND** the final rolling slot SHALL be the closed interval [as_of-1h, as_of), with no future time included
- **AND** an optional open calendar-hour diagnostic SHALL be separate and marked partial/UNKNOWN, never relabel that closed rolling slot
- **AND** filtered-only positives SHALL remain distinct from ingestion


### Requirement: Data States and Robustness

Every ingestion redesigned surface SHALL have explicit loading, empty,
partial-error, and unavailable states. Skeletons may only be transient loading
states. A surface SHALL NOT be considered complete if it remains a skeleton or
fake fixture when live data is unavailable.

A heartbeat/coverage read failure SHALL degrade listening only and preserve independently authoritative event counts, with the existing degraded-source note. An event-count read failure SHALL not draw fabricated zero even if heartbeat evidence is readable.

ID: REQ-dashboard-ingestion-dispatch-console-007
Source: bu-s11n0s.5 complete-protocol D1-D6; preserved governing requirement at a6f342bd54c207b9e672d7d4e51be6af6d4f2876
Scope: v1-mandatory

#### Scenario: Partial backend failure preserves usable sections

- **WHEN** the Timeline events endpoint succeeds but replay history fails for
  one expanded event
- **THEN** the ledger remains usable
- **AND** only the replay-history tab shows an error or unavailable state
- **AND** the error state identifies the failed surface

#### Scenario: Metrics unavailable is distinct from zero

- **WHEN** aggregate metrics cannot be loaded
- **THEN** KPI, sparkline, and pipeline surfaces render an unavailable state
- **AND** they do not render zero values unless the API explicitly reports zero

#### Scenario: Heartbeat query failure preserves committed count visibility

- **WHEN** the count read succeeds and the optional heartbeat query fails inside the registry-locked detail transaction
- **THEN** the response SHALL preserve the measured counts and report listening UNKNOWN
- **AND** the failed optional statement SHALL not abort the parent read/lock transaction
- **AND** the source failure SHALL be named without raw diagnostic content


## ADDED Requirements

### Requirement: Per-bucket listening state

Connector count buckets SHALL report LIVE only from a positive accepted heartbeat for their exact endpoint and DEAF only for a closed bucket inside positively complete receiver recording with no accepted heartbeat; all other missing-history states SHALL be UNKNOWN. Counts SHALL retain independent availability. Stored operational health, current liveness, expected-signal measurability and administrative policy SHALL remain separate. No sibling, checkpoint, unclassified row or current query success SHALL replace exact endpoint recording evidence.

ID: REQ-dashboard-ingestion-dispatch-console-004
Source: bu-s11n0s.5 complete-protocol D1-D6; heart-and-soul/vision.md failure and staleness honesty; owner-timezone-context and dashboard-design-language temporal contracts
Scope: v1-mandatory

#### Scenario: Original missing hours become deaf under actual complete recording

- **WHEN** events are seeded at hours 2 and 20 and a protected paired activation establishes complete recording before hours 5–7 with no accepted heartbeat in those hours
- **THEN** the API SHALL return 24 dense actual bucket_start values, exact counts at 2 and 20, and listening=deaf at 5–7
- **AND** the completeness witness SHALL be actual database-derived recording authority, not fixture-supplied JSON

#### Scenario: Same empty best-effort log remains unknown

- **WHEN** hours 5–7 have no log rows but no protected complete recording witness
- **THEN** those buckets SHALL be UNKNOWN even when the read succeeds
- **AND** the original DEAF outcome SHALL remain unfulfilled by this control alone

#### Scenario: Positive heartbeat is endpoint and bucket specific

- **WHEN** one accepted healthy, degraded or error heartbeat is read for endpoint A in one bucket while B is healthy in another
- **THEN** only A own observed bucket SHALL have LIVE evidence
- **AND** provider health SHALL retain its independent stored state
- **AND** row order and instance fan-in SHALL not cause sibling substitution

#### Scenario: Activation first seen retention and current partial do not certify absence

- **WHEN** a bucket precedes first_seen/activation, intersects the conservative seven-day floor, has invalidated/replaced partition coverage or is still open
- **THEN** missing heartbeat evidence SHALL be UNKNOWN with a closed reason
- **AND** MONTHLY partition survival beyond seven days SHALL not extend the guarantee

#### Scenario: Failed heartbeat query preserves count positives

- **WHEN** event counts are readable while the heartbeat/coverage read fails
- **THEN** counts SHALL remain authoritative and listening SHALL be UNKNOWN
- **AND** the parent transaction SHALL remain usable after optional-query rollback
- **AND** no exception text or coverage internals SHALL appear in the DTO

#### Scenario: Roster detail and older cached responses remain honest

- **WHEN** the same endpoint roster/detail share a canonical window or an old cached DTO lacks its keys/listening contract
- **THEN** current responses SHALL agree by key and old incompatible responses SHALL report unknown/unavailable
- **AND** the roster SHALL retain its bounded summary-only polling

#### Scenario: Direct SQL and marker forgery do not prove empty history

- **WHEN** an unpaired direct refresh or attempted caller marker write occurs after prior coverage
- **THEN** future no-heartbeat buckets SHALL not borrow that old coverage
- **AND** the ordinary direct producer SHALL retain its role and recency compatibility
