# health redesign — integration brief

**Date:** 2026-06-20
**Version:** v1
**Bundle path:** `none` — no Claude Design bundle exists for health; originated from the top-level Dispatch design language (`pr/overview/DESIGN_LANGUAGE.md` + `pr/overview/dispatch-kit/` + shipped sibling `docs/redesigns/ingestion-design-language.md`).
**Mode:** fresh
**Phase D verdict:** `proceed-with-amendments` (overall GREEN, ~$1/month/owner recommended design)
**Prior brief (if any):** none

> **Origination note.** This is not a port of a Claude Design bundle — the `/health`
> surface had no redesign bundle. The brief was originated from the canonical Dispatch
> language and the *current live implementation*, which a maturity audit confirmed is
> **genuinely real** (full CRUD over the SPO fact store, not a skin). The redesign problem
> is therefore **information architecture + orphaned-but-built backend capability + insight
> surfacing**, not fake data.

---

## 0. Design intent

> Captured via Phase 0.5 (no `VISION.md`), confirmed by the user, and persisted to
> `.tmp/redesign-health/section0.md`. **Binding — every spec section, component decision, and
> backend contract must trace back to it.** Phase D treats violations as automatic red.

### Problem being solved

The health surface is six disconnected data-entry pages (measurements, medications, conditions,
symptoms, meals, research) with **no `/health` landing** — the bare URL has nowhere to go. This
contradicts the manifesto's core promise: health is *"a story told over weeks, months, and years…
pieces only reveal their meaning when held together."* Today nothing holds them together — no place
the butler *announces* "here's how you've been," no continuity, no surfaced patterns. The redesign
gives the health butler a **Voice surface**: a `/health` briefing that speaks the story, with the
six pages becoming the evidence behind it.

### Primary audience

The owner (single-user, `roles=['owner']`), ranked #1 and effectively only. A private health record
read by the person it's about — not a clinician portal, not multi-patient. Design for honest
self-reflection, not compliance reporting.

### Deliberate design moves

1. **A `/health` Overview in the Voice** — two-column editorial (`1.4fr / 1fr`): left = a serif
   briefing + KPI strip; right = quiet attention index. The manifesto's "Insight" pillar as a page.
2. **Continuity over forms** — every sub-page reframed from "data entry" to "trajectory":
   measurements lead with the trend rule-list, not the input box. Type and rules carry hierarchy; no cards.
3. **State color only when health demands it** — red/amber/green reserved for genuine signal (high
   symptom severity, adherence falling, measurement out of range), never decoration.
4. **The butler hue (`--category-4` teal) lives only on the health letter-mark** — not "medical blue
   everywhere"; one square.
5. **Honesty about its own process** — trend narration and insights carry the status pill
   (`llm · cached` / `templated`) so the owner always knows whether a line was computed or model-written.
6. **LLM-driven proactive insight engine (FIRST-CLASS).** The Overview surfaces model-detected
   patterns: cross-signal correlations (symptom clusters vs Home Assistant bedroom temp / air quality,
   adherence dips preceding symptom flares, slow measurement drift), rendered in the Voice with the
   honesty pill. Cost is sized at owner cadence, not rejected; the cost-safe design runs correlation in
   the scheduled `insight-scan` job and has the Overview merely *read* candidates.

### What we are deliberately NOT doing

- **Not a diagnosis engine** ("a companion, not a doctor") — no risk scores, no "you may have X", no
  medical advice. Make the invisible visible; do not interpret clinically.
- **No celebration / gamification** — no streak confetti, no "Nice work!", no green-check dopamine.
  Adherence is stated, not rewarded.
- **No empty-state decoration** — a day with no symptoms is one serif-italic line.
- **No real-time / wearable-live dashboard** — continuity is weeks-and-months; wearable ingestion via
  google_health stays a background collector.
- **No fabricated/placeholder health data** — ever; every figure traces to a real backend read.

### Success criteria

- Owner lands on `/health` and within ~5s knows the single most important thing about their health
  right now, in a sentence.
- From the Overview, the owner reaches any concerning signal (missed doses, severe symptom, drifting
  measurement) in one click.
