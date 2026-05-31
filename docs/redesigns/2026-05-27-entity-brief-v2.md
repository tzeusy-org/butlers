# entity redesign — integration brief v2 (diff)

**Date:** 2026-05-27
**Version:** v2
**Prior brief:** [docs/redesigns/2026-05-17-entity-brief.md](2026-05-17-entity-brief.md) (v1, dated 2026-05-17, authored 2026-05-18 08:55, last amended 2026-05-22)
**Bundle path:** `pr/overview/entity-redesign/` (1 reconciliation commit since v1: `d48019548` aligning design pack to shipped six-tier + mode vocabulary)
**Mode:** diff — phases B (impact) + C (backend contract) re-run; A (input) and D (guardrails) unchanged from v1
**Phase D verdict:** v1 verdict (`clear`) reaffirmed. No new LLM features introduced; pricing card stable.

---

## 0. Design intent (unchanged, binding)

Section 0 of v1 is binding and unchanged. The new triple-store data model, contacts-as-predicates, Editorial+Workbench, Cmd-K Finder, Dispatch design language, and "no cards / no LLM per page / no bulk merge UX" rejections all stand. Refer to [v1 brief §0](2026-05-17-entity-brief.md) verbatim.

## 1. Scope (unchanged, binding)

Section 1 of v1 is binding and unchanged. Six routes (`/entities`, `/entities/hop`, `/entities/columns`, `/entities/concentration`, `/entities/:id`, `/entities/social-map`) plus app-wide Cmd-K. Refer to [v1 brief §1](2026-05-17-entity-brief.md).

---

## What shipped since v1 (2026-05-17 → 2026-05-27)

### Frontend integration — epic `bu-lh4ol`, **CLOSED 12/12**

All entity sub-routes shipped at `frontend/src/router-config.tsx:105-109`. EntityDetailView with Editorial+Workbench toggle (`bu-ar4zf`), Cmd-K Finder (`bu-xfjwk`), `/contacts → /entities?has=contact` 301 redirect (`bu-qsipw`), SubpageTabs (`bu-wx2r0`), 5 atom primitives (EntityMark, TierBadge, StateDot, KbMono, Pill — `bu-ec2wb`), entity-glosses canned strings (`bu-wi06b`), SocialMapView refactor (`bu-zvtxh`). e2e Playwright smoke (`bu-81rkz`). Hex literal migration to `entity-model.ts` (`bu-svmpx`). Editorial archetype = Display 44px (`bu-hm0oe`). Provenance fields on entity detail API (`bu-mg4dk`). Forget affordance (`bu-iny1e`).

### Backend contracts — epic `bu-ao6uh`, **OPEN 28/31 (90%)**

All 17 API rows from v1 §3 shipped, including the previously-`unclear` columns cascade (resolved client-side via chained `/neighbours` calls — no server endpoint). Endpoints live in `roster/relationship/api/router.py:2476-6350`:

- `GET /entities` (`router.py:2684`, `bu-7s86b`)
- `GET /entities/search` deterministic fuzzy (`router.py:2476`, `bu-q9uiw`)
- `GET /entities/queue` (`router.py:3199`, `bu-t1zfd`)
- `GET /entities/concentration` (`router.py:3557`, `bu-0vosj`)
- `GET /entities/{id}` + Workbench (`router.py:3756`, archived spec)
- `GET /entities/{id}/neighbours` (`router.py:4840`, `bu-4wn79`)
- `{notes,interactions,gifts,loans,timeline}` tab endpoints (`router.py:4104+`, archived spec)
- `POST /entities/{id}/contacts` + DELETE (`router.py:5095+`, `bu-u1w78`) — owner-only authz enforced (Amendment 12a)
- `POST /entities/{id}/archive` + `DELETE /entities/{id}` (`router.py:5480+`, `bu-l76uv`)
- `POST /entities/{id}/promote-tier` writes `dunbar_tier_override` fact (`router.py:5593`, `bu-wmigz`)
- `POST /entities/{id}/merge` entity-level (`router.py:5805`, `bu-jp6r6`)
- `GET /entities/{id}/activity` aggregator calling chronicler MCP tools (`router.py:6187`, `bu-ihiw4`)
- `POST /entities/queue/dismiss` (`router.py:3453`, `bu-297lj`)
- `POST /entities` (promote unidentified, `router.py:2908`, `bu-pzp9m`)

