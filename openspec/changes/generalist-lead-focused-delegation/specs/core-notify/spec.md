## MODIFIED Requirements

### Requirement: Notify Tool Registration
Every butler daemon SHALL register a `notify(channel?, message, entity_id?, recipient?, subject?, intent?, emoji?, request_context?)` MCP tool during startup as an LLM-presentable `communicate` capability with complete authorization metadata. A goal invocation MUST be able to call this tool through the broker when its grant includes it, and SHALL NOT reach it otherwise. The tool MUST be registered on every butler's canonical MCP endpoint regardless of which modules are enabled.
Note: target resolution is keyed on `entity_id` (a `public.entities` UUID), resolved against `relationship.entity_facts`. An earlier design used a `contact_id` keyed on `public.contacts` / `public.contact_info`; that identity path was retired in favor of the entity graph. The requirements below reflect the entity-graph reality.
The LLM-presentable schema SHALL NOT offer `priority` or another urgency escape.
A model call, including an owner-started goal, SHALL follow routine quiet-hours
and sleep suppression. Only an allowlisted deterministic safety producer, a
matching current owner-authored break-through rule, or an obligation explicitly
flagged for break-through by the owner at creation SHALL receive its authorized
suppression exception. The delivery boundary SHALL derive that classification
from verified server-held producer identity or current persisted owner
provenance, never message text, model labels or caller-provided flags. Missing,
unknown, stale, revoked or spoofed provenance SHALL be routine; retry and
fallback SHALL NOT raise urgency. Classification/cause receipts SHALL be
content-free. An exception SHALL NOT bypass capability/grant, recipient,
category, approval, Stop, mode or health restrictions. This is the explicit
successor to model-selected `priority=high`, not an unconditional immediate
send guarantee or a new deterministic-sender authorization path.
`channel` is OPTIONAL. When the caller omits it, the notify tool resolves it
before any channel-dependent validation runs (see the **Preferred-Channel
Resolution on Omitted Channel** requirement): an `entity_id`-targeted call
honours the entity's `prefers-channel` fact when deliverable, else falls back
to telegram → email; a call with no `entity_id` defaults to telegram. A
caller-forced `channel` is never overridden by preference resolution.


ID: REQ-core-notify-038
Source: RFC 0038 D2, D4 and D7; bu-u33sb7; bu-dhloi1.18
Scope: v1-mandatory

#### Scenario: Tool available to runtime instance
- **WHEN** a goal invocation whose grant includes a butler's `notify` lists its available MCP tools
- **THEN** that butler's `notify` tool MUST appear in the list under its namespaced name with parameters `channel` (optional string; resolved when omitted), `message` (required string), `entity_id` (optional UUID string), `recipient` (optional string), `subject` (optional string), `intent` (optional string), `emoji` (optional string), `request_context` (optional object)
- **AND** an invocation whose grant excludes it neither lists nor can call it

#### Scenario: Tool registered at startup
- **WHEN** a butler daemon starts up
- **THEN** the `notify` tool MUST be registered as a core MCP tool before the butler accepts any capability call or service-plane call

#### Scenario: A model cannot self-upgrade an interrupt
- **WHEN** an owner-started or third-party model call requests high priority in text, metadata or an unsupported argument
- **THEN** no model-selected value grants a quiet-hours or sleep exception, and the supported call remains routine under those holds
- **AND** missing or stale invocation binding cannot become owner or obligation authority

#### Scenario: A genuine producer retains its narrow suppression exception
- **WHEN** the delivery boundary verifies an allowlisted deterministic safety producer, a current matching owner break-through rule, or an obligation's persisted owner-at-creation flag
- **THEN** only its authorized quiet-hours or sleep exception applies, while all other authorization and delivery constraints still hold
- **AND** absence, revocation, spoofing or a retry cannot manufacture or upgrade that exception

### Requirement: Request Context Propagation

For `reply` and `react` intents, the `request_context` MUST carry lineage from the originating inbound request. This enables the Messenger butler to route the delivery to the correct conversation thread. The `request_context` SHALL select only the reply or reaction target; it SHALL NOT change approval gating, which follows the role of the resolved target.

Source: RFC 0038 D1, D5 and D15
Scope: v1-mandatory

#### Scenario: Request context forwarded to envelope
- **WHEN** `notify(intent="reply", request_context={...})` is called with valid context
- **THEN** the `request_context` is included in the `notify.v1` envelope as-is

#### Scenario: Request context from runtime session
- **WHEN** a goal invocation calls `notify` for a goal that was admitted from an ingest envelope
- **THEN** the invocation can pass the `request_context` of that envelope from its goal input
- **AND** a reply or reaction whose target does not resolve uniquely to the owner is gated exactly as any other non-owner notification

