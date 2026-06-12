# memory redesign — integration brief

**Date:** 2026-06-12
**Version:** v1
**Bundle path:** `pr/overview/memory-redesign/`
**Mode:** fresh
**Phase D verdict:** proceed-with-amendments
**Prior brief (if any):** none

---

## 0. Design intent

> Captured from `pr/overview/memory-redesign/VISION.md` (authored and user-approved 2026-06-12). **This section is binding — every spec section, every component decision, every backend contract must trace back to it.** Phase D treats violations of intent as automatic red regardless of cost math.

### Mission

`/memory` is where the owner audits what the house believes. Every belief carries its confidence, its age, and its provenance; the page makes the act of remembering — raw observation becoming durable knowledge — legible. **Show the believing, not just the beliefs.**

### Problem being solved

Today's `/memory` is functionally complete and structurally mute: seven equal-weight card sections. Five pains: (1) the lifecycle is invisible — consolidation status, last-run time, dead-letter count surface nowhere; (2) belief renders dishonestly flat — confidence is a static progress bar, decay has no visual presence; (3) one table shape three times, against doctrine; provenance (`fact —derived_from→ episode`) exists in the data model and nowhere in the UI; (4) two duplicated search affordances; (5) housekeeping renders at the same hierarchy as knowledge.

### Primary audience

**Owner** — single principal. Reads the page as the sysadmin of their own life: to trust that consolidation is keeping up, to audit why the system believes something, and to correct the record. Agents use MCP, never this page. Household members, external users, multi-tenant projections explicitly out of scope.

### Deliberate design moves

1. **The pipeline is the page's spine** — header band answers "is remembering working" first; dead letters earn red only when non-zero. *Why:* observability-first doctrine; a stalled pipeline silently corrupts everything downstream.
2. **Three registers, three shapes** — ledger (facts), standing orders (rules), daybook (episodes). Metaphor governs form, never nouns. *Why:* doctrine forbids the unified table; the house-ledger grammar gives each kind its correct rhythm.
3. **Confidence is ink** — effective (decayed) confidence as mono numeral; fading facts dim to `--dim`; confirmation re-inks. *Why:* type is the system; typographic decay is honest and calm at scale.
4. **Provenance is one click** — `derived_from` chains; detail pages cross-reference episode ↔ fact ↔ rule; entity anchors link out to `/entities`. *Why:* belief without provenance is not auditable.
5. **One search** — single band backed by the inspect endpoint; registers are the results surface. *Why:* one affordance per signal.
6. **The attention rail owns all state color**; register rows stay neutral. *Why:* Dispatch §1b; entity-redesign precedent.
7. **Housekeeping demoted, not hidden** — retention/compaction/re-embed in a quiet bottom band. *Why:* maintenance must not outshout knowledge.

### What we are deliberately NOT doing

