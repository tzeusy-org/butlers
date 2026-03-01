## Context

The education butler is a new domain butler in the Butlers framework — a long-running MCP server daemon with a dedicated PostgreSQL schema (`education`), opt-in modules, and ephemeral LLM CLI sessions spawned via the core spawner. It follows the standard butler architecture: triggers arrive (routed user messages, scheduled tasks), the spawner generates an ephemeral MCP config, a high-tier LLM CLI instance runs with access to the butler's tools, and the session is logged.

What makes the education butler distinct is its **stateful learning loop**: diagnostic assessment → curriculum planning → mind map materialization → teaching → spaced repetition → mastery tracking → analytics → re-planning. This loop spans days to weeks per topic, across dozens of ephemeral sessions, requiring durable state that persists between sessions and drives scheduling decisions.

The butler operates within the standard shared-DB architecture (`butlers` database, `education` schema, search path `education, shared, public`) and communicates with users exclusively through the notify/channel system (Telegram as primary channel, email for digests). Inter-butler communication is MCP-only through the Switchboard.

## Goals / Non-Goals

**Goals:**

- Design a database schema that efficiently represents mind maps as DAGs with per-node mastery state, supporting frontier queries and subtree operations
- Define an SM-2-inspired spaced repetition algorithm that maps cleanly onto the core scheduler's `schedule_create()` API using cron expressions
- Specify the session-spanning state machine for teaching flows — how a multi-day learning arc maintains coherence across independent ephemeral LLM sessions
- Establish the diagnostic assessment protocol for inferring user knowledge level from a small number of probe questions
- Design the curriculum planning algorithm that converts a topic + diagnostic results into an ordered DAG
- Define the analytics aggregation strategy (materialized vs. computed-on-read)
- Configure the butler identity (port, model tier, modules, schedule)

**Non-Goals:**

- Video content, live tutoring, or real-time collaboration
- Multi-user classrooms or group learning
- Content authoring tools (the LLM generates all content at runtime)
- Certification or credentialing
- Integration with external LMS platforms (Anki, Coursera, etc.)
- Mobile app or custom UI — dashboard API only
- Content sourcing from external references (v2 — for now, rely on model knowledge)

## Decisions

### D1: Mind Map DAG Storage — Relational Edges Table + JSONB Node Metadata

**Decision:** Store mind maps as a relational edges table (`mind_map_edges`) plus a nodes table (`mind_map_nodes`) with JSONB metadata, rather than a single JSONB document.

**Rationale:** A single JSONB document storing the full graph works for small maps but breaks down when:
- Concurrent sessions update different nodes (CAS conflicts on the whole document)
- Frontier queries require scanning every node (no index support inside JSONB arrays)
- Subtree operations need recursive traversal (SQL recursive CTEs are natural on relational edges)

**Schema:**