- Every sub-page leads with *what changed over time*, not an empty input form.
- No control on the surface is dead, and no number is fake.
- The page reads calm at 3am: no alarm styling unless a real health state demands it.

---

## 1. Scope

Seven surfaces: a **new `/health` Overview** (landing, absent today) + six **reframed sub-pages**
(measurements, medications, conditions, symptoms, meals, research). Design language = the canonical
**Dispatch** language (`pr/overview/DESIGN_LANGUAGE.md`); all required primitives and tokens already
exist in `frontend/`. Integration target = the live Butlers dashboard SPA (`frontend/src/`), served at
`…/butlers-dev/health`. The change is **additive** (new index route + re-skin); nothing outside
`/health/*` is touched.

### Sub-pages (Phase A — proposed set)

| Route | Page | Disposition | Canonical Dispatch primitives |
|---|---|---|---|
| `/health` | **Overview** (NEW landing) | new | two-column editorial; Eyebrow/DateEyebrow, Display/Headline, Voice/Elaboration, BriefingStatus pill, KpiStrip (4-cell), AttentionList, Section (right index), ButlerMark (`--category-4`) |
| `/health/measurements` | Measurements | refactor | Eyebrow + Display; KPI strip; MeasurementChart (re-skinned to tokens); trend rule-list (mono-time / status-dot / value / →) |
| `/health/medications` | Medications | refactor | rule-list (status-dot / med+dose / adherence delta / →); right "Next doses" NextList |
| `/health/conditions` | Conditions | refactor | rule-list (status-dot / condition+status / onset / →) |
| `/health/symptoms` | Symptoms | refactor | rule-list (6px **severity glyph** / symptom+freq / severity / →) |
| `/health/meals` | Meals | refactor | rule-list (mono-time / meal+nutrition / delta / →); right "Daily totals" mini-KPI |
| `/health/research` | Research | refactor | rule-list (time / topic+source-tag / excerpt / →); right "Topics" index |

### Design tokens (binding) — Phase A

All Dispatch surface, state, butler-hue, typography, and semantic tokens are **present** in
`frontend/src/index.css:1-290`. **No missing tokens; no token additions needed.** Health hue =
`--category-4` (teal), letter-mark only. Health-specific `--severity-low/medium/high`
(green/amber/red) already exist and are compatible with Dispatch state-color rules. Type families:
`--font-sans` Inter Tight (UI), `--font-serif` Source Serif 4 (Voice), `--font-mono` JetBrains Mono
(numerals/eyebrows). Full token table in `.tmp/redesign-health/` Phase A report; surface tokens
(`--bg/-elev/-deep`, `--fg/--mfg/--dim`, `--border/-soft/-strong`), state (`--red/--amber/--green`),
and category hues all verified against code.

---

## 2. Component impact

### Classification table (Phase B)

