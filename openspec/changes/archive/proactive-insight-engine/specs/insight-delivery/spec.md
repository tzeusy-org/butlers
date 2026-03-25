# Insight Delivery Pipeline

## Purpose
Defines how ranked and filtered insight candidates are delivered to the user via the existing `notify` contract. Covers delivery channel selection, digest batching for multi-insight delivery, standalone delivery for single insights, engagement tracking for adaptive feedback, and the visual rendering contract for insight notifications.

## ADDED Requirements

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
- **THEN** one engagement tracking row SHALL be created per delivered candidate in `shared.insight_engagement` with `insight_id`, `delivered_at`, and `engaged=FALSE`

#### Scenario: Engagement detection window
- **WHEN** the user sends any message to any butler (via any ingress channel) within 60 minutes of the insight's `delivered_at`
- **THEN** all engagement rows with `delivered_at` within the preceding 60 minutes and `engaged=FALSE` SHALL be updated to `engaged=TRUE`

#### Scenario: Engagement detection mechanism
- **WHEN** the Switchboard processes an ingress request
- **THEN** it SHALL check `shared.insight_engagement` for rows with `engaged=FALSE` and `delivered_at` within the last 60 minutes
- **AND** if any exist, it SHALL update them to `engaged=TRUE`
- **AND** this check SHALL be lightweight (indexed query) and SHALL NOT delay ingress processing

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
