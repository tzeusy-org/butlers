# Direction Report — Redesign Settings: Dispatch Console

**Status:** Phase 3 complete (planning artifacts shipped). Implementation handoff to `beads-coordinator`.

**Date:** 2026-05-16

**Author:** /project-direction flow (Claude)

**Epic:** `bu-do5q0`

**OpenSpec change:** `openspec/changes/redesign-settings-dispatch-console/`

**Source assets:** `pr/overview/settings-refactor/` (PLAN.md, DESIGN_LANGUAGE.md, settings-redesign.jsx, settings-expanded.jsx, primitives.jsx)

---

## 1. The project's real direction

Butlers is a personal AI agent framework where each butler is a long-running MCP server daemon. The `/settings` dashboard page is its **operator control plane** — the place a human operator goes to see what the system is configured to do, what it is spending, who can do what, and what is demanding attention. Today's `/settings` is a SaaS-style preferences stack (cards for blob storage, model catalog, QA settings, general settings, theme, refresh defaults, command palette) that conflates per-user setup (`/secrets` OAuth) with system administration (model catalog, audit) and never says, in one screen, "the system is in this state."

The redesign assets in `pr/overview/settings-refactor/` propose a Dispatch-language Console with three sub-routes, a server-side audit log, a permissions matrix, a hand-rolled SVG spend forecast, and a webhook registry — plus fold-ins for `/approvals`, `/butlers/{name}`, and `/memory` that absorb design pieces currently mis-placed on `/settings`. The owner has selected direction B (the Console) and approved full scope including permissions matrix, butler-detail/memory fold-ins, and the `/api/actions` → `/api/approvals` rename.

The direction is a clean break: replace the monolithic `/settings` page, audit every mutation, and align the operator surface with the Dispatch language already shipped on `/overview`, `/butlers`, and `/qa`.

## 2. What we should work on next

Foundations first. The OpenSpec change defines a strict gate ordering (design.md §D14a):

```
Phase 0 — Doctrine updates           [bu-9gzel]   P1
Phase 1a — Audit log primitive       [bu-h31sp]   P0
Phase 1b — complexity_tier + routing [bu-5tnp0]   P0
   │
   ├─→ Phase 2 — /settings/models    [bu-q2nz3]   P1
   ├─→ Phase 3 — /settings/spend     [bu-dvb7i]   P1
   ├─→ Phase 4 — /settings/permissions [bu-vz6pi]  P1
   ├─→ Phase 6 — /approvals          [bu-5xiu9]   P1
   ├─→ Phase 7 — /butlers/{name}     [bu-g4d49]   P1
   └─→ Phase 8 — /memory             [bu-1kzbg]   P1
           │
           └─→ Phase 5 — Console     [bu-ju4kh]   P1
                   │
                   └─→ Phase 11 — Cleanup [bu-ufqyb] P2
                           │
                           └─→ Phase 12 — Report [bu-jcsso] P1
```

Ready (no blockers): `bu-9gzel` (doctrine), `bu-h31sp` (audit), `bu-5tnp0` (catalog). Once those land, **six beads can run in parallel** (Phases 2/3/4/6/7/8) — no shared file conflicts. Phase 5 (Console) is the single fan-in; Cleanup + Report follow.

`beads-coordinator` should start by dispatching the three foundation beads (Phase 0 + Phase 1a + Phase 1b) and then fan out.

## 3. What we should stop pretending we can do

