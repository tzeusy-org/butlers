## ADDED Requirements

### Requirement: Finance recurrence and renewal absence is source-qualified

The Finance butler MUST resolve exactly one server-attested expected-signal producer before an
elapsed recurrence or renewal date can be classified as absent. Stale, dead/offline, unhealthy,
missing, unsupported, mixed, caller-asserted, or unreadable producer evidence MUST be
`unmeasurable` and MUST NOT create owner-behavior, missed-renewal, or inferred payment-state
wording.

ID: REQ-butler-finance-001
Source: RFC 0012 §Expected-signal producer provenance; RFC 0029 §Initial adoption
Scope: v1-mandatory

#### Scenario: Gmail provenance requires server ingress attestation

- **WHEN** a recurrence or tracked renewal is supported by server-attested Gmail ingress
- **THEN** its producer MUST be `connector:gmail`
- **AND** its required `producer_endpoint_identity` MUST equal the exact server-derived
  `source_endpoint_identity`
- **AND** a `source_message_id`, merchant match, or generic `source` label alone MUST NOT establish
  Gmail authority

#### Scenario: Healthy sibling Gmail endpoint cannot authorize absence

- **WHEN** the attested Gmail endpoint is dead, stale, unhealthy, missing, or unreadable while a
  different Gmail endpoint is healthy/current
- **THEN** the signal MUST be `unmeasurable` regardless of liveness row order
- **AND** the evaluator MUST NOT authorize absence from connector type alone

#### Scenario: Explicit owner provenance remains semantically bounded

- **WHEN** all supporting records carry a valid server-derived owner attestation
- **THEN** the producer MUST be `owner`
- **AND** an elapsed signal MUST mean only that no later owner-recorded observation exists
- **AND** it MUST NOT assert merchant behavior, payment success/failure, or subscription state

#### Scenario: SimpleFIN has no current expected-signal producer

- **WHEN** recurrence evidence comes from the in-process SimpleFIN scheduled sync
- **THEN** it MUST be `unmeasurable` under the current RFC 0029 producer vocabulary
- **AND** Gmail health, account `last_synced_at`, or `source=aggregator` MUST NOT be substituted for
  an exact connector heartbeat

#### Scenario: Manual CSV API and migrated rows require attestation

- **WHEN** recurrence evidence comes from current manual, CSV/bulk, API/bank-sync, backfill, split,
  or migrated rows without reserved server attestation
- **THEN** it MUST be `unmeasurable`
- **AND** caller metadata and schema source vocabulary MUST NOT be treated as liveness authority

#### Scenario: Subscription property-fact writer is not a hidden recurrence input

- **WHEN** `track_subscription_fact` writes its separate Finance subscription property fact
- **THEN** current recurrence and renewal readers MUST NOT treat that fact as dedicated-table
  subscription evidence
- **AND** a future reader MUST classify its caller-supplied provenance as `unmeasurable` unless the
  reserved server attestation contract is applied

#### Scenario: Dead source past the expected date emits no absence claim

- **WHEN** the sole mapped connector is stale, dead/offline, unhealthy, missing, or unreadable
  after `next_expected_date`
- **THEN** the signal MUST be `unmeasurable`, never `absent`
- **AND** Finance MUST emit no owner-behavior, missed-renewal, or inferred payment-state candidate
  or dashboard verdict

#### Scenario: Healthy elapsed source does not invent a notification policy

- **WHEN** the sole producer is healthy/current and `next_expected_date` has elapsed
- **THEN** RFC 0029 MAY classify the signal as `absent`
- **AND** Finance MUST NOT emit a new candidate unless a separately approved existing policy
  explicitly consumes that absent state
