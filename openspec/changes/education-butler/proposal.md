## Why

Learning complex topics is hard not because information is scarce, but because retention is. Users can ask an LLM to explain anything, but without structured review, adaptive pacing, and visibility into what they actually know vs. think they know, knowledge decays within days. An education butler turns passive Q&A into an active learning system — one that maps knowledge domains, schedules spaced reviews, and adapts to the user's demonstrated understanding over time.

## What Changes

- **New butler: `education`** — a long-running MCP server daemon dedicated to teaching, quizzing, and tracking mastery of user-requested topics
- **Spaced repetition engine** — dynamically creates future scheduled prompts at staggered intervals (SM-2-inspired algorithm) to reinforce newly taught material, delivered via the user's preferred channel (Telegram, email, etc.)
- **Mind map state machine** — for every topic the user studies, the butler builds and maintains a hierarchical concept graph (mind map) with per-node mastery scores, review timestamps, and dependency edges. This is the butler's primary planning artifact for deciding what to teach next
- **Diagnostic assessment engine** — before teaching a topic, the butler runs a calibration sequence to infer the user's existing knowledge level. Generates targeted probe questions, interprets responses, and maps results onto the mind map as pre-existing mastery so teaching starts at the right depth
- **Curriculum planning** — generates dependency-ordered learning paths (syllabi) from mind map structure. Determines what to teach next based on prerequisite satisfaction, mastery gaps, and estimated effort per concept. The strategic layer between the mind map (territory) and teaching flows (execution)
- **Interactive teaching UX flows** — multi-session, multi-day workflows that take a user from "teach me X" through diagnostic assessment, curriculum planning, guided explanation, quiz loops, spaced review, and eventual mastery
- **Learning analytics** — synthesizes raw mastery data into actionable insights: retention rates by domain, learning velocity trends, optimal review timing, predicted syllabus completion, and periodic progress digests delivered via the user's preferred channel
- **High-tier model configuration** — runtime sessions use a top-tier general-knowledge model (Claude Opus 4.6, GPT 5.2-high, etc.) to ensure expert-level explanations and accurate domain knowledge
- **Mastery dashboard API** — butler-specific API routes exposing mind map state, mastery scores, review schedules, and quiz history for the web dashboard

## Capabilities

### New Capabilities

These capabilities form a directed pipeline — each stage feeds the next. The learning loop is:

```
  diagnostic-assessment
          │
          ▼
  curriculum-planning ──→ mind-map (DAG materialization)
          │                    │
          ▼                    ▼
   teaching-flows ◄──── spaced-repetition
          │                    │
          ▼                    ▼
   mastery-tracking ───→ learning-analytics
          │                    │
          └────────────────────┘
                   │
                   ▼
          (feedback into curriculum-planning:
           re-plan based on actual mastery)
```

1. **Diagnostic assessment** infers what the user already knows → seeds initial mastery
2. **Curriculum planning** takes those seeds + the topic's concept space and produces an ordered syllabus (the *plan*)
3. **Mind map** materializes the curriculum as a persistent DAG — nodes are concepts, edges are prerequisites, each node carries mastery state. The mind map is the curriculum made concrete and trackable
4. **Teaching flows** walk the user through the mind map's frontier nodes (concepts whose prerequisites are satisfied but not yet mastered)
5. **Spaced repetition** schedules future review prompts for nodes the user has been taught, feeding answers back into mastery scores
6. **Mastery tracking** records quiz results and updates per-node mastery on the mind map
7. **Learning analytics** aggregates mastery data into trends, retention curves, and progress forecasts — which feed back into curriculum re-planning (e.g., slow down on a struggling subtree, skip ahead on a strong one)

The loop is continuous: analytics trigger curriculum re-planning, which restructures the mind map frontier, which changes what teaching flows present next.

