# entity redesign — integration brief v3

**Date:** 2026-06-12
**Version:** v3
**Bundle path:** `pr/overview/entity-redesign/`
**Mode:** fresh (user-selected; prior briefs v1 2026-05-17 and v2 2026-05-27 acknowledged but not diffed — all their epics are closed)
**Phase D verdict:** proceed-with-amendments (5 amendments, §4)
**Prior brief (if any):** docs/redesigns/2026-05-27-entity-brief-v2.md (v2; its scope fully shipped per closed epics bu-lh4ol, bu-ao6uh, bu-uhjxr, bu-m8gb6)

**What v3 is:** an improvement pass on the *shipped* entity surface. The v1/v2 port landed the routes and the look; v3 (a) defines the entity-data lifecycle semantics — ingest → match → assert → look up → age — as a first-class spec, (b) finishes the designed-but-unbuilt depth in every view (workbench, sparkline, keyboard maps, merge flow, provenance rendering), and (c) adds the two consumer contracts the surface exists for: butler programmatic lookup and owner quick-refresh.

---

## 0. Design intent

> Drafted from the user's v3 directives (2026-06-12) and the prior shipped vision, confirmed via AskUserQuestion (mode=fresh, emphasis=semantics-first, relaxations: merge-review UX only). **This section is binding — every spec section, every component decision, every backend contract must trace back to it.** Phase D treats violations of intent as automatic red regardless of cost math.

### Problem being solved

The shipped entity surface (v1/v2 redesign) gave entities one home with alternate views — index+queue, hop, columns, concentration, editorial/workbench detail, Cmd-K finder — but the views are shallow ports: they show the *population*, not the *knowledge*. Workbench is a stub, the sparkline was never rendered, provenance fields are fetched but invisible, no merge flow exists, keyboard maps are absent. More fundamentally, the system lacks well-defined semantics for the entity data lifecycle: how raw ingested signals become entities, how new observations deterministically match existing entities (and how ambiguity is surfaced), how facts are looked up by butlers and by the owner, and how facts age. Without those semantics the views have nothing deep to render and butlers have nothing reliable to query.

### Primary audience

**Owner (v1)**, in two modes, both first-class:

1. **Owner-direct** — entities as a "quick-refresh" UI: open a friend/family/vendor page and immediately see latest interactions, core dates (birthdays, anniversaries), and what changed since last look.
2. **Owner's butlers** — programmatic lookup of "the owner's relationship to X" (facts + provenance + recency) to contextualize advice and decision-making, e.g. a finance butler resolving a vendor, a health butler resolving a practice, any butler resolving a person.

### Deliberate design moves

- **Semantics-first.** The lifecycle ingest → match → assert → look up → age is the spine of the spec; hop/columns/concentration/detail are renderers of that model, not products of their own.
- **Entity-level fact tracking is the core value.** Every fact carries provenance (`src`), assertion-time confidence (`conf`, immutable), owner verification (`verified`), and recency (`last_seen` / `observed_at`). The UX answers "what do I and my butlers know about X, where did it come from, and how fresh is it."
- **Quick-refresh as a primary UX job.** Detail front-loads latest interactions, core dates, and delta-since-last-visit; the three alternate views get the depth (weight ranking, drill, KPIs, keyboard) needed to actually explore.
- **Butler lookup as a contract.** A read-only `relationship_lookup` MCP tool any butler can call from within an already-running session.
- **Merge-review curation (newly allowed).** Single-pair compare → merge/dismiss flow on duplicate candidates, with a structural field diff and an audit trail.
- **Deepen, don't multiply.** No new top-level products; improvements live inside the existing `/entities` surface and the shipped Dispatch design language.

### What we are deliberately NOT doing

- **No LLM calls per page render** (stands from v1). All glosses stay canned strings (`frontend/src/lib/entity-glosses.ts`); search stays deterministic SQL.
- **No LLM-driven entity matching, even in background** (user explicitly declined this relaxation). Matching is deterministic: exact shared-identifier detection, explicit flags, rule-based scoring only.
- **No free-form bulk merge UX** (stands). Merge-review is single-pair only.
- **No card layouts** (stands). Rule-rows, hairlines, the shipped token set.
- **No generated prose affordances** — no "summarize the merge candidates", "narrate what changed", "explain this confidence score". Phase D pre-emptively reds these (§4).

### Success criteria

