# Tasks

## Phase 1: Health Butler (bu-ddb.2)

- [x] Seed health predicates into `predicate_registry` (new Alembic migration)
- [x] Rewrite `measurement_log`, `measurement_history`, `measurement_latest` as fact wrappers
- [x] Rewrite `symptom_log`, `symptom_history`, `symptom_search` as fact wrappers
- [x] Rewrite `medication_log_dose`, `medication_history` as fact wrappers
- [x] Rewrite `medication_add`, `medication_list` as property-fact wrappers
- [x] Rewrite `condition_add`, `condition_list`, `condition_update` as property-fact wrappers
- [x] Rewrite `research_save`, `research_search` as property-fact wrappers
- [x] Update `health_summary` and `trend_report` to query facts
- [x] Update all health butler tests
- [x] Write Phase 1 backfill script (bu-ddb.7)

## Phase 2: Relationship Butler (bu-ddb.3)

- [x] Seed relationship predicates into `predicate_registry` (new Alembic migration)
- [x] Rewrite `interaction_log`, `interaction_list` as temporal fact wrappers
- [x] Rewrite `note_create`, `note_list`, `note_search` as temporal fact wrappers
- [x] Migrate `quick_facts` (fact_set, fact_list) as property facts (key→predicate, value→content)
- [x] Rewrite `gift_add`, `gift_update_status`, `gift_list` as property-fact wrappers
- [x] Rewrite `loan_create`, `loan_settle`, `loan_list` as property-fact wrappers
- [x] Rewrite `reminder_create`, `reminder_list`, `reminder_dismiss` as property-fact wrappers
- [x] Rewrite `feed_get` as temporal fact wrapper
- [x] Rewrite life_event tools as temporal fact wrappers
- [x] Update all relationship butler tests
- [x] Write Phase 2 backfill script (bu-ddb.7)

## Phase 3: Finance Butler (bu-ddb.4)

- [x] Seed finance predicates into `predicate_registry` (new Alembic migration)
- [x] Rewrite `record_transaction`, `list_transactions` as temporal fact wrappers with dedup
- [x] Rewrite `track_subscription`, `upcoming_bills` as property-fact wrappers
- [x] Rewrite `track_bill` as property-fact wrapper
- [x] Implement `account` property fact for account tracking
- [x] Rewrite `spending_summary` as JSONB aggregation query on facts
- [x] Update all finance butler tests
- [x] Write Phase 3 backfill script (bu-ddb.7)

## Phase 4: Home Butler (bu-ddb.5)

- [x] Seed home predicates into `predicate_registry` (new Alembic migration)
- [x] Rewrite `ha_entity_snapshot` persistence as `ha_state` property fact with supersession
- [x] Implement HA device entity lazy-create/lookup pattern
- [x] Update all home butler tests
- [x] Write Phase 4 backfill script (bu-ddb.7)

## Cross-Cutting (bu-ddb.6)

- [x] Add GIN index on `facts.metadata`
- [x] Add partial B-tree indexes for meal, transaction, measurement aggregation predicates
- [x] Benchmark aggregation queries (nutrition_summary, spending_summary) against 10k facts
- [x] Write migration for all index additions (mem_008 or later)

## Coverage Review (bu-ddb.8)

- [x] Audit code changes from bu-ddb.2 through bu-ddb.5
- [x] Verify entity resolution contract across all migrations (no bare string subjects)
- [x] Produce coverage checklist mapping each table to its implementing bead
- [x] File gap beads for any uncovered tables
