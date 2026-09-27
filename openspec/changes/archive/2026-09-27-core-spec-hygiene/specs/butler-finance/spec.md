## RENAMED Requirements

- FROM: `### Requirement: CRUD-to-SPO migration -- finance domain (bu-ddb.4)`
- TO: `### Requirement: Finance data stored in dedicated tables with SPO mirror`

## MODIFIED Requirements

### Requirement: Finance Butler Schedules
The finance butler SHALL run bill/anomaly/budget/subscription intelligence as deterministic jobs that propose insight candidates through the switchboard insight broker, rather than prompt-mode tasks that notify directly, and SHALL run the deterministic SimpleFIN bridge as a separate ledger-sync job.

#### Scenario: Scheduled task inventory
- **WHEN** the finance butler daemon is running
- **THEN** it SHALL execute these `dispatch_mode = "job"` schedules, each resolving to the named handler:

  | Schedule | Cron | Handler |
  | --- | --- | --- |
  | `insight-scan` | `0 7 * * *` | `run_insight_scan` |
  | `bill-reconciliation-sweep` | `15 21 * * 0` | `run_bill_reconciliation_sweep` |
  | `anomaly-insight-scan` | `0 21 * * *` | `run_anomaly_insight_scan` |
  | `monthly-finance-digest` | `0 9 1 * *` | `run_monthly_finance_digest` |
  | `cost-claim-reconciliation-sweep` | `40 4 * * *` | `run_cost_claim_reconciliation_sweep` |
  | `simplefin-sync` | `17 4 * * *` | `run_simplefin_sync` |
  | `daily_briefing_contribution` | `55 6 * * *` | cross-butler briefing contribution |
  | `calendar_overlay_contribution` | `50 6 * * *` | cross-butler calendar overlay contribution |

- **AND** each intelligence job (`insight-scan`, `bill-reconciliation-sweep`, `anomaly-insight-scan`, `monthly-finance-digest`) SHALL propose candidates via `propose_insight_candidate()` for the switchboard's insight broker to dedup/cooldown/budget/deliver, rather than calling `notify()` directly; what each job proposes is specified in `finance-alerts/spec.md`
- **AND** `simplefin-sync` SHALL run with `job_name = "simplefin_sync"` and deterministically synchronize its Finance-owned ledger without calling `propose_insight_candidate()`, `notify()`, Switchboard routing, or an LLM runtime
- **AND** `cost-claim-reconciliation-sweep` SHALL deterministically reconcile shared cost claims against Finance evidence without an LLM session (see `finance-cost-claims/spec.md`)
- **AND** it SHALL NOT execute the six retired prompt-mode schedules that called `notify()` directly: `upcoming-bills-check` (15 21 * * 0), `subscription-renewal-alerts` (20 21 * * 0), `monthly-spending-summary` (0 9 1 * *), `anomaly-digest` (0 21 * * *), `budget-status-check` (0 9 * * 1), and `subscription-audit-monthly` (0 10 1 * *)

### Requirement: Finance Data Conventions
Financial data SHALL use precise numeric types, ISO currency codes, and tiered deduplication on the dedicated transaction table.

#### Scenario: Data type conventions
- **WHEN** financial data is recorded
- **THEN** amounts use `NUMERIC(14,2)` (never float), currency uses ISO-4217 uppercase codes (e.g., `USD`, `EUR`), timestamps use `TIMESTAMPTZ` preserving timezone, and direction is inferred as `debit` or `credit` from context

#### Scenario: Composite deduplication for non-email sources
- **WHEN** a transaction is recorded without a `source_message_id` and without an `external_id`
- **THEN** deduplication SHALL use the tiered UNIQUE partial index strategy on `finance.transactions`: Priority 1 `(account_id, external_id)`, Priority 2 `(source_message_id, merchant, amount, posted_at)`, Priority 3 `(account_id, posted_at, amount, merchant)` as fallback
- **AND** deduplication SHALL NOT use a `sha256` composite hash
- **AND** the existing `source_message_id`-based deduplication SHALL remain as Priority 2

### Requirement: Scheduled reconciliation sweep

The finance butler SHALL periodically reconcile stale pending and overdue bills
against recent transactions as a backstop for payments recorded without an
inline match.

#### Scenario: bill-reconciliation-sweep reconciles before reporting
- **WHEN** the `bill-reconciliation-sweep` job runs
- **THEN** it SHALL call `reconcile_bills(lookback_days=90)` before evaluating its other results
- **AND** auto-settled bills SHALL be proposed as a `bill-reconciled` insight candidate (priority 35, informational)
- **AND** ambiguous confirm-tier matches SHALL be proposed as a `bill-reconcile-candidate` insight candidate (priority 55) needing owner confirmation
- **AND** these SHALL be proposed via `propose_insight_candidate()` for delivery through the insight broker, not sent via a direct `notify()` digest

#### Scenario: Payment recorded before its bill is reconciled by the sweep
- **WHEN** a debit transaction was recorded before any matching bill existed
- **AND** a matching bill is later created (e.g. from a statement email)
- **THEN** `reconcile_bills` SHALL match the bill against the already-recorded
  transaction by scanning bill→transaction over the lookback horizon
- **AND** a high-confidence match SHALL be auto-settled on that sweep

### Requirement: Finance recurrence and renewal absence is source-qualified

The Finance butler MUST resolve exactly one server-attested expected-signal producer before an
elapsed recurrence or renewal date can be classified as absent. Stale, dead/offline, unhealthy,
missing, unsupported, mixed, caller-asserted, or unreadable producer evidence MUST be
`unmeasurable` and MUST NOT create owner-behavior, missed-renewal, or inferred payment-state
wording.

ID: REQ-butler-finance-001
Source: RFC 0012 §Expected-signal producer provenance; RFC 0029 §Initial adoption

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