- Owner opens a friend's entity page and within seconds sees: latest interactions (per channel), core dates, and a "N new facts since your last visit" delta — without any LLM call.
- A butler in an already-running session can call `relationship_lookup("Northwind Plumbing")` and receive active facts with provenance + recency, well-defined enough to act on.
- Every rendered fact answers "where did this come from, when, and is it owner-verified" — provenance visible on detail, workbench, and concentration.
- An incoming signal (email/telegram/calendar) deterministically creates-or-matches an entity; ambiguous matches land in the queue; the owner resolves a duplicate pair via compare → merge in under a minute, with an audit row.
- Hop, Columns, Concentration each have a defined job with the features to do it: weight-ranked + truncated neighbour groups, keyboard maps, row drill, footer KPIs.
- Workbench mode actually exists: 3-rail layout, raw triples view, confidence/staleness inspector, duplicate warning panel.

---

## 1. Scope

v3 touches the six shipped routes (`/entities`, `/entities/hop`, `/entities/columns`, `/entities/concentration`, `/entities/:id` editorial+workbench, `/entities/social-map`) plus the app-wide Cmd-K Finder, the relationship butler's API router and MCP tool surface, and the lifecycle semantics spec that underpins them. Design language is the shipped Dispatch system (`pr/overview/entity-redesign/reference/DESIGN_LANGUAGE.md`, binding). Integration target is the live stack: React 18.3 + react-router 7.13 + TanStack Query 5.90 + cmdk + shadcn/Tailwind frontend; FastAPI routers under `roster/relationship/api/router.py`; `relationship.entity_facts` triple store.

### Sub-pages

| Route | Source file(s) | Purpose | Sticky-nav parent? |
|-------|---|---|---|
| `/entities` | prompts/01-index.md, exp-index.jsx | Tabular landing with curation queue in right rail | Yes — parent for all `/entities/*` |
| `/entities/hop` | prompts/02-hop.md, exp-hop.jsx | Re-centring graph explorer; click nodes to walk predicates | Yes — child of /entities |
| `/entities/columns` | prompts/03-columns.md, exp-columns.jsx | Cascading Finder-style column drill; left-to-right nav | Yes — child of /entities |
| `/entities/concentration` | prompts/04-concentration.md, exp-concentration.jsx | Predicate-grouped weight balance sheet; bars + top-3 KPI | Yes — child of /entities |
| `/entities/:id` | prompts/05-detail-editorial.md, exp-detail.jsx (`DetailEditorial`) | Editorial detail page; hero + two-column layout; default mode | No — leaf; accessed from index |
| `/entities/:id?mode=workbench` | prompts/06-detail-workbench.md, exp-detail.jsx (`DetailWorkbench`) | Workbench console; three-rail layout, raw triples, confidence bars | No — toggle on /:id |
| `/entities/social-map` | README.md (final §) | Dunbar circles (untouched; refresh is separate work) | Yes — child of /entities |
| `/` (global) | prompts/07-finder.md, exp-command.jsx | App-wide Cmd-K/`/` spotlight; entity-first, type-mixed results | No — modal overlay, any page |

### Designed behaviors per view (Phase A, v3 addition — the unbuilt-depth checklist)

- **Index + Queue**: queue rail with 3 card types (unidentified/duplicate-candidate/stale), one commit button per card; state colour confined to queue; bulk-select gutter (archive/merge/forget) materializing on selection ≥1; keyboard map ↑↓/Space/Shift+↑↓/Esc/`n`/Cmd-K; serif-italic empty states.
- **Hop**: re-centre on click with instant (<100ms) rebuild, no physics; predicate wedge arcs; clickable breadcrumb trail + reset pill at depth>1; right detail pane with relations; conditional predicate filter chips; keyboard Esc-pop/`r`-reset/↑↓/Enter/1..9.
- **Columns**: columns-are-the-breadcrumb cascade; predicate-grouped sections, top-6 by weight + "+N more" (deferred v1); first column `--bg-elev`; horizontal auto-scroll via `scrollLeft`; "STEP DEEPER →" hint on rightmost; keyboard ↑↓→←/Enter.
- **Concentration**: predicate tabs with population counts; top-3 share headline KPI (22px tnum); bar anatomy width=weight/max·100% h=6px; footer KPI strip (total touches | orgs | top entity | tail <1%); read-mode (no row hover), rows sorted weight desc.
- **Detail-Editorial**: 1.4fr/1fr scaffold; hero with 40px mark + 44px display + serif gloss + badges + first/last-seen; 90-day sparkline (absent days 4% opacity, no axes); relations top-8 + see-all; activity top-8 + see-all; multi-valued contacts index with primary-first + amber unverified dot; curation rail 3×2 (merge/promote/demote/archive/forget(red)/edit-aliases) with serif gloss; workbench toggle pill; keyboard k/j/Esc/e/m/Shift+Backspace.
- **Detail-Workbench**: 240px|1fr|280px 3-rail; left rail top-relations + introduced-via + shares-emails-with merge hint (amber); middle 4-col KPI strip + raw triples mono 11px 4-colour syntax; right rail action list + confidence inspector (4px bars, amber <0.85); duplicate warning panel when state=duplicate-candidate; keyboard adds `t` and `?` mode toggle.
- **Finder**: Cmd-K/`/` overlay min(1100px,90vw)×70vh; empty query = owner-pinned set by weight; fuzzy scoring prefix=100/contact=70/substring=50/predicate=30, top 8, weight tie-break; preview pane (mark/name/gloss/relations×5, inert); Tab = hop-into (`/entities/hop?centre=:id`); result kinds extensible entity|rule|episode|approval|butler (entity only wired).

