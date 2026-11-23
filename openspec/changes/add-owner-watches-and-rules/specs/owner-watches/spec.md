## ADDED Requirements

### Requirement: The M7 predicate extends the one closed AST

M7 conditions SHALL use the first-switch evaluator (REQ-condition-evaluation-001 and -002) and its tagged AST, adding exactly these leaves: `expectation` (a registered `expect.*` kind, a subject UUID and a deviation state), `routine_deviation` (a confirmed routine UUID reaching `missed`), `condition` (an owner condition-ledger source and fingerprint reaching `opened` or `resolved`), `mode` (a registered mode ID reaching `entered` or `exited`), `read_check` (see the next requirement) and `bundle` (a registered correlation bundle ID, see the correlation requirement). `all`/`any` depth two, at most 16 leaves, 8KiB canonical JSON, grant and source validation, three-valued unknown propagation and the fire/outbox contract SHALL be unchanged. No text expression, code, SQL, template language, parallel observation registry or parallel correlator SHALL exist. The Python validator and the database predicate validator SHALL accept and refuse the same documents.

ID: REQ-owner-watches-001
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md S4 (M7 extension); bu-nt2eda; bu-4c7nvp.1.5
Scope: v1-reserved

#### Scenario: A new leaf names an unregistered kind

- **WHEN** an `expectation`, `mode` or `condition` leaf names a kind, mode or source that is not registered
- **THEN** registration refuses before any cursor, subscription or intent exists

#### Scenario: A leaf is unmeasurable

- **WHEN** the typed row a new leaf reads is unknown, unmeasurable or unconfigured
- **THEN** the leaf evaluates unknown and propagates through `all`/`any` without becoming false or healthy

#### Scenario: The two validators disagree

- **WHEN** the same predicate document is submitted to the Python validator and the database validator
- **THEN** both accept it or both refuse it

### Requirement: A read check is typed, trusted and never an author

A `read_check` leaf SHALL name one registered owner-granted read capability (one leaf per capability, with any dimension carried in the capability name, because leaves latch across events), a declared output schema and a typed comparison over declared fields. Its source SHALL be a trusted typed boundary (the quarantined boundaries of bu-4c7nvp.1.2); output from an untrusted source, model text, mail or any value outside the declared schema SHALL evaluate unknown, never true. A read check result SHALL NOT create, edit, widen or renew a watch or rule. A measure that is undeclared or unmeasurable SHALL refuse registration or show the watch as blind; it SHALL NOT read as healthy.

ID: REQ-owner-watches-002
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md S4 (M7 extension); bu-4c7nvp.1.2; bu-4c7nvp.1.5
Scope: v1-reserved

#### Scenario: Injected mail tries to author a watch

- **WHEN** an inbound message or a read check output contains text asking to create, widen or renew a watch or rule
- **THEN** no watch or rule is created, widened or renewed and the text is evidence only

#### Scenario: A read check returns a value outside its schema

- **WHEN** the read capability returns a field or type its declared schema does not name
- **THEN** the leaf evaluates unknown and the instance does not fire on it

#### Scenario: A watch names an undeclared measure

- **WHEN** a watch is created over a measure no registered capability declares
- **THEN** creation refuses, or the watch is shown blind with its cause, and it never reports healthy

### Requirement: A watch is owner-authored, exact-card approved and notify-only

A watch SHALL be an evaluator instance created, updated or deleted only by an owner-origin administer action approved on an exact card that binds the predicate digest, source and category boundary, grant digest, delivery mode, expiry and version. Only a model MAY propose a bounded watch for the owner to review, and a proposal SHALL NOT install one. Mail, source text, read-check output, routines and subscriptions are evidence only and SHALL NOT propose or install a watch (routine-anchored proposals remain with bu-xxfkhk). A watch SHALL notify only the owner through the RFC 0011 broker. It SHALL NOT have a non-owner recipient, URL, free-text template, external mutation, spend, arbitrary scheduled action or calendar auto-response. An existing rule or subscription SHALL NOT acquire watch authority by being renamed. Every read, wake and delivery SHALL retain current grants, labels, availability-only and private-custody restrictions, quotas, deduplication and Stop.

ID: REQ-owner-watches-003
Source: about/legends-and-lore/rfcs/0019-proactive-egress-and-automation-parked.md (notify-only narrowing); bu-nt2eda Q1; bu-4c7nvp.1.5
Scope: v1-reserved