```sql
-- A mind map is a named DAG for a topic
CREATE TABLE mind_maps (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT NOT NULL,                    -- "Python Fundamentals"
    root_node_id    UUID,                             -- FK to mind_map_nodes
    status          TEXT NOT NULL DEFAULT 'active',   -- active | completed | abandoned
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Each node is a concept within a mind map
CREATE TABLE mind_map_nodes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mind_map_id     UUID NOT NULL REFERENCES mind_maps(id) ON DELETE CASCADE,
    label           TEXT NOT NULL,                    -- "List Comprehensions"
    description     TEXT,                             -- brief concept summary
    depth           INTEGER NOT NULL DEFAULT 0,       -- distance from root (denormalized)
    mastery_score   FLOAT NOT NULL DEFAULT 0.0,       -- 0.0 to 1.0
    mastery_status  TEXT NOT NULL DEFAULT 'unseen',   -- unseen | diagnosed | learning | reviewing | mastered
    ease_factor     FLOAT NOT NULL DEFAULT 2.5,       -- SM-2 ease factor (per-node)
    repetitions     INTEGER NOT NULL DEFAULT 0,       -- SM-2 successful repetition count
    next_review_at  TIMESTAMPTZ,                      -- next spaced repetition due date
    last_reviewed_at TIMESTAMPTZ,
    effort_minutes  INTEGER,                          -- estimated learning effort
    metadata        JSONB NOT NULL DEFAULT '{}',      -- extensible (tags, notes, etc.)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Directed edges: parent → child means "parent is prerequisite of child"
CREATE TABLE mind_map_edges (
    parent_node_id  UUID NOT NULL REFERENCES mind_map_nodes(id) ON DELETE CASCADE,
    child_node_id   UUID NOT NULL REFERENCES mind_map_nodes(id) ON DELETE CASCADE,
    edge_type       TEXT NOT NULL DEFAULT 'prerequisite',  -- prerequisite | related
    PRIMARY KEY (parent_node_id, child_node_id)
);

CREATE INDEX idx_mmn_map_status ON mind_map_nodes (mind_map_id, mastery_status);
CREATE INDEX idx_mmn_next_review ON mind_map_nodes (next_review_at) WHERE next_review_at IS NOT NULL;
CREATE INDEX idx_mme_child ON mind_map_edges (child_node_id);
```

**Frontier query** (nodes whose prerequisites are all mastered but node itself is not):

```sql
WITH mastered AS (
    SELECT id FROM mind_map_nodes
    WHERE mind_map_id = $1 AND mastery_status = 'mastered'
)
SELECT n.* FROM mind_map_nodes n
WHERE n.mind_map_id = $1
  AND n.mastery_status IN ('unseen', 'diagnosed', 'learning')
  AND NOT EXISTS (
      SELECT 1 FROM mind_map_edges e
      WHERE e.child_node_id = n.id
        AND e.parent_node_id NOT IN (SELECT id FROM mastered)
  )
ORDER BY n.depth ASC, n.effort_minutes ASC NULLS LAST;
```

**Alternatives considered:**
- *Single JSONB document:* Simpler reads but CAS-conflict-prone, no SQL index support for frontier queries. Rejected.
- *Graph database (e.g., pgvector + adjacency):* Overkill for DAGs with <500 nodes per topic. Standard relational modeling is sufficient.

### D2: Spaced Repetition — SM-2 Variant Mapped to Core Scheduler

**Decision:** Implement SM-2 with per-node ease factors, using the core scheduler's `schedule_create()` to create one-shot review schedules.

**Algorithm:**

After a user completes a review for a mind map node:

```python
def sm2_update(node, quality: int) -> tuple[float, int, float]:
    """
    quality: 0-5 (0=complete blackout, 5=perfect recall)
    Returns: (new_ease_factor, new_repetitions, interval_days)
    """
    if quality >= 3:  # successful recall
        if node.repetitions == 0:
            interval = 1.0
        elif node.repetitions == 1:
            interval = 6.0
        else:
            interval = node.last_interval * node.ease_factor
        new_reps = node.repetitions + 1
    else:  # failed recall — reset
        interval = 1.0
        new_reps = 0

    new_ef = max(1.3, node.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
    return new_ef, new_reps, interval
```

**Mapping to core scheduler:** After computing the next interval, create a one-shot scheduled task:

```python
next_review = now + timedelta(days=interval)
cron = minute_hour_day_cron(next_review)  # e.g., "30 14 5 3 *"

schedule_create(
    name=f"review-{node.id}-rep{new_reps}",
    cron=cron,
    dispatch_mode="prompt",
    prompt=f"Spaced repetition review for node {node.id} in mind map {node.mind_map_id}. "
           f"Ask the user about: {node.label}. Repetition #{new_reps}, ease factor {new_ef:.2f}.",
    until_at=next_review + timedelta(hours=24)  # auto-disable if missed
)
```

The `until_at` ensures stale reviews auto-disable. The prompt includes enough context for the ephemeral session to reconstruct what to quiz on.

