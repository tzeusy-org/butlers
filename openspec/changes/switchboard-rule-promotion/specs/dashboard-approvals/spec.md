# Dashboard Approvals — Delta

## ADDED Requirements

### Requirement: Rule Promotion Suggestions API Endpoint

The dashboard API SHALL expose `GET /api/switchboard/rule-promotion-suggestions`, returning a paginated list of switchboard ingestion-rule promotion and demotion suggestions. This is a distinct endpoint namespace from `GET /api/approvals/suggestions` (autonomy tool-call suggestions) — the two suggestion families track different underlying tables (`switchboard.rule_promotion_suggestions` vs `autonomy_suggestions`) and are not merged into one response shape, though both render through the dashboard's approvals-surface visual language.

The endpoint SHALL accept query parameters `status` (default `pending_review`), `is_clearly_automated` (optional boolean filter), `limit` (default 20), `offset` (default 0).

Each returned suggestion object MUST include: `id`, `sender_key`, `source_channel`, `proposed_rule_type`, `proposed_condition`, `proposed_action`, `evidence_count`, `first_evidence_at`, `last_evidence_at`, `is_clearly_automated`, `status`, `created_rule_id`, `created_at`, `decided_at`, `decided_by`.

#### Scenario: Fetch pending rule promotion suggestions

- **WHEN** `GET /api/switchboard/rule-promotion-suggestions?status=pending_review` is called
- **THEN** the API MUST return all pending suggestions sorted by `evidence_count DESC, last_evidence_at DESC`
- **AND** the response status MUST be 200

#### Scenario: No pending suggestions

- **WHEN** `GET /api/switchboard/rule-promotion-suggestions?status=pending_review` is called and none exist
- **THEN** the API MUST return an empty array with response status 200

### Requirement: Rule Promotion Suggestion Confirm/Bulk-Confirm/Dismiss Endpoints

The dashboard API SHALL expose:
- `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm` — confirms a single suggestion, creating the corresponding `ingestion_rules` row.
- `POST /api/switchboard/rule-promotion-suggestions/bulk-confirm` — accepts a list of suggestion ids and confirms each independently, reporting per-id success/failure rather than failing the whole batch on one error. Intended for the batched automated-sender confirm affordance; the API MUST NOT skip the confirm requirement for any id in the batch (see `switchboard-rule-promotion` spec, "Owner-Confirmed Promotion").
- `POST /api/switchboard/rule-promotion-suggestions/{id}/dismiss` — accepts an optional `{"reason": string}` body, sets `cooldown_until`, transitions to `dismissed`.

All three endpoints require authenticated human actor context.

#### Scenario: Confirm a single suggestion

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm` is called with authenticated context on a `pending_review` suggestion
- **THEN** the response MUST include the created `rule_id`
- **AND** the response status MUST be 200

#### Scenario: Bulk-confirm reports per-item results

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/bulk-confirm` is called with 5 suggestion ids, one of which is already `dismissed`
- **THEN** the response MUST indicate 4 successful confirmations and 1 per-item failure, not a single all-or-nothing error
- **AND** the response status MUST be 200 for the batch call itself (individual failures are reported in the body, not via HTTP status)

#### Scenario: Dismiss with reason

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/{id}/dismiss` is called with `{"reason": "Sender's routing target changed"}`
- **THEN** the suggestion MUST transition to `dismissed` with the reason recorded

### Requirement: Rule Promotion Suggestions Dashboard Section

The approvals dashboard page SHALL include a "Rule Promotion" section, visually consistent with the existing Autonomy Suggestions section but rendering `switchboard.rule_promotion_suggestions` data, displayed when pending suggestions exist.

`route_to` suggestions MUST render as individual cards showing: the sender identity (`sender_key`), proposed target butler, evidence count, and first/last evidence dates, with "Confirm" and "Dismiss" actions per card.

`is_clearly_automated = TRUE` suggestions with `proposed_action` in (`skip`, `metadata_only`) MUST render grouped, with a single "Confirm all N" batched action calling the bulk-confirm endpoint, alongside the ability to expand and dismiss individual senders from the group before confirming the rest.

Demotion suggestions (rules flagged via spot-check drift) MUST render with a warning/alert visual style distinct from promotion cards, showing the rule's current scope description and the recent spot-check disagreement rate, with "Revoke rule" and "Keep rule" actions.

#### Scenario: Route-to suggestion card displayed individually

- **WHEN** a pending `route_to` suggestion exists
- **THEN** the dashboard MUST display it as its own card, not grouped with other suggestions

#### Scenario: Automated senders grouped with batch confirm

- **WHEN** 9 pending suggestions exist, all `is_clearly_automated=TRUE` with `proposed_action='skip'`
- **THEN** the dashboard MUST display them grouped under a single "Confirm all 9 automated senders" action

#### Scenario: Confirming from the dashboard calls the API

- **WHEN** a user clicks "Confirm" on an individual rule-promotion suggestion card
- **THEN** the dashboard MUST call `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm`
- **AND** the card MUST be removed from the section on success with a success toast naming the new rule

#### Scenario: No pending suggestions hides the section

- **WHEN** no pending rule-promotion or demotion suggestions exist
- **THEN** the Rule Promotion section MUST NOT be rendered