- No storage or schema migration (additive read-side deltas only; see §3).
- No memory authoring from the dashboard; only confirm/retract lifecycle attestations on the fact page, gated on backend endpoints — absent endpoints means absent affordance, never a dead button.
- No entity-graph visualization on `/memory` (lives at `/entities`).
- No charts for charts' sake: no embedding scatterplots, knowledge-graph hairballs, health scores, decay sparklines on rows.
- No metaphor nouns as labels — UI vocabulary stays Episodes / Facts / Rules.
- No chat-with-your-memory interface.
- No timeline playback (Chronicler's domain, RFC 0014).
- No status-as-a-word badges; state renders as {dot, glyph, numeral, dimming}.

### Success criteria

- Consolidation health readable **from the header band alone, without scrolling** (pending, last write-up, dead letters).
- "Why do you believe this?" answerable in **one click** from any fact row.
- Healthy day renders **zero red/amber pixels**.
- Fading facts visibly dimmed; superseded/expired only behind explicit filter.
- Exactly **one search affordance**, kind-scoped, results in register shapes.
- Three detail pages share one editorial skeleton (heading + state, KV band, provenance, cross-references).
- Retention policy editing survives intact; housekeeping subordinate but reachable.
- Page passes the Dispatch 10-point extension checklist.

---

## 1. Scope

This redesign touches `/memory` and its three detail routes (`/memory/facts/:id`, `/memory/rules/:id`, `/memory/episodes/:id`) in the Butlers dashboard frontend, replacing the card-grid implementation with the Dispatch design language extended by the bundle's `MEMORY_LANGUAGE.md` (house-ledger grammar). Integration target: the live Vite/React frontend under `frontend/src`, which already ships all Dispatch primitives and tokens. `ButlerMemoryTab` on butler detail pages is **out of scope** (see Open question 12).

### Sub-pages

| Route | Source file(s) | Purpose (one sentence) | Sticky-nav parent? |
|---|---|---|---|
| `/memory` | prompts/00-foundation.md, 01-overture, 02-register-facts, 03-register-rules, 04-register-episodes, 05-search-and-rail, 07-housekeeping | The main memory dashboard with overture header, pipeline band, three register types, attention rail, and housekeeping band. | N/A (main page) |
| `/memory/facts/:id` | prompts/06-detail-pages.md | Editorial detail page showing a single fact with decay arithmetic, provenance, and confirm/retract actions. | `/memory` |
| `/memory/rules/:id` | prompts/06-detail-pages.md | Editorial detail page showing a single rule with outcome record and provenance chain. | `/memory` |
| `/memory/episodes/:id` | prompts/06-detail-pages.md | Editorial detail page showing a single episode with full content, session link, and consolidation state. | `/memory` |

In-page sub-surfaces of `/memory`: overture band (eyebrow / display / Voice / KPI strip), pipeline band, register area (search band + kind pills + ledger / standing orders / daybook), attention rail + recent activity, housekeeping band (retention policies, compaction log, embeddings).

### Design tokens (binding)

Binding sources: `pr/overview/memory-redesign/DESIGN_LANGUAGE.md` (canonical Dispatch) + `MEMORY_LANGUAGE.md` (memory extension). Key extracts:

- **Color**: Dispatch surface tokens (`--bg`, `--bg-elev`, `--bg-deep`, `--fg`, `--mfg`, `--dim`, `--border`, `--border-soft`, `--border-strong`); state colors `--red`/`--amber`/`--green` only when state demands. Memory discipline (MEMORY_LANGUAGE §6): healthy page renders zero red/amber/green; `--red` in exactly three places (pipeline dead-letter numeral > 0, rail, anti-pattern sliver + harmful tally); `--amber` rail-only; `--green` never on this page; butler hues only on ButlerMark (daybook gutter, activity rows).
- **Typography**: Inter Tight (UI), Source Serif 4 (Voice/empty states), JetBrains Mono (numerals/eyebrows/ids); display 44px/500/-0.025em; all numerals `tabular-nums`. Belief typography table (MEMORY_LANGUAGE §4) is mandatory: confidence = mono numeral (never bar/donut/%), decay = dim to `--dim` (never color/strikethrough), permanence = two-letter mono tag, consolidation = glyph {◦ • ✕}, maturity = lowercase mono word, importance = ink weight.
- **Spacing**: 1280px max column, 48px/56px page padding, Band-3 grid `1.4fr 1fr` gap 56px, 4px multiples, hairline-separated rows (8–18px vertical padding by importance).
- **Motion**: Dispatch baseline + two additions (register cross-fade 200ms `cubic-bezier(0.22,1,0.36,1)`, daybook row expand 120ms height linear). No skeleton-pulse, no count-up.
- **Hard do-nots**: full Dispatch anti-pattern list + MEMORY_LANGUAGE §9 (no confidence gauges, no health score, no graph hairballs, no row sparklines, no 🧠 iconography, no metaphor-noun labels, no unfiltered superseded/expired rows, no second search box, no consolidation celebration, no equal-weight housekeeping).

---

## 2. Component impact

### Classification table (Phase B)

| Component | Verdict | Reuse target | Churn | Notes |
|---|---|---|---|---|
| Pipeline band | new | composes `Mono` | S | single mono line, lifecycle numerals + `─→` connectors, red dead-letter numeral only when > 0 |
| KPI strip (4 cells) | new | `Mono`, page-shell KPI grid | S | eyebrow + 32px mega-number, hairline-divided, no fills |
| Ledger row (facts) | new (adapts list primitives) | Dispatch grid-row pattern; existing `Fact` type | M | subject·predicate / content / belief column; fading rows dim whole row to `--dim`; ↳ glyph when `source_episode_id` |
| Standing-orders row (rules) | new | Dispatch list primitives; existing `Rule` type | M | §NN gutter, 2-line clamp, tally line (red `harmful` fragment), maturity mono word, anti-pattern left red sliver |
| Day-group header | new | hairline + mono eyebrow typography | S | TODAY / YESTERDAY / dated |
| Daybook row (episodes) | new | `ButlerMark`, `Mono` | M | 50px time gutter (importance ≥ 8 → `--fg`), 2-line clamp expandable, glyph ◦/•/✕ |
| Unified search input | new | existing Input + `Pill` | S | `/` focuses, Enter submits q+kind URL params; results reuse register row components under mono kind-headers |
| Kind / validity / maturity / status filter pills | reuse | `Pill.tsx` | S | single-select, URL params, inverted active state |
| Attention rail | new | Dispatch attention-list pattern | L | 5 condition rows; empty → serif-italic "Nothing waiting."; all page state color lives here |
| Recent activity list | adapt | `MemoryActivityTimeline` structure, de-carded | S | mono time · ButlerMark · sans summary, 20 rows |
| Retention policies grid | adapt (keep) | existing `RetentionPoliciesSection` | S | restyled rule-grid, kind constrained to valid set, single dirty-state `Save` commit pill |
| Compaction log list | adapt (keep) | existing `CompactionLogSection` | S | quiet list, bytes omitted when null |
| Embeddings surface | adapt | existing `ReembedPanel` | M | inline dry-run result line, pill-morph confirm, mono status line |
| Detail-page editorial skeleton | adapt | existing detail pages (card → band) | M | eyebrow / content-as-heading / state line / KV band / kind section / provenance / commit footer |
| Fact decay-arithmetic line | new | `Mono` | S | `confidence 0.94 · decays 0.002/day · last confirmed 12d ago · effective 0.92` |
| Pagination footer | adapt | `Pill` + `Mono` | S | `1–50 of N` + prev/next pills, offset-based |
| Commit / secondary pills + 5s pill-morph confirm | new | Dispatch pill spec | S | Confirm (commit) / Retract (secondary), gated on backend deltas |

Removed: `MemoryTierCards` (card grid), `MemoryBrowser` tab/table/badge chrome on `/memory`, standalone `InspectSection`, per-tab searches. (See Open question 12 on `MemoryBrowser`'s use by `ButlerMemoryTab`.)

### Stack delta (Phase B)

**No blockers.** No new npm dependencies; Dispatch tokens and all three font families already in `frontend/src/index.css` (lines 60–133, 245–247); primitives `Voice`/`Mono`/`Eyebrow`/`Pill`/`ButlerMark`/page shell already in `frontend/src/components/ui/`; React Query + `useSearchParams` patterns already used by other redesigned pages (copy `IngestionPage.tsx`). Routing unchanged. Churn summary:

| Change | Effort |
|---|---|
| Replace MemoryTierCards with pipeline band + KPI strip | M |
| Replace MemoryBrowser tabs + InspectSection with unified search + three registers | L |
| Demote housekeeping to quiet footer band | S |
| Restyle detail pages (card → band, badges → glyphs/numerals, editorial skeleton) | M |
| Add attention rail + recent activity column | L |
| Wire URL params (register/q/kind/validity/offset) | S |

Test churn: `MemoryBrowser` tests rewrite (L); detail-page tests adapt (M); retention/compaction/reembed tests survive (S).

---

## 3. Backend contract delta

### Affordance inventory (Phase C)

| Affordance | Sub-page(s) | Data needed | Source |
|---|---|---|---|
| Pipeline band dead-letter numeral | `/memory` | `dead_letter_episodes` | `/stats` extend |
| Voice sentence | `/memory` | `unconsolidated_episodes`, `last_consolidation_at`, `last_consolidation_facts_produced` | `/stats` extend |
| KPI LAST WRITE-UP cell | `/memory` | `last_consolidation_at` + facts-produced count | `/stats` extend |
| Rail: dead letters / write-up overdue | `/memory` | same stats fields | `/stats` extend |
| Rail: important facts fading | `/memory` | count of `validity=fading AND importance>=8` | `/facts` importance filter |
| Fact arithmetic line | fact detail | `confidence`, `decay_rate`, `last_confirmed_at` | exists |
| Fact supersedes / superseded-by links | fact detail | forward `supersedes_id` exists; reverse lookup missing | `/facts/:id` extend |
| Confirm / Retract footer | fact detail | mutation endpoints | new |
| Episode derived-facts list | episode detail | facts where `source_episode_id = :id` | `/facts` filter |
| Daybook status filter | `/memory` | filter on `consolidation_status` enum | `/episodes` extend |
| Embeddings surface + rail drift row | `/memory` | per-tier stale counts | exists (`/reembed/pending`) |

### API delta (Phase C — every row evidence-graded; no fixtures exist in this bundle)

| Path | Method | Status | Existing handler | Delta / shape | Evidence | Drives |
|---|---|---|---|---|---|---|
| `/api/memory/stats` | GET | **extend** | `src/butlers/api/routers/memory.py:195` | + `last_consolidation_at: str\|null`, `last_consolidation_facts_produced: int\|null`, `dead_letter_episodes: int` (default 0); backward-compatible additive fields | live-endpoint (memory.py:195; episodes schema mem_001:42–61) | overture, pipeline, rail ×3 |
| `/api/memory/facts/:id/confirm` | POST | **new** | — | `{}` → `ApiResponse[Fact]`; delegates to `storage.confirm_memory()` (storage.py:1970, mirrors MCP `memory_confirm` tools/feedback.py:17) | live-endpoint (storage fn verified) | fact Confirm |
| `/api/memory/facts/:id/retract` | POST | **new** | — | `{}` → `ApiResponse[Fact]` with `validity='retracted'`; delegates to `storage.forget_memory()` (storage.py:1724) | live-endpoint (storage fn verified) | fact Retract |
| `/api/memory/episodes` | GET | **extend** | memory.py:263 (has `consolidated: bool` only) | + `status: str` enum {pending, consolidated, failed, dead_letter}; legacy bool kept, status takes precedence | live-endpoint (memory.py:266; constraint mem_001:58–61, indexes :75, :87) | daybook filter |
| `/api/memory/facts` | GET | **extend** | memory.py:403 (no source_episode_id filter) | + `source_episode_id: str\|null` param | live-endpoint (FK mem_001:118) | episode provenance |
| `/api/memory/facts` | GET | **extend** | memory.py:403 (no importance filter) | + `importance_min: float\|null` param; rail reads `meta.total` | live-endpoint (column mem_001:113) | rail fading count |
| `/api/memory/facts/:id` | GET | **extend** | memory.py:491 | + `superseded_by: str\|null` via reverse query `WHERE supersedes_id = $1` | live-endpoint (FK mem_001:119) | fact provenance chain |
| `/api/memory/reembed/pending` | GET | **exists** | memory.py:2056 | `counts: dict[tier,int]`, `total` — per-tier, sufficient | live-endpoint (model memory.py:243–251) | embeddings + rail |
| `/api/memory/inspect` | GET | **exists** | memory.py:1916 (pure tsvector :1943/:1971/:1999) | one offset across all kinds (see Open question 7) | live-endpoint | unified search |

### Schema migration impact (Phase C)

- **One new table, additive-only**: `public.consolidation_runs(id, butler, consolidated_at, episodes_processed, facts_produced, facts_updated, rules_created, confirmations_made, errors)` — written once per successful consolidation run (consolidation.py:84–125 already returns these counts). Needed because `last_consolidation_facts_produced` is otherwise underivable. **No changes to existing memory tables** — keeps faith with Section 0's no-migration bullet (additive audit table, per Phase D note 3).
- All other deltas query existing columns (`consolidation_status`, `source_episode_id`, `importance`, `supersedes_id`); cross-butler aggregation follows the established `_fan_out_memory_queries()` pattern (memory.py:101) — no schema-isolation breach.

### Proposed backend epic (Phase C)

**Epic: `memory redesign — backend contracts`**

| # | Bead | Effort | Depends |
|---|---|---|---|
| 1 | Create `consolidation_runs` audit table + write-on-completion in consolidation pipeline | M | — |
| 2 | Extend `GET /stats` with three consolidation fields | S | 1 |
| 3 | `POST /facts/:id/confirm` | S | — |
| 4 | `POST /facts/:id/retract` | S | — |
| 5 | `GET /episodes` `status` enum filter (backward-compat with `consolidated`) | S | — |
| 6 | `GET /facts` `source_episode_id` filter | S | — |
| 7 | `GET /facts` `importance_min` filter | S | — |
| 8 | `GET /facts/:id` `superseded_by` reverse lookup | S | — |

Beads 3–8 parallel; bead 2 blocked by 1.

---

## 4. Guardrails

### LLM-cost feasibility (Phase D; pricing source `references/llm-pricing.md`, last_verified 2026-05 — within 60-day window)

| Feature | Trigger model | $/call | Freq/user/day | $/user/day (1) | $/user/day (100) | Verdict |
|---|---|---|---|---|---|---|
| Voice line | templated from stats — verified NOT LLM (prompts/01:34) | $0 | ~10 loads | $0 | $0 | green |
| Unified search | Enter-submit; verified pure Postgres tsvector (memory.py:1943/1971/1999) — no embedding call | $0 | 10–50 | $0 | $0 | green |
| Re-embed run | owner-triggered batch; **local** SentenceTransformer all-MiniLM-L6-v2 (embedding.py:9–16) — zero API tokens | $0 API | ≤1 on model change | $0 | $0 | green |
| Attention rail | threshold checks on stats/counts | $0 | 30s poll | $0 | $0 | green |
| Recent activity | DB read, 15s poll | $0 | continuous | $0 | $0 | green |
| Confirm / retract | DB mutations | $0 | 1–5 clicks | $0 | $0 | green |
| Consolidation pipeline | pre-existing 6-hourly cron per butler — **unchanged**; redesign adds no run-now affordance and no cadence change (rail overdue row is action-less, prompts/05:45) | n/a | unchanged | $0 delta | $0 delta | green |

#### Red verdicts

None.

#### Recommended de-scopes before spec phase

None on cost grounds. One guard to carry into the spec: **the rail's "write-up overdue" row stays action-less** — no "run consolidation now" affordance may be added later, since that is the only place a future change could multiply the pre-existing 6-hourly spawn cost.

### Manifesto / identity preservation (Phase D)

| Source | Cited | Verdict | Drift |
|---|---|---|---|
| `roster/` (no memory butler exists) | roster listing | clear | — |
| Chronicler manifesto | roster/chronicler/MANIFESTO.md:38,64 | clear | boundary respected both sides (no timeline playback) |
| General butler manifesto | roster/general/MANIFESTO.md:15,29 | clear | capability claim, not page claim |
| Dispatch hue rule | DESIGN_LANGUAGE.md:78,273 | clear | hue only on ButlerMark |
| Doctrine: read-mostly / not-chat / not-uniform-feed / single-owner | about/heart-and-soul/design-language.md:27–62, security.md:6 | clear | redesign is doctrine-*correcting* |
| **OpenSpec `dashboard-domain-pages` memory-page layout** | spec.md:402–409 | **drift — amendment required** | spec mandates the exact three-section layout being deleted |
| **OpenSpec tier-card health badges** | spec.md:413–423 | **drift — amendment required** | MUST-level green/amber word badges vs. redesign's badge ban |
| **OpenSpec browser tables** | spec.md:433–451 | **drift — amendment required** | progress bars, colored badges, per-tab search, page size 20 |
| **OpenSpec `butlerScope` prop** | spec.md:443 | **risk — dependency** | `ButlerMemoryTab` consumes the browser the redesign replaces |
| **OpenSpec detail pages** | spec.md:472–530 | **drift — amendment required** | card/badge structure vs. editorial skeleton |
| VISION no-migration bullet vs. `consolidation_runs` | VISION.md:109–113 | clear, with note | additive-only audit table within intent; spec must state additive-only |
| VISION mutation scope | VISION.md:114–119 | clear | Confirm sole commit pill; Retract secondary (prompts/06:80–81) |

#### Drift write-ups

The only real drift is **OpenSpec**: `dashboard-domain-pages` (spec.md:402–457 + detail requirements) codifies the *current* implementation at MUST level — three-section layout, health badges, confidence progress bars, colored permanence/validity/maturity badges, per-tab searches, page size 20. The redesign deliberately contradicts essentially all of it, in several cases *because* heart-and-soul doctrine bans those patterns. This must land as an explicit OpenSpec change before/alongside implementation — not silent drift. The amendment must also enumerate the backend deltas (§3) so every new affordance has a verified wire (per prior FE→BE reconciliation experience).

#### Recommended manifesto/spec updates

1. **OpenSpec amendment (blocking)**: rewrite `dashboard-domain-pages` memory-page + memory-detail requirements to the new band grammar, register shapes, belief typography, one-search rule; retire health-badge / progress-bar / word-badge MUSTs citing design-language.md:59–62.
2. **`ButlerMemoryTab` preservation-or-migration bead** (spec.md:443 `butlerScope`).
3. **Additive-only clause** for `consolidation_runs`.
4. **Action-less "write-up overdue" row** pinned in spec.
5. No butler MANIFESTO updates — none claims this surface.

### Intent compliance

No red verdicts exist. All four drift/risk items are *consequences of executing* Section 0 (the spec describes the page Section 0 exists to replace) — intent reinforced, not contradicted. No escalation required.

---

## 5. Open questions

Consolidated from Phases A–D. Items 1–6 are resolved here by decision; 7–12 carry into the spec phase.

1. *(A1)* Fact supersedes/superseded-by both directions — **resolved**: backend bead 8 adds `superseded_by`; page omits the direction when absent.
2. *(A2)* `/reembed/pending` shape — **resolved**: per-tier `counts` dict verified live (memory.py:243–251).
3. *(A3)* `effectiveConfidence` precision — **resolved**: `decay_rate` is per-day; use fractional days; clamp to [0,1]; unit-test the edges (prompts/00 acceptance).
4. *(A6)* Ledger default validity — **resolved**: absent `validity` param means `active` (URL param only written when non-default), preserving deep-link round-trip.
5. *(A10)* Rail deep-link URL shapes — **resolved**: `/memory?register=facts&validity=fading`; episodes dead-letter uses the new `status` param: `/memory?register=episodes&status=dead_letter` (foundation URL table gains `status`; the `dead letter` pill maps to it).
6. *(A9/D)* Housekeeping one-commit-per-surface reading — **resolved**: three hairline-divided sub-surfaces, each ≤ 1 commit-class action; if review reads the band as one surface, demote `re-embed` to secondary.
7. *(A7)* `inspect` pagination semantics — one offset across all kinds vs per-kind groups; **spec phase must pin** (current handler paginates the union; acceptable for v1 — state it).
8. *(A5)* KV band ≠ KPI strip — confirm as distinct components in the spec component inventory.
9. *(A8)* Empty provenance section omits the eyebrow too — pin in spec acceptance.
10. *(B3)* Test churn — MemoryBrowser tests rewrite; plan in frontend epic.
11. *(B5)* Hardcoded `/memory?tab=` links elsewhere in frontend — audit and update as part of foundation bead.
12. *(B/D)* `MemoryBrowser` is consumed by `ButlerMemoryTab` (spec.md:443) — keep the legacy component alive for the tab or add a migration bead; must not break silently.

---

## 6. Handoff

`/project-direction` is not installed in this repo. Equivalent machinery executed instead:

- **Spec phase**: OpenSpec change via `opsx:ff` amending `dashboard-domain-pages` memory requirements + new backend contracts. Binding artifacts: `pr/overview/memory-redesign/DESIGN_LANGUAGE.md`, `pr/overview/memory-redesign/MEMORY_LANGUAGE.md`, this brief's Section 0.
- **Beads phase**: two epics — `memory redesign — backend contracts` (§3 outline) and `memory redesign — house-ledger frontend` (one bead per bundle recipe 00–07 + test/link-audit work), frontend blocked on the backend beads it needs (stats extension blocks overture + rail; mutations gate the fact commit footer non-blockingly).

Carry-forward instructions:

- `DESIGN_LANGUAGE.md` + `MEMORY_LANGUAGE.md` are **binding**. Every spec section must preserve them.
- Section 0 of this brief is **binding**. Spec drift away from intent fails reconciliation.
- No red-verdict features exist; the four Phase D amendments (§4) are mandatory spec content.
- After implementation, write `pr/overview/memory-redesign/RECONCILIATION.md` auditing what landed vs. the pack, including FE→BE wiring verification for every new affordance.
