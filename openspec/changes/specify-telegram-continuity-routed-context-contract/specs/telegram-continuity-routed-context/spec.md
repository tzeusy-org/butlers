# Telegram Conversation Continuity at Goal Admission

## Purpose

Preserve deterministic, bounded and trust-fenced Telegram conversation continuity
as goal input/context under RFC 0038 D1/D5/D6/D15. Sticky target affinity, affinity
TTL reclassification and correction re-dispatch retire. This is a future behavior
contract, not a claim that runtime implementation, cutover or archive has occurred.

## ADDED Requirements

### Requirement: Conversation Identity for Goal Continuity

Telegram continuity SHALL use the existing `external_conversation_id`, independently
of `reply_target_ref`, with no target-affinity lookup or model classification before
goal admission. History SHALL select `message_inbox` rows only by
`request_context ->> 'external_conversation_id'`, never by a per-message reply target
or `source_thread_identity`. The existing Telegram field mapping and forum-topic
boundary SHALL remain binding; no new wire field or identity table is introduced.

ID: REQ-telegram-continuity-routed-context-001
Source: RFC 0038 D1, D5, D15; connector-telegram-bot ingest.v1 Field Mapping; module-pipeline Conversation History for Routing Context
Scope: v1-mandatory

#### Scenario: New conversation needs no affinity
- **WHEN** an accepted Telegram message has no earlier history in its conversation
- **THEN** it follows ordinary goal admission without an affinity lookup or classifier
- **AND** absence of history is not represented as successfully loaded context

#### Scenario: Follow-up context grants no target or goal ownership
- **WHEN** another accepted message arrives in the same Telegram conversation
- **THEN** bounded same-conversation history can supply context, but its prior domain
  choices neither select a target butler nor prove a goal join
- **AND** only the existing exact server-held owner-reply binding contract may join
  a live goal; otherwise the existing ordinary admission rules apply

#### Scenario: Conversation identity stays distinct from reply targets
- **WHEN** Telegram messages carry the current split identity fields
- **THEN** history uses `telegram:<chat_id>`, with `:topic:<message_thread_id>` only
  when Telegram marks the message `is_topic_message`, as `external_conversation_id`
- **AND** separate per-message `reply_target_ref` values do not split that history
- **AND** different conversations/topics do not share history, and the envelope
  carries no retired `event.external_thread_id`

### Requirement: No Affinity TTL Reclassification

Telegram continuity SHALL NOT create a 24-hour target-affinity TTL, consult a prior
target pin, or reclassify a message when such a pin would have expired. The separate
15-minute/30-message context bound SHALL remain unchanged. This prose amendment
SHALL NOT change live email behavior or create a post-cutover email routing exception.

ID: REQ-telegram-continuity-routed-context-002
Source: RFC 0038 D1, D5, D15; canonical bu-u5euwb.16; generalist collision dispositions and connector-base-spec routing retirement
Scope: v1-mandatory

#### Scenario: No default target-affinity TTL
- **WHEN** no Telegram continuity configuration is provided
- **THEN** no 24-hour affinity setting or target pin is inferred
- **AND** the existing bounded context rule still applies

#### Scenario: A conversational gap does not trigger classification
- **WHEN** a new message follows a gap beyond the former affinity TTL
- **THEN** accepted reasoning ingress still follows ordinary goal admission without
  classification, and only history inside the current bounded window is eligible

#### Scenario: Email has no new legacy routing exemption
- **WHEN** this Telegram contract is amended or later applied at the switch
- **THEN** this spec-only amendment has made no live email runtime/configuration change
- **AND** target-state email follows generalist's adopted retirement of affinity and
  classification; this capability creates no separate email policy or TTL override

### Requirement: Unavailable Prior Domain Grants No Dispatch

An unavailable or unregistered previously-used domain SHALL NOT become an affinity
target, classification fallback or dispatch destination. Accepted ingress SHALL
follow the existing admission contract and capability availability/authorization
checks. Unavailable context SHALL NOT be fabricated, selected from another conversation
or treated as evidence authenticating a sender.

ID: REQ-telegram-continuity-routed-context-003
Source: RFC 0038 D1, D2, D5, D15; generalist butler-switchboard and goal-orchestration
Scope: v1-mandatory

#### Scenario: A previously used domain is absent
- **WHEN** history mentions a domain that is no longer registered or available
- **THEN** no target-affinity fallback, classifier or route dispatch runs
- **AND** unavailable history/capabilities are reported honestly under their existing
  contracts, without fabricating continuity or starting a domain reasoning session

