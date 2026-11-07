## ADDED Requirements

### Requirement: External fact proposals park through a deterministic, push-free, rule-free path
The approvals module SHALL provide a park entry point, `park_external_proposal`, through which the Relationship proposal importer parks a pending `relationship_assert_fact` action outside the gate wrapper, using deduplication key `external_mcp:<proposal_id>`, a server-set expiry and structured template evidence. It SHALL use the same durable admission protocol as the other park paths, and when approval-delivery admission is enabled it SHALL create only one standalone `collapsed` presentation, as `park_prepared_action` does, so the action never reaches a delivery provider. Such an action SHALL match no standing rule, auto-approve under no policy, and emit no approval-request push or digest entry. It SHALL appear on the dashboard approvals list like any pending action. Its `origin` SHALL stay `NULL`, within the existing `NULL | 'prepared'` values; external provenance SHALL live only in the Relationship fact approval context. Approving an action whose deduplication key starts with `external_mcp:` SHALL record no autonomy approval history, SHALL neither count toward nor create a standing-rule promotion suggestion, and SHALL refuse `create_rule=true`. Otherwise approval, rejection, edit, expiry and execution SHALL use the existing status transitions and the shared executor unchanged.

ID: REQ-module-approvals-010
Source: RFC 0039 D13 steps 3 and 4 (no standing rule, no push, no promotion, origin not widened); [Observed] src/butlers/modules/approvals/park.py (`park_pending_action` notifies when admission is enabled) and operations.py (approval feeds promotion and `create_rule`); heart-and-soul/security.md Approval Gates
Scope: v1-mandatory

#### Scenario: A matching standing rule does not approve an external proposal
- **WHEN** an active standing rule would match `relationship_assert_fact` with the proposal's arguments and the importer parks it
- **THEN** the action SHALL remain `pending` with no `approval_rule_id`

#### Scenario: Parking an external proposal sends no push
- **WHEN** the importer parks 5 external proposals in one pass
- **THEN** no `approval_request` notification and no digest message SHALL be submitted for them

#### Scenario: Admission-enabled parking stays collapsed
- **WHEN** approval-delivery admission is enabled and the importer parks an external proposal
- **THEN** the only presentation recorded for the action SHALL be `collapsed`, and no delivery intent SHALL reach a provider

#### Scenario: Deduplication key prevents a second park
- **WHEN** the importer attempts to park a proposal whose deduplication key `external_mcp:<proposal_id>` already exists
- **THEN** no second pending action SHALL be created

#### Scenario: Origin values are unchanged
- **WHEN** an external proposal is parked
- **THEN** its `origin` SHALL be `NULL` and the `pending_actions` origin constraint SHALL be unchanged

#### Scenario: Approving outside proposals never suggests a rule
- **WHEN** the owner approves five external proposals with the same tool and argument shape
- **THEN** no autonomy approval history row and no promotion suggestion SHALL exist for that pattern

#### Scenario: An approve request cannot create a rule from an outside proposal
- **WHEN** an approve request for an external proposal carries `create_rule=true`
- **THEN** the request SHALL be refused, no standing rule SHALL be created, and the action SHALL stay `pending`

#### Scenario: Owner decision uses the shared executor
- **WHEN** the owner approves the action through `POST /api/approvals/actions/{id}/approve` with the exact displayed call
- **THEN** the shared executor SHALL dispatch `relationship_assert_fact` with the stored arguments and no model session

## Source References

- Non-Negotiable Rule 1 (owner sovereignty) and Rule 4 (deterministic infrastructure), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/security.md`, Approval Gates.
- RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D13.
- RFC 0021 (decision loop), shared executor and status transitions.
