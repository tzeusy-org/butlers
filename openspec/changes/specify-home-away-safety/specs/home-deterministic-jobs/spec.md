## ADDED Requirements

### Requirement: Away-safety thresholds are owner-confirmed configuration
Home SHALL read the away-safety thresholds only from the owner's own declarations. These are a freezer's maximum, hysteresis, dwell, maximum reading age and unit, an away door's open duration, and the blind dwell. A watch with any value missing SHALL report `unconfigured` and SHALL NOT alert. A missing value MUST NOT be filled from a code default, a suggested value or the `home:thresholds:*` default fallback. Declarations SHALL be written only through owner-authenticated Home dashboard routes. They MUST NOT be configured from Home Assistant names or attributes, or by any model tool.

ID: REQ-home-deterministic-jobs-002
Source: heart-and-soul/vision.md Rules 3 and 4; RFC 0038 D18 and S6; bu-jv1moa.32 (no hard-coded unsafe defaults)
Scope: v1-mandatory

#### Scenario: An unconfigured watch is visible and silent
- **WHEN** a declared freezer, away door or the blind check lacks any required value
- **THEN** its dashboard status and producer result SHALL name it `unconfigured`
- **AND** no condition SHALL open and no message SHALL be sent for it

#### Scenario: Out-of-range or untrusted configuration is refused
- **WHEN** a declaration is submitted with a value outside its validated bounds (design.md, Configuration), from an unauthenticated caller, or naming an entity outside the declared domain
- **THEN** the route SHALL refuse it without storing any part of it
- **AND** the refusal SHALL be content-blind and audited by outcome only

#### Scenario: Removing a declaration resolves its condition
- **WHEN** the owner removes a declaration, or clears its away-door duration
- **THEN** the next complete healthy run SHALL resolve that declaration's open condition with reason `watch_removed`

### Requirement: Home sensing projection and the blind condition
Home SHALL have exactly one sensing group per installation. Its subject, `sensing_group` source reference and maximum age SHALL be registered in the `home.sensing` perception source profile by a privileged bootstrap or activation act, as the `health.dependencies` profile is; the perception writer refuses any subject not registered there. No dashboard route or job SHALL create a sensing group or change its subject, source reference or maximum age. The owner's routes edit only which declarations the group covers and `blind_after_minutes`.

Home SHALL write the canonical `home.sensing` slot for that group from the Home Assistant source-health lease and the readability of the declared entities, as follows:
- `available` when every declared entity is readable;
- `degraded` when some are unreadable;
- `unavailable` when all are unreadable;
- state `unmeasurable` when the lease is missing, in error or expired;
- `unconfigured` when the group profile is not registered or no entity is declared.

Blindness is judged per declared role. The roles are each declared `home.safety_watch` life-safety role (`leak`, `smoke`, `co`, `gas`), `freezer` and `away_door`. A role is blind in a run when the lease is not healthy, or when every declared entity of that role is unreadable. A partial loss within a role is not blind; it stays `degraded` and each unreadable entity stays unknown in its own producer. A role's blindness counts only over consecutive blind runs: Home SHALL record when a role's blind streak started, and any run in which that role is not blind ends the streak. While the owner is confirmed away, a role blind for at least the owner-configured `blind_after_minutes` SHALL open one `home:sensing-blind` condition per absence, naming every currently blind role by role, never by entity label. There is no code-set dwell. Blindness MUST NOT resolve, downgrade or imply clearing any other condition, and MUST NOT be reported as all clear.

ID: REQ-home-deterministic-jobs-003
Source: RFC 0038 S3 situation registry (home.sensing row); home-deterministic-jobs "HA Source Health Guard for Snapshot Readers"; REQ-event-response-contract-003 and 005
Scope: v1-mandatory

#### Scenario: Home goes blind while the owner is away
- **WHEN** the owner is confirmed away and the lease has been unhealthy for at least the blind dwell
- **THEN** one `home:sensing-blind` condition SHALL open, identified by the sensing group and the absence's leave edge
- **AND** the owner SHALL be told Home cannot see the house and since when, naming each declared role whose alerts cannot reach them

#### Scenario: One role goes blind while others still read
- **WHEN** the owner is confirmed away, every declared leak and smoke sensor has been unreadable for at least the blind dwell, and the freezer still reads
- **THEN** one `home:sensing-blind` condition SHALL open naming the `leak` and `smoke` roles, while the slot stays `degraded`
- **AND** the message SHALL NOT name the freezer, and SHALL NOT claim coverage for any role that has no declared entity

#### Scenario: A partial loss within a role is not blind
- **WHEN** one of two declared smoke sensors is unreadable and every other role reads
- **THEN** no blind condition SHALL open, the slot SHALL be `degraded`, and the unreadable sensor SHALL stay unknown in its own producer

