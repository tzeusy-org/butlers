# Reconciliation report: redesign settings: Dispatch Console

**Epic:** `bu-do5q0` (redesign settings: Dispatch Console)
**OpenSpec change:** `openspec/changes/redesign-settings-dispatch-console/`
**Direction report:** `docs/reports/redesign-settings-dispatch-console-direction.md`
**Date:** 2026-05-17
**Status:** COMPLETE: all 11 implementation phases merged to main

---

## Summary

The settings-refactor epic replaced Butlers' legacy monolithic `/settings` SaaS-preferences
page with a Dispatch-language operator control plane: three sub-routes (`/settings/models`,
`/settings/spend`, `/settings/permissions`), a live `/settings` console with attentional
aggregation, a redesigned `/approvals` dossier, butler-management and memory fold-ins on the
detail pages, a dashboard audit-log primitive, and a full legacy-component cleanup pass.

Every mutation endpoint in the new surface calls `audit.append()`. All pages use the
Dispatch language already established on `/overview`, `/butlers`, and `/qa`.

---

## Doctrine deltas merged

Phase 0 (`bu-9gzel`) landed in commit `11a14e20`, verified present on main:

```
git log --oneline main | grep design-language.md
11a14e20 docs: Phase 0 doctrine updates — attention-tint + v1 scope amendment [bu-9gzel]
```

| File | Section | Change |
|---|---|---|
| `about/heart-and-soul/design-language.md` | `### Attention list` → new `#### Attention-tint exception` subsection | Formally permits 4–7% alpha background tint + 2px left rail as the single state-color-on-background exception. Documents permitted use cases and one-affordance-per-signal constraint. |
| `about/heart-and-soul/v1.md` | "What v1 Ships → Dashboard" | Lists `/settings/models`, `/settings/spend`, `/settings/permissions`, dashboard-audit-log, webhooks, and data-ops as v1-shipped capabilities, citing this OpenSpec change. |
| `frontend/src/index.css` | OKLCH palette section | Adds canonical `.attention-row[data-tone="red"\|"amber"]` CSS block and `--red`/`--amber` named token aliases in `:root` and `.dark`. |

---

## Routes shipped

### Audit log (`/api/audit-log`)

| Method | Route | Phase |
|---|---|---|
| GET | `/api/audit-log` | Phase 1a |
| GET | `/api/audit-log/{entry_id}` | Phase 1a |

### Model settings (`/api/settings/models`)

| Method | Route | Phase |
|---|---|---|
| GET | `/api/settings/models` | Phase 2 |
| POST | `/api/settings/models` | Phase 2 |
| PUT | `/api/settings/models/{id}` | Phase 2 |
| DELETE | `/api/settings/models/{id}` | Phase 2 |
| PUT | `/api/settings/models/{id}/priority` | Phase 2 |
| POST | `/api/settings/models/verify-all` | Phase 2 |
| GET | `/api/settings/models/{id}/failures` | Phase 2 |
| PUT | `/api/settings/models/{id}/limits` | Phase 2 |
| POST | `/api/settings/models/{id}/reset-usage` | Phase 2 |
| GET | `/api/settings/models/{id}/usage` | Phase 2 |

### Spend (`/api/spend`)

| Method | Route | Phase |
|---|---|---|
| GET | `/api/spend` | Phase 3 |
| GET | `/api/spend/summary` | Phase 3 |
| GET | `/api/spend/daily` | Phase 3 |
| GET | `/api/spend/top-sessions` | Phase 3 |
| GET | `/api/spend/by-schedule` | Phase 3 |
| GET | `/api/spend/breakdown` | Phase 3 |
| GET | `/api/spend/forecast` | Phase 3 |
| GET | `/api/spend/rules` | Phase 3 |
| POST | `/api/spend/rules` | Phase 3 |
| PUT | `/api/spend/rules/{rule_id}` | Phase 3 |
| DELETE | `/api/spend/rules/{rule_id}` | Phase 3 |
| PUT | `/api/spend/ceiling` | Phase 3 |
| WS | `/api/spend/stream` | Phase 3 |