### Design tokens (binding — `reference/DESIGN_LANGUAGE.md`)

- **Surfaces (dark canonical)**: `--bg` oklch(0.145 0 0); `--bg-elev` 0.205; `--bg-deep` 0.115; `--fg` 0.985; `--mfg` 0.708; `--dim` 0.55; `--border` 1 0 0/0.10; `--border-soft` /0.06; `--border-strong` /0.18.
- **State (foreground/border only, never fill)**: `--red` oklch(0.685 0.250 29.2) blocker/forget; `--amber` oklch(0.810 0.185 84.0) unidentified/duplicate/unverified/low-confidence (<0.85); `--green` oklch(0.790 0.195 148.2) healthy/positive.
- **Butler hues** `--category-1..8`: letter-mark only, never background/border/button. **Tier ramps** `--tier-1..6`: 6px square in TierBadge.
- **Type**: Inter Tight (UI), Source Serif 4 (voice/empty states), JetBrains Mono (times/IDs/KPIs/eyebrows). Scale: Display 44/500/-0.025em; Title 24–28/500; Body 14; Voice serif 16/1.6; Eyebrow mono 10/0.14em; Mono inline 11. `tabular-nums` on every numeric — non-negotiable.
- **Spacing**: page 48px 56px; section gutter 56px; gaps in multiples of 4; rows 8–18px vertical pad; never `padding: 24px` on a list item.
- **Motion**: 120–200ms fades only, `cubic-bezier(0.22,1,0.36,1)`; tooltips instant. Forbidden: springs, bounce, parallax, scale, shimmer, skeleton-pulse, count-up.
- **Hard do-nots** (selection): no cards anywhere (rule-rows only); no per-row kebabs; no hue on entity type; no hardcoded predicate IDs outside `entity-model.ts` + predicate catalog; never collapse multi-valued contact facts; never bury "forget"; no emoji in chrome; no decorative SVG; no filling quiet days with mock content.

---

## 2. Component impact

### Classification table (Phase B — verdicts vs the live code, 2026-06-12)