#### Scenario: The sensing group is not registered
- **WHEN** the `home.sensing` profile has no registered subject
- **THEN** the slot and the blind check SHALL report `unconfigured`, and no route SHALL be able to create one

#### Scenario: Blindness never clears an alarm
- **WHEN** any `home:safety`, `home:freezer` or `home:away-door` condition is open and sensing becomes blind
- **THEN** that condition SHALL stay open and keep its re-escalation
- **AND** no producer SHALL resolve anything by omission from a blind run

#### Scenario: Sight returns or the owner returns
- **WHEN** no declared role is blind for one complete run, or the owner's return is confirmed
- **THEN** the blind condition SHALL resolve with reason `sight_restored` or `owner_returned`
- **AND** while it stays open, a change in the set of blind roles SHALL update the roles it names

### Requirement: Freezer temperature condition
Home SHALL judge a declared freezer's dwell from consecutive runs. A run qualifies only when all of the following hold:
- the source-health lease is healthy;
- the snapshot's `last_updated` is within the owner's maximum reading age;
- the reading is numeric and in the declared unit;
- the reading is above the owner's maximum.

Home SHALL record when the qualifying streak started and carry it into the condition's metadata when the condition opens. Home SHALL open one `home:freezer` condition when `now - streak_start` is at least the owner's dwell. A run with a known reading at or below the maximum ends the streak. A run that cannot qualify for any other reason (lease not healthy, reading unavailable, missing, non-numeric, stale or in another unit) is unknown: it ends the streak, and it carries an open condition forward without resolving it and opens nothing. An open condition SHALL resolve only on a known, fresh reading at or below the maximum minus the owner's hysteresis.

ID: REQ-home-deterministic-jobs-004
Source: bu-jv1moa.6 unshipped freezer promise; home-deterministic-jobs "Home Safety Sentinel Job"; REQ-event-response-contract-002
Scope: v1-mandatory

#### Scenario: A held warm reading opens one condition
- **WHEN** every run of a declared freezer has qualified since a streak start at least its dwell ago
- **THEN** one `home:freezer` condition SHALL open with fingerprint `(entity_id)` and the streak start in its metadata
- **AND** the owner SHALL be sent the owner's label, the reading, the maximum and how long it has been warm, never Home Assistant's `friendly_name`

#### Scenario: Readings around the limit do not flap
- **WHEN** an open freezer condition's readings fall below the maximum but stay above the maximum minus hysteresis
- **THEN** the condition SHALL stay open
- **AND** a dip below the maximum before the dwell elapses SHALL end the streak and open nothing

#### Scenario: An unmeasurable freezer is unknown, not cold
- **WHEN** the sensor is unavailable, its snapshot is older than the maximum reading age, it reports a different unit, or the source lease is not healthy
- **THEN** the freezer condition SHALL be neither opened nor resolved, and any qualifying streak SHALL end
- **AND** an open condition SHALL keep re-escalating, marked "sensor not reporting"

### Requirement: Away door condition
Home SHALL open one `home:away-door` condition for a `departure_watch` item whose away duration is configured, when that item has been continuously not secure, while the owner is confirmed away, for at least that duration counted from the later of the item's last state change and the leave edge's confirmation. The condition SHALL resolve when the item reads secure, when the owner's return is confirmed, or when the leave edge is retracted. The departure card remains the only message at departure; this condition MUST NOT be a second departure producer. The away duration SHALL be at least 30 minutes, the departure card's whole delivery window (`DEPARTURE_WINDOW`), so a door listed on the card can never also open this condition while the card may still be delivered.