| Component | Current path | Classification | Effort | Notes |
|---|---|---|---|---|
| Eyebrow | `components/ui/Eyebrow.tsx` | reuse | S | page kicker |
| Display | `components/ui/Display.tsx` | reuse | S | replaces `text-3xl font-bold` headlines on all 7 pages |
| StateDot | `components/ui/StateDot.tsx` | reuse | S | row status marker |
| ButlerMark | `components/ui/ButlerMark.tsx` | reuse | S | health hue/letter-mark |
| Voice | `components/ui/Voice.tsx` | reuse | S | Overview elaboration prose |
| Page (archetype) | `components/ui/page.tsx:50` | reuse | S | `editorial` for Overview; `list` for sub-pages |
| KpiStrip | `components/overview/KpiStrip.tsx` | reuse | S | Overview KPI cells |
| AttentionList | `components/overview/AttentionList.tsx` | reuse | M | Overview attention; needs health item-shape mapping |
| DateEyebrow / BriefingStatus / Headline / Elaboration / NextList / Section | `components/overview/*.tsx` | reuse | S–M | compose Overview as `DashboardPage.tsx` does the global one |
| RuntimeSummaryKpi | `components/overview/RuntimeSummaryKpi.tsx` | reuse | S | optional cell |
| **/health Overview page** | **new** `pages/HealthOverviewPage.tsx` | **new** | **L** | net-new landing; composes the above |
| ConditionTracker | `components/health/ConditionTracker.tsx:217` | adapt | M | Table→Dispatch rows; status Badge→StateDot; keep Dialog/Form |
| SymptomTracker | `components/health/SymptomTracker.tsx:291` | adapt | M | Table→rows; severity bar → row mark; keep filters/Dialog |
| MealTracker | `components/health/MealTracker.tsx:291` | adapt | M | Table→rows; type Badge→mark; keep filters + MealForm |
| ResearchTracker | `components/health/ResearchTracker.tsx:308` | adapt | M | Table→rows w/ expand; keep search + tag filters + Form |
| MeasurementTracker | `components/health/MeasurementTracker.tsx` | adapt | M | same Table-of-rows pattern; verify mark/meta shape |
| MeasurementChart | `components/health/MeasurementChart.tsx:110` | adapt | M | re-skin palette to health-hue tokens (lines 44-47 hardcoded hex); Badge tabs → Dispatch; chart logic reusable |
| **MedicationTracker** | `components/health/MedicationTracker.tsx:312` | **replace** | **L** | card grid + nested expandable DoseLog table; rebuild as Dispatch rows, dose history → detail affordance |
| *Form.tsx (6 forms) | `components/health/*Form.tsx` | reuse | S | create/edit forms inside Dialogs; re-skin only if they use Card |
| shadcn `Card` shells (6 pages) | `pages/*Page.tsx` | **replace** | S | remove Card/CardHeader/CardContent; replace with `<Page archetype="list">` + Eyebrow/Display |
| Health briefing/insight data hook | **new** `hooks/use-health-briefing.ts` | **new** | L | powers Overview headline/elaboration/attention |

### Stack delta (Phase B)

- **No new npm dependencies.** recharts, TanStack Query, react-router v7, and all Dispatch primitives
  already ship. **No token additions** (all in `index.css:1-290`).
- **Routing: add `/health` index route** — one entry in `router-config.tsx` (alongside `:97-102`).
  Repoint nav `nav-config.ts:84` from `/health/measurements` → `/health`. **No redirect breakage** —
  the 6 sub-paths are unchanged; only a new parent is added; bookmarks keep working. Consider a
  collapsible sub-nav group mirroring the Ingestion sub-route pattern (`router-config.tsx:158-174`).
- **Overview data path = the one real fork** (decided in §3/§4):
  1. **Client-composed** (cheap, no backend, no LLM): assemble from existing hooks, deterministic
     KPIs + attention list, no Voice prose. Effort M.
  2. **Server-composed briefing/insight** (manifesto "Insight" pillar, costs): new
     `GET /api/health/briefing` mirroring `GET /api/dashboard/briefing` + `use-health-briefing` hook.
     Effort L. **This is the LLM insight engine.**
- **No state-management additions** — TanStack Query covers it; `["health-*"]` keys already namespaced.

### Butlers touched (Phase B → scopes the manifesto pass)

| Butler | Why touched | Manifesto risk |
|---|---|---|
| **health** | Primary; all 7 surfaces; insight engine realizes the "Insight" pillar | **Y** |
| chronicler | Meals dual-write → `chronicler.point_events` `eating_event` lane; Overview reads only | Y (low, read-only) |
| memory | Meals/nutrition facts (`health` scope) the Overview may read | Y (low, read-only) |
| calendar | Only if Overview pulls appointments (net-new cross-butler read) | Y (conditional) |
| home_assistant | Data source for the insight engine (HA sensors); reached via MCP only | N |

---

## 3. Backend contract delta

> **Headline (Phase C):** the entire deterministic Overview + every reframed sub-page is **reads over
> data that already has endpoints** — three of them orphaned-but-built. Only dashboard dose-logging
> needs a new write route; only the insight feed + LLM briefing/correlation engine need genuinely new
> backend. **No new table or column is required for anything.**

### Affordance inventory (Phase C)

