## ADDED Requirements

### Requirement: Known-contact drops are marked on the stored filtered row

When a connector drops a message from a sender it recognises as a known contact, whether by label exclusion, connector-scope block, or global-scope skip, it SHALL record `important_dropped=true` and `basis="known_contact"` in the stored `full_payload.drop_context`. For Gmail, every such drop SHALL also freeze content-free classification state from the same immutable snapshot used to classify the sender, including drops of unmarked senders. A retained-cache positive marker SHALL remain lower-bound evidence when classification is unavailable and SHALL NOT establish completeness. The added classification object SHALL contain only its fixed version, state, bounded reason, generation, last successful refresh and observation timestamp. Drop metadata SHALL contain no contact identities, contact list, message content, provider detail, exception text or credentials; `payload.raw` SHALL remain empty. A sender not recognised as known SHALL have no positive important-drop marker. Replay SHALL omit the entire `drop_context` object. Historical classification observation SHALL be captured as timezone-aware UTC at the immutable classification/drop decision, independently of provider event time and persistence time; a loaded historical projection SHALL satisfy `last_success_at <= classification.observed_at <= last_success_at + 900 seconds`. Existing provider `event.observed_at` and persisted `received_at` window semantics SHALL remain unchanged.

ID: REQ-ingestion-policy-001
Source: bu-q7vx1q.43 six accepted outcomes; surface-known-contact-drops; heart-and-soul/development.md honesty/privacy
Scope: v1-mandatory

#### Scenario: Known contact dropped by a block rule is marked

- **WHEN** a connector-scope block rule drops a message whose sender is a known contact
- **THEN** the filtered row contains `important_dropped=true` and `basis="known_contact"` in `full_payload.drop_context`
- **AND** Gmail also records the fixed classification fields from that same snapshot
- **AND** `full_payload.payload.raw` remains empty

#### Scenario: Stranger drop is not marked

- **WHEN** a rule drops a Gmail message whose sender is not recognised as a known contact
- **THEN** the filtered row has no positive `important_dropped` marker
- **AND** it retains the fixed content-free classification context, including an unavailable state when classification is unknown

#### Scenario: Replay omits the marker

- **WHEN** a row carrying drop context is drained for replay
- **THEN** the submitted envelope contains no `drop_context` key

#### Scenario: Retained known contact is a lower bound

- **WHEN** a Gmail drop recognises a sender from retained contacts after refresh failure or TTL expiry
- **THEN** the positive marker remains present
- **AND** classification is unavailable and cannot certify the window complete

#### Scenario: All Gmail drop sites freeze classification

- **WHEN** Gmail records a label-excluded, connector-blocked or global-skipped message
- **THEN** each path records classification from one immutable snapshot
- **AND** all paths retain their existing rule action and empty raw payload
- **AND** classification observation is captured at that decision as UTC separately from old/backfill, future or malformed provider dates and delayed flush

### Requirement: Known-contact classification has explicit availability

Gmail SHALL expose an immutable known-contact snapshot with contacts, loaded/unloaded/stale/failed state, bounded reason, last successful refresh and a nonnegative generation. A successful empty read SHALL be loaded and available. No pool or first failed read SHALL be unavailable; failed, cancelled or stale refreshes SHALL retain previous contacts and last-success time while reporting unavailable. Availability SHALL expire at the existing 900-second TTL without requiring a new successful operation. Concurrent refreshes and drop classification SHALL use atomic generations so an older completion cannot overwrite newer evidence. The compatibility contact-set getter and existing policy tiers/actions SHALL retain their behavior; classification availability SHALL remain distinct from provider/auth health. Failure observability SHALL use closed reasons without contact identities, exception tails or credential values. A successful contact query SHALL remain loaded in the local snapshot and genuine drop-time history when publication fails or times out; publication/admission availability SHALL be a separate truth and SHALL NOT alter contacts, successful-refresh time, query generation, tier/rule behavior or provider health. For these cancellation guarantees, a cancelled refresh SHALL mean cancellation of an unfinished contact query; cancellation of publication after actual query success SHALL retain that successful loaded result.

ID: REQ-ingestion-policy-002
Source: bu-q7vx1q.43 criteria 1/5; docs/connectors/gmail-ingestion-policy.md; GmailPolicyEvaluator existing 900-second cache
Scope: v1-mandatory

#### Scenario: First load failure and no pool are unknown

- **WHEN** the actual contact query fails before a successful load or no pool exists
- **THEN** classification is unavailable with the appropriate bounded reason
- **AND** the contact-set getter still returns its compatible empty set

#### Scenario: Successful empty is available

- **WHEN** the actual query successfully returns no contacts
- **THEN** the snapshot is loaded and available with a successful refresh timestamp
- **AND** failed or timed-out publication does not demote that query snapshot or genuine loaded drop-time history
- **AND** current registry/admission and overall API availability remain independently checked

#### Scenario: Refresh failure preserves prior contacts

- **WHEN** a query fails after a successful nonempty load
- **THEN** the contacts and previous last-success timestamp remain unchanged
- **AND** classification becomes unavailable with a newer generation

#### Scenario: Stale snapshot is unavailable before a new query

- **WHEN** the last successful refresh has aged through the existing TTL
- **THEN** the snapshot is stale and unavailable even without another completed refresh
- **AND** retained contacts remain usable by existing policy

#### Scenario: Concurrent refresh completion cannot overwrite newer evidence

- **WHEN** poll and backfill request a refresh concurrently
- **THEN** published snapshots form a coherent generation order
- **AND** a sender decision and its stored classification refer to the same immutable snapshot