#### Scenario: The owner approves a watch card

- **WHEN** the owner approves the exact card for a new watch
- **THEN** one instance is registered with an immutable boundary, a stable ID and version, and an approval receipt
- **AND** a changed predicate, source, grant, delivery mode or expiry needs a new card

#### Scenario: A watch names a third party

- **WHEN** a watch proposal carries a non-owner recipient, URL or free-text template
- **THEN** the proposal is refused and nothing is registered

#### Scenario: A model proposes a watch

- **WHEN** a model session proposes a watch
- **THEN** the proposal is a reviewable card only and no instance exists until the owner approves it

#### Scenario: Non-model text asks for a watch

- **WHEN** a mail body, source text or read-check output asks for a watch
- **THEN** it is evidence only and produces no proposal card

### Requirement: Watches have finite limits and delivery defaults

At most 50 watches SHALL be active at once. A watch SHALL expire 90 days after approval or renewal, and the owner SHALL be offered a renewal card seven days before expiry; an unrenewed watch SHALL expire without deleting its observations. Delivery SHALL default to digest unless the owner chose right away. `never` or `disable` SHALL pause a watch without deleting its observations or receipts. An evaluator instance fires once; the watch runner re-arms the next generation after delivery, so a watch keeps watching without a second approval. Generation 1 MAY fire on the current snapshot. Every later generation SHALL be edge-triggered: it fires only on an event past the prior generation's fire cursor, so a condition that stays true does not fire again. A fired watch SHALL deliver once per fire intent; duplicate events, restart and late input SHALL NOT produce a second delivery.

ID: REQ-owner-watches-004
Source: bu-nt2eda Q3; bu-4c7nvp.1.5
Scope: v1-reserved

#### Scenario: The fifty-first watch

- **WHEN** 50 watches are active and the owner approves another
- **THEN** the approval refuses with the cap named and no instance is created

#### Scenario: A watch nears expiry

- **WHEN** a watch is seven days from its 90-day expiry
- **THEN** the owner is offered a renewal card, and without approval the watch expires with its observations retained

#### Scenario: The owner says never

- **WHEN** the owner answers a watch notification with never or disables the watch
- **THEN** the watch pauses, its observations and receipts stay, and no further delivery occurs

#### Scenario: A fired condition is still true after re-arm

- **WHEN** a fired condition is still true after re-arm
- **THEN** there is no second fire until a new qualifying event past the cursor

#### Scenario: An event is replayed

- **WHEN** the same source event is delivered twice or replayed after a restart
- **THEN** one fire intent and one notification result

### Requirement: Finance alert configuration migrates to watches without duplicate alerting

Finance `alert_config` facts SHALL migrate to watches through an explicit type-by-type mapping covering `large_transaction`, `budget_exceeded`, `new_merchant` and `price_change`. Each type SHALL map to a watch shape, be covered by an existing single-output path, or be shown blind. `large_transaction` becomes a watch, and its response flag SHALL be suppressed while the watch is active or paused and restored when the watch is deleted or expires. For `price_change`, `budget_exceeded` and `new_merchant` the stored `alert_config` fact does not govern the existing path, before or after migration (price changes are detected unconditionally, budget insights come from `budget_status` thresholds and new merchants from `anomaly_scan`), and no watch is created. Those three configs SHALL be shown to the owner as superseded or blind and SHALL NOT be shown as active alert settings. During and after migration a qualifying event SHALL produce one owner output: the legacy alert path and the watch SHALL NOT both notify. A migrated `large_transaction` watch SHALL carry the same threshold, currency and enabled state as its source fact, and migration SHALL NOT enable a disabled alert.

ID: REQ-owner-watches-005
Source: bu-4c7nvp.1.5; roster/finance/tools/alerts.py; roster/finance/jobs/finance_jobs.py; roster/finance/tools/budgets.py; roster/finance/tools/anomaly_detection.py
Scope: v1-reserved

#### Scenario: A large-transaction alert migrates

- **WHEN** an enabled `large_transaction` alert_config is migrated
- **THEN** a watch with the same threshold and currency exists, and one qualifying transaction produces one notification

#### Scenario: A type covered by its existing path is migrated

- **WHEN** a `price_change`, `budget_exceeded` or `new_merchant` alert_config is migrated
- **THEN** no watch is created, the config is shown to the owner as superseded or blind (not as an active setting), and its existing path still produces the one output

