## ADDED Requirements

### Requirement: Provider Occurrence and Series Identity

Calendar provider sync SHALL request provider-expanded instances (Google `singleEvents=true`) so the projection holds one `calendar_event_instances` row per visible provider occurrence instead of one row per recurring master. The existing `calendar_events` + `calendar_event_instances` ledger SHALL be reused; no parallel occurrences table SHALL be created.

The provider payload parser SHALL retain the provider series and instance identity it currently drops: `recurringEventId` (the series id) and `originalStartTime` (the immutable scheduled start of the instance, a `dateTime` or a date-only `date`). A provider instance that carries both SHALL be keyed by `(source, recurringEventId, originalStartTime)`. The instance's `origin_instance_ref` SHALL be derived from that key (`{recurringEventId}:{originalStartTime normalized to UTC, or the date for an all-day instance}`) and SHALL NOT embed the instance's current display start. The key and the series id SHALL be carried in the projected event metadata so readers can group occurrences by series. A non-recurring provider event keeps its existing `origin_instance_ref` derivation.

A moved instance (its displayed start or end changed while `originalStartTime` did not) SHALL keep its `origin_instance_ref`, update `starts_at`/`ends_at` in place, and remain one row. A cancelled exception (a provider instance with `status=cancelled`, possibly minimally populated with only its id, `recurringEventId` and `originalStartTime`) SHALL remove or suppress only that occurrence and SHALL NOT delete its siblings or the series. The provider-to-projection mapping SHALL NOT infer series membership from title, time or attendees.

ID: REQ-module-calendar-031
Source: bu-h40h2b.6 acceptance 2-3; bu-vpn5ij; decision A1/B1 (bu-2o1dn8); Google Calendar events.list singleEvents semantics
Scope: v1-mandatory

#### Scenario: One visible occurrence per recurringEventId and originalStartTime

- **WHEN** a provider response contains three instances sharing one `recurringEventId` with distinct `originalStartTime` values
- **THEN** the projection holds exactly three instance rows, one per `(recurringEventId, originalStartTime)`
- **AND** the workspace and the Chronicler calendar adapter each show three occurrences, not one series row
- **AND** the series id is recoverable from each row's metadata

#### Scenario: A moved instance keeps its identity while its window changes

- **WHEN** a later sync delivers the same `(recurringEventId, originalStartTime)` with a different `start`/`end`
- **THEN** the same instance row is updated in place and its `origin_instance_ref` is unchanged
- **AND** the displayed window is the new window
- **AND** no second row is created and no stale copy remains

#### Scenario: A minimally populated cancelled exception suppresses only its occurrence

- **WHEN** the provider delivers `status=cancelled` for one instance with only its id, `recurringEventId` and `originalStartTime`
- **THEN** only that occurrence stops being visible
- **AND** every other occurrence of the series remains projected
- **AND** the absence of title, start or end on the cancelled payload is not an error

#### Scenario: Non-recurring events keep their existing key

- **WHEN** a provider event has no `recurringEventId`
- **THEN** its `origin_instance_ref` derivation and the time-drifted re-sync prune behave as before

#### Scenario: Chronicler idempotency key survives a move

- **WHEN** a provider occurrence is moved after the Chronicler calendar adapter has projected it
- **THEN** the adapter's `calendar:{origin_instance_ref}` source reference is unchanged, so the move revises the same intent instead of creating a second one
- **AND** an occurrence cancelled by the provider is withdrawn only for that source reference

### Requirement: Versioned Cursor Reset and Bounded Occurrence Resync

The sync cursor SHALL record the expansion mode it was issued under as a cursor version. A stored cursor whose version is not the current occurrence-expanded version (including every cursor issued under `singleEvents=false`) SHALL NOT be reused as a `syncToken`; the next sync SHALL perform a bounded full resync with `sync_token=None` and store a cursor under the current version. Provider rejection of a token for a changed query (Google `410 Gone`) SHALL follow the same bounded full-resync path.

A resync SHALL stage its full result and commit it only after the provider has returned a complete result and a fresh cursor: it SHALL upsert every delivered occurrence by its identity, retire previous-version projection rows for that source (the collapsed master rows and any occurrence the provider no longer lists) in the same commit, and only then advance the cursor and `last_synced_at`. A resync that fails, times out or returns a partial result SHALL leave the prior projection and prior cursor untouched, SHALL record the failure in `last_sync_error` (fail-open, no raise into the poller), and SHALL be retried on the next sync tick. A retry of any resync, or a repeated delivery of the same page, SHALL be idempotent: it SHALL converge to the same rows without duplicating or resurrecting a cancelled occurrence.

Rollback SHALL be defined: reverting the sync to the collapsed-master mode SHALL bump the cursor version again and run the same bounded, commit-at-end full resync, never reuse an occurrence-mode token, and never leave a mixed projection that renders both a master and its instances for one source.

ID: REQ-module-calendar-032
Source: bu-h40h2b.6 acceptance 3 and 7; bu-vpn5ij; decision A1/B1 (bu-2o1dn8); Google incremental-sync token query-parameter rule
Scope: v1-mandatory

#### Scenario: A collapsed-mode token triggers a bounded full resync

- **WHEN** the stored cursor carries no version or a version older than the current occurrence-expanded version
- **THEN** the next sync does not send that token to the provider
- **AND** it performs a bounded full resync and stores a current-version cursor on success

#### Scenario: A failed resync does not clear the prior projection