| Affordance | Endpoint | Exists? | Evidence | Gap |
|---|---|---|---|---|
| Overview KPI strip (latest weight/HR/blood_sugar/temp/BP) | `GET /api/health/measurements/latest?types=` | Yes (router.py:1352) | live-endpoint | **reuse** |
| KPI sleep / wellness (sleep, resting HR, HRV, SpO2, steps) | `GET /measurements/sleep/latest` + `/measurements/latest?types=` | Yes (router.py:1439,1352) | live-endpoint | **reuse** |
| Data-freshness chips | `GET /measurements/sources` | Yes (router.py:1493) | live-endpoint | **reuse (orphaned)** |
| Trend sparklines / narration | `GET /measurements/trend?type=&window_days=&bucket=` | Yes (router.py:1549) | live-endpoint | **wire-orphaned** (built, zero FE consumer) |
| Active-meds / recent conditions·symptoms·meals cards | `GET /medications?active=true`, `/conditions`, `/symptoms`, `/meals` | Yes (router.py:320/574/737/957) | live-endpoint | **reuse** |
| Dashboard dose-logging | `POST /medications/{id}/doses` | No (only GET at :396) | live-endpoint (gap) | **new-route (write)** |
| Proper adherence % (frequency-expected) | `GET /medications/{id}/adherence` | No (client computes naive ratio) | live-endpoint (gap) | **new-route (read)** |
| Nutrition rollup (calories/macros over range) | `GET /nutrition/summary?start=&end=` | No (MCP `nutrition_summary` only) | live-endpoint (gap) | **new-route (read)** |
| Attention / insight list | `GET /api/health/insights` **or** shared switchboard reader | No reader anywhere | live-endpoint (gap) + spec | **new-route (read; cross-schema)** |
| Voice briefing headline + elaboration | `GET /api/health/briefing` | No (only global `/api/dashboard/briefing`) | analog + spec | **new-LLM-composer** |
| Cross-signal correlations (HA env ↔ sleep/symptoms; adherence dip → flare; drift) | fold into insight job → `GET /api/health/insights` | No route joins home/HA with health.facts | spec | **new-LLM-composer (cross-butler MCP)** |

### API delta — new/changed routes

| Path | Method | Status | Request | Response | Evidence |
|---|---|---|---|---|---|
| `/api/health/measurements/{latest,sleep/latest,sources,trend}` | GET | exists / **wire-orphaned** | per query | existing models (models.py:334/351/374/401) | live-endpoint |
| `/api/health/medications/{id}/doses` | **POST** | **new-route** | `{taken_at?, skipped?=false, notes?}` | `Dose` (models.py:111), 201 | live-endpoint (gap) |
| `/api/health/medications/{id}/adherence` | **GET** | **new-route** | `?window_days=30` | `{expected_doses, taken_doses, skipped_doses, adherence_rate}` | live-endpoint (gap) |
| `/api/health/nutrition/summary` | **GET** | **new-route** | `?start=&end=` | `{total_calories, total_protein_g, …, daily_avg, meal_count, days}` | live-endpoint (gap) |
| `/api/insights?butler=health` (switchboard) **or** `/api/health/insights` | **GET** | **new-route** | `?status=pending&limit=` | `{insights:[{id,category,priority,message,metadata,created_at,status,expires_at}]}` | live-endpoint (gap) + spec |
| `/api/health/briefing` | **GET** | **new-LLM-composer** | owner-only | `Briefing = {greet, headline, elaboration, source, state_class, generated_at}` | analog + spec |

No `fixture`-only rows → no `unclear` rows to resolve. (All evidence is `live-endpoint` or `spec`.)

### Schema migration impact (Phase C)

**No new tables and no new columns for any affordance.** All reads/writes target existing
`health.facts` (predicates `measurement_*`, `meal_*`, `medication`, `took_dose`, `symptom`,
`condition`). Dose-logging writes the same `took_dose` fact the MCP tool already writes — **no DDL**.
The insight feed reads existing `public.insight_candidates` (`core_010`). **One grant concern:**
non-switchboard butlers hold `INSERT` only on that table; `SELECT` is held by `butler_switchboard_rw`.
**Preferred resolution:** host the reader on the **switchboard** cross-cutting API (already has
`SELECT`) as `GET /api/insights?butler=health` — keeps schema isolation, no grant migration. Fallback:
a tiny idempotent grant-only migration for the health/dashboard role.