#### Scenario: The large-transaction flag returns

- **WHEN** a `large_transaction` watch is deleted or expires
- **THEN** the response flag it suppressed is restored and the transaction still produces one output

#### Scenario: A type no evaluator measures

- **WHEN** a configured alert type has no evaluator that measures it
- **THEN** it is shown blind with its cause and no alert is invented

#### Scenario: Migration runs twice

- **WHEN** the migration is re-run after a restart
- **THEN** no duplicate watch and no duplicate notification results

### Requirement: Guards and modes coalesce replay and only tighten automatically

Obligation `wake_when` conditions and situation guards SHALL coalesce replay into finite bundles and SHALL NOT turn a stale event into a realtime one. The adopted sick exemptions (safety, medication, bills due within 48 hours, and replies to people) SHALL apply. "In flight" is a travel-derived posture flag, not the `travel` mode. Automatic entry SHALL be limited to the tighten-only `asleep` and `house_unattended` modes and the in-flight posture, SHALL record a receipt, and SHALL NOT arm the absence plan, which needs declared or owner-confirmed travel; any change that loosens a hold SHALL use the existing step-up boundary and SHALL NOT occur automatically. Observed-sleep fusion SHALL use only existing phone, Home Assistant and wearable inputs and SHALL report unknown when they are missing; it SHALL NOT add a connector or source.

ID: REQ-owner-watches-006
Source: bu-u33sb7 Q4, Q5, Q6 and Q8; RFC 0038 S3 mode registry; bu-4c7nvp.1.5
Scope: v1-reserved

#### Scenario: A stale event arrives during a held mode

- **WHEN** an event older than the realtime window arrives while a mode holds the obligation class
- **THEN** it is coalesced into the finite replay bundle and is not delivered as realtime

#### Scenario: Sleep inputs are missing

- **WHEN** no permitted phone, Home Assistant or wearable input is available
- **THEN** observed sleep is unknown and no mode is entered on it

#### Scenario: A mode would loosen

- **WHEN** an automatic change would loosen a hold or break-through set
- **THEN** it does not apply until the owner completes step-up

### Requirement: Correlation is deterministic and graduation needs an exact owner card

The first correlation consumer SHALL match a delay against a commitment from typed rows and SHALL NOT run a model for deterministic matching. A repeated model-judged bundle MAY be offered as a standing rule only after three useful owner verdicts and no `never` verdict in the preceding 60 days. The offer SHALL be a reviewable card; only the owner's approval of that exact card SHALL create a rule. The offer SHALL NOT install, broaden or renew a rule, and a rule SHALL NOT be promoted into git automatically. Confirmed-routine publication and routine-anchored proposals remain with bu-xxfkhk, and Chronicler SHALL NOT gain a notification role.

ID: REQ-owner-watches-007
Source: bu-nt2eda Q2; bu-4c7nvp.1.5
Scope: v1-reserved

#### Scenario: A delay overlaps a commitment

- **WHEN** a typed delay row overlaps a typed commitment row
- **THEN** the bundle leaf matches deterministically with no model run

#### Scenario: Graduation history is insufficient

- **WHEN** a judgment has fewer than three useful verdicts, or a `never` within 60 days
- **THEN** no rule is offered

#### Scenario: The owner approves the offered rule

- **WHEN** the owner approves the exact rule card
- **THEN** one runtime rule exists with its own receipt, and declining or ignoring the card creates nothing

### Requirement: M7 consumers land dark and roll back safely

Watch, guard, mode and correlation consumers SHALL stay dark where the RFC 0038 runtime is not active. A rollback SHALL disable the new consumer and admission while preserving every receipt and observation; it SHALL NOT silently restore unsafe authority, lose a recorded effect or widen egress. This capability authorizes no deploy, switch execution, operational secret or device change, live data mutation or external communication.

ID: REQ-owner-watches-008
Source: bu-4c7nvp.1.5 delivery boundary; bu-276pli
Scope: v1-reserved

#### Scenario: The consumer is rolled back

- **WHEN** the watch consumer is disabled
- **THEN** no new watch fires, and existing receipts and observations remain readable

#### Scenario: The runtime is not active

- **WHEN** the RFC 0038 goal runtime is not active
- **THEN** no watch registers or delivers