- **`/api/audit` (PLAN.md's prefix).** The existing router is mounted at `/api/audit-log`. The OpenSpec change keeps that prefix; PLAN.md's `/api/audit` is rejected to avoid an in-flight rename of an already-shipped surface. (R2 found this drift; user decision: keep `/api/audit-log`.)
- **Reusing `complexity_tier` for catalog grouping without remap.** The CHECK constraint today is `trivial|medium|high|extra_high|discretion|self_healing` (task-complexity). PLAN.md needs `reasoning|workhorse|cheap|specialty|local|legacy` (model category). The decision is to rename + remap the column values in a single migration (design.md §D4 has the table). Don't pretend the existing values are sufficient.
- **A new `costs.py` and a separate `spend.py`.** `costs.py` already serves `/api/costs/summary`, `/api/costs/daily`, `/api/costs/top-sessions`, `/api/costs/by-schedule`. The decision is to rename the file to `spend.py` and migrate paths to `/api/spend/summary`, `/api/spend/daily`, `/api/spend/top-sessions`, `/api/spend/by-schedule`; do not run two parallel namespaces.
- **The three approval hooks PLAN.md anticipated.** R2 confirmed `use-approval-actions.ts`, `use-autonomy-suggestions.ts`, `use-approval-rules.ts` **do not exist**. The single existing hook is `use-approvals.ts`; refactor that one. Don't write deletion tasks for files that aren't there.
- **A separate `PUT /api/models/{id}/role` endpoint.** PLAN.md mentions it but the existing `PUT /api/settings/models/{id}` already accepts the relevant edits. Dropped; documented as out-of-scope in proposal.md.
- **A density toggle, theming knobs, or onboarding tooltips.** Dispatch is dark-canonical with paper-warm light variant. No other themes. No tour overlays.
- **A graphical "wiring diagram" of butlers ↔ models ↔ permissions.** Tempting but SaaS-coded. The matrix is enough.

## 4. Doctrine updates that ship with this change

| File | Section | Change |
|---|---|---|
| `about/heart-and-soul/design-language.md` | `### Butler hue scope` (~L837) and `### Attention list` (~L820) | Add the 4–7% alpha attention-tint + 2px left rail as the single state-color-on-background exception. Without this amendment, the existing rule rejects the pattern as a violation. |
| `about/heart-and-soul/v1.md` | "What v1 Ships → Dashboard" | List `/settings/models`, `/settings/spend`, `/settings/permissions`, `dashboard-audit-log` infra, webhooks/data-ops as v1-shipped. |
| `frontend/src/index.css` | OKLCH palette section | Add `.attention-row[data-tone=red|amber]` CSS block (canonical body in design.md §D2). |

These ship in the **same PR as `bu-9gzel`** (Phase 0). No consumer code yet — they unblock everything.

## 5. The beads graph (why it's coherent with doctrine/spec)

**12 child beads** + epic. Each maps to a specific Phase in tasks.md and an OpenSpec scenario in `specs/*/spec.md`. Every mutation bead asserts `audit.append()`. Every page bead asserts Dispatch language and a Playwright e2e. The cleanup bead is gated behind ALL replacement pages landing; the report bead is gated behind cleanup.

Coherence with doctrine: every spec.md carries a `## Source References` section citing `about/heart-and-soul/` and PLAN.md. The doctrine amendments in Phase 0 explicitly permit the only new design exception (attention-tint). v1.md is updated to acknowledge the new capabilities, removing the scope drift that R1 flagged.

Coherence with implementation: R2's drift findings (audit prefix, complexity_tier values, costs.py existence, missing model_catalog columns, non-existent approval hooks, GeneralSettingsCard location) are all reflected in the tasks list with explicit fix-up instructions. R3 confirmed PASS for internal consistency (audit invariant, wipe phrase, defer bounds, tier order, forecast formula). R4 confirmed 39/40 PLAN.md endpoints captured (the 40th, `PUT /api/models/{id}/role`, is explicitly dropped).

## 6. Open items the human should look at before dispatch

1. **Discretion / self_healing tier remap.** The migration table maps `discretion → specialty` and `self_healing → specialty`. The other four mappings (extra_high/high → reasoning, medium → workhorse, trivial → cheap) are clean. The two specialty bucketings are intent-guesses — worth a glance before `bu-5tnp0` lands. If "self_healing" should become its own first-class tier, raise it now.
2. **Spend rule savings job cadence.** Tasks.md §5.4 says "daily." That is a guess — confirm before `bu-dvb7i` ships.
3. **Approval auto-decisions copy.** Resolved to "auto-approve" (design.md §D13) — neutral over "merge" / "land". Visible in `bu-5xiu9`. Easy to flip later if the wrong word.
4. **Anomaly detection threshold.** Deferred to a TODO in `bu-dvb7i`. Will not block the epic but is a known follow-up.

## 7. Out of scope / explicit no's (recap)

- Per-user OAuth setup stays on `/secrets`. `/settings` is **system-side only**.
- No new charting library — spend forecast is hand-rolled SVG.
- No density toggle, no theme toggle UI, no onboarding tour.
- No wiring-diagram visualization.
- No `/api/models/{id}/role` endpoint.
- No `/api/audit` prefix; keep `/api/audit-log`.
- No new design tokens — only the new `.attention-row` CSS class consuming existing OKLCH variables.

## 8. Reconciliation passes summary (Phase 1–3)

| Pass | Scope | Verdict | Material findings |
|---|---|---|---|
| R1 | Doctrine alignment | NEEDS-EDIT | Malformed §1b reference; doctrine update needed; v1.md scope amendment needed; dashboard-shell missing Source References. **Fixed in changeset.** |
| R2 | Implementation fitness | NEEDS-EDIT | complexity_tier value drift; audit prefix drift; costs.py exists; non-existent hook deletions; GeneralSettingsCard path. **Fixed in changeset.** |
| R3 | Cross-spec consistency | PASS | (one nit: CRUD verb templates — acceptable) |
| R4 | PLAN.md fidelity | NEEDS-EDIT (minor) | Missing `/api/models/{id}/role` endpoint; namespace shift undocumented; theming not explicitly rejected. **Fixed in changeset.** |
| R5 | Beads decomposition | (planning) | 30-bead plan; user-validated DAG. Implemented at 12 beads (one per logical PR-sized unit). |

All findings are reflected in `proposal.md`, `design.md`, `tasks.md`, and the per-capability spec deltas. `openspec validate redesign-settings-dispatch-console` returns clean.

## 9. Handoff

The planning artifacts are complete. `beads-coordinator` should:

1. Start by dispatching the three ready beads: `bu-9gzel` (doctrine), `bu-h31sp` (audit foundation), `bu-5tnp0` (catalog foundation). These can run in parallel — no shared files.
2. Once the foundations close, dispatch Phases 2/3/4/6/7/8 in parallel (six beads). They touch distinct files; no merge conflict surface between them.
3. Phase 5 (Console, `bu-ju4kh`) is single-threaded — it consumes summaries from Phases 2/3/4 (and the doctrine for the attention-tint).
4. Cleanup (`bu-ufqyb`) follows everything.
5. Report (`bu-jcsso`) is the final close-out.

The implementation does **not** belong to `/project-direction`. This report is the explicit handoff.

---

## 10. Round 2 reconciliation summary (R6–R10)

A second pass of 5 parallel reconciliation agents covered post-fix verification (R6), end-to-end implementation chains (R7), bead quality (R8), test/quality-gate coverage (R9), and production risk/security (R10). All five findings rolled back into the changeset; `openspec validate` still passes.

**Verdicts:** R6 NEEDS-EDIT (two stale `/api/audit` paths) → fixed. R7 NEEDS-EDIT (5 wiring gaps) → closed via new design.md §D15. R8 NEEDS-EDIT (3 beads too wide, 3 ambiguity nits) → addressed via bead `notes` (splits deferred to coordinator's discretion). R9 NEEDS-EDIT (audit-spy fixture + test-DB isolation for wipe + GeneralSettingsCard.test deletion) → folded into tasks.md. R10 **FIX-FIRST** (CRITICAL: wipe auth, complexity_tier one-shot, audit silent-failure) → all three closed below.

**New design.md sections added in Round 2:**
- **§D15 Async job ownership** — every async/scheduled job assigned a single owning module/bead (approval re-presentation timer, notification dispatcher quiet-hours check, spend rules `saved_7d` daily job, memory cleanup, verify-all rate-limit storage, Console aggregator cache, AttentionStrip cap, webhook delivery retries).
- **§D16 Authentication contract** — every mutation endpoint and WS endpoint requires `X-API-Key`; wipe endpoint refuses with `503 {auth_unconfigured}` if `DASHBOARD_API_KEY` is unset; WS endpoints require `?api_key=<value>` query param at upgrade; reason field rejects credential patterns via `validate_no_secrets()`; webhook secret returned once, never echoed.
- **§D17 Failure-mode contracts** — `audit.append()` raises `AuditTableNotAvailableError` on missing table (no silent skip); audit row commits in same SQL transaction as the state change (atomic); wipe wraps all drops in one transaction (atomic rollback on any failure); Console aggregator returns partial response with amber attention item on sub-system failure rather than erroring the whole call; WS reconnect emits full snapshot.
- **§D18 Migration safety contracts** — complexity_tier becomes a TWO-PHASE migration (Phase 1a expands CHECK to accept old+new, Phase 1b drops old after 7-day soak); costs.py rename ships dual-mounted for 90 days with `Deprecation` + `Sunset` headers per RFC 8594; pending_actions why/evidence soak for 7 days, `NOT NULL` deferred to follow-up.
- **§D19 Forecast sanity** — `projection_confidence: "low" | "normal"` based on `days_elapsed`; Console "spend near ceiling" suppressed when confidence is low to avoid false positives in the first 2 days of a month.

**Spec-delta updates in Round 2:**
- `dashboard-audit-log/spec.md` — added scenario for `audit.append()` raising on missing table; clarified same-transaction guarantee.
- `dashboard-permissions/spec.md` — added scenarios for wipe auth, wipe partial-drop rollback, reason credential filter, webhook secret never echoed (added `secret_prefix` field for identification).
- `dashboard-settings-console/spec.md` — added scenarios for AttentionStrip cap (5 items + `attention_truncated_count`), partial sub-system failure handling, WS auth at upgrade.
- `dashboard-spend-dashboard/spec.md` — added `projection_confidence` field to forecast response.

**Tasks.md updates in Round 2:**
- §2.2 — `audit.append()` contract includes raise-on-missing + same-transaction.
- §2.2.1, §2.2.2 — new sub-tasks for the audit-spy fixture + negative test.
- §3.1, §3.1.2 — two-phase migration with §3.1.2 marking Phase 1b as a separate follow-up bead.
- §3.5 — verify-all rate-limit storage explicitly in-memory; probe prompt fixed; per-model failure handling.
- §5.0 — costs.py dual-mount with 90-day deprecation window.
- §6.4 — permissions endpoint requires X-API-Key + reason credential filter.
- §6.5 — wipe endpoint auth ordering (auth before phrase) + single-transaction drops.
- §7.1 — Console caps attention[]; partial-failure mode; WS auth at upgrade.
- §8.6 — notification dispatcher location pinned to `src/butlers/modules/approvals/notifier.py`; re-presentation owned by existing scheduler.
- §11.2 — GeneralSettingsCard.test.tsx deletion explicitly listed; §11.2.1 adds orphan-import lint check.
- §12.2 — pre-flight check that `bu-9gzel` (doctrine PR) is merged before writing the report.

**Bead annotations in Round 2:**
Each foundation/page bead (`bu-h31sp`, `bu-5tnp0`, `bu-vz6pi`, `bu-dvb7i`, `bu-q2nz3`, `bu-ju4kh`, `bu-5xiu9`, `bu-ufqyb`, `bu-jcsso`) received a `notes:` entry summarizing the R6–R10 contract changes that bind it. View with `bd show <id>`.

**Bead splits considered but deferred:**
- R8 recommended splitting `bu-5tnp0` (catalog foundation) into 1b + 1c, `bu-vz6pi` (permissions) into matrix / data-ops / webhooks, and `bu-5xiu9` (approvals) for agent-contract isolation. These splits are reasonable but the current bead descriptions are now sharp enough that the coordinator can subdivide in flight if a bead grows too large for a single PR. **Recommendation:** keep current 12-bead structure; if a worker reports a bead as too wide during execution, split via `bd create --deps discovered-from:<parent>` then.

**Risks NOT closed (deferred to follow-up beads after epic):**
- `complexity_tier` Phase 1b (drop old CHECK values after 7-day soak).
- `/api/costs/*` sunset (delete after 90-day deprecation).
- `pending_actions.why/evidence` `NOT NULL` constraint (after 7-day agent rollout soak, if emission ≥ 99%).
- Smarter spend estimator (replace naive linear extrapolation).
- Future top-level `/audit-log` page (`/settings/permissions` only shows the reel today).

**Final status:** The changeset captures all 14 first-round findings + the 11 second-round findings (5 wiring, 11 risk, plus bead/test recommendations). `openspec validate` clean. Dispatch-ready when the three foundation beads (`bu-9gzel`, `bu-h31sp`, `bu-5tnp0`) are ready to pick up.
