# Settings · refactor plan

> Hand-off prompt for a Claude Code session. Read this end-to-end before
> writing any code. The HTML prototype at `index.html` is the source of
> truth for what the result should *look like*; this document is the
> source of truth for what to *build*.

---

## 1. Orientation

The Butlers app is being moved page-by-page into a new design language
("Dispatch") — already shipped on `/`, `/butlers`, and `/qa`. This
refactor brings `/settings` and `/approvals` into the same language and
takes the opportunity to clarify the information architecture.

### Files in this folder

| File | What it is |
|---|---|
| `index.html` | Open in a browser. The DesignCanvas shows three top-level direction proposals, then the three settings sub-routes, then adjacent-route designs. This is the visual spec. |
| `settings-redesign.jsx` | Three direction proposals (Ledger / Console / Manifest) plus the Console renderer and the expanded model catalog. |
| `settings-expanded.jsx` | The three sub-route renderers (Spend, Permissions/Data, plus the new Approvals page) and two integration references (Butlers, Memory) that fold into existing pages. |
| `primitives.jsx` | Palette + shared atoms (`ButlerMark`, `StatusDot`, `Sev`, `StripeChart`, etc.). |
| `design-canvas.jsx` | Pan/zoom presentation shell. Not part of the live app. |
| `DESIGN_LANGUAGE.md` | The language spec. **Read this first.** |

### The pick

The user has chosen **direction B · The Console** as the `/settings`
shell. Build that one. Discard Ledger and Manifest, but keep them in
the prototype as reference — useful for the model-catalog page when it's
opened in a wide window.

---

## 2. The decisions already taken

These are settled. Don't re-litigate them.

1. **`/settings` is system-side only.** Per-user OAuth (Google, Spotify,
   Telegram, Steam) stays on `/secrets`. Don't merge them.
2. **The settings tree has exactly three sub-routes:**
   - `/settings` — the Console (overview panel grid).
   - `/settings/models` — the model catalog.
   - `/settings/spend` — the spend dashboard.
   - `/settings/permissions` — permissions matrix, audit reel, data ops.
3. **Per-butler configuration lives on `/butlers/{name}`,** not under
   settings. Fold the prompt / tools / fallback-chain / activity design
   (see `ButlersExpanded` in `settings-expanded.jsx`, rendered in the
   bottom canvas section) into the existing `butler-detail-page.jsx`.
4. **Memory configuration lives on `/memory`.** The tier flow, retention
   policy table, compaction log, and memory-inspect search from
   `MemoryExpanded` belong there.
5. **`/approvals` is its own top-level route.** The `ApprovalsPage`
   component **replaces** the existing approvals page entirely.
6. **The model catalog is grouped by complexity tier**, not by provider
   company. Six tiers, canonical order:
   `reasoning → workhorse → cheap → specialty → local → legacy`. Within
   each tier, sort by **priority desc, then enabled desc**.

---

## 3. The visual language — non-negotiables

Read `DESIGN_LANGUAGE.md` in full. Quick reference:

- **Type:** Inter Tight (UI) · Source Serif 4 (Voice) · JetBrains Mono
  (numerals, eyebrows, IDs). No system fonts. No Inter (regular).
- **Display weight is 500, never 700.** Tight tracking does the work
  that weight would do.
- **Numerals are `font-variant-numeric: tabular-nums`. Always.**
- **State color** (red/amber/green) appears only on foreground or
  border. The single exception this refactor establishes:
  - **4–7% alpha background tints** on rows or panels that *demand
    human attention* (open approval, auth-renewal needed, model in
    error). Pair the tint with a 2px left rail in the same color.
- **Butler hues** stay exclusive to the letter-mark. Never on
  backgrounds, buttons, borders.
- **One affordance per signal.** No two of {dot, sliver, numeral,
  color} for the same thing.
- **Empty states:** serif italic, one sentence. *"Nothing waiting."*
- **No drop shadows. No emoji. No gradient hero. No "delight" motion.**

---

## 4. Routes & their components

```
/                       Overview            (already shipped)
/butlers                Butlers index       (already shipped)
/butlers/{name}         Butler detail       ← FOLD IN: prompt, tools, fallback-chain, kill-switch, activity
/qa                     QA dossier          (already shipped)
/memory                 Memory page         ← FOLD IN: tier flow, retention table, compaction log, inspect search
/approvals              Approvals inbox     ← REPLACE: with ApprovalsPage from this prototype
/settings               Settings Console    ← NEW: panel grid + attention strip
/settings/models        Model catalog       ← NEW: tier-grouped, priority sort, per-row controls
/settings/spend         Spend dashboard     ← NEW: forecast, breakdowns, routing rules, alerts
/settings/permissions   Permissions & data  ← NEW: full matrix, audit reel, webhooks
/secrets                Per-user OAuth      (unchanged)
```

---

## 5. Backend surface

These endpoints are referenced inline in the prototype (look for the
`ApiWireFooter` at the bottom of each expanded route). They're the
hand-shake between this refactor and the FastAPI server.

