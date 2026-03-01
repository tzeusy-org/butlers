## 1. Butler Identity & Roster Scaffolding

- [ ] 1.1 Create `roster/education/` directory with required files: `butler.toml`, `MANIFESTO.md`, `CLAUDE.md`, `AGENTS.md`
- [ ] 1.2 Write `butler.toml` — port 40107, `claude-opus-4-6` model, `claude-code` runtime, `butlers` DB with `education` schema, `memory` + `contacts` modules, scheduled tasks (nightly-analytics, weekly-progress-digest, weekly-stale-flow-check)
- [ ] 1.3 Write `MANIFESTO.md` — value proposition (personalized spaced repetition learning), scope boundaries, educator persona
- [ ] 1.4 Write `CLAUDE.md` — educator persona, Interactive Response Mode (React/Affirm/Follow-up/Answer/React+Reply), Memory Classification taxonomy (subjects: topic/concept/user; predicates: learning_outcome/struggle_area/prerequisite_mastered/learning_preference/study_pattern), tool listing, teaching behavior guidelines
- [ ] 1.5 Initialize `AGENTS.md` with `# Notes to self` header
- [ ] 1.6 Create `roster/education/skills/` with symlinks to `butler-memory` and `butler-notifications` shared skills
- [ ] 1.7 Update `roster/switchboard/CLAUDE.md` with routing rules for education butler (keywords: teach, learn, study, quiz, review, explain)

## 2. Database Schema & Migrations

- [ ] 2.1 Create `roster/education/migrations/__init__.py` (empty)
- [ ] 2.2 Write migration `001_education_tables.py` — `mind_maps` table (id, title, root_node_id, status, created_at, updated_at)
- [ ] 2.3 Add `mind_map_nodes` table to migration — (id, mind_map_id FK, label, description, depth, mastery_score, mastery_status, ease_factor, repetitions, sequence, next_review_at, last_reviewed_at, effort_minutes, metadata JSONB, created_at, updated_at)
- [ ] 2.4 Add `mind_map_edges` table to migration — (parent_node_id FK, child_node_id FK, edge_type, PK on parent+child) with child index
- [ ] 2.5 Add `quiz_responses` table to migration — (id, node_id FK, mind_map_id FK, question_text, user_answer, quality CHECK 0-5, response_type, session_id, responded_at) with indexes on (node_id, responded_at) and (mind_map_id, responded_at)
- [ ] 2.6 Add `analytics_snapshots` table to migration — (id, mind_map_id FK, snapshot_date, metrics JSONB, created_at) with unique index on (mind_map_id, snapshot_date)
- [ ] 2.7 Set `branch_labels = ("education",)`, `revision = "education_001"`, verify Alembic chain resolution

## 3. Mind Map Tools (education-mind-map)

- [ ] 3.1 Create `roster/education/tools/` package with `__init__.py`
- [ ] 3.2 Implement `_helpers.py` — shared utilities (`_row_to_dict`, DAG cycle detection via recursive CTE)
- [ ] 3.3 Implement `mind_maps.py` — `mind_map_create(pool, title)`, `mind_map_get(pool, mind_map_id)`, `mind_map_list(pool, status?)`, `mind_map_update_status(pool, mind_map_id, status)`
- [ ] 3.4 Implement `mind_map_nodes.py` — `mind_map_node_create(pool, mind_map_id, label, ...)`, `mind_map_node_get(pool, node_id)`, `mind_map_node_update(pool, node_id, **fields)`, `mind_map_node_list(pool, mind_map_id, mastery_status?)`
- [ ] 3.5 Implement `mind_map_edges.py` — `mind_map_edge_create(pool, parent, child, edge_type)` with DAG acyclicity validation, `mind_map_edge_delete(pool, parent, child)`, depth recomputation on edge changes
- [ ] 3.6 Implement `mind_map_frontier(pool, mind_map_id)` — frontier query (nodes with all prerequisite parents mastered, ordered by depth then effort)
- [ ] 3.7 Implement `mind_map_subtree(pool, node_id)` — recursive CTE for all descendants
- [ ] 3.8 Implement mind map lifecycle hooks — auto-complete when all nodes mastered (in `mind_map_node_update`), staleness check for abandonment

## 4. Mastery Tracking Tools (education-mastery-tracking)

- [ ] 4.1 Implement `mastery.py` — `mastery_record_response(pool, node_id, mind_map_id, question_text, user_answer, quality, response_type, session_id?)` — inserts quiz_response, computes weighted mastery score, updates node mastery_score and mastery_status per state machine
- [ ] 4.2 Implement mastery score computation — weighted average of last 5 qualities with exponential recency weighting, clamped to [0.0, 1.0]
- [ ] 4.3 Implement mastery status state machine — valid transitions: unseen→diagnosed, unseen→learning, diagnosed→learning, learning→reviewing (quality>=3), reviewing→mastered (score>=0.85 AND last 3 reviews quality>=4), reviewing→learning (quality<3)
- [ ] 4.4 Implement `mastery_get_node_history(pool, node_id, limit?)` — quiz responses for a node, most recent first
- [ ] 4.5 Implement `mastery_get_map_summary(pool, mind_map_id)` — aggregate stats (total nodes, mastered count, struggling nodes, avg mastery)
- [ ] 4.6 Implement `mastery_detect_struggles(pool, mind_map_id)` — nodes with 3+ consecutive quality<=2 or declining mastery score