Schema: `relationship.entity_facts` + `relationship.entity_predicate_registry` live, indexes pre-warmed (`bu-892tf`, `bu-hlovw`). `relationship.credentials` carve-out table created (`bu-uj3xv`). All §6b amendments shipped: Amendment 5 chronicler boundary (`bu-f5qcp`), 6 tier promotion as fact, 12a/b/c owner authz (`bu-i99z3`), 13 reader inventory, 14 reconciler job (`bu-75a3s`), 15 transitive Finder enforcement test (`bu-wqmck`), 16 `chronicler_list_episodes(entity_id=)` prereq (`bu-aqe7n`). Integration tests (`bu-4vhjq`). Diagrams (`bu-3qfda`).

**Open in this epic:**
- `bu-ixb3p` (P2) — RFC 0004 amendment archival
- `bu-u1mw8` (P3) — `v1.md` doctrine update post-RFC 0004 A2
- `bu-qz58b` (P1) — gen-1 spec-to-code reconciliation report (`docs/reports/entity-redesign-phase-2.md`); gated on `bu-7jo43` + `bu-yrqrk` + the two above

### Contacts→triples migration — epic `bu-uhjxr`, **OPEN 9/17 (53%, P0)**

Mid-flight. Pre-migration snapshot + write-path inventory + central writer + dual-write shim + backfill + parity tests + orphan resolver + read-path cut-over all closed. Currently in dual-write soak with reads cut over to `relationship.entity_facts` (`bu-akads`, 2026-05-24).

**Open in this epic:**
- `bu-k9ylx` (P0) — **write-path cut-over** (terminal step; gated on read-stability soak)
- `bu-hpv4u` (P0) — 30-day post-cut-over verification report
- `bu-e2ja9` (P0) — drop `public.contact_info` (gated on `bu-hpv4u` sign-off)
- `bu-pl8fy` (P2) — secured=true credential rows separate bead (decides drop scope)
- `bu-fa5ex` (P2) — secured contact_info reveal endpoint design (post-bead 8)
- `bu-5lijl` (P2) — re-pointing plans for 7 relationship tool files (tasks/notes/life_events/dates/labels/groups/relationships)
- `bu-6orfp` (P1) — `backfill_facts.py` contact reads at bead 10
- `bu-7jo43` (P1) — gen-1 spec-to-code reconciliation for migration (`docs/reports/entity-redesign-reconciliation-migration.md`)

### Decommission contact detail page — epic `bu-m8gb6`, **OPEN 4/7 (57%, P1)**

Spec ratified (`bu-m8gb6.1`), parity inventory (`bu-m8gb6.2`), entity-detail contact-channel card (`bu-m8gb6.3`), redirect API resolver (`bu-m8gb6.4`).

**Open in this epic:**
- `bu-m8gb6.5` (P1) — redirect contact detail routes to entity detail
- `bu-m8gb6.6` (P2) — remove obsolete contact detail artifacts
- `bu-m8gb6.7` (P1) — spec-to-code reconciliation

### Cross-cutting follow-ups (open)

- `bu-yrqrk` (P1) — cross-change archive gate (depends on `page-primitive-spec-sync` + `detail-page-archetype` archival)
- `bu-91zdb.4` (P1) — google-health identity migration to email-prefixed key
- `bu-4c1ks` (P2) — chronicler port `entity_id` population to owner-only adapters
- `bu-65z3z` (P2) — remove LEFT JOIN contacts compat once `interaction_log()` uses entity_id subjects
- `bu-6m9an` (P2) — dual-dispatch secured-reveal during `contact_info → entity_info` migration
- `bu-h77um` (P2) — secrets BE-2 follow-up: ORM models for `butler_secrets` + `entity_info` test-state columns
- `bu-w2zo6` (P2, blocked) — Telegram resolve via has-handle triple test (blocked on cut-over)

---

## 2. Component impact — diff against v1 §2

v1 classification table verdict-by-verdict. Columns added: `Shipped?` / `Where shipped` / `Drift from v1 verdict`.