### `/settings` Console
- `GET /api/settings/console`
  → header counts + attention strip items. Cache 10s.
- `WS  /api/settings/stream`
  → live updates: approval count, model verification result, spend tick.

### `/settings/models`
- `GET    /api/models`
  → full catalog, with priority, enabled, state, usage24h, usage30d,
    spend7d, used_by, failures7d.
- `PUT    /api/models/{id}/priority`  `{ delta: 5 }`
  → idempotent priority adjustment.
- `PUT    /api/models/{id}/enabled`  `{ enabled: bool }`
- `PUT    /api/models/{id}/role`
- `POST   /api/models/{id}/test`
  → run a 1-token completion against the model, return latency + ok.
- `DELETE /api/models/{id}`
  → soft-delete; moves to `legacy` tier. Hard delete via separate flow.
- `POST   /api/models`
  → add a new provider/model.
- `POST   /api/models/verify-all`
  → re-verify every key.
- `GET    /api/models/{id}/failures?since=24h`
  → tail of recent failure logs for the detail panel.

**Sort contract.** The catalog is sorted server-side by
`(tier, priority DESC, enabled DESC, family ASC)`. The frontend never
sorts; it only filters.

**Routing contract.** When a butler asks the runtime for a model in
tier T, the runtime selects the **highest-priority enabled** model in
that tier whose state ∈ {verified, untested}. If none qualifies, fall
through to the next tier.

### `/settings/spend`
- `GET /api/spend?period=24h|7d|30d|90d|ytd|all`
- `GET /api/spend/breakdown?by=butler,model,feature`
- `GET /api/spend/forecast`
  → projected month-end land, plus the day-by-day series for the chart.
- `GET /api/spend/rules`
- `POST /api/spend/rules`
- `PUT /api/spend/rules/{id}`
- `DELETE /api/spend/rules/{id}`
- `PUT /api/spend/ceiling`
- `WS  /api/spend/stream`
  → per-call spend events. The chart never lags.

### `/settings/permissions`
- `GET /api/permissions`
- `PUT /api/permissions/{butler}/{perm}`  `{ granted: bool, reason: str }`
  → reason is **required**. Permissions changes are audited.
- `GET /api/audit?since=&actor=&action=&limit=`
- `GET /api/audit/{id}`
- `POST /api/data/export`  `{ scope: 'full' | 'memory' | 'audit' | 'config' }`
  → returns a signed URL to download an encrypted zip.
- `DELETE /api/data/wipe`  `{ phrase: str }`
  → requires the literal phrase `WIPE EVERYTHING IRREVERSIBLY`.
- `GET    /api/webhooks`
- `POST   /api/webhooks`
- `PUT    /api/webhooks/{id}`
- `DELETE /api/webhooks/{id}`
- `POST   /api/webhooks/{id}/test`

### `/approvals` (top-level)
- `GET  /api/approvals?state=waiting|decided|all`
- `GET  /api/approvals/{id}`
  → includes `title`, `butler`, `ts`, `expires`, `why` (serif paragraph),
    `evidence` (mono lines), `proposed_action` (the thing being asked).
- `POST /api/approvals/{id}/approve`  `{ edits?: object }`
- `POST /api/approvals/{id}/deny`     `{ reason?: str }`
- `POST /api/approvals/{id}/defer`    `{ hours: int }`
- `GET  /api/approvals/history?since=`
- `GET  /api/approvals/policy`
- `PUT  /api/approvals/policy`
- `WS   /api/approvals/stream`

---

## 6. Implementation plan (suggested order)

Each task is sized to be a single PR.

### Phase 1 — Foundations
1. **Audit log primitive.** Add `audit.append()` in the backend. Every
   subsequent write endpoint in this refactor uses it. Schema:
   `ts, actor, action, target, note, ip, request_id`. No deletes ever.
2. **Catalog data model.** Migrate `models` table: add `priority INT`,
   `enabled BOOL`, `tier TEXT`, `usage_24h_calls INT`, `usage_30d_calls
   INT`. Default everything; backfill `tier` from existing roles.
3. **Routing change.** Update the runtime model-selection function to
   the new contract (§5 above). Old "default model = first verified"
   logic is replaced by `(tier, priority desc, enabled desc)`.

### Phase 2 — `/settings/models`
4. Build the catalog endpoint set (§5). Server-side sort.
5. Build `ModelCatalogPage` from the JSX in
   `settings-redesign.jsx :: ModelCatalogExpanded`. Wire the priority
   stepper, enable toggle, test, edit, delete, and the filter chips.
6. Hook up an `ApiWireFooter` analog in dev mode only — it shows the
   endpoints each page is hitting. Off in prod.

### Phase 3 — `/settings/spend`
7. Build the spend endpoints. Forecast can be naive at first
   (linear-extrapolate mtd ÷ days_elapsed × days_in_month); leave a
   TODO for a smarter estimator.
8. Build `SpendDashboardPage` from `settings-expanded.jsx ::
   SpendDashboard`. The SVG forecast chart is hand-rolled — keep it that
   way, no chart library. The breakdown bars are 8 lines of CSS each.
