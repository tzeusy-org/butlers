# Insight Delivery Pipeline

## Purpose
Defines how ranked and filtered insight candidates are delivered to the user via the existing `notify` contract. Covers delivery channel selection, digest batching for multi-insight delivery, standalone delivery for single insights, engagement tracking for adaptive feedback, and the visual rendering contract for insight notifications.

## Requirements

### Requirement: Digest Mode Delivery
When the effective daily budget is greater than 1, insights SHALL be delivered as a single batched digest message rather than individual notifications.

#### Scenario: Digest formatting
- **WHEN** the delivery cycle selects B candidates (B > 1) for delivery
- **THEN** it SHALL compose a single digest message grouping insights as a numbered list
- **AND** each item SHALL include the origin butler name in brackets and the insight message
- **AND** the digest SHALL be prefixed with a header indicating the count (e.g., "Daily Insights (3):")

#### Scenario: Digest delivered as single notify call
- **WHEN** a digest is composed
- **THEN** it SHALL be delivered via a single `notify()` call with `intent='insight'`
- **AND** the `metadata` field of the notify envelope SHALL include `insight_count` and `insight_ids` (list of candidate UUIDs)

#### Scenario: All candidates in digest share delivery timestamp
- **WHEN** a digest is delivered
- **THEN** all candidate rows included in the digest SHALL have their `delivered_at` set to the same timestamp
- **AND** all SHALL have `status` set to `'delivered'`

### Requirement: Standalone Delivery
When the effective daily budget is 1, the single insight SHALL be delivered as a standalone message without digest framing.

#### Scenario: Standalone message format
- **WHEN** the delivery cycle selects exactly 1 candidate for delivery
- **THEN** it SHALL be delivered via `notify()` with `intent='insight'`
- **AND** the message SHALL be the candidate's `message` field directly, without digest numbering or header
- **AND** the origin butler SHALL be indicated in the message prefix (e.g., "[Health] You haven't logged blood pressure in 12 days")

### Requirement: Delivery Channel Selection
The insight delivery pipeline SHALL use the user's preferred channel, with per-candidate channel preferences as optional overrides.

#### Scenario: Default to user's primary channel
- **WHEN** a candidate has no `channel` specified (NULL)
- **THEN** the delivery SHALL use the owner contact's primary channel as resolved by the `notify` contract's default recipient resolution