| Component | v1 verdict | Shipped? | Where shipped | Drift |
|---|---|---|---|---|
| Eyebrow | adapt | ✓ | `frontend/src/components/ui/Eyebrow.tsx` | none |
| Voice | adapt | ✓ | `frontend/src/components/ui/Voice.tsx` | none |
| Display | adapt | ✓ | `frontend/src/components/ui/Display.tsx` | none |
| Title | adapt | ✓ | `frontend/src/components/ui/Title.tsx` | none |
| EntityMark | new | ✓ | `frontend/src/components/ui/EntityMark.tsx` | none |
| TierBadge | new | ✓ | `frontend/src/components/ui/TierBadge.tsx` | none |
| StateDot | new | ✓ | `frontend/src/components/ui/StateDot.tsx` | none |
| Pill | adapt | ✓ | shadcn `badge.tsx` + Pill variant | none |
| CommitBtn | new | ✓ | shadcn `button.tsx` + Dispatch variants | none |
| Tick | new | ✓ | shadcn `checkbox.tsx` | none |
| KbMono | new | ✓ | `frontend/src/components/ui/KbMono.tsx` | none |
| SubpageTabs | new | ✓ | `frontend/src/components/relationship/SubpageTabs.tsx` | none |
| Row | adapt | **partial** | inlined in `EntitiesIndexPage.tsx` ~L400-450; no standalone wrapper | **G1** — inlined |
| StatePill | adapt | **partial** | inlined in `EntitiesIndexPage.tsx` ~L280-300 | **G1** — inlined |
| QueueCard | new | **partial** | inlined in `EntitiesIndexPage.tsx` ~L350-380 | **G1** — inlined |
| Section | new | ✗ | not extracted; subsections use bare `<div>` + Tailwind | **G1** — not built |
| ActivitySpark | new | ✗ | `ActivityFeed.tsx` exists (timeline) but no 90-day spark bar chart | **G2** |
| BreadcrumbStrip | new | ✗ | `breadcrumbs.tsx` primitive exists, not wired into EntityDetailPage | **G3** |

**Stack delta vs v1:**
- No new npm dependencies. ✓
- Routes shipped (`router-config.tsx:91, 105-109`). ✓
- Fonts loaded at `frontend/index.html:7-9`. ✓ (v1 open question 11 resolved.)
- `/contacts → /entities?has=contact` redirect live. ✓

**Butlers touched (current):** `relationship` + `chronicler` as v1 anticipated. Plus `switchboard` (identity preamble re-pointed via `bu-akads`) and minor `home/modules/__init__.py` ripple from module re-registration. No phantom butlers. v1 Amendment 25 "module-vs-butler distinction doc" delivered (`bu-9vh0i`).

---

## 3. Backend contract delta — diff against v1 §3

### API delta — verdict-by-verdict

All 17 v1 rows shipped. The previously-`unclear` columns cascade was resolved client-side (no server endpoint). Detailed map in the "Backend contracts" section above. Every endpoint live at `roster/relationship/api/router.py`.

### Schema migration — current state

- `relationship.entity_facts` (triple store) — live, schema matches `openspec/specs/relationship-facts/spec.md` exactly. UNIQUE `(subject, predicate, object) WHERE validity='active'`; all four indexes pre-warmed.
- `relationship.entity_predicate_registry` — seeded with full contact + relational catalog + `dunbar_tier_override`.
- `relationship.credentials` — carve-out table for `secured=true` rows.
- `public.entities` — unchanged columns; `(type, last_seen DESC)` and `(last_seen DESC)` indexes added.
- `public.contacts` + `public.contact_info` — **legacy, in transition.** Reads cut over (`bu-akads`); dual-write active; write cut-over pending (`bu-k9ylx`); drop gated on 30-day verification report.

### §6b amendments — current state