| Component/behavior | Verdict | Where (file:line) | Notes |
|---|---|---|---|
| Eyebrow / Voice / Display / Title | shipped | EntityDetailPage.tsx:2072–2100 | Editorial archetype only in mode=editorial; workbench falls back to "overview" (44→32px regression) |
| EntityMark glyph | shipped (inlined ×2) | EntitiesIndexPage.tsx:473; HopPage.tsx:40 | DRY violation; no extracted component |
| TierBadge | missing | — | Not rendered anywhere in current code |
| StateDot | missing | — | States rendered as text, not dots |
| Row | partial | EntitiesIndexPage.tsx:511 | Inlined in EntityTable; no extracted Row |
| Pill (filter chips) | shipped | EntitiesIndexPage.tsx:420 | Type + state + has_contact chips |
| Section | shipped | EntityDetailPage.tsx (inline section components) | |
| Queue Rail (3 card types) | shipped | EntitiesIndexPage.tsx:627 | promote/archive/forget per entry; no evidence drill |
| Tab Strip | shipped | SubpageTabs.tsx | Route-aware |
| Toolbar search | partial | EntitiesIndexPage.tsx:420 | Plain text input; NOT wired to `/entities/search` fuzzy API (only Cmd-K is) |
| Bulk Gutter (selection → archive/merge/forget) | missing | — | No multi-select state anywhere |
| Breadcrumb Strip | shipped | Page archetype on all views | |
| Hop graph (wedges, trail, reset) | partial | HopPage.tsx:134–191, 253–275 | Predicate-grouped fan-out + reset pill; no trail visualization, no weight ranking, no truncation |
| Columns cascade | partial | ColumnsPage.tsx:66–139 | Cascade works (?path= CSV); no keyboard nav, no top-6-by-weight, no "+N more" |
| Concentration | partial | ConcentrationPage.tsx | Tabs (46), rollup top-3 (106–112); weight as `w=123` text not bars (154); no footer KPI strip; rows not clickable; provenance fields fetched but unrendered |
| Detail-Editorial | partial | EntityDetailPage.tsx:2072– | Hero + contacts + timeline + facts grid + curation rail (2203–2330) shipped; NO sparkline render; NO k/j stepping |
| Detail-Workbench | missing (stub) | EntityDetailPage.tsx:101, 1864, 1892, 2075 | Mode toggle wired but renders generic "overview" archetype; no 3-rail, no triples view, no confidence inspector, no merge hints, no duplicate panel, no KPI strip |
| Finder | partial | EntityFinder.tsx | Cmd-K + backend fuzzy scoring live; NO preview pane, NO Tab-to-hop, entity kind only |
| Keyboard maps (all views) | missing/partial | — | No k/j on detail, no ↑↓→← on columns, no list keyboard on index |
| Social Map | shipped (untouched) | SocialMapPage.tsx → SocialMapView → ConcentricCirclesCanvas | No recency/staleness treatment, no drill |

### Stack delta (Phase B)

No new dependencies; no blockers. Within existing stack:

- **Sparkline**: recharts 3.7 already installed (unused for entities) or a ~30-line custom SVG per the design (vertical sticks, no axes).
- **Confidence/staleness bars**: custom 4px divs/SVG; no library.
- **Keyboard nav**: manual keydown handlers + roving focus; no dependency.
- **Workbench 3-rail**: CSS grid.

### Current API consumption map (Phase B)

| View | Endpoint | Handler (roster/relationship/api/router.py) |
|------|----------|------------|
| Index | GET `/entities` | :2666 |
| Queue rail | GET `/entities/queue` | :3182 |
| Hop | GET `/entities/{id}` + `/neighbours` | :3739, :4881 |
| Columns | GET `/entities/{id}/neighbours` (chained per column) | :4881 |
| Concentration | GET `/entities/concentration` | :3540 |
| Detail | GET `/entities/{id}` + `/facts`-family tabs (notes :4101, interactions :4159, gifts :4219, loans :4278, timeline :4342, linked-contacts :4411, message-threads :4568) + `/activity` :6284 | |
| Finder | GET `/entities/search` | :2459 |
| Writes | POST `/entities` (promote), `/contacts`, `/archive`, DELETE, `/promote-tier` :5866, `/merge` :6077, `/queue/dismiss` | |

### Butlers touched (Phase B, corrected per Phase D)

| Butler | Surfaces touched | Manifesto path | Why touched |
|---|---|---|---|
| relationship | All entity views + queue + finder + all API handlers + new MCP lookup tool | roster/relationship/MANIFESTO.md | Owns the entity surface and `relationship.entity_facts` |
| chronicler | Activity timeline / sparkline source | roster/chronicler/MANIFESTO.md | `/entities/{id}/activity` aggregates via MCP (router.py:6284); boundary guardrail test exists |
| switchboard | Ingress identity resolution | roster/switchboard/MANIFESTO.md | Resolves inbound channel identifiers → entity; **reads `public.*` only; must never write `relationship.entity_facts`** (Phase D amendment 4) |
| memory (module, NOT a butler) | `memory_entity_resolve/create/store_fact` ingest tools; social map canvas | — (module mounted on relationship, roster/relationship/butler.toml:120-121) | Phase D corrected scope: identity contract is the relationship butler's |

---

## 3. Backend contract delta

### Lifecycle semantics — current state (Phase C; the spine of the v3 spec)