## 5. Spaced Repetition Engine (education-spaced-repetition)

- [ ] 5.1 Implement `spaced_repetition.py` — SM-2 algorithm: `sm2_update(node, quality)` returning (new_ease_factor, new_repetitions, interval_days)
- [ ] 5.2 Implement `spaced_repetition_record_response(pool, node_id, quality)` — runs SM-2, updates node (ease_factor, repetitions, next_review_at, last_reviewed_at), creates one-shot schedule via `schedule_create()`
- [ ] 5.3 Implement one-shot cron computation — convert target datetime to 5-field cron (`minute hour day month *`), set `until_at` to target + 24h
- [ ] 5.4 Implement schedule naming — `review-{node_id}-rep{N}` pattern, cleanup of prior schedule for same node before creating new one
- [ ] 5.5 Implement batch review cap — `spaced_repetition_pending_reviews(pool, mind_map_id)` returns nodes with next_review_at <= now; if >20 pending, create single `review-{mind_map_id}-batch` schedule instead
- [ ] 5.6 Implement `spaced_repetition_schedule_cleanup(pool, mind_map_id)` — remove all pending review schedules for completed/abandoned mind maps

## 6. Diagnostic Assessment Tools (education-diagnostic-assessment)

- [ ] 6.1 Implement `diagnostic.py` — `diagnostic_start(pool, mind_map_id)` — initializes flow state to DIAGNOSING, returns concept inventory (10-15 concepts spanning beginner to expert)
- [ ] 6.2 Implement `diagnostic_record_probe(pool, mind_map_id, node_id, quality, inferred_mastery)` — records probe result in flow state and quiz_responses (response_type='diagnostic'), updates node mastery_score (conservative 0.3-0.7, never 1.0) and mastery_status to 'diagnosed'
- [ ] 6.3 Implement `diagnostic_complete(pool, mind_map_id)` — finalizes diagnostic, returns summary of inferred mastery levels, transitions flow state to PLANNING
- [ ] 6.4 Implement adaptive probe sequencing logic in diagnostic assessment skill prompt — binary search starting at median difficulty, converging in 3-7 questions

## 7. Curriculum Planning Tools (education-curriculum-planning)

- [ ] 7.1 Implement `curriculum.py` — `curriculum_generate(pool, mind_map_id, topic, goal?, diagnostic_results?)` — orchestrates LLM concept decomposition → node/edge creation → topological sort → sequence numbering
- [ ] 7.2 Implement topological sort with tie-breaking — (1) depth, (2) effort_minutes, (3) diagnostic mastery — writes `sequence` integer to each node
- [ ] 7.3 Implement `curriculum_replan(pool, mind_map_id, reason?)` — re-computes sequence based on current mastery state, optionally adds/removes nodes via LLM
- [ ] 7.4 Implement `curriculum_next_node(pool, mind_map_id)` — returns highest-priority frontier node (lowest sequence among frontier nodes)
- [ ] 7.5 Implement structural constraint validation — max depth 5, max 30 nodes per topic (enforced in curriculum_generate, not in node_create)

## 8. Teaching Flow Orchestration (education-teaching-flows)

- [ ] 8.1 Implement `teaching_flows.py` — `teaching_flow_start(pool, topic, goal?)` — creates mind map, initializes flow state at PENDING in KV store (key: `flow:{mind_map_id}`), immediately transitions to DIAGNOSING
- [ ] 8.2 Implement `teaching_flow_get(pool, mind_map_id)` — reads flow state from KV store
- [ ] 8.3 Implement `teaching_flow_advance(pool, mind_map_id)` — state machine transitions: DIAGNOSING→PLANNING (on diagnostic complete), PLANNING→TEACHING (on curriculum generated), TEACHING→QUIZZING (after explanation), QUIZZING→REVIEWING (on quiz complete with spaced rep scheduled), REVIEWING→TEACHING (frontier has more), REVIEWING→COMPLETED (all mastered)
- [ ] 8.4 Implement `teaching_flow_abandon(pool, mind_map_id)` — marks flow abandoned, cleans up pending review schedules, updates mind map status
- [ ] 8.5 Implement `teaching_flow_list(pool, status?)` — lists flows with optional status filter
- [ ] 8.6 Implement session context assembly — prompt builder that reads flow state + frontier + recent 10 quiz responses + memory context for injection into ephemeral session prompts
- [ ] 8.7 Implement staleness detection — scheduled weekly check, auto-abandon flows with last_session_at > 30 days

## 9. Learning Analytics (education-learning-analytics)