| # | Amendment | Status |
|---|---|---|
| 1 | RDF triples supersede RFC 0004 §3 | ✓ shipped (drop pending) |
| 1.1 | Migration safety contract (10 + 2 ordered beads) | ⏳ 9/12 closed; 3 P0s open |
| 2 | RFC 0007 namespace fix | **drift G4** (see below) |
| 3 | RFC 0007 envelope conformance | spot-check pending (P2; not blocking) |
| 4 | `/api/search` reconciliation | ✓ shipped at `/entities/search` |
| 5 | Chronicler aggregator (MCP, no direct SQL) | ✓ shipped + guardrail test |
| 6 | Tier promotion is a fact | ✓ shipped |
| 7 | Editorial vs Workbench archetype | ✓ shipped via `bu-hm0oe` |
| 7.upd | Type-ratio carve-out citation | ✓ shipped via `bu-x0eej` |
| 8 | `<Page>` primitive conformance | ⏳ gated on `bu-yrqrk` cross-change archive |
| 9 | Token discipline | ✓ shipped via `bu-svmpx` |
| 10 | Vocabulary + persistence (`?mode=` + localStorage) | ✓ shipped |
| 11 | `v1.md` doctrine update | ⏳ `bu-u1mw8` (P3) |
| 12a | Writes owner authz | ✓ shipped via `bu-i99z3` |
| 12b | Reads owner authz | ✓ shipped |
| 12c | Deploy gate (DASHBOARD_API_KEY) | ✓ shipped via `bu-yv4da` |
| 13 | Reader inventory | ✓ shipped (`bu-akads` covered enumerated readers) |
| 14 | Dual-write reconciliation contract | ✓ shipped via `bu-75a3s` reconciler job |
| 15 | Transitive Finder enforcement (banned set) | ✓ shipped via `bu-wqmck` |
| 16 | `chronicler_list_episodes(entity_id=)` prereq | ✓ shipped via `bu-aqe7n` |
| 1.1.A.3.upd | Orphan resolver (Python script) | ✓ shipped via `bu-yxdzq` |

---

## 4. Guardrails — diff against v1 §4

LLM-cost feasibility: **unchanged.** No LLM-driven affordances introduced; all "voice gloss" copy uses `frontend/src/lib/entity-glosses.ts` canned-string switch on `(tier, state, category)` per Amendment 23. `GET /entities/search` is deterministic fuzzy per Amendment 24. Both anti-temptation guardrails embedded in spec.

Manifesto / identity preservation: **unchanged.** Chronicler boundary preserved by `/entities/{id}/activity` aggregator going through MCP. Guardrail test `bu-f5qcp` scans for `FROM chronicler.` / `JOIN chronicler.` SQL in the relationship router and fails on hit.

---

## 5. Gaps discovered (new in v2 — not in current bead graph)

Each gap below cites the evidence and proposes a next-step. `/project-direction` Phase 3 must either (a) confirm each is already covered by `bu-qz58b` / `bu-7jo43` / `bu-m8gb6.7` reconciliation-bead acceptance criteria, or (b) file a new bead under the appropriate epic with `discovered-from:` link to this brief.

### G1 — Reusable Row / StatePill / QueueCard / Section primitives not extracted

**Evidence:** Row (~L400-450), StatePill (~L280-300), QueueCard (~L350-380) inlined in `EntitiesIndexPage.tsx`. No `Section.tsx` primitive exists; subpages use bare `<div>` + Tailwind. v1 §2 classified all four as `new` with effort M expecting standalone reusability.

**Spec consequence:** Hop / Columns / Concentration pages currently duplicate the visual pattern by hand. Future redesigns reusing Dispatch will hit the same cost.

**Next-step:** New bead under `bu-ao6uh` or as a `bu-ao6uh` follow-up — extract Section, Row, StatePill, QueueCard into `frontend/src/components/ui/`. Effort S (post-hoc extraction). Priority P2.

### G2 — ActivitySpark sparkline missing from entity detail

**Evidence:** `ActivityFeed.tsx` provides the timeline list (`bu-ihiw4`) but no 360×56px 90-day bar chart. v1 §2 classified ActivitySpark as `new` effort M (recharts BarChart, monochrome).

**Spec consequence:** v1 §0 design intent "every fact carries provenance; surface them in Editorial" is met for columnar provenance in Workbench but not visually for historical density.

**Next-step:** Either (a) file new bead under `bu-ao6uh` for ActivitySpark with `discovered-from:` this brief; or (b) formally descope to a v3 sweep. Priority P3. Recommendation: descope — the timeline list already serves the read use case; the spark is decoration.

### G3 — BreadcrumbStrip not wired to entity detail

**Evidence:** `frontend/src/components/ui/breadcrumbs.tsx` primitive exists. EntityDetailPage does not render a breadcrumb. v1 §2 classified as `new` effort M.

**Spec consequence:** Re-centring from Hop/Columns back to Index loses navigational context. Minor UX gap.