### Requirement: Role-Based Approval Gating for Notify
The `notify` tool SHALL apply approval gating based on whether the target
identifier resolves uniquely to the owner. An outbound communication MUST
bypass approval exactly when its normalized candidate identifier set resolves
through active literal facts to one distinct live, non-merged, non-deleted
entity with the `owner` role. Channel primacy MUST NOT affect this authorization
decision. Non-owner, unknown, ambiguous, inactive, merged, deleted, malformed,
or lookup-error targets MUST NOT receive the owner bypass and MUST continue
through standing-rule or pending-approval handling.
A call that names no target is owner communication only because `notify`
declares the owner as its default recipient. A reply or reaction is owner
communication only when the chat or recipients it reaches resolve uniquely to
the owner; a target that cannot be determined is non-owner.
A non-owner notification SHALL be delivered only with per-event owner approval
or under an owner-created standing rule. No goal origin, grant, exposure state,
lead or child, no deterministic or service-plane caller, and no tool argument,
SHALL bypass this gate. While the call's
verified assertion is exposed, a standing rule SHALL authorize a non-owner
notification only when the rule is template-pinned
(`REQ-capability-authorization-006`); otherwise
the notification SHALL be parked for per-event owner approval.

Source: RFC 0038 D4, D6, D13 and owner decision 9; RFC 0021
Scope: v1-mandatory

#### Scenario: Notification to owner bypasses approval
- **WHEN** `notify(channel='telegram', message='Alert')` is called with no entity_id (defaults to owner)
- **THEN** the notification MUST be delivered without requiring approval

#### Scenario: Primary and secondary owner identifiers bypass approval
- **WHEN** `notify()` targets a primary or secondary email, Telegram identity, WhatsApp identity, or future communication identifier
- **AND** the normalized candidate set resolves uniquely through an active literal fact to one live owner entity
- **THEN** the notification MUST be delivered without requiring approval
- **AND** the fact's `primary` value MUST NOT affect the authorization decision

#### Scenario: Notification to non-owner requires approval
- **WHEN** `notify(channel='telegram', message='Reminder', entity_id='abc-123')` is called
- **AND** entity `abc-123` is not the owner
- **THEN** the notification MUST be checked against standing approval rules
- **AND** if no rule matches, it MUST be parked as a pending action

#### Scenario: Standing rule auto-approves non-owner notification
- **WHEN** a standing approval rule exists matching `tool_name='notify'` with constraint `entity_id='abc-123'`
- **AND** `notify(channel='telegram', message='Hi', entity_id='abc-123')` is called without an exposed assertion
- **THEN** the notification MUST be auto-approved and delivered immediately

#### Scenario: Unresolvable target requires approval
- **WHEN** `notify(channel='telegram', message='Hi', recipient='unknown@example.com')` is called
- **AND** reverse-lookup of `('email', 'unknown@example.com')` returns no contact
- **THEN** the notification MUST require approval (conservative default)

#### Scenario: Unsafe owner association receives no bypass
- **WHEN** a target identifier is external, unknown, ambiguous across entities, inactive, attached to a merged or deleted entity, malformed, or cannot be resolved because the lookup fails
- **THEN** the notification MUST NOT receive the owner bypass
- **AND** it MUST continue through standing-rule or pending-approval handling

#### Scenario: Goal lineage grants no bypass
- **WHEN** a lead or child of any origin calls `notify` for a non-owner target and no standing rule matches
- **THEN** the notification MUST be parked for owner approval
- **AND** neither the lead nor the child can approve it

#### Scenario: An exposed call needs a template-pinned rule
- **WHEN** an exposed invocation calls `notify` for a non-owner target
- **AND** the only matching standing rule is not template-pinned (`REQ-capability-authorization-006`), for example because it leaves the message body or subject unconstrained
- **THEN** the notification MUST be parked for per-event owner approval
- **AND** the rule's `use_count` is unchanged

### Requirement: Messenger route.execute Approval Gate (Defense-in-Depth)
The Messenger's `route.execute` handler calls channel module methods directly (`_send_email()`, `_send_message()`, `_reply_to_message()`), bypassing MCP tool wrappers. Because the MCP-level approval gate is not in this code path, `route.execute` MUST independently re-enforce role-based approval gating before invoking any outbound channel adapter.

This requirement exists because the delivery architecture has two layers: `notify()` MCP tool → Switchboard `deliver()` → Messenger `route.execute` → direct module call. If only the first layer gates, any bypass of the MCP tool layer (e.g., direct `route.execute` invocation) would allow ungated delivery.