**Cross-butler red flag (schema isolation):** cross-signal correlation needs HA sensor data, which
lives in the **`home` butler**, not `health.facts`. The composer **must reach HA data via MCP/Switchboard,
never a direct `home.*` DB read.** This makes correlation an orchestrated LLM+MCP step, not a SQL join.

### Proposed backend epic — *Health Overview backend contract* (Phase C, ordered)

1. **[wire]** Wire the three orphaned read routes (`/measurements/trend`, `/latest`, `/sources`) — no BE change, FE consumption only. **S. No migration.**
2. **[new]** `POST /api/health/medications/{id}/doses` — dashboard dose-logging over `medication_log_dose`; add `DoseCreateRequest`; invalidate briefing cache. **S. No migration.**
3. **[new]** `GET /api/health/nutrition/summary` — thin route over `diet.nutrition_summary`; add response model. **S. No migration.**
4. **[new]** `GET /api/health/medications/{id}/adherence` — frequency-expected; lift `_frequency_to_doses_per_day` (health_jobs.py:83) into a shared helper. **M. No migration.**
5. **[new]** Insight reader — **prefer** shared `GET /api/insights?butler=health` on switchboard (has SELECT); else `GET /api/health/insights` + grant-only migration. **M.** Powers the deterministic attention list.
6. **[new-LLM]** `GET /api/health/briefing` — mirror `/api/dashboard/briefing`; reuse classify/fallback/lint/cache; templated-fallback default, LLM elaboration behind a cost flag. **L. No migration. Gated on Phase D.** *(Depends on #5.)*
7. **[new-LLM]** Cross-signal correlation **in the `insight-scan` job** (not on GET): extend `health_jobs.py` to fan out HA reads via MCP/Switchboard, emit correlation candidates → surfaced through #5. **XL. No migration; cross-butler MCP only. Highest cost/risk; deferrable.** *(Depends on #5 + `home` butler MCP.)*

**Dependency summary:** #1–#3 independent, ship first (zero-LLM Overview). #4 depends on the shared
frequency-helper. #6 depends on #5. #7 depends on #5 + `home` MCP. #6–#7 are the only LLM-cost items
and are individually deferrable behind the deterministic Overview (#1–#5).

---

## 4. Guardrails

### LLM-cost feasibility (Phase D)

Pricing source: `references/llm-pricing.md` (`last_verified: 2026-05`, <60 days → no refetch). Model:
health runs `codex (gpt-5.4-mini)`; costed conservatively at the **Haiku 4.x** row ($1/$5 per MTok) as
the closest listed tier, with a **Sonnet 4.x** ($3/$15) sensitivity. v1 `users=1`.

| Affordance | Cadence | tok in/out | calls/day | $/mo (Haiku) | Verdict |
|---|---|---|---|---|---|
| `GET /health/briefing` (a) live-on-load + 5-min TTL | ~6 distinct 5-min windows | 3,000/250 | ~6 | **$0.77** | **GREEN** |
| `GET /health/briefing` (b) templated-only (flag off) | deterministic | 0/0 | 0 | **$0** | **GREEN** |
| briefing **Sonnet** sensitivity | same as (a) | 3,000/250 | ~6 | $2.30 | **YELLOW** (keep cache) |
| Correlation (a) **live-on-GET fan-out** (naive) | every Overview load | ~15,000/800 | ~10 | $5.70 (Sonnet $17.1) | **RED** |
| Correlation (b) **scheduled `insight_scan` job** (recommended) | 1 run/job; Overview reads candidates | ~15,000/800 | 1/day costed | **$0.57 daily / ~$0.02 monthly** | **GREEN** |

**Bottom line per owner/month:** **recommended ≈ $1** (briefing cached Haiku + correlation in job) vs
**naive ≈ $21** (uncached Sonnet briefing + live-on-GET Sonnet correlation) — ~20×. The recommended
correlation design is **~10× cheaper at daily / ~300× at the live monthly cron**, and removes the only
RED. `users=100` sensitivity: recommended stays GREEN (the job is per-deployment, not per-pageview).

#### Red verdicts

- **Cross-signal correlation as live-on-GET fan-out → RED.** (a) Sonnet $0.57/day/owner exceeds the
  $0.50 red line; (b) no stable cache (`llm-pricing.md:73`); (c) violates Section 0's "No real-time
  dashboard" rejection; (d) adds cross-butler MCP latency to a ~5s page.

#### Recommended de-scopes before spec phase

1. **De-scope live-on-GET correlation → the scheduled `insight_scan` job** (Phase C design #7): the
   Overview reads `public.insight_candidates` via the insight reader (#5). **Cron-cadence sub-decision:**
   `roster/health/butler.toml:58` is `0 7 15 * *` (5-field = **monthly**, the 15th), not daily. Decide in
   spec whether monthly correlation freshness meets the Insight pillar; weekly/daily stays GREEN on cost.
2. **Briefing tier-bump guard (gate, not kill):** if voice-lint quality forces Sonnet, keep the 5-min
   TTL cache + templated-only default; do not enable per-load LLM elaboration without the cache.
3. **Non-diagnostic voice-lint is a hard gate** (see manifesto pass) — a **spec acceptance criterion**,
   not a nicety.

### Manifesto / identity preservation (Phase D)

| Feature | Pillar / rule | Compliant? | Required guardrail |
|---|---|---|---|
| Voice briefing | Insight; "Companion not a Doctor"; design §8 | Yes, conditional | voice-lint (below); "Everything is in hand." once when quiet |
| Insight correlation copy | Insight; "not interpret clinically" | Yes, conditional | **co-occurrence framing only**, never causal |
| Adherence % | Insight; §8 "no celebration" | Yes | state the fact ("12 of 14 doses taken"), never reward |
| Symptom severity | "log… severity… patterns emerge" | Yes | owner's own severity value + trend; no added clinical adjectives |
| Measurement "drifting upward" | verbatim manifesto language (`:28`) | Yes | allowed as worded; banned: disease labels ("toward hypertension") |
| KPI / trend sparklines | Continuity | Yes | deterministic; no LLM; no honesty pill needed |
| Honesty pill | Agency "only honesty"; Section 0 move #5 | **Required** | every model-written line carries it |
| Chronicler meal-lane | Meal Write Path | Yes — read-only | Overview reads `nutrition_summary`; does not touch `MealsAdapter` |
| Memory `facts` | Continuity; `health` scope | Yes — read-only | reads existing facts; no new predicate, no write |

**Voice-lint rules (extend the global `voice_lint_passes`; on failure → templated fallback, never raise):**
1. **Diagnosis/advice blocklist:** `diagnos*`, "you (may|might|could) have", "risk of", "symptom of",
   "consistent with", "indicates", "should see a doctor", disease name adjacent to "you", any treatment advice.
2. **Causation guard (correlation):** reject causal connectives ("because/causing/due to/leads to/results
   in"); allow temporal co-occurrence only ("on nights the bedroom was warm, sleep ran shorter").
3. **No celebration/judgment:** reject `!`, first person, praise tokens, green-check/streak language.
4. **Tense:** past for events, present for state; reject future ("will","going to") — prediction is diagnosis-adjacent.
5. **Reference-range, not verdict:** pair a measurement with the *owner's own* stored range; reject clinical adjectives ("elevated","dangerously high").
6. **"the" over "your"** where it reads.

#### Drift write-ups / Recommended manifesto updates

None. Chronicler + memory contracts are read-only-brushed, not reframed (Phase C: no new tables/columns,
no new predicates). No manifesto update required.

### Intent compliance (Phase D Pass 3)

All **6 deliberate moves** honored by the recommended plan (✅ each). Of the **5 rejections**, the only
leak is "No real-time dashboard," which appears **only** in the naive live-on-GET correlation design —
already killed in Pass 1 and de-scoped above. The recommended design (deterministic Overview + cached
briefing + correlation in the scheduled job + enforced non-diagnostic voice-lint) has **no
intent-conflict-red feature**. The voice-lint non-diagnostic gate is the load-bearing guardrail keeping
the FIRST-CLASS insight engine on the right side of "a companion, not a doctor"; it must enter the spec
as an acceptance criterion. **No item needs escalation to the user.**

---

## 5. Open questions

Consolidated from Phases A–D. Items for `/project-direction` Phase 1 (doctrine) + Phase 2 (spec).

1. **(D, spec decision)** Insight-scan cron is **monthly** (`butler.toml:58` `0 7 15 * *`), not daily.
   Does monthly correlation freshness meet the Insight pillar, or change to weekly/daily (cost stays GREEN)?
2. **(C, architecture)** Insight reader host: shared switchboard `GET /api/insights?butler=health`
   (preferred — has SELECT, no migration) vs `GET /api/health/insights` + grant-only migration. Pick one.
3. **(B/C)** Overview data path for v1: ship the **deterministic** Overview (#1–#5, zero LLM) first and
   add the briefing (#6) + correlation (#7) as a follow-on, or build the LLM layer in the same milestone?
4. **(A/D)** Briefing LLM elaboration default: templated-only with LLM behind a flag (GREEN $0), or
   live-on-load with 5-min cache (GREEN $0.77/mo)? Confirm the default.
5. **(A, honesty mismatch)** `MeasurementChart.tsx:39-41` exposes type tabs `glucose`/`sleep`/`oxygen`
   that the create form can't produce (uses `blood_sugar`; wellness uses `spo2`/`steps`) → perpetual "No
   data". Rename/replace or source from wellness predicates as part of the chart adapt.
6. **(A)** KPI strip: which 4 KPIs? (vitals: BP/weight/glucose/temp vs mixed: BP/weight/adherence%/symptoms).
7. **(B, Risk 1)** MedicationTracker is **replace** (card-grid + nested DoseLog), the highest-churn item;
   confirm dose history → detail/expand affordance and preserve Active/All filter + adherence semantics.
8. **(B, Risk 3)** MeasurementChart hardcoded hex (`#3b82f6/#f43f5e`, lines 44-47) → token-driven
   (`--category-4`); recharts needs literal colors, so a token→hex bridge (read computed CSS var), not a class.
9. **(A)** HA environmental sensors have **zero dashboard surface** today; the manifesto's "environmental
   health correlation" is the insight engine's job — confirm no separate HA panel is in v1 scope.
10. **(A/B)** Nav: `/health` index with flat children vs collapsible group vs in-page sub-nav (Ingestion
    sub-route precedent `router-config.tsx:158-174`).
11. **(B, Risk 6)** `pages/HealthPages.view-only.test.tsx` asserts current behavior — update/extend, don't
    silently delete assertions, during the refactor.
12. **(C)** Adherence helper: lift `_frequency_to_doses_per_day` (health_jobs.py:83) into a shared module so
    the route and the job agree on the denominator.

---

## 6. Handoff to `/project-direction`

This brief is the input to a `/project-direction` run with **feature evaluation focus** scoped to `health`.

```
/project-direction --focus=feature \
  --brief=docs/redesigns/2026-06-20-health-brief.md \
  --binding-design-language=pr/overview/DESIGN_LANGUAGE.md \
  --binding-design-intent=docs/redesigns/2026-06-20-health-brief.md#0-design-intent \
  --red-flag-policy=descope-or-escalate
```

(No `--bundle` — health has no Claude Design bundle; the binding design language is the top-level Dispatch doc.)

Carry-forward instructions:

- `pr/overview/DESIGN_LANGUAGE.md` is **binding**. Every spec section must preserve it (surfaces-not-cards,
  Display-500 not bold, hue on letter-mark only, state color only when state demands, one commit button,
  serif-italic empty states).
- Section 0 of this brief is **binding**. Spec drift away from intent fails reconciliation.
- The one RED LLM feature (live-on-GET correlation) is **de-scoped** to the scheduled `insight_scan` job
  (backend epic #7); spec must not re-introduce live-on-GET correlation.
- The **non-diagnostic voice-lint rules (§4)** must be encoded as spec **acceptance criteria** for the
  briefing + insight engine.
- Ship order: backend epic #1–#5 deliver a fully deterministic, zero-LLM Overview that satisfies the
  success criteria; #6–#7 (LLM briefing + correlation) are a deferrable follow-on milestone.
- After `/project-direction` Phase 3 produces the beads graph, Phase G of `butlers-redesign-prompt` will
  split out the **backend epic** (`health redesign — backend contracts`) per §3 and wire the frontend epic
  `blocked-by` it.