**Cost control:** Cap concurrent pending reviews at 20 per mind map. If a user has 20+ nodes due for review, batch them into a single "review session" schedule rather than 20 individual ones.

**Alternatives considered:**
- *Leitner system (box-based):* Simpler but less adaptive — SM-2's per-item ease factor better handles concepts of varying difficulty. Rejected.
- *FSRS (Free Spaced Repetition Scheduler):* More modern but complex to implement and harder to explain to users. Consider for v2.

### D3: Teaching Flow State Machine — State Store + Session Prompts

**Decision:** Maintain teaching flow state in the core state store (KV JSONB), with each ephemeral session receiving a structured prompt that includes the current flow state.

**Flow states:**

```
           ┌─────────────┐
           │   PENDING    │  User says "teach me X"
           └──────┬───────┘
                  ▼
           ┌─────────────┐
           │  DIAGNOSING  │  Probe questions, calibration
           └──────┬───────┘
                  ▼
           ┌─────────────┐
           │  PLANNING    │  Generate curriculum DAG
           └──────┬───────┘
                  ▼
           ┌─────────────┐
     ┌────▶│  TEACHING    │◀─── Pick next frontier node
     │     └──────┬───────┘
     │            ▼
     │     ┌─────────────┐
     │     │  QUIZZING    │  Test comprehension
     │     └──────┬───────┘
     │            ▼
     │     ┌─────────────┐
     │     │  REVIEWING   │  Spaced repetition sessions
     │     └──────┬───────┘
     │            │
     │    ┌───────┴────────┐
     │    ▼                ▼
     │  (frontier      (all nodes
     │   has more)      mastered)
     │    │                │
     └────┘         ┌──────▼──────┐
                    │  COMPLETED   │
                    └─────────────┘
```

**State store key pattern:** `flow:{mind_map_id}`

**State store value:**

```json
{
    "status": "teaching",
    "mind_map_id": "uuid",
    "current_node_id": "uuid",
    "current_phase": "explaining",
    "diagnostic_results": { "node_id": {"quality": 4, "inferred_mastery": 0.7} },
    "session_count": 12,
    "started_at": "2026-02-26T10:00:00Z",
    "last_session_at": "2026-02-28T14:30:00Z"
}
```

Each ephemeral session reads this state, performs one step (explain a concept, ask a quiz question, evaluate an answer), updates the state, and exits. The next trigger (user reply or scheduled review) spawns a new session that continues from the updated state.

**Why state store, not a DB table:** Flow state is ephemeral session coordination — it changes every session and is only read by the education butler itself. The state store's CAS semantics protect against concurrent session races. The relational tables (mind_map_nodes, quiz_responses) hold the durable learning data.

### D4: Diagnostic Assessment — Adaptive Probe Sequence

**Decision:** Use a 3-7 question adaptive probe sequence that starts at medium difficulty and narrows based on responses.

**Protocol:**

1. Butler generates a topic concept inventory (10-15 key concepts spanning beginner to expert)
2. First probe targets the median difficulty concept
3. If correct → probe a harder concept. If incorrect → probe an easier one.
4. Binary search converges in 3-4 questions. Add 1-3 targeted probes for ambiguous areas.
5. Map results onto mind map nodes: correct answers seed `mastery_status = 'diagnosed'` with `mastery_score` proportional to confidence.

**Question generation is LLM-driven** — the ephemeral session receives a skill prompt that instructs it to generate calibration questions for the topic, evaluate answers, and call `mind_map_node_update()` to seed mastery scores.

This is deliberately not algorithmic — the LLM's judgment on what constitutes a "medium difficulty" question for "distributed systems" is better than any hardcoded rubric. The skill prompt constrains the format (multiple choice or short answer, one question per message, explicit scoring criteria).

### D5: Curriculum Planning — Topological Sort with Effort Weighting