ID: REQ-home-deterministic-jobs-005
Source: bu-jv1moa.6 unshipped away-door promise; bu-jv1moa.8 departure card (#5790); heart-and-soul/vision.md Rule 4
Scope: v1-mandatory

#### Scenario: A door still open well into an absence
- **WHEN** a watched door with an away duration of D minutes has been not secure for D minutes since the later of its last change and the leave confirmation, and the owner is still confirmed away
- **THEN** one `home:away-door` condition SHALL open with fingerprint `(entity_id, leave edge id)`
- **AND** the owner SHALL be sent the owner's label, its state and how long it has been open

#### Scenario: The departure card and the condition do not duplicate each other
- **WHEN** the departure card listed an item at departure and the item is still not secure after its away duration
- **THEN** the condition SHALL open as a distinct fact ("still open N min into your absence")
- **AND** no departure card SHALL be queued by this producer, and no condition SHALL open before the duration

#### Scenario: An item open at departure gets the card first
- **WHEN** a watched item is not secure when the owner's leave is confirmed and stays not secure
- **THEN** the departure card SHALL be the first message about it
- **AND** the condition SHALL open no earlier than 30 minutes after the leave confirmation, and only after the item's own away duration

#### Scenario: Return, retraction or closure resolves it
- **WHEN** the item reads secure, the owner's return is confirmed, or the leave edge is retracted
- **THEN** the condition SHALL resolve with reason `secured`, `owner_returned` or `premise_retracted`, and SHALL send no message

#### Scenario: Editing the watch while away takes effect on the next run
- **WHEN** the owner changes an item's secure states or away duration while away
- **THEN** the next run SHALL re-evaluate the item against the edited row, with no arming snapshot kept
- **AND** an open condition the edited row no longer supports SHALL resolve as `watch_removed`, and a newly supported one SHALL open under the same absence fingerprint

#### Scenario: A new absence is a new condition
- **WHEN** the owner returns and later leaves again with the same item open
- **THEN** a new condition SHALL open for the new leave edge after its own duration, and the earlier episode SHALL stay resolved

### Requirement: The away premise comes from the canonical owner presence
The away-door and blind producers SHALL treat the owner as away only when all of the following hold:
- the `owner.place` slot is `known`, fresh and `outside`;
- the latest non-retracted confirmed `owner.place` transition is a leave that is not late;
- the slot and that transition agree.

The owner's return is the latest non-retracted, non-late confirmed enter after that leave edge.

They SHALL read both through the private owner perception reader that the departure card uses. They MUST NOT infer presence from Home Assistant entities, modes or any other source. Hosting or a house-sitter does not change "away" in this version; mode-aware holds belong to bu-dhloi1.21. Otherwise the premise is `unknown` (slot unknown, stale or unmeasurable; reader unavailable; or slot and transition disagree). In that case no away condition SHALL open, and none SHALL resolve except through the item's own secure reading.

ID: REQ-home-deterministic-jobs-006
Source: RFC 0038 S3 situation registry (owner.place row); bu-jv1moa.17 presence reducer; heart-and-soul/vision.md Rule 4
Scope: v1-mandatory

#### Scenario: Conflicting or missing presence is unknown
- **WHEN** the slot says `outside` but the latest transition is an enter, or the slot is stale or unknown, or the reader is not configured
- **THEN** the producers SHALL return `presence_unknown` and open nothing
- **AND** open away conditions SHALL be carried forward, not resolved, and keep re-escalating with "away status unknown"

#### Scenario: A late or retracted leave is no premise
- **WHEN** the only leave is late, or is retracted after a condition opened
- **THEN** no condition SHALL open from it, and an open one SHALL resolve as `premise_retracted` without a message

### Requirement: Away-safety delivery, repetition and quiet hours
The three producers SHALL deliver through the routine Home notify path. A first send suppressed by quiet hours or the context bus SHALL leave the condition without a delivery marker, and the first run after the suppression lifts SHALL deliver it. Re-escalation follows the ledger as it is: the ladder is relative to the condition's opening, not to its first send. The rungs fall 1 hour after opening, 4 hours after that, then every 24 hours, as an opt-in ladder on their own sources only. A rung that falls while a send is suppressed is skipped, not queued: the ledger advances to the next rung and nothing is re-sent for the skipped one. Before every send of a `home:away-door` or `home:sensing-blind` message, the producer SHALL re-read the away premise and SHALL NOT send unless the owner is still confirmed away. They SHALL be read-only and model-free. They MUST NOT break quiet hours or sleep unless the owner prerequisite in design.md (P1) is answered and implemented under the bu-dhloi1.3 derived-urgency contract.

ID: REQ-home-deterministic-jobs-007
Source: bu-dhloi1.3 and bu-u33sb7 q3=A (who may break quiet hours); owner-condition-ledger "Owner Condition Ledger Representation"; REQ-event-response-contract-001
Scope: v1-mandatory

#### Scenario: A night-time condition is held, not lost
- **WHEN** a condition opens during quiet hours and the send is suppressed
- **THEN** the condition SHALL stay open with no delivery marker
- **AND** the first run after quiet hours SHALL send it once
- **AND** the re-send ladder SHALL keep counting from the opening, so a rung that fell inside quiet hours is skipped and the next re-send is the next rung after the window

#### Scenario: The owner comes home just before a send
- **WHEN** an away-door or blind message is due and the owner's return was confirmed since the run read the premise
- **THEN** the producer SHALL re-read the premise before sending, send nothing, and resolve the condition as `owner_returned`

#### Scenario: Concurrent runs send once
- **WHEN** two runs of the away-safety job overlap, or a run retries after a crash between opening and sending
- **THEN** one condition per fingerprint SHALL exist and the owner SHALL be sent one message per opening or escalation

#### Scenario: No model and no actuation
- **WHEN** any of the three producers runs
- **THEN** it SHALL make no model call and no Home Assistant service call, and SHALL offer no prepared action