**Ingest** — Entities/facts created via the fact-extraction skill pipeline (roster/relationship/.agents/skills/fact-extraction/SKILL.md, steps 1–5) running inside already-triggered relationship sessions: `memory_entity_resolve()` (deterministic salience scoring; NONE/MEDIUM/HIGH confidence bands) → `memory_entity_create()` with `metadata.unidentified=true` on NONE → `memory_store_fact(entity_id, predicate, ...)`. Central writer `relationship_assert_fact()` (roster/relationship/tools/relationship_assert_fact.py; router.py:6073+) validates predicates against `relationship.entity_predicate_registry`, enforces idempotency on (subject, predicate, object), parks owner-subject writes as pending_actions (RFC 0017). Connectors stamp `point_events.entity_id`. **Gap: `conf` is hardcoded 1.0 at write time (src/butlers/modules/memory/storage.py:767); no calibration semantics.**

**Match** — Deterministic, no LLM (verified). Queue buckets computed at router.py:3054–3250: *unidentified* = `metadata.unidentified='true'`; *duplicate-candidate* = metadata flag OR shared `has-email`/`has-phone` object values across >1 subject (`_DUP_DETECTION_PREDICATES`, router.py:3129–3132) with evidence `{predicate, shared_value, peer_entity_ids}`; *stale* = no active fact with `last_seen` in 365 days. Priority unidentified > duplicate > stale. **Gap: no merge-review contract — queue exposes peer_entity_ids but no compare endpoint; POST /merge takes entityA/entityB/keepAs with no evidence/justification flow.**

**Assert** — Single ingress through `relationship_assert_fact()`; supersession on (src, conf, verified, last_seen) change → old row `validity='superseded'`; explicit retraction via memory_forget; archive tombstones + retracts. **Gap: no `observed_at` (observed vs inferred indistinguishable); structured provenance metadata only on edge-facts.**

**Look up** — Frontend: `/entities/search` 4-tier deterministic ranking (prefix=100/contact=70/substring=50/predicate=30, router.py:2459+). Switchboard: `resolve_contact_by_channel()` deterministic SQL on active facts. **Gap: NO butler-facing read contract — butlers can write facts but cannot programmatically read them; `relationship_lookup` does not exist.**

**Age** — Stale = 365 days without `last_seen`; `last_seen` stamped on ingest; Dunbar tier override stored as fact (router.py:5871+). **Gaps: no read-time staleness/recency score; no per-fact freshness signal in UI; no delta-since-last-visit machinery (no view-marks table).**

### Affordance inventory (Phase C)

| Affordance | Surface | Data needed | Lifecycle stage | Status |
|---|---|---|---|---|
| Merge-review pair compare | new compare view from queue + workbench | side-by-side SPO diff, shared evidence, provenance | match | **new endpoint** |
| Queue evidence drill | queue rail | predicate, shared_value, peer_entity_ids (clickable) | match | exists (sparse, unwired) |
| Core dates block | detail hero/right col | date facts + provenance | assert/look-up | exists (client-side extraction; provenance unrendered) |
| Latest interactions per channel | detail quick-refresh | interaction summaries, occurred_at, src | look-up | exists (legacy facts schema) |
| Delta-since-last-visit | detail banner | view mark timestamp, facts added since | age | **new table + endpoints** |
| 90-day activity sparkline | detail hero | time-bucketed counts | age | extend /activity (binning) |
| Provenance/confidence display | detail, workbench, concentration | src, conf, verified, primary, last_seen | look-up | exists (data only, no UI) |
| Hop/columns weight ranking + top-N | hop, columns | weight, fact_count per neighbour | look-up | extend /neighbours |
| Concentration footer KPIs + row drill | concentration | rollup fields; row → /entities/:id | look-up | exists (partial) |
| Finder preview pane + Tab-to-hop | finder | gloss, relations ×5 | look-up | extend /search or compose |
| Butler lookup MCP | all butlers | facts[] + provenance + recency + entity header | look-up | **new MCP tool** |
| Fact staleness signals | all views | last_seen → staleness score (read-time) | age | new (computed, no storage) |

### API delta (Phase C; all rows evidence=`live-endpoint` unless marked)