**Decision:** Curriculum generation is a two-phase process: (1) the LLM generates the concept graph (nodes + prerequisite edges), (2) a deterministic algorithm computes the learning order.

**Phase 1 — LLM concept decomposition:** A skill prompt instructs the session to decompose the topic into concepts with prerequisite relationships. Output is structured JSON that the session feeds into `mind_map_create()` and `mind_map_node_create()` tool calls.

**Phase 2 — Learning order:** Topological sort of the DAG, with ties broken by:
1. Depth (shallower first — breadth-first through the prerequisite graph)
2. Effort estimate (lower effort first within the same depth — quick wins build momentum)
3. Diagnostic mastery (partially-known concepts before unknown — reinforce rather than start cold)

This ordering is stored as a `sequence` integer on each node, updated whenever the curriculum is re-planned (e.g., after analytics feedback identifies a struggling subtree).

### D6: Learning Analytics — Materialized Aggregates via Scheduled Job

**Decision:** Compute analytics aggregates via a nightly scheduled job (`dispatch_mode = "job"`) that writes to an `analytics_snapshots` table, rather than computing on every read.

**Schema:**

```sql
CREATE TABLE analytics_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mind_map_id     UUID REFERENCES mind_maps(id) ON DELETE CASCADE,
    snapshot_date   DATE NOT NULL,
    metrics         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_analytics_map_date ON analytics_snapshots (mind_map_id, snapshot_date);
```

**Metrics JSONB structure:**

```json
{
    "total_nodes": 25,
    "mastered_nodes": 12,
    "mastery_pct": 0.48,
    "avg_ease_factor": 2.3,
    "retention_rate_7d": 0.82,
    "retention_rate_30d": 0.65,
    "velocity_nodes_per_week": 3.2,
    "estimated_completion_days": 14,
    "struggling_nodes": ["uuid1", "uuid2"],
    "strongest_subtree": "uuid3",
    "total_quiz_responses": 87,
    "avg_quality_score": 3.8,
    "sessions_this_period": 15,
    "time_of_day_distribution": {"morning": 8, "afternoon": 5, "evening": 2}
}
```

**Why materialized:** Analytics queries scan the full quiz_responses and mind_map_nodes tables. Running these on every dashboard load or digest generation is wasteful. A nightly job amortizes the cost. The dashboard API serves the latest snapshot; the weekly digest skill reads the last 7 snapshots to compute trends.

**Alternatives considered:**
- *PostgreSQL materialized views:* Less flexible for the JSONB metrics structure, and refresh still needs a trigger. A job gives us more control.
- *Compute on read with caching:* Adds caching complexity. The nightly snapshot is simpler and sufficient for daily-resolution analytics.

### D7: Butler Identity and Runtime Configuration

**Decision:** Use Claude Opus 4.6 as the default model, port 40107, with memory + telegram + contacts modules enabled.

**butler.toml outline:**

```toml
[butler]
name = "education"
port = 40107
description = "Personalized tutor with spaced repetition, mind maps, and adaptive learning"

[butler.runtime]
model = "claude-opus-4-6"
max_concurrent_sessions = 3

[runtime]
type = "claude-code"

[butler.db]
name = "butlers"
schema = "education"

[[butler.schedule]]
name = "nightly-analytics"
cron = "0 3 * * *"
dispatch_mode = "job"
job_name = "compute_analytics_snapshots"

[[butler.schedule]]
name = "weekly-progress-digest"
cron = "0 9 * * 0"
dispatch_mode = "prompt"
prompt = "Generate and send a weekly learning progress digest for the user. Read analytics snapshots for the past 7 days, identify trends, highlight achievements, flag struggling areas, and deliver via the user's preferred channel."

[modules.memory]
[modules.contacts]
```

**Model justification:** Teaching requires expert-level domain knowledge and nuanced pedagogical judgment (calibrating difficulty, generating good quiz questions, evaluating free-form answers). This is explicitly not a task for a smaller model. The trade-off is cost — mitigated by keeping sessions focused (one concept per session) and batching reviews.