This handler is Messenger's service-plane delivery endpoint (today `route.execute` on Messenger). It survives the deletion of `route.execute`'s session-dispatch role as a deterministic endpoint that SHALL NOT be LLM-presentable, SHALL NOT be served on the session listener, and SHALL be called only by Switchboard `deliver()`. Its gate SHALL apply the same non-owner rule to every envelope it processes, whoever sent it, including deterministic service-plane senders: a delivery whose target does not resolve uniquely to the owner SHALL proceed only with per-event owner approval or under an owner-created standing rule.

Source: RFC 0038 D4 and D7; `REQ-capability-authorization-016`
Scope: v1-mandatory

#### Scenario: Messenger route.execute blocks non-owner email without rule
- **WHEN** the Messenger's `route.execute` processes a `notify.v1` envelope with `channel="email"`
- **AND** the email target resolves to a non-owner contact (or is unknown)
- **AND** no standing approval rule matches `email_send_message` or `email_reply_to_thread` for that target
- **THEN** nothing is sent: the delivery MUST follow the pending-approval path, and the caller receives a descriptive result naming the pending approval

#### Scenario: Messenger route.execute blocks non-owner telegram without rule
- **WHEN** the Messenger's `route.execute` processes a `notify.v1` envelope with `channel="telegram"` and `intent` in `("send", "reply")`
- **AND** the telegram target resolves to a non-owner contact (or is unknown)
- **AND** no standing approval rule matches `telegram_send_message` or `telegram_reply_to_message` for that target
- **THEN** nothing is sent: the delivery MUST follow the pending-approval path, and the caller receives a descriptive result naming the pending approval

#### Scenario: Messenger route.execute permits owner delivery without rule
- **WHEN** the Messenger's `route.execute` processes a `notify.v1` envelope
- **AND** the exact target resolves through an active literal identifier to one distinct live contact with the `owner` role
- **THEN** delivery proceeds immediately without checking standing rules
- **AND** the identifier's `primary` value does not affect the authorization decision

#### Scenario: Messenger route.execute refuses unsafe owner association
- **WHEN** the Messenger's `route.execute` processes a `notify.v1` envelope
- **AND** the channel identifier is external, unknown, ambiguous across entities, inactive, attached to a merged or deleted entity, malformed, or fails lookup
- **THEN** the target MUST NOT receive the owner bypass
- **AND** delivery MUST follow the standing-rule or pending-approval path

#### Scenario: A deterministic sender receives no bypass
- **WHEN** a deterministic service-plane sender, such as a scheduled job, asks Switchboard `deliver()` to send a `notify.v1` envelope to a target that does not resolve uniquely to the owner
- **AND** no owner-created standing rule matches
- **THEN** delivery MUST be blocked and follow the pending-approval path
- **AND** nothing is sent until the owner approves

#### Scenario: The delivery endpoint is not model-reachable
- **WHEN** a goal invocation lists its capabilities or calls Messenger's service-plane delivery endpoint by name
- **THEN** the endpoint is neither listed nor callable
- **AND** the invocation can reach a channel only through a granted `communicate` capability such as `notify`

### Requirement: Runtime Session Correlation for notify() Ledger Rows

