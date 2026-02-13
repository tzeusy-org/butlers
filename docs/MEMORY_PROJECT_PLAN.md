# Butler Memory System — Project Plan

> Memory is implemented as a common module loaded by each relevant butler. Memory data lives in that butler's own database, and memory tools are exposed by that butler's MCP server.

---

## Design Principles

1. **Module, not infrastructure service.** Memory is a reusable module (`[modules.memory]`) instead of a dedicated standalone role.
2. **Per-butler data ownership.** Each butler stores memory rows in its own DB (`butler_<name>`), preserving existing isolation guarantees.
3. **Three memory types, three lifecycles.** Episodic, semantic, and procedural memory use distinct schemas and transitions.
4. **Forgetting is a feature.** Facts/rules decay by subjective confidence, and stale data fades naturally.
5. **Local-first retrieval.** Embeddings are generated in-process (MiniLM-L6); no network dependency for search.
6. **Fail-open runtime.** Memory unavailability never blocks primary user workflows.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     Butler Daemon (per butler)                  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Memory Module                                              │  │
│  │                                                            │  │
│  │  MCP tools: store/search/recall/context/feedback          │  │
│  │  Embedding engine: MiniLM-L6 (local)                      │  │
│  │  Workers: consolidate, decay_sweep, episode_cleanup       │  │
│  └───────────────────────────┬────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│                    PostgreSQL: butler_<name>                    │
│               memory tables + pgvector + full-text indexes      │
└──────────────────────────────────────────────────────────────────┘
```

Multiple butlers can enable the same module. Each instance operates independently against its host DB.

---

## The Three Memory Types

### Episodes (What Happened)
- High-volume, short-lived observations extracted from sessions.
- Stored automatically after session completion.
- TTL-managed (default 7 days).

### Facts (What Is True)
- Distilled subject/predicate/content knowledge.
- Lifecycle: `active -> fading -> expired` plus `superseded`/`retracted`.
- Supports supersession links for corrections.

### Rules (How To Behave)
- Procedural guidance learned from repeated outcomes.
- Maturity: `candidate -> established -> proven`.
- Harmful evidence can demote or invert into `anti_pattern`.

---

## Database Schema (Module-Owned Tables)

### Prerequisites

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
```

### Required tables
- `episodes`
- `facts`
- `rules`
- `memory_links`
- `memory_events`
- `rule_applications`
- `embedding_versions`

### Key schema constraints
- Active fact uniqueness: partial unique index on `(tenant_id, scope, subject, predicate)`.
- Lifecycle states are canonical and validated.
- Provenance fields (`tenant_id`, `source_butler`, `source_episode_id`, timestamps) are mandatory.

---

## Confidence Decay and Rule Quality

### Effective confidence

```text
effective_confidence = confidence * exp(-decay_rate * days_since_last_confirmed)
```

### Permanence classes
- `permanent` (`decay_rate=0.0`): identity/biographical constants.
- `stable` (~year half-life): long-term preferences.
- `standard` (~quarter half-life): ongoing interests/projects.
- `volatile` (~weeks): temporary states/plans.
- `ephemeral` (~days): transient observations.

### Thresholds
- `effective_confidence >= retrieval_threshold`: retrievable.
- `expiry_threshold <= effective_confidence < retrieval_threshold`: fading.
- `< expiry_threshold`: expired/retracted per type policy.

### Rule effectiveness

```text
effectiveness = success_count / (success_count + 4 * harmful_count + 0.01)
```

Harmful outcomes carry 4x penalty to bias toward safe behavior.

---

## Retrieval and Context

### Search modes
- `semantic`: vector similarity.
- `keyword`: full-text (`tsvector`).
- `hybrid`: reciprocal-rank fusion.

### Composite scoring

```text
score =
  0.4 * relevance +
  0.3 * importance +
  0.2 * recency +
  0.1 * effective_confidence
```

Weights are configurable in module config.

### Context assembly (`memory_context`)
- Deterministic ordering (`score DESC`, `created_at DESC`, `id ASC`).
- Section quotas for facts/rules/episodes.
- Deterministic tokenizer-based token budgeting.
- Structured output injected into system prompt.

---

## MCP Tool Surface (Per Butler)

When enabled, the module registers these tools on the hosting butler MCP server.

### Writing
- `memory_store_episode(content, butler, session_id?, importance?)`
- `memory_store_fact(subject, predicate, content, importance?, permanence?, scope?, tags?)`
- `memory_store_rule(content, scope?, tags?)`

### Reading
- `memory_search(query, types?, scope?, mode?, limit?, min_confidence?)`
- `memory_recall(topic, scope?, limit?)`
- `memory_get(type, id)`

### Feedback
- `memory_confirm(type, id)`
- `memory_mark_helpful(rule_id)`
- `memory_mark_harmful(rule_id, reason?)`

### Management
- `memory_forget(type, id)`
- `memory_stats(scope?)`

### Runtime context
- `memory_context(trigger_prompt, butler, token_budget?)`

---

## Integration with Butler Runtime