- [ ] 9.1 Implement `analytics.py` — `analytics_compute_snapshot(pool, mind_map_id, snapshot_date)` — computes all metrics and upserts into analytics_snapshots
- [ ] 9.2 Implement retention rate computation — 7d and 30d windows, only response_type='review', % with quality>=3
- [ ] 9.3 Implement learning velocity — nodes transitioned to 'mastered' per week, averaged over last 4 weeks
- [ ] 9.4 Implement estimated completion — (unmastered nodes) / velocity, null when velocity is 0 or all mastered
- [ ] 9.5 Implement time-of-day distribution bucketing — morning (6-12), afternoon (12-18), evening (18-6) from quiz_responses.responded_at
- [ ] 9.6 Implement `analytics_compute_all(pool)` — compute snapshots for all active mind maps (called by nightly-analytics scheduled job)
- [ ] 9.7 Implement `analytics_get_snapshot(pool, mind_map_id, date?)` — latest or specific date snapshot
- [ ] 9.8 Implement `analytics_get_trend(pool, mind_map_id, days=30)` — time-series of snapshots ascending
- [ ] 9.9 Implement `analytics_get_cross_topic(pool)` — comparative stats across all active mind maps
- [ ] 9.10 Implement feedback loop trigger — when struggling_nodes >= 3 or retention_rate_7d < 0.60, signal curriculum_replan()

## 10. Skills

- [ ] 10.1 Write `roster/education/skills/diagnostic-assessment/SKILL.md` — adaptive probe protocol, question format constraints (MC or short answer, one per message), scoring criteria, binary search on difficulty
- [ ] 10.2 Write `roster/education/skills/curriculum-planning/SKILL.md` — topic decomposition instructions for LLM, structured JSON output format, constraint reminders (max depth 5, max 30 nodes, DAG), re-planning triggers
- [ ] 10.3 Write `roster/education/skills/teaching-session/SKILL.md` — single-concept explanation flow, Socratic questioning, quiz generation (1-3 questions), answer evaluation, quality scoring rubric
- [ ] 10.4 Write `roster/education/skills/review-session/SKILL.md` — spaced repetition review protocol, recall question format, quality scoring, batch review handling (up to 20 nodes)
- [ ] 10.5 Write `roster/education/skills/progress-digest/SKILL.md` — weekly digest composition from analytics snapshots, trend identification, achievement highlighting, struggle area flagging, notify() delivery

## 11. Dashboard API

- [ ] 11.1 Create `roster/education/api/models.py` — Pydantic models: MindMapResponse, MindMapNodeResponse, QuizResponseModel, AnalyticsSnapshotResponse, TeachingFlowResponse, MasterySummaryResponse
- [ ] 11.2 Create `roster/education/api/router.py` — APIRouter with prefix `/api/education`, `_get_db_manager` stub, `_pool` helper
- [ ] 11.3 Implement `GET /api/education/mind-maps` — list mind maps with pagination, optional status filter
- [ ] 11.4 Implement `GET /api/education/mind-maps/{id}` — full mind map with all nodes and edges (DAG structure)
- [ ] 11.5 Implement `GET /api/education/mind-maps/{id}/frontier` — current frontier nodes
- [ ] 11.6 Implement `GET /api/education/mind-maps/{id}/analytics` — latest analytics snapshot + optional trend (days param)
- [ ] 11.7 Implement `GET /api/education/quiz-responses` — paginated quiz history with optional node_id and mind_map_id filters
- [ ] 11.8 Implement `GET /api/education/flows` — list teaching flows with optional status filter
- [ ] 11.9 Implement `GET /api/education/analytics/cross-topic` — cross-topic comparison dashboard

## 12. Tests

- [ ] 12.1 Create `roster/education/tests/test_mind_maps.py` — mind map CRUD, node CRUD, edge creation with acyclicity validation, frontier query, subtree query, lifecycle transitions
- [ ] 12.2 Create `roster/education/tests/test_mastery.py` — quiz response recording, mastery score computation, status state machine, struggle detection, graduation threshold
- [ ] 12.3 Create `roster/education/tests/test_spaced_repetition.py` — SM-2 interval calculation, ease factor bounds, failed recall reset, schedule creation, batch cap, schedule cleanup
- [ ] 12.4 Create `roster/education/tests/test_diagnostic.py` — diagnostic start/record/complete, mastery seeding constraints (0.3-0.7), flow state transitions
- [ ] 12.5 Create `roster/education/tests/test_curriculum.py` — topological sort with tie-breaking, sequence numbering, structural constraints, re-planning, next-node selection
- [ ] 12.6 Create `roster/education/tests/test_teaching_flows.py` — flow state machine transitions, session context assembly, staleness detection, abandon cleanup
- [ ] 12.7 Create `roster/education/tests/test_analytics.py` — snapshot computation, retention rates, velocity, time-of-day bucketing, trend retrieval, feedback loop trigger
- [ ] 12.8 Create `roster/education/tests/test_api.py` — all dashboard API endpoints, pagination, filters, error cases
