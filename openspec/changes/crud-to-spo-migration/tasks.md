# Tasks

## Phase 1: Health Butler (bu-ddb.2)

- [ ] Seed health predicates into `predicate_registry` (new Alembic migration)
- [ ] Rewrite `measurement_log`, `measurement_history`, `measurement_latest` as fact wrappers
- [ ] Rewrite `symptom_log`, `symptom_history`, `symptom_search` as fact wrappers
- [ ] Rewrite `medication_log_dose`, `medication_history` as fact wrappers
- [ ] Rewrite `medication_add`, `medication_list` as property-fact wrappers
- [ ] Rewrite `condition_add`, `condition_list`, `condition_update` as property-fact wrappers
- [ ] Rewrite `research_save`, `research_search` as property-fact wrappers
- [ ] Update `health_summary` and `trend_report` to query facts
- [ ] Update all health butler tests
- [ ] Write Phase 1 backfill script (bu-ddb.7)

## Phase 2: Relationship Butler (bu-ddb.3)

- [ ] Seed relationship predicates into `predicate_registry` (new Alembic migration)
- [ ] Rewrite `interaction_log`, `interaction_list` as temporal fact wrappers
- [ ] Rewrite `note_create`, `note_list`, `note_search` as temporal fact wrappers
- [ ] Migrate `quick_facts` (fact_set, fact_list) as property facts (key→predicate, value→content)
- [ ] Rewrite `gift_add`, `gift_update_status`, `gift_list` as property-fact wrappers
- [ ] Rewrite `loan_create`, `loan_settle`, `loan_list` as property-fact wrappers
- [ ] Rewrite `reminder_create`, `reminder_list`, `reminder_dismiss` as property-fact wrappers
- [ ] Rewrite `feed_get` as temporal fact wrapper
- [ ] Rewrite life_event tools as temporal fact wrappers
- [ ] Update all relationship butler tests
- [ ] Write Phase 2 backfill script (bu-ddb.7)

## Phase 3: Finance Butler (bu-ddb.4)

- [ ] Seed finance predicates into `predicate_registry` (new Alembic migration)
- [ ] Rewrite `record_transaction`, `list_transactions` as temporal fact wrappers with dedup
- [ ] Rewrite `track_subscription`, `upcoming_bills` as property-fact wrappers
- [ ] Rewrite `track_bill` as property-fact wrapper
- [ ] Implement `account` property fact for account tracking
- [ ] Rewrite `spending_summary` as JSONB aggregation query on facts
- [ ] Update all finance butler tests
- [ ] Write Phase 3 backfill script (bu-ddb.7)

## Phase 4: Home Butler (bu-ddb.5)

- [ ] Seed home predicates into `predicate_registry` (new Alembic migration)
- [ ] Rewrite `ha_entity_snapshot` persistence as `ha_state` property fact with supersession
- [ ] Implement HA device entity lazy-create/lookup pattern
- [ ] Update all home butler tests
- [ ] Write Phase 4 backfill script (bu-ddb.7)

## Cross-Cutting (bu-ddb.6)

- [ ] Add GIN index on `facts.metadata`
- [ ] Add partial B-tree indexes for meal, transaction, measurement aggregation predicates
- [ ] Benchmark aggregation queries (nutrition_summary, spending_summary) against 10k facts
- [ ] Write migration for all index additions (mem_008 or later)

## Coverage Review (bu-ddb.8)

- [ ] Audit code changes from bu-ddb.2 through bu-ddb.5
- [ ] Verify entity resolution contract across all migrations (no bare string subjects)
- [ ] Produce coverage checklist mapping each table to its implementing bead
- [ ] File gap beads for any uncovered tables