### Session -> Episode pipeline
1. Session completes.
2. Daemon emits `memory_store_episode(...)`.
3. Module embeds and stores episode with TTL.

### Spawner integration
1. Trigger received.
2. Daemon builds context via `memory_context(...)`.
3. Returned block is injected after `CLAUDE.md`.
4. Session runs with memory-aware context.

### Scheduled lifecycle jobs
Configured on the hosting butler:

```toml
[butler.schedule]
consolidate = { cron = "0 */6 * * *", prompt = "Run memory consolidation" }
decay_sweep = { cron = "0 3 * * *", prompt = "Run confidence decay sweep" }
episode_cleanup = { cron = "0 4 * * *", prompt = "Expire old episodes" }
```

---

## Module Configuration

Memory config is module-scoped in each butler's `butler.toml`.

```toml
[modules.memory]
enabled = true
embedding_model = "all-MiniLM-L6-v2"
embedding_dimensions = 384

[modules.memory.episodes]
default_ttl_days = 7
max_entries = 10000

[modules.memory.facts]
retrieval_confidence_threshold = 0.2
expiry_confidence_threshold = 0.05

[modules.memory.rules]
promote_to_established = { min_successes = 5, min_effectiveness = 0.6 }
promote_to_proven = { min_successes = 15, min_effectiveness = 0.8, min_age_days = 30 }
harmful_to_antipattern = { min_harmful = 3, max_effectiveness = 0.3 }

[modules.memory.retrieval]
default_limit = 20
default_mode = "hybrid"
context_token_budget = 3000
score_weights = { relevance = 0.4, importance = 0.3, recency = 0.2, confidence = 0.1 }
```

---

## Dashboard Integration

### Butler-scoped memory view (`/butlers/:name/memory`)
- Facts panel with confidence/permanence and provenance.
- Rules panel with maturity/effectiveness and harmful markers.
- Episode stream with consolidation state.

### Fleet memory view (`/memory`)
- Aggregated overview across butlers by API fanout.
- Unified search over facts/rules/episodes.
- Consolidation activity and health indicators.

All dashboard writes go through each target butler's MCP/API surface (no direct cross-butler write path).

---

## State Store vs Memory

| | State Store | Memory Module |
|---|---|---|
| Purpose | Structured operational state | Unstructured/semi-structured learned knowledge |
| Schema | Key-value JSONB | Episodes/facts/rules + embeddings |
| Lifecycle | Explicit mutation only | Decay, supersession, expiry |
| Ownership | Per-butler DB | Per-butler DB |
| Access | Butler tools | Butler memory module tools |

---

## Scalability

Per-butler expected profile remains small:
- Episodes: 5-50/day (rolling TTL window).
- Facts: 1-5/week net growth (supersession bounded).
- Rules: 1-5/month.

At this scale, a single Postgres primary per butler with pgvector indexes is sufficient.

---

## Migration Strategy

- Keep module migrations shared in one chain (for example `mem_*` revisions).
- Apply the same migration set in each butler DB where `[modules.memory].enabled = true`.
- Preserve chain naming/path conventions and unique revision IDs.

---

## Milestones

### M1: Module + schema foundation
- [ ] Add memory module scaffold under `src/butlers/modules/memory.py`.
- [ ] Add module-owned migrations (`episodes`, `facts`, `rules`, `memory_links`, `memory_events`).
- [ ] Register memory MCP tools on hosting butler servers.
- [ ] Integrate local embeddings and hybrid search.
- [ ] Add targeted tests for memory tools and schema invariants.

### M2: Lifecycle behavior
- [ ] Implement confidence decay and threshold transitions.
- [ ] Implement rule effectiveness/maturity progression.
- [ ] Implement anti-pattern inversion.
- [ ] Implement decay and cleanup scheduled tasks.

### M3: Consolidation engine
- [ ] Implement deterministic consolidation batching.
- [ ] Implement extraction -> persist pipeline with idempotency.
- [ ] Implement supersession, confirmations, and provenance links.
- [ ] Add integration tests for end-to-end consolidation.

### M4: Runtime integration
- [ ] Wire spawner to call `memory_context` before session start.
- [ ] Wire session completion to `memory_store_episode`.
- [ ] Add `[modules.memory]` config schema and validation.
- [ ] Enable module in target butlers.

### M5: Dashboard and ops
- [ ] Butler-scoped memory API endpoints and UI.
- [ ] Aggregated memory overview endpoint/UI.
- [ ] Activity feed and health indicators.
- [ ] Audit/event visibility and operator controls.

---

## Non-Goals

- Reintroducing a dedicated shared memory role as infrastructure.
- Direct SQL access across butler databases for memory reads/writes.
- Replacing role-specific operational state stores with memory tables.

---

## Design References

Patterns adapted from:
- Generative Agents (composite recall signals)
- MemoryBank (forgetting/decay intuition)
- A-Mem (link-rich evolving memories)
- CASS Memory System (rule maturity, harmful weighting, anti-patterns)
- Hybrid retrieval patterns (vector + lexical fusion)