#### Scenario: Cancelled refresh remains unknown

- **WHEN** an in-flight refresh is cancelled after entering refresh state
- **THEN** classification remains unavailable and previous contacts/timestamp survive
- **AND** cancellation propagates rather than appearing as success
- **AND** this cancelled-refresh condition refers to an unfinished contact query, while publication cancellation after query success retains its loaded result

#### Scenario: Classification is separate from provider health

- **WHEN** Gmail provider connectivity is healthy but contact classification is unavailable
- **THEN** the known-contact check remains unavailable
- **AND** provider health and rule admission retain their existing meaning

### Requirement: Known-contact drop episodes open one standing owner condition

A scheduled Switchboard job SHALL read the unanswered marked drops from known contacts in the trailing seven days (statuses `filtered` and `replay_failed`) and group them by `(filter_reason, sender_identity)`. It SHALL reconcile that complete set into the owner condition ledger under source `switchboard:dropped-known`, with a fingerprint hashed from the pair, so one episode is one condition however many messages it dropped. The condition snapshot and any candidate SHALL carry only the count and window bounds: no sender address, subject, body or filter reason. On an `opened` or `reopened` transition only, the job SHALL propose exactly one insight candidate of category `dropped-known` whose premise is that condition; a later scan that only updates the count SHALL propose nothing. A condition SHALL resolve when its rows are replayed or leave the window, and a later drop from the same pair SHALL reopen it with one new candidate. While current or historical classification is unknown, or the read fails, the job SHALL change no condition and propose nothing, because an unknown read SHALL NOT resolve an episode. The job SHALL NOT use `runtime_attention_outbox` and SHALL NOT replay, narrow or alter what is dropped.

ID: REQ-ingestion-policy-003
Source: bu-q7vx1q.88.2; bu-q7vx1q.4 move #4 slice S2; src/butlers/core/owner_conditions.py state-ledger contract
Scope: v1-mandatory

#### Scenario: A drop episode opens once and notifies once

- **WHEN** two marked drops from one sender by one rule are read and later a third arrives
- **THEN** exactly one condition exists for that pair and exactly one candidate was proposed
- **AND** the third drop updates the count without proposing again

#### Scenario: Replay or aging resolves and a new drop reopens

- **WHEN** the episode's rows are replayed or leave the seven day window
- **THEN** the condition resolves
- **AND** a later drop from the same pair reopens it with one new candidate

#### Scenario: Unknown classification changes nothing

- **WHEN** current classification is unavailable or a drop in the window has unknown historical classification
- **THEN** the job opens, resolves and proposes nothing and reports a degraded run

#### Scenario: Nothing private is stored

- **WHEN** a condition or candidate is written for an episode
- **THEN** neither contains the sender address, subject, body or filter reason

### Requirement: Block and skip rules may except people the owner knows

A `block` or `skip` ingestion rule SHALL accept an optional per-rule boolean `except_known_contacts`, stored in the rule's condition and absent unless true. The Switchboard rule API SHALL persist and return it and SHALL refuse it with 422 on any other action, including when an action change would strand it. The policy evaluator SHALL pass over a matching rule that carries it only when the envelope's `known_contact` is `true`, and SHALL continue evaluating later rules. `known_contact` SHALL be `true` or `false` only from a loaded known-contact snapshot and `None` otherwise: a stranger, an unclassified sender and an unflagged rule SHALL keep today's behavior, so unknown classification leaves the rule in force and the drop keeps its unknown classification. The option SHALL default off, SHALL change no existing rule, and SHALL add no known-contact source and no global floor. The Gmail connector SHALL set `known_contact` from the snapshot it already uses for the known-contact marker; other connectors apply the rule as written until they supply the signal. The connector SHALL also send the classification as the boolean `control.known_contact` on the `ingest.v1` envelope, only when the snapshot is loaded, and the Switchboard SHALL read it into the envelope it evaluates against global rules, so a flagged global `skip` rule is passed over too; an absent or non-boolean value SHALL leave the rule in force. `known_contact` is taken from the raw `From` header with no SPF, DKIM or DMARC check, and Gmail filters upstream: a forged known-contact `From` on a domain without DMARC enforcement can bypass an `except_known_contacts` rule, whether it matches on content, header or label. The impact is ingestion only and grants no action authority.

ID: REQ-ingestion-policy-004
Source: bu-q7vx1q.88.3; bu-q7vx1q.4 move #4 slice S3; surface-known-contact-drops proposal deferred slices
Scope: v1-mandatory

#### Scenario: A known contact passes a flagged rule

- **WHEN** a Gmail message from a sender in the loaded snapshot matches a block or skip rule with `except_known_contacts`
- **THEN** that rule does not apply and the message is ingested unless a later rule drops it

#### Scenario: Unflagged, stranger and unknown keep the rule in force

- **WHEN** the rule is unflagged, the sender is not a known contact, or the snapshot is not loaded
- **THEN** the rule applies as written
- **AND** a drop with an unloaded snapshot keeps its unknown classification

#### Scenario: The classification reaches the Switchboard's global rules

- **WHEN** a known contact's message matches a flagged global `skip` rule and the envelope carries `control.known_contact: true`
- **THEN** the Switchboard SHALL pass the rule over
- **AND** an envelope without the field, or with `false`, SHALL still be skipped

#### Scenario: The option is only valid on block and skip

- **WHEN** a rule is created or changed so `except_known_contacts` sits on any other action
- **THEN** the API refuses it with 422