- `education-diagnostic-assessment`: Pre-teaching calibration system that probes the user's existing knowledge of a topic. Covers probe question generation strategies, response interpretation heuristics, confidence-level inference, and mapping diagnostic results onto mind map nodes as initial mastery seeds. **Upstream of:** curriculum-planning, mind-map
- `education-curriculum-planning`: Dependency-ordered syllabus generation from the topic's concept space, informed by diagnostic results. Covers prerequisite graph traversal, learning path optimization (shortest path to user's goal), effort estimation per concept node, adaptive re-planning when mastery scores deviate from predictions, and syllabus lifecycle (creation, progress, completion, abandonment). **Upstream of:** mind-map. **Downstream of:** diagnostic-assessment, learning-analytics (feedback loop)
- `education-mind-map`: Materializes the curriculum as a persistent DAG with per-node mastery state. Covers mind map schema design (nodes, edges, mastery scores, review timestamps), DAG operations (add/remove/reorder nodes, subtree queries), frontier computation (nodes whose prerequisites are met but not yet mastered), and mind map review/pruning lifecycle. **Upstream of:** teaching-flows, spaced-repetition. **Downstream of:** curriculum-planning, mastery-tracking (score updates)
- `education-teaching-flows`: End-to-end UX workflows that walk the mind map's frontier. Spans guided explanation, quiz delivery, answer evaluation, Socratic follow-ups, and multi-day review arcs. Orchestrates the other capabilities — calls diagnostic-assessment at onboarding, consults curriculum-planning for sequencing, reads mind-map for current state, triggers spaced-repetition after teaching. **Downstream of:** mind-map, curriculum-planning. **Upstream of:** mastery-tracking
- `education-spaced-repetition`: SM-2-inspired scheduling engine for review prompts. Covers interval calculation (ease factor, repetition count, inter-repetition intervals), dynamic schedule creation/update via core scheduler, delivery through notify channels, and performance-based interval adjustment. Operates on mind map nodes that have been taught at least once. **Downstream of:** mind-map, teaching-flows. **Upstream of:** mastery-tracking
- `education-mastery-tracking`: Persistent state for quiz results, per-node mastery scores on the mind map, learning velocity metrics, and struggle-area detection. Covers the database schema, state mutations, and mastery threshold logic that drives graduation (node marked mastered) and re-review decisions. **Downstream of:** teaching-flows, spaced-repetition. **Upstream of:** mind-map (score writeback), learning-analytics, curriculum-planning (re-plan trigger)
- `education-learning-analytics`: Higher-order analysis layer over mastery tracking data. Covers retention rate computation by domain/topic, learning velocity trends over time, optimal review time detection (time-of-day and day-of-week patterns), predicted syllabus completion dates, weekly/monthly progress digest generation, and cross-topic comparative performance. **Downstream of:** mastery-tracking. **Upstream of:** curriculum-planning (feedback loop)
- `education-butler-identity`: Butler roster configuration (butler.toml, MANIFESTO.md, CLAUDE.md, AGENTS.md), model tier selection, module enablement, and personality/system prompt design for an expert educator persona. Infrastructure capability — no data dependencies on the learning pipeline

### Modified Capabilities

- `core-scheduler`: Education butler requires runtime schedule creation with computed future fire times (not just static cron) — the scheduler spec already supports `schedule_create()` with cron expressions, but the education butler's spaced repetition engine will be a heavy consumer of dynamic one-shot and recurring schedules. No spec-level changes needed; this is a usage note.

## Impact

### Database
- New `education` PostgreSQL schema with tables for mind maps (JSONB graph structure), mastery scores, quiz sessions, review queue state, diagnostic results, curricula/syllabi, and analytics aggregates
- Alembic migration(s) in `src/butlers/migrations/versions/`
- Heavy use of core state store for ephemeral session state (active quiz context, in-progress explanations, diagnostic session state)

### Runtime
- High token consumption per session — expert-level explanations with Opus-tier models are expensive. Needs cost-aware scheduling (don't fire 50 review prompts simultaneously)
- Significant scheduler load — each active topic can generate 4-8 future review schedules. Need to consider schedule cleanup for abandoned topics

### Modules
- **Required:** memory (learning outcomes, struggle areas, prerequisite knowledge), telegram (primary interactive channel), contacts (identity resolution)
- **Optional:** email (for digest-style review summaries), calendar (block study time)

### API
- New dashboard routes at `roster/education/api/router.py` for mind map visualization, mastery dashboard, quiz history, and review schedule management

### Switchboard
- New routing rules for education-related triggers (e.g., "teach me", "quiz me", topic questions routed from other butlers)

### Skills
- Butler-specific skills for multi-step workflows: topic onboarding, diagnostic assessment, curriculum generation, quiz generation, mind map construction, review session orchestration, progress digest composition