### Requirement: No Pin Conflict or Guessed Goal Join

Concurrent messages SHALL NOT be resolved by target-pin conflict, reclassification
or guessed chat-to-goal ownership. Independent goals SHALL remain independent;
existing authenticated owner-reply joins SHALL retain their exact prompt/effect,
recipient, liveness, locking, immutable source and authority/accounting checks.

ID: REQ-telegram-continuity-routed-context-004
Source: RFC 0038 D1, D5; generalist goal-orchestration Whole-goal accountability and owner-reply join contract
Scope: v1-mandatory

#### Scenario: Prior messages used multiple domains
- **WHEN** messages in one conversation previously used several domains' capabilities
- **THEN** no ambiguous affinity pin is chosen and no conflict classifier runs
- **AND** that history does not grant capability access or bind a later message to a goal

#### Scenario: A later turn cannot repair a pin that no longer exists
- **WHEN** another accepted turn follows concurrent messages
- **THEN** it follows ordinary admission unless the existing exact owner-reply join
  contract proves a live binding; no target re-pin or conflict-then-reclassify runs
- **AND** unknown, foreign, mismatched or expired bindings follow the existing
  ordinary admission outcome without guessed continuity or enlarged authority

### Requirement: Replay and Dedup Inherit Existing Ingest Idempotency

A duplicate Telegram update SHALL retain existing connector/ingress idempotency and
resolve to the goal already admitted from that envelope. This contract SHALL NOT
introduce a second dedup store, affinity write, routing decision or domain session.
Canonical accepted request/sender lineage and crash-safe checkpoint semantics remain.

ID: REQ-telegram-continuity-routed-context-005
Source: RFC 0038 D5; connector-telegram-bot Idempotency and Safety; generalist connector-base-spec and module-pipeline
Scope: v1-mandatory

#### Scenario: Duplicate update returns the existing admission
- **WHEN** an already accepted Telegram update is redelivered with canonical key
  `tg:<chat_id>:<message_id>`, shared with the user-client connector
- **THEN** existing dedup returns the existing admission/goal and canonical request
  reference, without a second goal, affinity write or route dispatch
- **AND** the documented `telegram:<endpoint_identity>:<update_id>` fallback is used
  only when chat/message IDs are unavailable; checkpoint advances only after ingest
  acceptance, with the existing blocked-source acknowledgement and safe replay rules

### Requirement: No Correction Redispatch or Repinning

The goal runtime SHALL NOT use `correct_route` to re-dispatch a message, create a
domain session or re-pin a Telegram target. Correction SHALL follow the adopted
new-goal/Stop or within-goal capability contract without a new correction protocol,
replacement session identity or authority grant from conversation history.

ID: REQ-telegram-continuity-routed-context-006
Source: RFC 0038 D1, D5; canonical bu-u5euwb.16; generalist removal of Misroute Correction Re-dispatch
Scope: v1-mandatory

#### Scenario: A wrong domain choice does not re-pin the chat
- **WHEN** a goal's lead determines that another domain's capability is appropriate
- **THEN** it may call only capabilities within its existing grant and goal
- **AND** no original message is re-dispatched and no chat affinity pin is written

#### Scenario: The next message has no corrected target
- **WHEN** another accepted Telegram message follows a wrong goal outcome
- **THEN** no previous correction or pin selects a butler or grants a goal join
- **AND** the existing new-goal/Stop and exact owner-reply admission rules remain
  binding without classification or replacement domain sessions

### Requirement: Bounded Goal Admission Conversation Context

A deterministic history block SHALL provide conversation continuity as goal
input/context under the existing module-pipeline contract, never as a `route.v1`
envelope or downstream domain prompt. It SHALL supplement the accepted message and
its immutable request/sender context, with at most the most recent 30 messages within
the most recent 15 minutes of that same conversation. Admission SHALL NOT use a model
to classify, summarize or select a target for this block. Later retrieval SHALL retain
existing authorized context/domain read boundaries and private-custody restrictions.

ID: REQ-telegram-continuity-routed-context-007
Source: RFC 0038 D1, D5, D6, D15; generalist module-pipeline Conversation History for Routing Context and connector-base-spec Request Context Construction
Scope: v1-mandatory

#### Scenario: Context supplements the accepted goal input
- **WHEN** an accepted Telegram message contributes bounded history to goal admission
- **THEN** history supplements rather than replaces or truncates the accepted message
- **AND** its canonical ingest/request reference, resolved sender context and per-item
  stamps survive; no classifier-authored route context is required