| Path/Tool | Method/Kind | Status | Existing handler | Evidence | Drives |
|---|---|---|---|---|---|
| /entities, /search, /queue, /concentration, /{id}, /notes, /interactions, /gifts, /loans, /timeline, /linked-contacts, /message-threads, /activity, /merge, /promote-tier, /archive, /queue/dismiss, POST /entities, /contacts | GET/POST | exists | router.py:2459–6284 (per map in §2) | live-endpoint | all shipped views |
| **GET /entities/{id}/facts** | GET | **extend/new** | facts embedded in detail + tabs today | spec (`openspec/specs/relationship-facts/spec.md`) | fact drill + provenance display; params: predicate_filter, validity_filter, pagination; returns full provenance incl. validity + observed_at |
| **POST /entities/compare** | POST | **new** | — | spec-to-be | merge-review: `{entity_a, entity_b}` → `{a: {facts, info}, b: {facts, info}, shared: {predicates, values}, divergent}` — structural diff ONLY (Phase D amendment 2) |
| **POST /entities/{id}/view-mark** + **GET /entities/{id}/delta-facts** | POST/GET | **new** | — | spec-to-be | delta-since-last-visit; auto-timestamp mark; delta = facts where asserted/modified > mark |
| **GET /entities/{id}/activity?bins=daily&window=90d** | GET | **extend** | router.py:6284 | live-endpoint | sparkline buckets (chronicler stays MCP-only) |
| **GET /entities/{id}/neighbours?rank=weight&per_predicate=6** | GET | **extend** | router.py:4881 | live-endpoint | hop/columns top-N + "+N more" counts |
| **mcp: relationship_lookup(entity_ref) → {entity, facts[+provenance, recency]}** | MCP | **new** | — (relationship_assert_fact.py:159+ is the write twin) | spec-to-be | butler lookup contract; read-only, in-session-only, docstring ≤300 tokens (Phase D amendment 1) |

### Schema migration impact (Phase C, amended by Phase D)

- `relationship.entity_facts` — add `observed_at TIMESTAMPTZ NULL`, `metadata JSONB NULL`. **`conf` stays immutable assertion-time certainty; NO stored decay** (Phase D amendment 3 — merge conflict-resolution at src/butlers/modules/memory/tools/entities.py:834–837 keeps higher-conf facts and would be corrupted by decay). Staleness is a read-time computation from `observed_at`/`last_seen`.
- `relationship.entity_view_marks` — new: `(id PK, entity_id FK UNIQUE, marked_at)`.
- `relationship.merge_reviews` — new audit table: `(id, entity_a, entity_b, shared_facts, divergent_facts JSONB, outcome merged|dismissed|pending, reviewed_at, created_at)`.
- No `public.*` changes. No cross-butler SQL anywhere (chronicler via MCP; switchboard reads `public.*` identity tables only).
- **Spec must resolve the two-fact-stores ambiguity** (Phase D drift 3): memory-module `facts` table vs `relationship.entity_facts` — name ONE canonical store for provenance/drill/compare/lookup.

### Proposed backend epic (Phase C outline; final graph cut by /project-direction)

**Epic: entity v3 — lifecycle semantics & backend contracts**

| # | Bead | Effort | Depends on |
|---|---|---|---|
| 1 | Fact provenance columns (`observed_at`, `metadata`) + read-time staleness score semantics | M | — |
| 2 | Merge-review contract: POST /entities/compare + relationship.merge_reviews | L | 1 |
| 3 | Delta-since-last-visit: view_marks table + view-mark/delta-facts endpoints | S | — |
| 4 | Dedicated GET /entities/{id}/facts drill (filters, pagination, full provenance) | M | 1 |
| 5 | `relationship_lookup` MCP tool (read-only; in-session-only; ≤300-token docstring) | S | — |
| 6 | /activity 90-day binning extension (chronicler MCP boundary preserved + guardrail test extended) | M | — |
| 7 | /neighbours weight ranking + per-predicate top-N + remainder counts | M | — |
| 8 | Lifecycle semantics spec doc: ingest→match→assert→look up→age as OpenSpec capability (incl. matching rules, supersession, staleness definition, canonical-store decision) | M | — (FIRST; gates all) |
| 9 | No-LLM guardrail tests: source-scan for model calls in compare/merge/lookup paths; switchboard never references relationship.entity_facts | S | 2, 5 |

Frontend epic (split per Phase G): workbench build-out (3-rail, triples view, confidence/staleness inspector, duplicate panel), sparkline, keyboard maps (index/columns/detail/hop), bulk gutter, merge-review UI, provenance badges (detail/concentration/workbench), concentration bars + footer KPIs + row drill, finder preview pane + Tab-to-hop, queue evidence drill, toolbar search → fuzzy API, component extraction (EntityMark/Row/TierBadge/StateDot), delta banner.

---

## 4. Guardrails

### LLM-cost feasibility (Phase D; pricing last_verified 2026-05, 42 days — within window, no drift)

