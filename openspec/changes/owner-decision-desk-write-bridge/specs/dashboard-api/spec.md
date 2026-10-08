## ADDED Requirements

### Requirement: Decision Intent Endpoints

The dashboard API SHALL expose `POST /api/decisions/{bead_id}/intent` with body `{option}` for the
authenticated owner. It SHALL record a `dashboard` decision intent under the Owner Decision Desk
recording contract and return `ApiResponse<DecisionIntent>` with `meta.created`. A conflicting live
intent SHALL return HTTP 409, an option or bead that fails validation SHALL return HTTP 422 with
the named reason, and an unavailable digest SHALL return HTTP 503. The endpoint SHALL NOT call `bd`,
reach the tracker, or apply the decision itself.

The API SHALL expose two connector routes for Telegram callbacks.
`GET /api/decisions/prompts/{prompt_id}`, authorized for the dashboard owner or by the Telegram
connector's scoped callback credential, returns the prompt's bead id, offered options, creation
time, delivery outcome, and the bead's live intent if any. `POST
/api/decisions/prompts/{prompt_id}/choose` with body `{option_index}` records a `telegram` intent
for the prompt's offered option at that index. It SHALL require Telegram provenance, the connector
callback credential together with actor `owner@telegram`, and SHALL refuse anything else with HTTP
403, including the dashboard owner, who records through the owner intent route instead. The choose
route SHALL refuse with HTTP 409 and reason `options_changed` when the bead's current options
differ from the prompt's snapshot. The connector credential SHALL authorize exactly these two
routes in addition to the approval callback routes.

Each `GET /api/decisions` item SHALL additionally carry `intent`: `null` when the bead has no live
or failed intent, otherwise the most recent intent's `id`, `option`, `status`, `source`,
`created_at`, `failure_reason`, and `last_error`. A failure to read intents SHALL leave `intent`
`null` and add `decision_intents` to `meta.sources_degraded`; it SHALL NOT fail the digest.

#### Scenario: Owner records an intent through the API

- **WHEN** the owner posts an offered option for an open decision
- **THEN** the response is HTTP 200 with a `pending` intent and `meta.created: true`
- **AND** a repeated identical post returns the same intent with `meta.created: false`

#### Scenario: Connector credential is confined to its routes

- **WHEN** a request presents the connector callback credential on any decision route other than
  the prompt detail and choose routes
- **THEN** it is not authorized by that credential

#### Scenario: The choose route requires Telegram provenance

- **WHEN** the dashboard owner, or the connector credential without actor `owner@telegram`, posts
  to the choose route
- **THEN** the response is HTTP 403 and no intent is recorded

#### Scenario: The digest shows recorded intent state

- **WHEN** an open decision has a pending, applying, applied, or failed intent
- **THEN** its `GET /api/decisions` item carries that intent's status and option
- **AND** a failed intent carries its categorical `failure_reason`