- **WHEN** a bounded full resync fails or returns a partial result after the provider returned some pages
- **THEN** the previously projected rows and the previous cursor are unchanged
- **AND** `last_sync_error` records the failure and the source reports stale rather than empty
- **AND** the next tick retries the full resync

#### Scenario: Previous-version rows are retired only on success

- **WHEN** a bounded full resync completes
- **THEN** the collapsed master rows for the source are retired in the same commit that inserts the expanded occurrences
- **AND** no reader observes a state with both a master and its instances for one source

#### Scenario: Resync retry is idempotent

- **WHEN** the same resync is run twice, or a page is delivered twice
- **THEN** the projection after the second run equals the projection after the first
- **AND** a cancelled occurrence stays absent

#### Scenario: Rollback re-resets rather than reusing a token

- **WHEN** the expansion mode is reverted
- **THEN** the cursor version changes, the stored token is not reused, and a bounded full resync repopulates the collapsed projection in a single commit

### Requirement: Bounded Occurrence Projection Window and Honest Coverage

Because provider expansion of an open-ended series is unbounded, occurrence projection SHALL be limited to a bounded window of `full_sync_window_days` back and a configured forward horizon ahead (default 180 days, never less than the 90-day maximum radar scan window). The full resync SHALL pass the window to the provider as `timeMin`/`timeMax`. Because a provider incremental token cannot carry `timeMin`/`timeMax`, the module SHALL enforce the same window when writing delivered changes and SHALL record, per source, the `coverage_through` instant up to which occurrences are known to be projected.

Occurrences first entering the forward horizon as time passes are not delivered by an incremental token. When remaining forward coverage falls below a configured fraction of the horizon, the module SHALL run a bounded full resync to advance `coverage_through`. Until then, and whenever the source is failed or stale, any reader asking about a period beyond `coverage_through`, or about a source whose last successful sync is older than the stale threshold, SHALL receive an explicit partial or stale coverage indication and SHALL NOT be answered as though the period were empty. Coverage SHALL be exposed through the existing source freshness surface as additive fields (`coverage_through` and a `coverage` state of `complete`, `partial` or `stale`).

ID: REQ-module-calendar-033
Source: bu-h40h2b.6 acceptance 3 and 5; bu-vpn5ij; Google events.list syncToken restrictions
Scope: v1-mandatory

#### Scenario: Projection never extends beyond the bounded window

- **WHEN** a series has occurrences beyond the forward horizon
- **THEN** only occurrences inside the window are projected

#### Scenario: Beyond-coverage period is partial, not quiet

- **WHEN** a reader asks about a date after `coverage_through`
- **THEN** the source reports `coverage = "partial"`
- **AND** the absence of rows for that date is not reported as an empty day

#### Scenario: Horizon advance resync

- **WHEN** remaining forward coverage drops below the configured fraction of the horizon
- **THEN** a bounded full resync advances `coverage_through` and obeys the commit-at-end failure rule

#### Scenario: Stale source never reads as empty

- **WHEN** a source's last successful sync is older than the stale threshold or its last sync failed
- **THEN** its coverage is `stale`
- **AND** the previously projected rows remain readable

### Requirement: Structured Event Category Evidence

The projection SHALL carry, per instance, a `category` drawn from `meeting`, `hold`, `travel`, `all_day` or `uncategorized`, derived only from explicit structured evidence, in this precedence: (1) an owner-recorded category on the event, set only through an explicit owner action and never overwritten by a sync; (2) provider structured fields: `all_day` (date-only boundaries) yields `all_day`; Google `eventType` of `outOfOffice` or `focusTime` yields `hold`; `workingLocation` is status-only and carries no category; and multi-party structure (at least one non-self, non-resource attendee, or provider conference data) yields `meeting`; (3) otherwise `uncategorized`.

The category SHALL NOT be derived from the title, description, location text, calendar name, organizer string, source name or any free-text heuristic, including for flights and trips; an event the evidence does not classify is `uncategorized`, never silently `meeting` and never silently `travel`. The derivation SHALL be a pure function of the stored event so a reader can recompute it, and it SHALL be exposed to the shared analysis boundary as a category field on the conflict candidate. Provider fields used as evidence SHALL be read-side metadata only; no provider write authority is added.

ID: REQ-module-calendar-034
Source: bu-h40h2b.6 acceptance 2 and 4; bu-vpn5ij; decision B1 (bu-2o1dn8); RFC 0009 no-fabrication doctrine
Scope: v1-mandatory

#### Scenario: Owner category wins and survives sync

- **WHEN** the owner has recorded `travel` on an event and a later sync redelivers the event
- **THEN** its category stays `travel`

#### Scenario: Provider status events are holds, not meetings

- **WHEN** a provider event has `eventType=outOfOffice` or `focusTime`
- **THEN** its category is `hold`

#### Scenario: Multi-party structure is meeting evidence

- **WHEN** a timed event has at least one non-self, non-resource attendee or provider conference data and no owner category
- **THEN** its category is `meeting`

#### Scenario: A flight-like title without structured evidence is uncategorized

- **WHEN** a timed event is titled "Flight to Singapore" and has no owner category, no status `eventType` and no multi-party structure
- **THEN** its category is `uncategorized`
- **AND** the title is not consulted

#### Scenario: All-day boundaries are all-day

- **WHEN** an event has date-only `start.date` and `end.date`
- **THEN** its category is `all_day`