**No telegram module in butler.toml** — outbound messaging goes through `notify()` → Messenger butler. The education butler doesn't need direct Telegram API access.

### D8: Quiz Response Storage — Relational Table for Analytics

**Decision:** Store individual quiz responses in a dedicated table (not the state store) for durable analytics queries.

```sql
CREATE TABLE quiz_responses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id         UUID NOT NULL REFERENCES mind_map_nodes(id) ON DELETE CASCADE,
    mind_map_id     UUID NOT NULL REFERENCES mind_maps(id) ON DELETE CASCADE,
    question_text   TEXT NOT NULL,
    user_answer     TEXT,
    quality         INTEGER NOT NULL CHECK (quality BETWEEN 0 AND 5),  -- SM-2 quality
    response_type   TEXT NOT NULL DEFAULT 'review',  -- diagnostic | teach | review
    session_id      UUID,                             -- FK to sessions table
    responded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_qr_node ON quiz_responses (node_id, responded_at DESC);
CREATE INDEX idx_qr_map_date ON quiz_responses (mind_map_id, responded_at DESC);
```

The `quality` field (0-5) feeds directly into the SM-2 algorithm. The `response_type` distinguishes diagnostic probes from teaching quizzes from spaced repetition reviews — important for analytics (retention rate should only count review-type responses).

## Risks / Trade-offs

**[High token cost per session] → Mitigation:** Use focused, single-concept sessions. The skill prompts should instruct the LLM to explain one concept, ask 1-3 questions, and exit — not open-ended tutoring. Budget ~2K output tokens per teaching session, ~500 per review session. The nightly analytics job runs as a native Python job (no LLM), and review batching caps concurrent scheduled prompts.

**[Mind map quality depends on LLM concept decomposition] → Mitigation:** The curriculum planning skill prompt should include explicit structure requirements (max depth 5, max 30 nodes for a single topic, prerequisite edges must form a DAG). Validate DAG acyclicity in the `mind_map_edge_create()` tool before persisting. If the LLM generates a cycle, reject and re-prompt.

**[State store flow state can become stale] → Mitigation:** Flow state includes `last_session_at`. If >30 days since last session, the teaching flow auto-transitions to `abandoned`. A scheduled task checks for stale flows weekly and cleans up associated pending review schedules.

**[Schedule proliferation from spaced repetition] → Mitigation:** Cap at 20 pending review schedules per mind map. Batch overflows into a single "review session" schedule. Clean up schedules when a mind map is marked `completed` or `abandoned`.

**[Diagnostic assessment accuracy] → Mitigation:** Diagnostic results seed mastery at conservative scores (0.3-0.7, never 1.0). The teaching flow will naturally correct miscalibrations — if a "diagnosed" node's first quiz reveals low understanding, it drops to `learning` status. The system is self-correcting.

**[Cross-session coherence for multi-day flows] → Mitigation:** Each session prompt includes a structured context block assembled from: (1) flow state from KV store, (2) current mind map frontier from DB query, (3) recent quiz responses from DB query, (4) memory context from memory module. This gives the ephemeral session enough context to continue naturally without relying on prior session transcripts.

## Open Questions

1. **Topic scope boundaries:** When a user says "teach me machine learning," how deep does the butler go? Should there be a configurable max node count or max depth, or should the LLM decide based on the user's stated goal?

2. **Multi-topic interleaving:** Can a user study multiple topics concurrently? The schema supports it (each topic is a separate mind map), but the review scheduling and analytics need to handle cross-topic prioritization.

3. **Review fatigue detection:** How many review prompts per day before the user gets annoyed? Should there be a daily cap, and should it be configurable or adaptive?

4. **Dashboard mind map visualization:** The API will expose the DAG structure, but the frontend rendering approach (force-directed graph? tree layout? custom React component?) is a frontend decision outside this spec.
