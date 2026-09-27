## ADDED Requirements

### Requirement: Rule Promotion Suggestion Confirm, Dismiss, and Rule-Enabled Endpoints

The dashboard API SHALL expose three per-suggestion owner actions under `/api/switchboard/rule-promotion-suggestions/{id}`; there is no bulk-confirm endpoint:

- `POST .../{id}/confirm` mints the `ingestion_rules` row for a `pending_review` suggestion and returns `ApiResponse[IngestionRule]`. It returns 404 when the suggestion does not exist, 409 when it is not `pending_review`, and 422 for an invalid id or a `route_to` target that is not a registered butler. Each confirm is audited as `rule_promotion_confirm`.
- `POST .../{id}/dismiss` accepts an optional body `{reason, cooldown_days}` (`cooldown_days` defaults to 30 and MUST be non-negative), transitions the suggestion to `dismissed`, and records the reason, `cooldown_until`, and `decided_by = 'owner'`. It returns 404 when the suggestion does not exist and 409 when it is not `pending_review`.
- `POST .../{id}/rule-enabled` accepts `{enabled}` and toggles the rule minted from that suggestion, returning `{rule_id, enabled}`. It returns 404 when the suggestion or its rule does not exist and 409 when the suggestion has no minted rule. This is the reversible control for auto-applied promotions.

#### Scenario: Confirm a single suggestion

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm` is called on a `pending_review` suggestion
- **THEN** the response status MUST be 200 and the body MUST carry the created ingestion rule
- **AND** a second confirm of the same suggestion MUST return 409

#### Scenario: Dismiss with reason

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/{id}/dismiss` is called with `{"reason": "Sender's routing target changed"}`
- **THEN** the suggestion MUST transition to `dismissed` with the reason and a cooldown recorded

#### Scenario: Disable an auto-applied rule

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/{id}/rule-enabled` is called with `{"enabled": false}` for an auto-applied suggestion
- **THEN** the minted rule MUST be disabled and the response MUST report `{rule_id, enabled: false}`
- **AND** calling it with `{"enabled": true}` MUST re-enable the same rule

### Requirement: Rule Promotion Approvals Banner

The approvals dashboard page SHALL render a rule-promotion banner, visually consistent with the Autonomy Suggestions section, fed by `GET /api/switchboard/rule-promotion-suggestions`.

- Each `pending` suggestion MUST render as its own card showing the sender and proposed action, the evidence count, and "Confirm rule" and "Dismiss" actions.
- Each `auto_applied` suggestion MUST render informationally (it needs no confirmation) with its evidence count marked as clearly automated and a reversible "Disable rule" / "Re-enable rule" control backed by the rule-enabled endpoint.
- When the read fails, the banner MUST show a degraded-source note rather than hiding itself.

#### Scenario: Pending suggestion renders as its own card

- **WHEN** a pending `route_to` suggestion exists
- **THEN** the banner MUST display it as its own card, not grouped with other suggestions

#### Scenario: Confirming from the dashboard calls the API

- **WHEN** the owner clicks "Confirm rule" on a pending card
- **THEN** the dashboard MUST call `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm`
- **AND** on success the card MUST leave the banner and a "Routing rule created" toast MUST appear

#### Scenario: Auto-applied promotions are reversible

- **WHEN** an auto-applied promotion is listed
- **THEN** it MUST render without a confirm action
- **AND** clicking "Disable rule" MUST call the rule-enabled endpoint and the control MUST then offer "Re-enable rule"

#### Scenario: Nothing to show hides the banner

- **WHEN** there are no pending items, no auto-applied items, and no read error
- **THEN** the rule-promotion banner MUST NOT be rendered

## MODIFIED Requirements

### Requirement: Approvals Live Stream

The dashboard SHALL fan approval lifecycle events onto the unified fleet event bus (`WS /api/events/stream`). There is no dedicated approvals stream route.

#### Scenario: Stream event shape

- **WHEN** an approval transitions state
- **THEN** an event `{type: "approval", data: {kind: "created"|"approved"|"rejected"|"deferred"|"executed"|"expired"|"abandoned", approval_id, ...}}` is broadcast on `WS /api/events/stream`.

### Requirement: Rule Promotion Suggestions API Endpoint

The dashboard API SHALL expose `GET /api/switchboard/rule-promotion-suggestions`, taking no query parameters and returning `ApiResponse[RulePromotionSurface]` with two sections, `pending` and `auto_applied`, drawn from `switchboard.rule_promotion_suggestions`. This is a distinct endpoint namespace from `GET /api/approvals/suggestions` (autonomy tool-call suggestions) — the two suggestion families track different underlying tables and are not merged into one response shape, though both render through the dashboard's approvals-surface visual language.

- `pending` holds `pending_review` promotion suggestions that need an owner decision: every `route_to:<butler>` suggestion and any suggestion that is not clearly automated. Clearly-automated suggestions whose action is `skip` or `metadata_only` auto-apply and never appear here. Each item carries `id`, `sender_key`, `source_channel`, `proposed_rule_type`, `proposed_condition`, `proposed_action`, `evidence_count`, `is_clearly_automated`, `first_evidence_at`, `last_evidence_at`, and `created_at`, ordered by `created_at ASC`.
- `auto_applied` holds suggestions confirmed by the auto-apply actor, newest decision first and capped at 50. Each item carries `id`, `sender_key`, `source_channel`, `proposed_action`, `evidence_count`, `created_rule_id`, `rule_enabled` (the minted rule's live `enabled` state), `decided_at`, and `decided_by`.

If either section's query fails, that section MUST be returned empty and its source name (`rule_promotion_pending` or `rule_promotion_auto_applied`) MUST be listed in `meta.sources_degraded`, so a read failure is never shown as a genuinely empty queue.

#### Scenario: Fetch pending rule promotion suggestions

- **WHEN** `GET /api/switchboard/rule-promotion-suggestions` is called and pending suggestions exist
- **THEN** the response status MUST be 200
- **AND** `data.pending` MUST list them oldest first, excluding clearly-automated `skip`/`metadata_only` suggestions
- **AND** `data.auto_applied` MUST list auto-applied promotions with their minted rule's `rule_enabled` state

#### Scenario: No pending suggestions

- **WHEN** `GET /api/switchboard/rule-promotion-suggestions` is called and no pending or auto-applied suggestions exist
- **THEN** the API MUST return empty `pending` and `auto_applied` arrays with response status 200
- **AND** `meta.sources_degraded` MUST NOT name either section

## REMOVED Requirements

### Requirement: Rule Promotion Suggestion Confirm/Bulk-Confirm/Dismiss Endpoints

**Reason**: No bulk-confirm endpoint exists; clearly-automated suggestions auto-apply instead, and the shipped per-suggestion actions include a rule-enabled toggle.

**Migration**: See "Rule Promotion Suggestion Confirm, Dismiss, and Rule-Enabled Endpoints" in this spec.

### Requirement: Rule Promotion Suggestions Dashboard Section

**Reason**: The grouped "Confirm all N" batch action and the demotion Revoke/Keep cards were never built; the shipped surface is a banner of pending cards plus reversible auto-applied items.

**Migration**: See "Rule Promotion Approvals Banner" in this spec.