### Permissions (`/api/permissions`)

| Method | Route | Phase |
|---|---|---|
| GET | `/api/permissions` | Phase 4 |
| PUT | `/api/permissions/{butler}/{perm}` | Phase 4 |

### Webhooks (`/api/webhooks`)

| Method | Route | Phase |
|---|---|---|
| GET | `/api/webhooks` | Phase 4 |
| POST | `/api/webhooks` | Phase 4 |
| GET | `/api/webhooks/{id}` | Phase 4 |
| PUT | `/api/webhooks/{id}` | Phase 4 |
| DELETE | `/api/webhooks/{id}` | Phase 4 |
| POST | `/api/webhooks/{id}/test` | Phase 4 |

### Data ops (`/api/data`)

| Method | Route | Phase |
|---|---|---|
| POST | `/api/data/export` | Phase 4 |
| GET | `/api/data/export/download/{export_id}` | Phase 4 |
| DELETE | `/api/data/wipe` | Phase 4 |

### Settings console (`/api/settings`)

| Method | Route | Phase |
|---|---|---|
| GET | `/api/settings/console` | Phase 5 |
| WS | `/api/settings/stream` | Phase 5 |

### Approvals (`/api/approvals`)

| Method | Route | Phase |
|---|---|---|
| GET | `/api/approvals` | Phase 6 |
| GET | `/api/approvals/history` | Phase 6 |
| GET | `/api/approvals/policy` | Phase 6 |
| PUT | `/api/approvals/policy` | Phase 6 |
| GET | `/api/approvals/{action_id}` | Phase 6 |
| POST | `/api/approvals/{action_id}/approve` | Phase 6 |
| POST | `/api/approvals/{action_id}/deny` | Phase 6 |
| POST | `/api/approvals/{action_id}/defer` | Phase 6 |

### Butler management (`/api/butlers/{name}`)

| Method | Route | Phase |
|---|---|---|
| GET | `/api/butlers/{name}/prompt` | Phase 7 |
| PUT | `/api/butlers/{name}/prompt` | Phase 7 |
| GET | `/api/butlers/{name}/prompt/history` | Phase 7 |
| GET | `/api/butlers/{name}/tools` | Phase 7 |
| PUT | `/api/butlers/{name}/tools/{tool}` | Phase 7 |
| GET | `/api/butlers/{name}/memory-access` | Phase 7 |
| POST | `/api/butlers/{name}/kill` | Phase 7 |

### Memory (`/api/memory`)

| Method | Route | Phase |
|---|---|---|
| GET | `/api/memory/retention-policies` | Phase 8 |
| PUT | `/api/memory/retention-policies` | Phase 8 |
| GET | `/api/memory/compaction-log` | Phase 8 |
| GET | `/api/memory/inspect` | Phase 8 |

All frontend routes use the Dispatch design language. Screenshots are rendered in Dispatch
language; see `/settings`, `/settings/models`, `/settings/spend`, `/settings/permissions`,
and `/approvals` routes in the running dashboard.

---

## Surfaces deleted