**Next-step:** New bead under `bu-ao6uh` follow-ups — wire breadcrumbs into EntityDetailPage. Effort S. Priority P3.

### G4 — API namespace drift: `/api/butlers/relationship/entities/*` (v1 Amendment 2) vs `/api/relationship/entities/*` (shipped)

**Evidence:** v1 Amendment 2 binding text: "Live under `/api/butlers/relationship/entities/*` (consistent with `relationship-tabs-to-entities/spec.md:111-121` and `rfcs/0007:31` auto-discovery prefix)". Shipped at `router.py:127` with `prefix="/api/relationship"`. Contact endpoints in `dashboard-relationship/spec.md` still cite `/api/butlers/relationship/contacts/:id`. The active sibling change `decommission-contact-detail-page` also uses `/api/butlers/relationship/*`. Two prefixes coexist in the same butler.

**Spec consequence:** Either (a) the RFC 0007 auto-discovery prefix changed from `/api/butlers/relationship/` to `/api/relationship/` at some point and Amendment 2 + dashboard-relationship spec are stale, or (b) the entity surface deliberately ships at a shorter prefix and the spec was never reconciled.

**Next-step:** P1 — verification bead under `bu-ao6uh` reconciliation. Investigate: (i) confirm shipped prefix is intentional, (ii) update v1 brief Amendment 2 citation, (iii) update `dashboard-relationship/spec.md` contact endpoints if RFC 0007 prefix actually changed, OR (iv) rename the entity router to align with sibling contact prefix.

### G5 — Frontend canned-gloss enforcement test missing

**Evidence:** v1 §5 Anti-temptation guardrail 1 mandates canned-gloss enforcement. Frontend implementation lives at `frontend/src/lib/entity-glosses.ts` (shipped via `bu-wi06b`) but no automated test scans Editorial detail components for LLM API calls or template literal interpolation that could carry user-injected content into an LLM. Backend transitive Finder enforcement (`bu-wqmck`) covers the search-side guardrail but not the gloss-side.

**Spec consequence:** An implementer reaching for an LLM-summarized voice line in a future PR will not fail any test. v1's $0/user/day cost math depends on this invariant.

**Next-step:** P2 — new bead under `bu-ao6uh` follow-ups: `test_entity_glosses_no_llm.spec.ts` (frontend test) that statically scans `EntityDetailView` and `entity-glosses.ts` for imports from `anthropic` / `openai` / `cohere` and for `fetch`/`axios` calls to non-localhost. Mirror Amendment 15's banned-set list.

### G6 — Spec-to-code reconciliation reports not yet written

**Evidence:** `bu-qz58b` requires `docs/reports/entity-redesign-phase-2.md` and `bu-7jo43` requires `docs/reports/entity-redesign-reconciliation-migration.md`. Neither file exists. Both beads are gated on prerequisite work (migration P0s + cross-change archive gate). They are the canonical gap-finder beads from v1 Phase 3.

**Spec consequence:** Until these reports run, gen-2 drift (any divergence introduced since v1) cannot be authoritatively claimed empty. v2 brief is best-effort; reconciliation bead output is authoritative.

**Next-step:** No new bead. These already exist. Note: when `bu-7jo43` unblocks (post-migration drop), it should ingest G1-G5 above as candidate findings.

---

## 6. Open questions from v1 — current state