Deterministic-by-design ($0 marginal LLM): confidence/staleness columns, merge-review compare, delta-since-last-visit, facts drill + provenance badges, activity binning, all view-depth work, keyboard maps.

| Feature | Trigger | tokens_in | tokens_out | Model | $/call | Freq/day | $/day (u=1) | $/day (u=100) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| `relationship_lookup` caller-side (inside already-running sessions) | on-demand tool call | ~1,700 marginal | 0 | Sonnet (caller's) | $0.005 | ~10 | **$0.05** | $5.10 | **yellow** |
| `relationship_lookup` IF new spawn triggers/schedules feed it | multi-step agent | 8,000 | 600 | Sonnet | $0.033 | 1–5 ×N butlers | $0.03–0.17/butler | ×100 | **red if speced as new triggers** |
| Any generated-prose affordance (explain confidence / summarize merge / narrate delta) | per render | 3,000 | 200 | Sonnet | $0.012 | 10 loads | $0.12 | $12 | **intent-conflict-red (pre-emptive)** |

#### Red verdicts

1. **`relationship_lookup` as session-spawn magnet (conditional red).** The tool is a deterministic read; cost lives at the caller and is fine only while callers are sessions that would run anyway. New cron/scheduled jobs whose purpose is calling it = $16.50/day at u=100 ×5 butlers and displaces NOT-bullet 1 to the caller. **Re-scope:** spec states in-session-only; no new spawn triggers; docstring ≤300 tokens (it lands in every mounting butler's tool inventory).
2. **Generated prose anywhere on the surface (pre-emptive intent-conflict-red).** Violates NOT-(1) and NOT-(2)/(5). **Kill** any "summarize differences", "suggest merge verdict", "what's-new digest" spec language. Deterministic alternative already shipped: canned glosses, raw fact lists, two-column structural diff.

#### Recommended de-scopes before spec phase

1. `relationship_lookup` = read-only, in-session-only; no new spawn triggers or schedules to feed it.
2. `POST /entities/compare` returns structural field diff only; no scoring/ranking/recommendation text from any model; encode as no-LLM source-scan guardrail test.
3. Strike "confidence-decay" as worded; replace with read-time staleness score (see drift 1).

### Manifesto / identity preservation (Phase D)

| Butler | Manifesto cite | What v3 does | Verdict | Drift |
|---|---|---|---|---|
| relationship | MANIFESTO.md:8, :31, :61 | Owns all surfaces; exposes lookup; adds provenance | **identity preserved** (one flag) | Confidence-decay conflates fact-truth with interaction-recency (drift 1) |
| chronicler | MANIFESTO.md:26–31, :34–43 | 90-day binning extends MCP-fed /activity | **identity preserved** | None if aggregation stays MCP-only; extend guardrail test; heavy bins → RFC'd read-only view (heart-and-soul vision.md:72–78, RFC 0010), never quiet SQL |
| switchboard | MANIFESTO.md:11, :27–29 | Ingress identity resolution | **drift flagged** | Switchboard must read `public.*` identity tables only; fact assertion happens inside relationship-routed sessions (drift 2) |
| memory (module) | — (no manifesto; module on relationship, butler.toml:120–121) | resolve/create/store_fact in ingest; social map | **drift flagged** (scoping + ownership) | Two fact stores: memory-module `facts` vs `relationship.entity_facts` (router.py:331) (drift 3) |

#### Drift write-ups

1. **Confidence-decay vs owner-verified semantics.** No doctrine defines fact confidence; code writes `confidence=1.0` (storage.py:767) and **merge keeps the higher-confidence fact** (entities.py:834–837, 885–886). Auto-decay would silently flip merge winners (old owner-stated fact decayed to 0.6 loses to fresh low-quality extraction at 1.0) and contradicts the explicit retract+replace correction model. MANIFESTO.md:61 decay is interaction-recency, a different axis. **Reconciliation:** `conf` immutable assertion-time certainty; separate read-time **staleness score** from `observed_at`; UI displays both axes distinctly.
2. **Switchboard fact writes.** Classify-and-route contract (MANIFESTO.md:11) + non-responsibility for domain logic (:27–29). **Reconciliation:** switchboard reads `public.*` only; v3 spec states it as an invariant with a guardrail test that switchboard code never references `relationship.entity_facts`.
3. **Two fact stores.** Provenance badges, facts drill, compare, and lookup must all name ONE source of truth, and it must be the same table the merge flow mutates. **Reconciliation:** spec declares the canonical fact store (memory-module facts table on the relationship schema mount), documents `entity_facts` as projection or schedules consolidation; `relationship_lookup` reads canonical only; scope table corrected (memory is a module).

#### Recommended manifesto updates

- relationship MANIFESTO: one line distinguishing *fact confidence* (assertion-time, owner-correctable) from *staleness* (time-since-observed).
- No switchboard/chronicler manifesto changes — v3 conforms to them.

### Intent compliance

All red/drift verdicts are **reinforced by** Section 0, not in tension with it: the conditional red on `relationship_lookup` enforces NOT-(1) at the caller; the pre-emptive prose red enforces NOT-(1)/(5); the matching path was verified deterministic, satisfying NOT-(2); merge-review stays single-pair per the only relaxation granted. No verdict contradicts intent; nothing escalates to the user beyond the five amendments, which are hereby accepted into Section 0 as binding constraints.

---

## 5. Open questions

Consolidated from Phases A–D; `/project-direction` Phase 1–2 must resolve each.

1. **(A) Social map refresh** — README final §: untouched in design pack; v3 audit shows no recency/staleness treatment and no drill. In-scope as a small bead or explicitly deferred? (Recommend: defer, one bead for staleness dimming + click-through.)
2. **(A) Predicate catalog extensibility** — prompts/00-foundation.md:42–52: how do new predicates get added at runtime? Registry table exists; UI/spec story for adding one is undefined.
3. **(A) Columns "+N more" side sheet** — prompts/03-columns.md:71: inert "+N" row vs absent in v3? (Phase C contract supports remainder counts.)
4. **(A) Workbench "shares emails with" merge-hint shape** — prompts/06-detail-workbench.md:59: side panel vs inline; v3 has the compare endpoint to back it — wire hint → compare view?
5. **(A) Finder owner-pinned set definition** — prompts/07-finder.md:68: hard list, tier threshold, or recency window?
6. **(A) Detail k/j sibling scope** — prompts/05-detail-editorial.md:57: does the sibling list scope follow the navigation source (Index vs Hop vs Columns), and how is it tracked?
7. **(B) Search duality** — toolbar plain-text search vs Cmd-K fuzzy: wire toolbar to `/entities/search` or remove toolbar search in favour of Cmd-K?
8. **(B) Provenance render scope** — provenance on every fact row everywhere, or hover/tooltip on editorial + always-on in workbench? (Design language suggests workbench=always, editorial=on-demand.)
9. **(C) Interactions store** — latest-interactions quick-refresh reads the legacy facts schema; consolidate onto canonical store now or read-through?
10. **(C) `observed_at` backfill** — historical facts have no observed_at; staleness score fallback to `last_seen`? Define precedence.
11. **(D) Canonical fact store decision** — drift 3 demands the spec name one store; this is the single highest-leverage open decision and gates beads 1, 2, 4, 5.
12. **(D) Staleness thresholds** — 365d stale-bucket exists; what curve/labels does the read-time staleness score use (fresh/aging/stale bands)? Must align with MANIFESTO.md:61 interaction-decay framing.

---

## 6. Handoff to `/project-direction`

This brief is the input to a `/project-direction` run with **feature evaluation focus** scoped to `entity` v3.

Concrete invocation:

```
/project-direction --focus=feature \
  --brief=docs/redesigns/2026-06-12-entity-brief-v3.md \
  --bundle=pr/overview/entity-redesign/ \
  --binding-design-language=pr/overview/entity-redesign/reference/DESIGN_LANGUAGE.md \
  --binding-design-intent=docs/redesigns/2026-06-12-entity-brief-v3.md#0-design-intent \
  --red-flag-policy=descope-or-escalate
```

Carry-forward instructions:

- `reference/DESIGN_LANGUAGE.md` is **binding**. Every spec section must preserve it.
- Section 0 of this brief is **binding**, including the five Phase D amendments folded into it.
- The lifecycle semantics spec (backend bead 8) is the FIRST artifact: ingest→match→assert→look up→age, matching rules, supersession, staleness definition, canonical-store decision. Everything else traces to it.
- All `red`-verdict items are de-scoped as specified in §4; any reintroduction re-enters Phase D.
- All `identity drift flagged` items carry their reconciliation from §4 into the spec.
- After `/project-direction` Phase 3 produces the beads graph, Phase G of `butlers-redesign-prompt` splits the backend epic (`entity v3 redesign — backend contracts`) from the frontend epic and wires `blocked-by` frontend→backend.