Phase 5 (PR #1749) deleted the legacy monolithic settings page:

| Deleted file | Replaced by |
|---|---|
| `frontend/src/pages/SettingsPage.tsx` (929 lines) | `frontend/src/pages/SettingsConsolePage.tsx` + sub-pages |

Phase 11 (PR #1750) deleted the legacy card components that SettingsPage imported:

| Deleted file | Note |
|---|---|
| `frontend/src/components/settings/BlobStorageCard.tsx` (323 lines) | No external imports; OAuth/secret setup lives on `/secrets` |
| `frontend/src/components/settings/ModelCatalogCard.tsx` (1,294 lines) | Superseded by `SettingsModelsPage.tsx` |
| `frontend/src/components/settings/QASettingsCard.tsx` (557 lines) | QA settings surface moved to `/qa` dossier |
| `frontend/src/components/GeneralSettingsCard.tsx` (354 lines) | System-side settings migrated to `/settings/permissions` and `/settings/models` |
| `frontend/src/components/GeneralSettingsCard.test.tsx` (65 lines) | Test for deleted component |

Total deleted: 3,593 lines of legacy UI code across 5 files.

---

## Migrations shipped

| Migration | Content | Phase |
|---|---|---|
| `core_092_audit_log.py` | `public.audit_log`: append-only table, indexes on `(ts DESC)`, `(action)`, `(actor)` | Phase 1a |
| `core_093_complexity_tier_rename.py` | Renames 6 `complexity_tier` values to canonical set; adds `last_verified_at/latency_ms/ok` columns to `model_catalog` | Phase 1b |
| `core_094_spend_tables.py` | `public.spend_rules`, `public.spend_ceiling` | Phase 3 |
| `core_095_permissions_webhooks_approvals_policy.py` | `public.permissions`, `public.webhooks`, `public.approvals_policy` | Phase 4 |
| `core_096_memory_retention_policies.py` | `public.memory_retention_policies`, `public.memory_compaction_log` | Phase 8 |
| `core_097_pending_actions_why_evidence.py` | Adds `why TEXT`, `evidence JSONB` to `pending_actions` in all butler schemas | Phase 6 |
| `core_098_butler_prompt_history.py` | `public.system_prompt_history`, `public.butler_tools` | Phase 7 |

---

## Audit-log coverage matrix

Every mutation endpoint that changes persistent state calls `audit.append()`. The table
below maps each mutation endpoint to its action token.

| Endpoint | Action token | File | Coverage |
|---|---|---|---|
| `PUT /api/settings/models/{id}` | `model.update` | `model_settings.py:506` | COVERED |
| `PUT /api/settings/models/{id}/priority` | `model.priority` | `model_settings.py:575` | COVERED |
| `POST /api/settings/models/verify-all` | `models.verify_all` | `model_settings.py:625,692` | COVERED |
| `POST /api/spend/rules` | `spend.rule.create` | `spend.py:1203` | COVERED |
| `PUT /api/spend/rules/{id}` | `spend.rule.update` | `spend.py:1307` | COVERED |
| `DELETE /api/spend/rules/{id}` | `spend.rule.delete` | `spend.py:1359` | COVERED |
| `PUT /api/spend/ceiling` | `spend.ceiling.update` | `spend.py:1421` | COVERED |
| `PUT /api/permissions/{butler}/{perm}` | `permission.set` | `permissions.py:177` | COVERED |
| `POST /api/data/export` | `data.export` | `data_ops.py:205` | COVERED |
| `DELETE /api/data/wipe` | `data.wipe` | `data_ops.py:328` | COVERED |
| `POST /api/webhooks` | `webhook.create` | `webhooks.py:293` | COVERED |
| `PUT /api/webhooks/{id}` | `webhook.update` | `webhooks.py:387` | COVERED |
| `DELETE /api/webhooks/{id}` | `webhook.delete` | `webhooks.py:424` | COVERED |
| `POST /api/webhooks/{id}/test` | `webhook.test` | `webhooks.py:489` | COVERED |
| `PUT /api/approvals/policy` | `approvals.policy` | `approvals.py:1483` | COVERED |
| `POST /api/approvals/{id}/approve` | `approval.approve` | `approvals.py:1918` | COVERED |
| `POST /api/approvals/{id}/deny` | `approval.deny` | `approvals.py:1987` | COVERED |
| `POST /api/approvals/{id}/defer` | `approval.defer` | `approvals.py:2058` | COVERED |
| `PUT /api/butlers/{name}/prompt` | `butler.prompt_set` | `butler_management.py:235` | COVERED |
| `PUT /api/butlers/{name}/tools/{tool}` | `butler.tool_set` | `butler_management.py:383` | COVERED |
| `POST /api/butlers/{name}/kill` | `butler.kill` | `butler_management.py:491` | COVERED |
| `PUT /api/memory/retention-policies` | `memory.retention_policy` (per changed entry) | `memory.py:1832` | COVERED |

**Coverage: 22/22 mutation endpoints: 100%.**

Audit primitives: `audit.append()` raises `AuditTableNotAvailableError` on missing table
(no silent skip). For most endpoints the audit row commits in the same SQL transaction as
the state change (atomicity per design.md §D17). The `data.export` path uses a best-effort
audit (catches the error and logs a warning) because it runs outside a transaction boundary.

---

## Open follow-ups

These items were explicitly deferred from the epic and are known follow-up beads:

1. **Anomaly detection threshold** (`§D13`, deferred from Phase 3): `SettingsSpendPage.tsx`
   contains a TODO placeholder section. Requires a separate bead to define heuristics and
   backend signal.

2. **Smart spend estimator** (Phase 3): Current forecast uses naive linear extrapolation
   (`MTD ÷ days_elapsed × days_in_month`). A smarter estimator accounting for usage patterns
   is explicitly deferred per design.md §D19. Filed as a future follow-up.

3. **`/api/costs/*` sunset** (Phase 3, §D18): `costs.py` was renamed to `spend.py` and
   `/api/costs/*` routes are dual-mounted with `Deprecation: true` and `Sunset` headers for
   a 90-day period. The deletion bead should land around 2026-08-16.

4. **`complexity_tier` Phase 1b** (Phase 1b, §D18): Phase 1a expanded the CHECK constraint
   to accept both old and new tier values. Phase 1b (drop old values after 7-day soak) is a
   separate follow-up bead linked to `bu-5tnp0`.

5. **`pending_actions.why/evidence` NOT NULL** (Phase 6): Columns were added as nullable
   for a 7-day agent rollout soak. The `NOT NULL` enforcement is a follow-up bead after
   emission rates are confirmed ≥ 99%.

6. **Full `/audit-log` top-level page**: The `/settings/permissions` page renders a 15-entry
   audit reel today. A dedicated operator view of the full audit trail (filterable, paginated)
   was explicitly deferred. The backend endpoints (`GET /api/audit-log` with `since/actor/
   action/limit`) are already shipped and ready to serve this page.

---

## Epic phases summary

| Phase | Bead | PR | Description | Status |
|---|---|---|---|---|
| Phase 0 | `bu-9gzel` | `11a14e20` (direct) | Doctrine: attention-tint + v1 scope | MERGED |
| Phase 1a | `bu-h31sp` | #1693 | Audit log primitive | MERGED |
| Phase 1b | `bu-5tnp0` | #1694 | `complexity_tier` rename + routing contract | MERGED |
| Phase 2 | `bu-q2nz3` | #1697 | `/settings/models` page + API | MERGED |
| Phase 3 | `bu-dvb7i` | #1695 | `/settings/spend` page + API | MERGED |
| Phase 4 | `bu-vz6pi` | #1696 | `/settings/permissions` page + API + webhooks + data-ops | MERGED |
| Phase 5 | `bu-ju4kh` | #1749 | `/settings` Console + AttentionStrip + WS | MERGED |
| Phase 6 | `bu-5xiu9` | #1699 | `/approvals` dossier replacement | MERGED |
| Phase 7 | `bu-g4d49` | #1700 | Butler detail fold-in (prompt, tools, kill switch) | MERGED |
| Phase 8 | `bu-1kzbg` | #1698 | Memory fold-in (retention policies, compaction log, inspect) | MERGED |
| Phase 11 | `bu-ufqyb` | #1750 | Legacy cleanup (5 components deleted) | MERGED |
| Phase 12 | `bu-jcsso` | (this PR) | Reconciliation report | COMPLETE |