9. Routing-rules table is store-and-eval. Persist as JSON. Order is
   significant (top-to-bottom). Each rule has a 7-day "saved" metric
   computed by the runtime.

### Phase 4 — `/settings/permissions`
10. Build the permissions endpoints. **Every** mutation requires a
    reason field and writes to the audit log.
11. Build `PermissionsPage` from `settings-expanded.jsx ::
    DataExpanded`. The big matrix, the audit reel (last 15 + link to
    `/audit`), the data ops sub-grid, the webhooks table.
12. Wipe phrase enforcement is server-side. Frontend just collects.

### Phase 5 — `/settings` Console
13. Build the Console endpoint (§5).
14. Build `SettingsConsolePage` from `settings-redesign.jsx ::
    SettingsConsole`. Each panel summary fetches from its own sub-route
    endpoint (parallel queries) — keep panels independent so a slow
    fetch in one doesn't block the page.
15. The **AttentionStrip** at the top draws from
    `/api/settings/console`'s `attention[]` array. Items have shape
    `{ tone: 'red'|'amber', kind, text, action_route }`.

### Phase 6 — `/approvals` replacement
16. Migrate the existing approvals data model if needed (add a `why`
    serif-paragraph field and `evidence` array of strings).
17. Build `ApprovalsPage` from `settings-expanded.jsx ::
    ApprovalsPage`. **Replace** the old `/approvals` route — delete the
    old component file.
18. Wire the quiet-hours editor. Quiet hours are stored as
    `{ start_hour, end_hour, timezone }` and consulted by the
    notification dispatcher.

### Phase 7 — `/butlers/{name}` integration
19. Fold the `ButlersExpanded` design (sections: fallback chain, system
    prompt, tools, memory access, activity) into the existing
    `butler-detail-page.jsx`. Don't create a parallel route.
20. The system prompt becomes a real CRUD surface with version history.
    Each `PUT` snapshots the previous prompt; `GET .../prompt/history`
    returns the chain.

### Phase 8 — `/memory` integration
21. Fold the `MemoryExpanded` design (tier flow, retention table,
    compaction log, search) into the existing memory page.
22. Retention policies become a small admin table keyed by `kind`
    (event / fact / preference / summary / transcript / embedding).

---

## 7. Acceptance criteria

A reasonable definition of "done":

- [ ] All routes in §4 render and use the Dispatch language. The page
      passes the test: *if a stranger printed it on letterhead, would
      I trust it?*
- [ ] No emoji anywhere in the new pages.
- [ ] Numerals are tabular everywhere.
- [ ] State color appears only when state demands. The attention
      tint pattern is implemented consistently (4–7% alpha + matching
      left rail).
- [ ] The model catalog sorts (tier, priority desc, enabled desc)
      server-side. Frontend priority stepper updates round-trip
      within 200ms in dev.
- [ ] The spend chart is hand-rolled SVG. The forecast line is dashed
      from "today" forward.
- [ ] Permissions mutations refuse without a `reason` field.
- [ ] `/approvals` replaces — not duplicates — the existing route.
      The old component file is deleted in the same PR.
- [ ] Per-butler config does not exist under `/settings/`. Anyone
      looking for it lands on `/butlers/{name}`.
- [ ] The audit log records every config change. Inspecting `/audit`
      shows a stream that reads as prose.

---

## 8. Things explicitly out of scope

- Building a new design system. Reuse what's in `frontend/src/`.
- Theming knobs. The Dispatch language is dark-canonical with a
  paper-warm light variant. Don't add other themes.
- "Density" toggle. The pages are dense by default; don't add a
  comfortable mode.
- Onboarding tooltips on settings. The page is for someone who knows
  what they're doing.
- A graphical "wiring diagram" of butlers ↔ models ↔ permissions. Tempted
  but rejected — it would be SaaS-coded. The matrix is enough.

---

## 9. Open questions for the user before you ship

Surface these to Tze before merging:

1. **Wipe phrase.** Should it be `WIPE EVERYTHING IRREVERSIBLY` or
   something Tze writes? (Prototype assumes the former.)
2. **Default routing tier on butler creation.** Currently the catalog
   defaults to `workhorse`; some butlers might want to start on
   `cheap` to gather data before promoting.
3. **Anomaly detection trigger.** The Spend page references a "3σ over
   24h baseline" alert; is that the right threshold or do we want
   something less twitchy?
4. **Audit retention.** Prototype says "indefinite · no expiry". Confirm
   that's acceptable storage-wise.
5. **Approval auto-decisions copy.** *"QA may merge low &amp; medium
   severity without asking"* — is "merge" the right verb or do we
   want "land"?

---

## 10. The north star

Every page should pass:

> *If the system were handing me a sheet of paper, would I trust the
> typography of that sheet?*

Newspapers from 1965 pass. Bank statements from private banks pass.
Default Bootstrap dashboards do not pass. The settings tree should
pass. If a page in this refactor doesn't pass that test, it isn't done.