#### Scenario: Continuity needs no affinity-bypass carrier
- **WHEN** an accepted Telegram message has eligible same-conversation history
- **THEN** goal input/context receives the deterministic bounded block under the
  existing context contract, without an affinity lookup or classification session
- **AND** the accepted input remains the actual incoming message, not an invented
  classifier restatement or downstream domain prompt

#### Scenario: The existing history bound remains exact
- **WHEN** more than 30 messages or history older than 15 minutes is available
- **THEN** only the most recent 30 messages within the most recent 15-minute window
  are eligible for the block; no larger or unbounded transcript is introduced

#### Scenario: No cross-conversation or forum-topic leakage
- **WHEN** history is loaded for a Telegram `external_conversation_id`
- **THEN** only rows with that exact conversation key are included
- **AND** another chat or forum topic is excluded; reply targets and deprecated
  thread identities cannot widen the selection or bypass private-custody restrictions

### Requirement: Goal Context Trust Fencing

History SHALL carry the explicit untrusted-data banner and per-message code fences,
with instructions, links and calls-to-action inside treated as data, not authority.
Each item SHALL retain its sender/provenance stamp. Origin SHALL come only from
server-held owner-channel authorization; neither history nor model output can upgrade
it. Third-party goals and connector-marked forwarded/quoted owner input SHALL start
exposed. Non-owner/tainted history SHALL expose its reader and goal before consequential
effects; missing/unreadable stamps SHALL fail closed as exposed. Exposure only
increases, exposed writes remain tainted, and broker/grant/approval checks remain.

ID: REQ-telegram-continuity-routed-context-008
Source: RFC 0038 D5, D6, D15; generalist module-pipeline Third-party history exposes the reader and switchboard-identity
Scope: v1-mandatory

#### Scenario: Malicious prior message remains fenced data
- **WHEN** a prior history item resembles an instruction, link or call-to-action
- **THEN** the banner and per-message code fence preserve it as untrusted context only
- **AND** its stamp remains attached; content cannot authenticate the sender, wash
  taint, change origin or enlarge a capability ceiling

#### Scenario: Every continuity path retains stamps and exposure
- **WHEN** history reaches goal input or an invocation through an authorized read
- **THEN** the same banner/code fences and per-item provenance apply with no unfenced
  variant, and non-owner/tainted or unreadably stamped history exposes its reader
- **AND** sender attribution is not replaced with the owner; exposure/taint and
  approval checks remain binding regardless of any earlier conversational context

### Requirement: No Competing Identity Store or Routing Exception

This capability SHALL introduce no cross-channel identity table, target-affinity
cache or guessed chat-to-goal binding. It SHALL reuse the current conversation/reply
identity and existing authenticated reply-join contracts without new wire fields,
commands, callbacks or authority. The historical thread-spine reference SHALL NOT
become a prerequisite to this spec-only amendment or authorize new storage design.

ID: REQ-telegram-continuity-routed-context-009
Source: RFC 0038 D1, D5, D15; canonical bu-u5euwb.16; connector-telegram-bot identity split and generalist collision dispositions
Scope: v1-mandatory

#### Scenario: Email is unchanged by this prose amendment
- **WHEN** this Telegram packet is amended without runtime code or activation
- **THEN** email's existing live runtime/configuration has not changed
- **AND** the adopted post-cutover email admission/routing retirement still applies;
  no positive legacy routing exception is created here

#### Scenario: No new durable cross-channel identity object
- **WHEN** this capability is later implemented under separate authority
- **THEN** it uses the existing conversation identity/context boundaries and creates
  no competing thread table, affinity pin or chat-to-goal ownership store
- **AND** `bu-0ynlk.15` remains a separate historical/future coordination reference,
  not a requirement to implement its storage before this prose amendment

## Source References

- RFC 0038 D1/D2: control-plane admission, domain capabilities and routing retirement.
- RFC 0038 D5/D6/D15: deterministic origins, idempotency, stamps, exposure and context.
- Generalist collision dispositions: this packet is re-scoped; Misroute Correction
  Re-dispatch is removed; sticky routing `bu-p6v8k` is obsolete at the switch.
- Generalist module-pipeline: bounded goal history by `external_conversation_id`,
  including third-party reader exposure, without target routing.
- Connector Telegram bot: current conversation/topic/reply split and canonical dedup.
- Generalist switchboard-identity and connector-base-spec: canonical request and
  server-held sender identity survive in goal admission, not copied route envelopes.