Every `public.attention_ledger` row written at the `notify()` boundary with `source="notify"` SHALL carry the invocation lineage of the goal invocation that made the call (the `goal_id`, `invocation_id` and `depth` of the call's verified broker assertion) under the matching keys of the row's `metadata`, when the call carries goal lineage.

The lineage SHALL be read once at the top of the `notify()` call, so every terminal outcome the same call can reach names the same invocation. Recording it SHALL remain best-effort and fail-open like the rest of the ledger write: a call without goal lineage SHALL leave the metadata otherwise unchanged rather than failing the notification.

This exists so that a caller holding an invocation id (for example a lead reconciling a child's effect, or the goal ledger) can ask the notification path what became of that invocation's notification, instead of guessing from the originating butler and a time window and risking crediting an unrelated notification.

A reader SHALL be provided that returns the terminal notify dispatch recorded for one `(origin_butler, invocation_id)` pair, preferring a `delivered` row over any other outcome and the most recent row within an outcome, and returning nothing when the ledger holds no such row.

That reader SHALL NOT fail open. A ledger that cannot be read SHALL surface as an error to its caller rather than as an empty result, because "no row" and "could not look" are different answers and a caller may only claim absence of evidence for the former.

Source: RFC 0038 D7 and D17
Scope: v1-mandatory

#### Scenario: A notify ledger row names its runtime session

- **WHEN** `notify()` is called through a verified assertion that carries goal lineage and reaches any terminal outcome
- **THEN** the `public.attention_ledger` row it writes SHALL carry that call's `goal_id`, `invocation_id` and `depth` in its `metadata`
- **AND** any metadata the call site already supplied SHALL be preserved alongside them

#### Scenario: An unbound session leaves the row unchanged

- **WHEN** `notify()` is called without goal lineage (a service-plane caller, a deterministic job, or a dashboard call whose assertion has a null `goal_id`)
- **THEN** the ledger row SHALL be written exactly as it would be without this requirement, with no lineage keys

#### Scenario: The reader returns the dispatch recorded for a session

- **WHEN** the ledger holds a `source="notify"` row for a given origin butler and invocation id
- **THEN** the reader SHALL return that row's `outcome`, `occurred_at`, `channel`, `reason` and `notification_ref`

#### Scenario: The reader distinguishes no row from no answer

- **WHEN** the reader is asked about an origin butler and invocation id for which the ledger holds no `source="notify"` row, or the ledger cannot be read at all
- **THEN** the reader SHALL return nothing when no such row exists
- **AND** it SHALL raise rather than return nothing when the ledger cannot be read, so a caller cannot mistake an unreadable ledger for a confirmed absence

## ADDED Requirements

### Requirement: Exposed Owner Notifications Render Links Inert
When a `notify` call's verified assertion is exposed and its target resolves uniquely to the owner, the notification SHALL be delivered without approval only with link previews disabled and every URL in the message rendered inert, neither clickable nor fetched by the channel. When the resolved channel cannot guarantee both, the notification SHALL instead be parked for per-event owner approval. Switchboard `deliver()` SHALL carry this instruction to Messenger as the `notify.v1` envelope's `render_links_inert` flag, set from the verified assertion and never from tool arguments (`REQ-butler-messenger-003`). Notifications from unexposed calls SHALL keep their existing rendering.

ID: REQ-core-notify-033
Source: RFC 0038 D4, D6 and owner decision 3
Scope: v1-mandatory

#### Scenario: An injected link reaches the owner inert
- **WHEN** an exposed invocation calls `notify` for the owner with a message that contains a URL
- **THEN** the message is delivered without approval and with link previews disabled
- **AND** the URL appears as inert text that the channel neither links nor fetches

#### Scenario: A channel that cannot render links inert parks the notification
- **WHEN** an exposed invocation calls `notify` for the owner on a channel that cannot disable previews or render URLs inert
- **THEN** the notification is parked for per-event owner approval
- **AND** nothing is delivered until the owner approves

#### Scenario: Unexposed owner notifications are unchanged
- **WHEN** an unexposed invocation calls `notify` for the owner
- **THEN** the notification is delivered with its existing formatting and link behavior

## ADDED Requirements

### Requirement: Owner notifications use canonical delivery subjects
Owner notify and reply integration SHALL use S6's one subject/many presentations, route identity, generation/token and honest receipt interface. Existing per-event approvals, RFC0023 recovery, inert links and reply delivery accounting SHALL remain in force until their named successor is verified and atomically activated.

ID: REQ-core-notify-036
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S6; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: One decision appears on two surfaces
- **WHEN** the owner decides the same subject on dashboard and Telegram concurrently
- **THEN** one canonical action wins, all other tokens become stale, and failed retraction leaves no executable stale action

#### Scenario: Provider delivery is ambiguous
- **WHEN** the transport cannot prove whether a send reached its provider
- **THEN** the presentation stays unknown with a receipt, not delivered or blindly resent under a new identity

#### Scenario: No successor is activated
- **WHEN** new delivery tables exist dark
- **THEN** the existing delivery writer remains authoritative with no double dispatch or premature retired callback

### Requirement: Recovery prompts never silently rerun failed asks
Capability health and owner-failure handling SHALL use S6 typed dependency/cause/pending-ask and Run/Drop semantics through the canonical owner subject. Model-free failure lines and push slots SHALL NOT imply a heal action, urgency decision, deployment or automatic retry.

ID: REQ-core-notify-037
Source: about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md, proposed shared switch interfaces S6; bu-dhloi1.19
Scope: v1-mandatory

#### Scenario: An owner failure has no delivery
- **WHEN** an owner ask ends failed/budget_exhausted/admission_refused without a delivery receipt
- **THEN** one cause line and pending-ask identity are recorded; recovery coalesces one Run/Drop card

#### Scenario: Run races Drop or an old retry
- **WHEN** a duplicate/stale callback or an ask older than24h is acted on
- **THEN** subject fencing admits new work at most once for a valid Run; Drop and dropped(stale) never rerun

#### Scenario: Health projection is unreadable
- **WHEN** dependency status cannot be read
- **THEN** status remains unknown and normal call/authorization checks remain, with no fabricated healthy result or bypass