| # | v1 open question | Resolved? | Notes |
|---|---|---|---|
| 1 | Social Map untouched / refresh as separate work | ✓ | SocialMapView extracted (`bu-zvtxh`); Dunbar circles unchanged. |
| 2 | `?mode=workbench` vs icon-button toggle | ✓ | `?mode=workbench` shipped (Amendment 10 + `bu-monvg`). |
| 3 | Bulk merge UX | ✓ | Confirmed out of scope per v1 §0; no follow-up filed. |
| 4 | BreadcrumbStrip pattern | ✗ | **G3** — primitive exists, not wired. |
| 5 | Social-map jsx missing | ✓ | Existing `SocialMapPage` preserved. |
| 6 | KbMono shape | ✓ | Shipped. |
| 7 | Hop predicate filter with 0/1 predicates | ✓ | Implemented in `HopPage`. |
| 8 | Concentration hardcoded vs general predicates | ✓ | Tabs flip per predicate; generalized via predicate_registry. |
| 9 | Orbit variant | ✓ | Not shipped — explicitly not a default page per v1. |
| 10 | "Unverified" derived state overlap | ✓ | Queue treats `unidentified` and `unverified` as distinct. |
| 11 | Font loading | ✓ | Loaded at `frontend/index.html:7-9`. |
| 12 | Token namespace coexistence | ✓ | No conflicts surfaced. |
| 13 | SocialMapView refactor for nested-route composition | ✓ | `bu-zvtxh`. |
| 14 | Entity-first reordering of CommandPalette results | ✓ | Shipped via `bu-xfjwk`. |
| 15 | `/entities/:id/columns?path=` ship vs client-side | ✓ | **Client-side.** No server endpoint. |
| 16 | Entity-level merge shape | ✓ | `bu-jp6r6` shipped `{entityA, entityB, keepAs}`. |
| 17 | `entity_info` vs `contact_facts` | ✓ | Resolved as `relationship.entity_facts` triple store. |
| 18 | Relations persisted vs derived | ✓ | Persisted as `object_kind='entity'` triples. |
| 19 | `entity_state_flags` separate vs `entities.metadata` | ✓ | Resolved as `relationship.entity_queue` (separate). |
| 20 | Contact predicate shape | ✓ | Triple `(subject=entity_id, predicate='has-X', object=literal)`. |
| 21 | Unidentified vs unverified | ✓ | Distinct. |
| 22 | Chronicler aggregator via MCP | ✓ | Enforced via `bu-f5qcp` guardrail test. |
| 23 | Canned-gloss enforcement in spec | ⏳ | Spec says it; test missing — **G5**. |
| 24 | Deterministic-Finder enforcement in spec | ✓ | Shipped via `bu-wqmck` transitive scan. |
| 25 | Module-vs-butler documentation drift | ✓ | `bu-9vh0i`. |

24/25 resolved. One pending (Q23 → G5).

---

## 7. Handoff to `/project-direction`

Concrete invocation:

```
/project-direction --focus=feature \
  --brief=docs/redesigns/2026-05-27-entity-brief-v2.md \
  --prior-brief=docs/redesigns/2026-05-17-entity-brief.md \
  --bundle=pr/overview/entity-redesign/ \
  --binding-design-language=pr/overview/entity-redesign/reference/DESIGN_LANGUAGE.md \
  --binding-design-intent=docs/redesigns/2026-05-17-entity-brief.md#0-design-intent \
  --mode=diff-reconcile \
  --red-flag-policy=descope-or-escalate
```

If `/project-direction` does not accept `--prior-brief` / `--mode=diff-reconcile` flags literally, paste this paragraph: "Run feature-evaluation reconciliation against the v2 diff brief at `docs/redesigns/2026-05-27-entity-brief-v2.md`. The prior v1 brief is at `docs/redesigns/2026-05-17-entity-brief.md`. v1's Section 0 design intent and Section 1 scope are binding and unchanged. v2 reports six gaps (G1-G6) — confirm each is either already covered by gen-1 reconciliation beads (`bu-qz58b` for backend, `bu-7jo43` for migration, `bu-m8gb6.7` for contact-detail decommission) or file new beads under the appropriate epic with `discovered-from:` link to this v2 brief. Do not duplicate existing in-flight beads. Apply `descope-or-escalate` policy to any red verdict surfaced during reconciliation."

**Carry-forward instructions:**

- v1 brief Section 0 + Section 1 remain binding.
- All six gaps (G1-G6) above are candidate work items. `/project-direction` Phase 3 must rule on each: covered-by-existing-bead OR new-bead-with-discovered-from.
- The three open P0s under migration epic `bu-uhjxr` (`bu-k9ylx`, `bu-hpv4u`, `bu-e2ja9`) drive the critical path; do not introduce new work that gates on them unless explicitly tagged.
- The two gen-1 reconciliation beads (`bu-qz58b`, `bu-7jo43`) are themselves gap-finders. They should ingest G1-G6 as candidate findings when they unblock.
- No new LLM features were introduced. Phase D guardrails from v1 stand unchanged.
- Phase G of the parent skill is already done — backend contracts epic (`bu-ao6uh`) and frontend integration epic (`bu-lh4ol`) exist with proper dependency wiring.