#### Scenario: Candidate-specified channel
- **WHEN** a candidate specifies `channel='email'`
- **THEN** the delivery SHALL use email as the channel for that candidate
- **AND** if the candidate is part of a digest, the entire digest SHALL use the most common channel among its candidates (majority wins, ties broken by first candidate's channel)

### Requirement: Engagement Tracking
The delivery pipeline SHALL track whether the user engaged with delivered insights to feed the adaptive delivery mechanism.

#### Scenario: Engagement row creation
- **WHEN** an insight (standalone or digest) is delivered
- **THEN** one engagement tracking row SHALL be created per delivered candidate in `public.insight_engagement` with `insight_id`, `delivered_at`, and `engaged=FALSE`

#### Scenario: Engagement detection window
- **WHEN** the OWNER sends any message to any butler (via any ingress channel) within 60 minutes of the insight's `delivered_at`
- **THEN** all engagement rows with `delivered_at` within the preceding 60 minutes and `engaged=FALSE` SHALL be updated to `engaged=TRUE`

#### Scenario: Engagement detection mechanism
- **WHEN** the Switchboard processes an ingress request
- **THEN** it SHALL resolve the sender's identity via the standard channel reverse-lookup (`resolve_contact_by_channel`) and, ONLY when the sender resolves to the owner, check `public.insight_engagement` for rows with `engaged=FALSE` and `delivered_at` within the last 60 minutes
- **AND** if any exist, it SHALL update them to `engaged=TRUE`
- **AND** ingress from a connector, an automated source, or any non-owner (including unresolved/unknown) sender SHALL NOT be counted as engagement, so connector noise cannot poison the disengagement ratchet
- **AND** this check SHALL be lightweight (indexed query) and SHALL NOT delay ingress processing

#### Scenario: Daily attention rollup survives raw-event purge
- **WHEN** the delivery cycle's cleanup step is about to delete `public.insight_engagement` rows older than the retention window
- **THEN** it SHALL first upsert one row per affected day into `public.attention_daily_rollup` (`day`, `insights_delivered`, `insights_engaged`) summarizing that day's delivery/engagement counts
- **AND** the total-disengagement check SHALL fall back to `public.attention_daily_rollup` for any day in its window no longer present in `public.insight_engagement`, so the disengagement ratchet's history is not truncated by the raw-event purge

### Requirement: Insight Notify Intent
Insights delivered via `notify` SHALL use a dedicated `intent='insight'` that the Messenger butler can render with appropriate visual treatment.

#### Scenario: Insight intent in notify envelope
- **WHEN** the delivery pipeline calls `notify()` for an insight
- **THEN** it SHALL set `intent='insight'` in the notify call
- **AND** the Messenger butler SHALL treat `intent='insight'` as equivalent to `intent='send'` for delivery mechanics
- **AND** the Messenger MAY apply visual differentiation (e.g., a prefix label or formatting) but this is not required for initial implementation

#### Scenario: Insight intent does not require approval for owner
- **WHEN** an insight is delivered to the owner contact via `notify(intent='insight')`
- **THEN** it SHALL bypass the approval gate (consistent with existing `intent='send'` behavior for owner-targeted notifications)

### Requirement: Failed Delivery Handling
If insight delivery fails, the candidate SHALL remain eligible for retry in the next delivery cycle.

#### Scenario: Notify call failure
- **WHEN** the `notify()` call for an insight delivery returns `status='error'`
- **THEN** the candidate's status SHALL remain `'pending'` (not marked as delivered or filtered)
- **AND** the failure SHALL be logged with the error details
- **AND** the candidate SHALL be eligible for delivery in the next cycle (subject to expiry)

#### Scenario: Repeated delivery failure
- **WHEN** a candidate fails delivery on 3 consecutive cycles
- **THEN** it SHALL be marked `status='filtered'` with metadata indicating delivery failure
- **AND** no cooldown SHALL be recorded (the insight was never delivered)

### Requirement: Delivery Reference Persistence
When a delivery succeeds, the pipeline SHALL persist `public.insight_candidates.delivery_ref` for every delivered candidate: the notification id, channel, whether the delivery was standalone, its 1-based digest line index (digest only), and, when the notify result carries a Telegram-shaped provider message with a resolvable chat, `chat_id` and `provider_message_id`. Persistence SHALL be best-effort inside its own savepoint: a failure SHALL NOT roll back the `delivered` status, cooldown, or engagement rows.

#### Scenario: Standalone Telegram delivery stores an editable reference
- **WHEN** a single candidate is delivered and the notify result contains the provider `message_id` and `chat.id`
- **THEN** `delivery_ref` records `channel="telegram"`, `standalone=true`, the chat id and the provider message id

#### Scenario: A result without a resolvable chat is not editable
- **WHEN** the notify result carries no chat (for example an email delivery)
- **THEN** `delivery_ref` omits the provider fields and any later amendment folds instead of editing

### Requirement: Amend Transport
The Switchboard SHALL expose an `amend_delivery` operation that edits a previously delivered Telegram message by routing `telegram_edit_message_text` to the butler owning the telegram module, so transport stays in the channel module. It SHALL return `{"status": "edited"}` when the edit is accepted, `{"status": "rejected"}` for a Telegram 400, and `{"status": "error"}` for anything transient, unroutable or unrecognised. An unrecognised result SHALL NEVER be reported as an edit.

#### Scenario: Rejected edit is reported, not raised
- **WHEN** Telegram answers the edit with HTTP 400
- **THEN** `amend_delivery` returns `{"status": "rejected"}` and the broker folds the correction


### Requirement: Finance insights use the canonical whole-envelope inert renderer

Finance insight delivery MUST use the existing authenticated canonical Messenger rendering boundary. Any extracted or unknown contributing display value, or independent exposure, SHALL require all links in the final envelope to be inert with previews disabled; only owner-only independently unexposed output SHALL retain ordinary rendering.

ID: REQ-insight-delivery-001
Source: bu-svqpzo.19 proposed owner-door qualification; RFC 0038 D4/D6; canonical Messenger bu-vbdug9.1
Scope: v1-mandatory

#### Scenario: R03 Owner-only unexposed names retain ordinary rendering

- **WHEN** every contributing display value is owner-origin and the envelope is independently unexposed
- **THEN** raw stored values and ordinary delivered names MUST remain unchanged
- **AND** an owner source label alone MUST NOT satisfy the independent exposure condition

#### Scenario: R04 Mixed digests protect the whole message

- **WHEN** an owner-origin contribution and an extracted or unknown contribution share a digest
- **THEN** all links in the final digest MUST render inert, independent of contribution order
- **AND** both underlying raw records and their origins MUST remain unchanged

#### Scenario: R05 Independent exposure also protects owner text

- **WHEN** all Finance names are owner-origin but the delivery envelope is independently exposed
- **THEN** the canonical whole-envelope inert rule and preview suppression MUST still apply
- **AND** owner field authority MUST NOT declassify the envelope

#### Scenario: R10 Candidate readback cannot reactivate extracted labels

- **WHEN** the actual Switchboard candidate API returns message and metadata or a UI displays that projection
- **THEN** the projection MUST be safe to display without active extracted links, including metadata labels
- **AND** count-only aggregate insight endpoints MUST NOT stand in for this guarantee and a raw owner-authorized record read MUST NOT be silently rewritten

#### Scenario: R11 Verified cancellation door keeps authority under inert rendering

- **WHEN** an owner-sourced cancellation URL accompanies an extracted service name or an independently exposed envelope
- **THEN** the raw stored door and `door_verified: true` MUST remain authoritative as specified by REQ-finance-alerts-002
- **AND** its final rendered URL MUST nevertheless be inert as part of the whole message; only the owner-only independently unexposed case retains ordinary live rendering

#### Scenario: R12 The actual notification bridge cannot clear protection

- **WHEN** the registered insight-delivery job sends through its production notify closure with any candidate order, channel tie or caller false flag
- **THEN** the closure MUST carry the persisted union of protection into the authenticated canonical Messenger boundary
- **AND** the existing direct-delivery branch or an ignored metadata flag MUST NOT count as enforcement; recipient resolution, intent feedback, budgets and failure mapping MUST remain intact

#### Scenario: R13 Unsupported rendering or permission failure refuses truthfully

- **WHEN** the selected channel cannot enforce protection or current recipient permission is unavailable
- **THEN** canonical fail-closed or PARK behavior MUST prevent a raw fallback send
- **AND** the candidate MUST NOT be falsely marked sent; provider-start, at-most-once and ambiguous-outcome rules MUST remain unchanged

#### Scenario: R14 Any used temporal continuation restores trusted protection

- **WHEN** an implementation actually routes Finance delivery through the generic temporal queue
- **THEN** the stored envelope, digest coalescer and real scheduler/background replay MUST preserve the union of protection and restore the trusted private mark before canonical dispatch
- **AND** copying a public Boolean into storage alone MUST NOT count; the broker-owned delay/retry path MAY instead be retained without adding a second queue
