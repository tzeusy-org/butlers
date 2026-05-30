# secrets redesign — integration brief

**Date:** 2026-05-25
**Version:** v1
**Bundle path:** `pr/overview/secrets-redesign/`
**Mode:** fresh
**Phase D verdict:** proceed-with-amendments (cost-clean; one doctrine-gap resolved by user — see §4 Intent compliance)
**Prior brief (if any):** none

---

## 0. Design intent

> Captured from `pr/overview/secrets-redesign/VISION.md` (auto-distilled from the bundle's `README.md` + `BRIEF.md`, then amended per user resolution of the owner-vs-member doctrine gap surfaced in Phase D). **This section is binding — every spec section, every component decision, every backend contract must trace back to it.** Phase D treats violations of intent as automatic red regardless of cost math.

### Problem being solved

Today's `/secrets` is a 3-tab shell (System / User / CLI runtimes) wrapping a flat `SecretsTable` of `••••••••` rows with a per-row eye-toggle reveal. Two specific pains:

1. **Opaque without leaking.** To know whether a credential is *the right one*, the owner has to reveal it. There is no fingerprint, no last-verified timestamp, no scope inventory, no provider-side state — the eye-toggle is the only diagnostic, and it is binary.
2. **Flat rhythm.** A silently-expired Google OAuth and a healthy Telegram token render at identical weight. Severity has no visual privilege; sick credentials hide in plain sight.

Compounding both: the User tab stacks six bespoke provider Setup cards (Google, Spotify, Home Assistant, WhatsApp, OwnTracks, Steam) in a single `<Card>`, each with divergent chrome — so the page has no visual rhythm even on a healthy day.

### Primary audience

**Owner** — single principal, per `about/heart-and-soul/security.md`. The owner rotates System keys, opens OAuth dances, owns the OAuth callbacks, and touches every credential family (System, User, CLI runtimes).

The identity switcher in the spine is a **projection lens** over the owner's view of household-member contact data — *not* an authentication boundary. Switching identity re-projects the User-tab credentials associated with a member entity, but every action (rotate, reauthorize, disconnect, probe) runs with owner privilege; the page does not log a member in. This matches existing single-owner doctrine (`about/heart-and-soul/security.md:8,18-20`) without introducing a new privilege tier.

A future RFC under `about/legends-and-lore/` may introduce a household-member privilege tier with its own session-identity mechanism. If and when it does, this page will gain real member-scoped enforcement; the current redesign is forward-compatible with that change because the same `?identity=<id>` URL state will then bind to a session principal rather than to a projection lens.

External users / operators are explicitly **not** in scope.

### Deliberate design moves

1. **Replace the masked-value blob with *evidence about the value*.** Each credential surfaces a stable fingerprint (`sha256:7a3f…`, mono), a scope inventory (granted vs required, with mismatches called out), a last-verified probe outcome (latency + ok/fail + tail), provider-side state (revoked / expiring / valid), and — when sick — an explicit *what breaks* list of butler features that will silently fail. Reveal stays as a tweak (default `eye`); evidence is the primary affordance.
2. **Passport-book IA.** Replace the 3-tab `<Tabs>` shell with a single passport-book surface. Left **spine** indexes every credential (pinned `needs-hand` group, then CLI runtimes, then System, then User integrations). Right **page** opens the focused credential in editorial depth: heading + state plaque, dense KV band, scopes when applicable, *what breaks*, probe result, audit stamps, cross-references, commit footer.
3. **Severity earns visual authority only when state demands it.** Per Dispatch §1b, a quiet day on `/secrets` reads as a calm inventory with zero red/amber pixels. The moment one OAuth expires, that one row claims colour and weight (sliver + commit pill) — and only that row. Status is one of {dot, sliver, numeral, colour}, never a word.
4. **One row template across all three families.** The User tab's six bespoke provider Setup cards collapse into one row template; per-provider oddities (OwnTracks webhook URL, Steam ID format, WhatsApp QR link) live in a provider-specific drawer. System / User / CLI runtimes read as the same family in the spine and as the same page-shape on the right.
5. **Inventory ≠ channel-health dashboard.** `/secrets` is the credential inventory; `/ingestion/connectors` is the channel-side view of the same OAuth (throughput, scope, route). Both pages can trigger reauth and both reflect status; OAuth callback returns the user to whichever page initiated the dance. Deep-links from `/ingestion/connectors` banners land on `/secrets/user#<provider>` at the focused passport page, not a tab top.

### What we are deliberately NOT doing

- **No storage migration.** System secrets stay in `butler_secrets`. User secrets stay on `entity_info`. CLI runtime tokens stay where they live today. The redesign is presentational + adds a small number of new read-side endpoints.
- **No bulk operations** (yet). Bulk rotate / bulk revoke / bulk export are out of scope. One credential at a time.
- **No merge with `/settings`.** `/settings` is system-side knobs; `/secrets` is credentials. Strictly disjoint surfaces.
- **No attempt to be the ingestion-channel health dashboard.** That lives at `/ingestion/connectors`. Cross-link, don't duplicate.
- **No padlock icons as row decoration.** The whole page is about secrets; drawing a padlock on every row is noise. Use the icon once, in the sidebar.
- **No asterisks as the only proof a secret exists.** `••••••••` is a weak signal; pair with fingerprint + last-verified, always.
- **No brand-coloured "Connect" / "Reauthorize" CTAs.** Reauthorize is a Dispatch commit pill (fg-on-bg). Provider name appears in mono, never as its logo or hex.
- **No status-as-a-word badges.** "Connected" / "Active" / "Linked" are banned. State is rendered as {dot, sliver, numeral, colour}.
- **No stacked bespoke provider Setup cards.** Replaced by the single row template (move 4).
- **No making the reveal-eye disappear.** It ships and remains the default, exposed as a per-page Tweak (`eye / hover / never`). Removing the eye is not the goal; demoting it from primary affordance to fallback is.
- **No "smart explanation" / LLM-narrated UI on `/secrets` surfaces.** Per Phase D, every voice-style surface in the bundle is stored prose, a templated string, or a verbatim provider error tail. Future beads that propose LLM-elaborated scope explanations, audit summaries, or "would-break" narratives must be rejected by reference to this brief.

### Success criteria

- The owner can distinguish a healthy credential from a sick one **without pressing the eye on any row**, by reading the spine alone.
- The identity switcher reprojects User-tab credentials per household-member entity, with all mutations still running under owner privilege (per the projection-lens semantics above).
- An expired Google OAuth surfaces in the `/secrets` spine within the same page-load that surfaces it on `/ingestion/connectors` — both views read the same source-of-truth state.
- The owner reauthorizes an expired OAuth in one click from either `/secrets` or `/ingestion/connectors`, and the OAuth callback returns them to the originating page.
- A day on which all credentials are healthy renders `/secrets` with **zero red/amber pixels**.
- Deep-links of the form `/secrets/user#<provider>` and `/secrets/system#<key>` land on the focused passport page (right-side page open, spine row highlighted), not a tab top.
- The reveal-eye is reclassified as a Tweak (default `eye`); removing the eye from a row does not impair the owner's ability to assess that row.
- The User tab's six bespoke Setup cards are replaced by one row template with provider-specific drawers; visually all three families share rhythm.
- Cross-page reauth bookkeeping survives an OAuth round-trip: the callback knows which page sent the user out and lands them back there.

---

## 1. Scope

This redesign replaces the entire `/secrets` surface in `frontend/` — `SecretsPage.tsx` (3-tab shell), `SecretsTable.tsx`, the six bespoke provider Setup cards in `frontend/src/components/settings/`, `CLIAuthCard.tsx`, and the embedded `EntityPicker` — with a single passport-book route (spine + page + tweaks). No storage migration; system secrets remain in `butler_secrets`, user secrets remain on `entity_info`. The binding design language is `pr/overview/secrets-redesign/DESIGN_LANGUAGE.md` (Dispatch). Integration target is the existing Vite/React 18.3.1/React Router 7.13/TailwindCSS 4.1.18/shadcn dashboard.

### Sub-pages

| Route | Source file(s) | Purpose | Sticky-nav parent? |
|-------|--------|---------|---|
| `/secrets` | secrets-passport.jsx, secrets-pages.jsx, secrets-spine.jsx | Main passport-book surface: left spine index + right page editorial | No |
| `/secrets?focus=<key>` | secrets-passport.jsx (URL param via `parseFocus`) | Deep-link to specific credential; routes to correct page based on key type (`u:`, `s:`, `c:`) | No |
| `/secrets?identity=<id>` | secrets-passport.jsx (identity switcher) | Switch active identity projection (projection-lens semantics; mutations remain owner-privileged) | No |
| `/secrets?sort=<mode>` | secrets-passport.jsx (sortMode state) | Change spine sort: `severity`, `recency`, or `alpha` | No |
| `/secrets/connect/<provider>` | prompts/00-foundation.md §5.2 | Begin OAuth dance; returns redirect URL | No |
| `/secrets/oauth/callback/<provider>` | prompts/00-foundation.md §5.2 | OAuth callback handler; re-routes to `/secrets?focus=u:<provider>&toast=connected` | No |

### Design tokens (binding)

#### Color

**Surfaces (dark mode canonical):**
- `--bg`: `oklch(0.145 0 0)` — page background
- `--bg-elev`: `oklch(0.205 0 0)` — code blocks, tooltips, active spine row
- `--bg-deep`: `oklch(0.115 0 0)` — sidebar, sticky bars
- `--fg`: `oklch(0.985 0 0)` — primary text
- `--mfg`: `oklch(0.708 0 0)` — muted text, eyebrows, secondary labels
- `--dim`: `oklch(0.55 0 0)` — tertiary text, deltas, disabled
- `--border`: `oklch(1 0 0 / 0.10)` — hairline rules
- `--border-soft`: `oklch(1 0 0 / 0.06)` — list separators
- `--border-strong`: `oklch(1 0 0 / 0.18)` — buttons, link underlines

**State color (three only; appear only when state demands):**
- `--red`: `oklch(0.685 0.250 29)` — expired, revoked, failed, high-severity breaks
- `--amber`: `oklch(0.810 0.185 84)` — expiring, scope mismatch, medium-severity breaks
- `--green`: `oklch(0.790 0.195 148)` — healthy, ok state

**Butler category hues** (`--category-1` through `--category-8`): mapped to butler letter-marks only. Never on buttons, borders, backgrounds, or UI chrome. Mapping: relationship (1), memory (2), calendar (3), health (4), household (5), education (6), qa (7), chronicler (8).

**Light mode** (paper-warm, oklch hue 85, not stark white): equivalent values in `primitives.jsx:23–41`.

#### Typography

| Role | Family | Size | Weight | Tracking | Leading |
|------|--------|------|--------|----------|---------|
| Display | sans (Inter Tight) | 44px | 500 | -0.025em | 1.08 |
| Title | sans (Inter Tight) | 22–24px | 500 | -0.015em | 1.2 |
| Body | sans (Inter Tight) | 13–14px | 400 | normal | 1.5 |
| Voice | serif (Source Serif 4) | 16px | 400 | normal | 1.6 |
| Eyebrow | mono (JetBrains Mono) | 10px | 400 | 0.14em | 1.0 |
| Mono inline | mono (JetBrains Mono) | 11px | 400 | normal | 1.4 |

**Hard rules:**
- Display weight **must be 500**, never 700. Tight tracking does the weight-work (`HANDOFF.md` §3, `DESIGN_LANGUAGE.md` §2b).
- **No bold display.** Period.
- Tabular numerals (`font-variant-numeric: tabular-nums`) on **every number, always** (`DESIGN_LANGUAGE.md` §2c).

#### Spacing & Rhythm

- **Base unit:** 4px multiples exclusively. Common: 4, 8, 12, 14, 16, 18, 24, 32, 36, 48, 56 (`DESIGN_LANGUAGE.md` §3e).
- **Page padding:** 48px vertical × 56px horizontal (`DESIGN_LANGUAGE.md` §3a).
- **Section gutter:** 56px.
- **Two-column editorial:** `grid-template-columns: 1.4fr 1fr; gap: 56px` (`DESIGN_LANGUAGE.md` §3b).
- **List item padding:** 8–18px vertical (10px for spine rows, 18px for read rows).
- **Line heights:** 1.08 (display), 1.2 (title), 1.5 (body), 1.55–1.6 (voice).

#### Motion

**Only allowed:** briefing paragraph cross-fade (200ms, `cubic-bezier(0.22, 1, 0.36, 1)`); sidebar chevron rotation (120ms, linear); theme toggle background fade (200ms, ease); tooltip appear/disappear (instant).

**Forbidden:** spring physics, bounce, parallax, scale-in, scale-on-hover, shimmer, skeleton-pulse, count-up animations, "delight" micro-interactions (`DESIGN_LANGUAGE.md` §6).

#### Hard "do not" list (composite — README five rules + HANDOFF §3 + BRIEF §9)

- No cards (bordered+shadowed boxes for list items). Hairlines and rhythm only.
- No drop shadows. One elevation.
- No bold display weight; 500 with tight tracking.
- State colour is foreground or border only. Never fill a row background with red. Only the 2px left-edge sliver may use colour-as-fill.
- Butler hues never leak. Letter-mark only.
- Status is never a word ("Connected", "Active", "Linked").
- No emoji anywhere — including empty states.
- Tabular numerals on every number, always.
- Padlock icons as decoration are banned (sidebar use only).
- Asterisks as the only proof a secret exists are banned (always pair with fingerprint + last-verified).
- Big "Connect" / "Reauthorize" CTAs with brand colour are banned (use commit pill).
- Stacking provider Setup cards is banned (replaced by row template + drawer).

---

## 2. Component impact

### Classification table

| Component | Verdict | Reuse target (if any) | Churn estimate | Notes |
|---|---|---|---|---|
| `DirectionPassport` | new | N/A | M | Orchestrates spine + page + tweaks; holds identity, focus, sort, search state. Replaces `SecretsPage.tsx` tab shell (lines 486–529). |
| `Spine` | new | N/A | M | Left index; search input, sort radios, identity chip, grouped by kind (needs-hand, CLI, System, User). |
| `SpineRow` | new | N/A | S | Single credential row; severity sliver, state dot, label, subline. |
| `SpineGroup` | new | N/A | S | Groups rows by kind; eyebrow + optional hint. |
| `SpineSearch` | new | N/A | S | Mono search input; filters spine entries by label/key. |
| `SortPicker` | new | N/A | S | Radio buttons: severity, recency, alpha. |
| `IdentitySwitcher` | adapt | EntityPicker (`SecretsPage.tsx:177–272`) | M | Current EntityPicker is a dropdown; redesign needs a compact identity chip in spine header. Adapt selection logic; redesign UI as a chip. |
| `PageUser` | replace | Integrations section (`SecretsPage.tsx:333–365`) + SecretsTable (user mode) | L | Collapse OAuth/token/apikey/webhook providers into one PageUser with per-kind KV variations. Delete the six provider Setup cards. |
| `PageSystem` | replace | SystemSecretsSection (`SecretsPage.tsx:58–169`) + SecretsTable (system mode) | M | Single system secret on page; shared vs local rowState, override affordance, plain-text-value branch. |
| `PageCli` | replace | `CLIAuthCard.tsx` | L | CLI runtime detail page; how-to-use snippet, rotate-with-reveal flow. |
| `WhatBreaks` | new | N/A | M | Lists butler features that fail on sick credentials; state-aware heading. Depends on the catalogue endpoint (Phase C bead 10, resolved as Option B server-side). |
| `ProbeResult` | new | N/A | S | Latency, code, timestamp, serif-italic message (verbatim provider error). |
| `ScopeBalance` | new | N/A | S | Numeric ratio (granted/required) with 1-row bar; compact spine view, full visa list on page. |
| `IdentityChip` | new | N/A | S | Name + role + color-coded dot; used in page header and spine. |
| `StampGlyph` | new | N/A | S | 1-char mono shape per audit action (✓ verified, ↻ rotated, ✕ failed, ⊘ revoked, ⊕ connected, ! warned, ⤳ overrode, ▷ attempted, ⊙ set). |
| `FingerprintRow` | new | N/A | S | Two-line stack: scheme·hash + verify command. |
| `SeverityPip` | new | N/A | S | 1-char mono pip (high/medium/low) for breaks rows. |
| `Eyebrow` | reuse | Not standalone today; extract from inline | S | Mono 10px eyebrow section titles. Add `/components/ui/Eyebrow.tsx`. |
| `Mono` | reuse | Not standalone today; wrapped inline | S | Extract to `/components/ui/Mono.tsx`. |
| `Voice` | reuse | Not standalone today; inline `<p>` | S | Add `/components/ui/Voice.tsx`. |
| `Display` | reuse | `<h1 className="text-3xl font-bold">` (`SecretsPage.tsx:505`) | S | Add `/components/ui/Display.tsx`; weight 500, tight tracking. |
| `Title` | reuse | `<h2 className="text-lg font-semibold">` scattered | S | Add `/components/ui/Title.tsx`. |
| `ProviderMark` | new | N/A | S | Mono 22px square letter-mark; hairline border, no color. |
| `StateDot` | reuse | `/components/ui/StateDot.tsx` exists | S | 6px circle; ok/amber/red/dim per state. Verify OKLch match against Dispatch spec. |
| `Sliver` | new | N/A | S | 2px vertical rail; colored only when state demands. |
| `StateLabel` | new | N/A | S | Mono 10px lowercase state label. |
| `Fingerprint` | new | N/A | S | Hash display with scheme/hash split. Replaces the `••••••••` masked-value blob. |
| `ScopeRow` | new | N/A | S | Flex wrap of scopes; granted vs required, missing called out amber. |
| `PillBtn` | reuse or adapt | `/components/ui/Pill.tsx` | S | Variants: pill, commit, danger. Verify current Pill against Dispatch spec. |
| `KV` | new | N/A | S | Label + mono value pair. Generic detail row. |
| `BlockHead` | new | N/A | S | Mono eyebrow with optional right caption. |
| `StampRow` | new | N/A | S | StampGlyph + date/time + action + actor + serif note. |
| `VisaRow` | new | N/A | S | Single scope row; dot + label + state indicator. |
| `TweaksPanel` | adapt | Not in current codebase; check ingestion/entity precedent | M | Reveal mode, default sort, show-verify-cmd, voice-paragraph toggle. State persistence via existing tweaks pattern if present. |
| `ButlerMark` | reuse | `/components/ui/ButlerMark.tsx` exists | S | 16px square letter-mark; butler hue only, no leakage outside boundary. |
| `StatusDot` | reuse or new | `StateDot.tsx` exists; may need variant | S | Verify StateDot covers all 4 states (ok/degraded/error/waiting). |

### Stack delta

- **Font imports** (Inter Tight / Source Serif 4 / JetBrains Mono): verify already loaded by prior Dispatch rollouts (ingestion-redesign, entity-redesign). If absent, add `@import` to `index.css`. **Effort: S** (likely present).
- **Color-token reconciliation**: dashboard `index.css` defines `--red/--amber/--green/--severity-*/--category-1..8` but does not name `--bg/--fg/--mfg/--dim/--border` explicitly (they may map through shadcn's `bg-background`/`text-foreground` Tailwind tokens). Verify exact variable names and reconcile if missing. **Effort: S–M.**
- **URL state binding** for `?focus=` `?identity=` `?sort=`: React Router 7.13 `useSearchParams()` already supported. **Effort: S.**
- **Tweaks-panel state persistence**: check if other redesigned pages (ingestion / entity) ship a tweaks pattern; reuse if so, otherwise localStorage. **Effort: S** if precedent exists; **M** if new.
- **WhatBreaks catalogue**: resolved as Option B per user decision — server-side table `public.provider_feature_catalogue` + `GET /api/secrets/breaks-catalogue` endpoint. Frontend renders from props (no client-side static). **Backend: M** (see §3 bead 10); **frontend: S**.
- **New API endpoints** (~10+ per HANDOFF.md §5): major Phase C scope; see §3 backend epic outline.
- **Audit page `?key=<key>` filter**: extend existing audit router. **Effort: S.**

**No breaking stack changes detected for other pages — the redesign is route-scoped.**

---

## 3. Backend contract delta

### Affordance inventory

| Affordance | Sub-page(s) | Data needed (fields) | Source of fixture |
|---|---|---|---|
| Spine inventory | `/secrets` | `{ cli: CliRuntime[], system: SystemSecret[], user: UserSecret[] }` aggregated by identity; counts by severity; "needs-hand" pinned group | `secrets-data.jsx:44–199` |
| Spine row rendering | `/secrets` | Per credential: `state`, `fingerprint`, `provider` (user) or `key` (system) or `id` (cli), `breaks` array (for high-severity count) | `secrets-data.jsx` + `prompts/00-foundation.md` §5.3 |
| Spine search/filter | `/secrets?sort=severity\|recency\|alpha` | Full inventory with sort key; identity enumeration for switcher | `secrets-data.jsx` |
| Identity switcher | `/secrets?identity=<id>` | Household member roster (name, id, entity reference) from relationship butler; **projection lens semantics — all mutations remain owner-privileged regardless of switcher state** | `HANDOFF.md` §2 |
| PageUser detail read | `/secrets?focus=u:<provider>` | `UserSecret` full shape: `state`, `fingerprint`, `issued`, `expires`, `lastVerified`, `lastUsed`, `scopesRequired`, `scopesGranted`, `feeds`, `failureTail`, `breaks[]`, `test`, `audit[]`, `webhook` (if kind=webhook) | `secrets-data.jsx:44–162`; `HANDOFF.md` §5.3 |
| PageSystem detail read | `/secrets?focus=s:<key>` | `SystemSecret`: `key`, `category`, `rowState` (shared\|local\|missing), `fingerprint`, `description`, `source`, `target`, `lastVerified`, `usedBy[]`, `plainValue` (when applicable), `breaks[]`, `test`, `audit[]` | `HANDOFF.md` §5.3; `prompts/03-page-system.md` |
| PageCli detail read | `/secrets?focus=c:<id>` | `CliRuntime`: `id`, `label`, `fingerprint`, `state`, `issued`, `expires`, `lastUsed`, `scopesRequired`, `scopesGranted`, `test` | `HANDOFF.md` §5.3 |
| Probe result | User/System/Cli detail pages | `TestResult`: `{ ok, code, latencyMs, at (pre-formatted "14:21 today"), message? }` | `secrets-data.jsx:62,86,110,134,157` |
| WhatBreaks catalogue render | User/System/Cli detail pages | `BreakEntry[]` per credential: `{ butler, feature, severity }`. Merged server-side from `public.provider_feature_catalogue` + per-credential state. | `secrets-data.jsx:57–61,83–85,105–109,130–133,153–156` |
| Audit history (inline) | User/System/Cli detail pages | Last 10 `AuditEvent[]`: `{ ts, actor, action, note }`. Full history at `/audit?key=<key>` | `secrets-data.jsx:63–68,87–91,111–116,135–138,158–161` |
| OAuth reauthorize flow | `/secrets/connect/<provider>` → callback | Redirect URL for OAuth begin; callback re-routes to `/secrets?focus=u:<provider>&toast=connected` | `HANDOFF.md` §4 |
| Probe mutation | User/System/Cli pages | Probe trigger returns new `TestResult` + refreshes credential cache | `prompts/02-page-user.md` §Mutations |
| Rotate mutation | User/System/Cli pages | Replace value; request `{ value }` (or `{ value, target }` for system); refreshes credential; writes audit | `prompts/02-page-user.md`, `prompts/03-page-system.md` |
| Disconnect/Revoke mutation | User/System/Cli pages | Soft-delete credential; writes audit | `prompts/02-page-user.md` §Mutations |
| System override | System page | Modal listing all butlers; `POST { value, target: '<butler>' }` creates local row; `DELETE ?target=<butler>` removes override | `prompts/03-page-system.md` |
| Fingerprint verification command | User/System pages | Reveal toggleable mono line: `"echo -n '<value>' \| sha256sum \| cut -c1-8"` | DESIGN_LANGUAGE.md typography |

### API delta

| Path | Method | Status | Existing handler | Request shape | Response shape | Evidence | Drives affordance(s) |
|---|---|---|---|---|---|---|---|
| `GET /api/secrets/inventory?identity=<id>` | GET | new | — | Query: `identity` (uuid) | `{ cli: CliRuntime[], system: SystemSecret[], user: UserSecret[] }` | spec: `prompts/00-foundation.md` §5.1; `HANDOFF.md` §5.1 | Spine inventory + all detail pages |
| `GET /api/secrets/user/<provider>?identity=<id>` | GET | new | — | Path: `provider`; Query: `identity` | `UserSecret` (single) | spec: `HANDOFF.md` §5.1 | PageUser deep-link refresh |
| `GET /api/secrets/system/<key>` | GET | new | — | Path: `key` | `SystemSecret` (single) | spec: `HANDOFF.md` §5.1 | PageSystem deep-link refresh |
| `GET /api/secrets/cli/<id>` | GET | new | — | Path: `id` | `CliRuntime` (single) | spec: `HANDOFF.md` §5.1 | PageCli detail read |
| `GET /api/secrets/audit/<scope>/<key>` | GET | new | — | Path: `scope`, `key`; Query: `limit=50` | `AuditEvent[]` | spec: `HANDOFF.md` §5.1 | Audit stamps + inline history |
| `GET /api/secrets/breaks-catalogue?provider=<p>` | GET | new | — | Query: `provider` (optional) | `BreakEntry[]` keyed by provider | spec: derived from Phase C bead 10 (Option B chosen) | WhatBreaks rendering |
| `POST /api/secrets/user/<provider>/reauthorize` | POST | new | — | Path: `provider`; Query: `identity` | `{ redirect_url }` | spec: `HANDOFF.md` §5.2 | PageUser OAuth begin |
| `POST /api/secrets/user/<provider>/rotate` | POST | new | — | Path: `provider`; Query: `identity`; Body: `{ value }` | `UserSecret` (updated) | spec: `HANDOFF.md` §5.2 | PageUser rotate |
| `POST /api/secrets/user/<provider>/disconnect` | POST | new | — | Path: `provider`; Query: `identity` | `{ status: "disconnected" }` | spec: `HANDOFF.md` §5.2 | PageUser disconnect |
| `POST /api/secrets/user/<provider>/probe` | POST | new | — | Path: `provider`; Query: `identity` | `TestResult` | spec: `HANDOFF.md` §5.2 | PageUser probe |
| `POST /api/secrets/system/<key>` | POST | new | — | Path: `key`; Body: `{ value, target: "shared" \| "<butler>" }` | `SystemSecret` (updated) | spec: `HANDOFF.md` §5.2; `prompts/03-page-system.md` | PageSystem set/override |
| `POST /api/secrets/system/<key>/probe` | POST | new | — | Path: `key` | `TestResult` | spec: `HANDOFF.md` §5.2 | PageSystem probe |
| `DELETE /api/secrets/system/<key>` | DELETE | new | — | Path: `key`; Query: `target` | `{ status: "deleted" }` | spec: `HANDOFF.md` §5.2 | PageSystem delete |
| `POST /api/secrets/cli/<id>/rotate` | POST | new | — | Path: `id` | `{ fingerprint, value }` (value once) | spec: `HANDOFF.md` §5.2 | PageCli rotate |
| `POST /api/secrets/cli/<id>/revoke` | POST | new | — | Path: `id` | `{ status: "revoked" }` | spec: `HANDOFF.md` §5.2 | PageCli revoke |
| `GET /api/oauth/<provider>/start` | GET | extend | `src/butlers/api/routers/oauth.py:156–250` (Google-only) | Query: `redirect_uri`, `account_hint?`, `force_consent?`; Path: `<provider>` | `{ authorization_url }` | live-endpoint: `oauth.py:156–300` (Google only; needs generalization) | OAuth begin for PageUser |
| `GET /api/oauth/<provider>/callback` | GET | extend | `oauth.py:400–600` (Google-only) | Query: `code`, `state` | Redirect to `/secrets?focus=u:<provider>&toast=connected` | live-endpoint: `oauth.py:400–600` | OAuth callback re-route |
| `GET /api/audit?key=<key>` | GET | extend | `src/butlers/api/routers/audit.py:180–220` exists but takes `action/actor/since`, not `key` | Query: `key` | `PaginatedResponse[AuditLogEntry]` filtered to credential | live-endpoint: `audit.py:180–220` needs `key` param | Audit deep-link |
| `GET /api/entities?entity_type=person` | GET | exists | `roster/relationship/api/router.py:XXX` | Query: `q`, `entity_type`, `limit` | `{ data: EntitySummary[] }` | live-endpoint: `relationship/api/router.py` | Identity switcher source |

**No `fixture`-only rows remain.** All shape evidence is grounded in either the bundle's spec docs (`HANDOFF.md` §5 / `prompts/00-foundation.md` §5) or live endpoint citations. The bundle's `secrets-data.jsx` mock is **illustrative**, and per Phase C every shape is independently anchored to the spec doc.

### Schema migration impact

#### New tables

- **`public.secret_probe_log`** (owned by switchboard / cross-butler; written by every probe endpoint).
  Columns: `id`, `credential_scope` (user\|system\|cli), `credential_key` (provider/key/id), `ok`, `code`, `latencyMs`, `at` (timestamptz; server formats to "14:21 today" on read), `message`, `recorded_at`.
  Index: `(credential_scope, credential_key, recorded_at DESC)` for fast LRU/recent-10 queries.
  Retention: ≥ 90 days live + archive path for full `/audit?key=` reel.

- **`public.provider_feature_catalogue`** (Option B per user decision; owned by switchboard; written by butlers at startup or via migration seed).
  Columns: `provider` (slug), `butler` (butler-name or `'*'`), `feature` (user-facing label), `severity` (high\|medium\|low), `required_scopes` (jsonb array).
  Index: `(provider, butler)`.
  Bootstrapped via Alembic seed during initial migration; each butler may UPSERT its own (provider, feature) rows on startup so the catalogue tracks the actual roster as it grows.

#### Column extensions

- **`butler_secrets`** (per-butler schema): add `lastVerified`, `lastTestOk`, `lastTestCode`, `lastTestMessage`. Backfill: NULL on migration; lazy-populate on first probe.
- **`entity_info`** (relationship schema): same columns. Same backfill rule.

#### Fingerprint computation

- **Decision: on-read, never persisted.** Use a PostgreSQL function `sha256(secret_value)::text[1:8]` evaluated in the SELECT. No migration; no risk of hash-as-side-channel-leak.

#### Audit log augmentation

- **No new table.** Reuse `public.audit_log` (core_092) for probe/rotate/disconnect/reauthorize events.
- **New audit action enum values:** `verified`, `failed`, `rotated`, `connected`, `disconnected`, `warned`, `overrode`, `attempted`, `set`.
- **New index:** `(target, recorded_at DESC)` for `/api/audit?key=` filtering (where `target` normalizes credential key format).

#### CLI runtime storage

- Verify current store exists (referenced by `CLIAuthCard`). If present, **extend** with `fingerprint`/`test` fields; if missing, **create** `cli_auth_runtime` (owned by switchboard).

#### Cross-butler concerns

- Identity enumeration uses existing `GET /api/entities` on the relationship router. No new cross-butler queries; respects schema isolation.
- Probe-log writes are cross-butler by design (every probe regardless of which butler owns the credential writes to one table). This is consistent with the existing public-schema audit_log pattern.

### Proposed backend epic

**Epic title:** `secrets redesign — backend contracts`

**Child beads** (proposed; bead creation is Phase G):

1. **[Core DB] `public.secret_probe_log` table + audit action enum values** — Create table; add `verified`/`failed`/`warned`/`attempted` to audit action enum; add `(target, recorded_at DESC)` audit index. **Effort: M.** Blocks: 3, 5, 7, 8, 9.
2. **[Core DB] Extend `butler_secrets` + `entity_info` with test-state columns** — `lastVerified/lastTestOk/lastTestCode/lastTestMessage`. Alembic migration + backfill (NULL initial). **Effort: S.** Blocks: 3.
3. **[API] Inventory endpoint** — `GET /api/secrets/inventory?identity=<id>`. Merge probe-log LRU + on-read fingerprint computation. **Effort: M.** Blocks: 4, 6, 11.
4. **[API] Per-credential reads** — `GET /api/secrets/user/<provider>`, `/system/<key>`, `/cli/<id>`. Reuse bead-3 query logic. **Effort: S.** Blocks: 7, 8, 9.
5. **[API] Audit history endpoint** — `GET /api/secrets/audit/<scope>/<key>`; server-side timestamp pre-formatting. **Effort: S.** Drives frontend audit display.
6. **[API] `public.provider_feature_catalogue` + breaks-catalogue endpoint** — Create table + Alembic seed; per-butler startup UPSERT hook; `GET /api/secrets/breaks-catalogue?provider=<p>`. **Effort: M.** Drives WhatBreaks rendering.
7. **[API] OAuth per-provider unification — reauthorize flow** — Generalize `oauth.py` to support `<provider>` path; resolve scope-set from butler.toml; `POST /api/secrets/user/<provider>/reauthorize?identity=<id>`. **Effort: L.** Blocks: 8, 12.
8. **[API] User credential mutations** — rotate/disconnect/probe. Audit on every action. **Effort: M.**
9. **[API] System credential mutations** — set/probe/override/delete. Audit. **Effort: M.**
10. **[API] CLI runtime mutations** — rotate (returns fingerprint+value once) / revoke. Verify storage. **Effort: S.**
11. **[Audit] Extend `/api/audit` with `key=` filter** — Add `key` query param; filter `public.audit_log` by normalized `target`. **Effort: S.**
12. **[API] OAuth callback re-routing to `/secrets?focus=u:<provider>&toast=connected`** — Update existing callback; integrate page-of-origin bookkeeping so `/ingestion/connectors`-initiated dances return there instead. **Effort: S.** Depends on bead 7.

**Dependency graph:**
```
1 (probe log + audit enum) ──► 3 (inventory) ──► 4 (per-credential reads) ──► {7, 8, 9, 10}
                                                                                 │
2 (extend tables) ────────────► 3                                                │
                                                                                 ▼
6 (catalogue) ──► WhatBreaks render                                          12 (OAuth callback reroute)
5 (audit history) ──► StampRow + inline audit
11 (audit key filter) ──► /audit?key= link
```

Critical path: 1 → 2 → 3 → 4 → 7 → 12. Parallelisable: 5, 6, 11. Estimated ~3 weeks of serial backend work + parallel branches.

---

## 4. Guardrails

### LLM-cost feasibility

Pricing source: `references/llm-pricing.md` (verify `last_verified` ≥ 60 days back against anthropic.com/pricing before spec phase, per Phase D protocol).

| Feature | Trigger model | tokens_in | tokens_out | Model class | $/call | Freq/user/day | $/user/day (v1: users=1) | $/user/day (sensitivity: users=100) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| Voice paragraph under page heading (`provider.brief` + "Feeds the {feeds} butler", `secrets-pages.jsx:202`) | Stored prose — static catalogue concat | n/a | n/a | none | $0 | n/a | **$0.00** | $0.00 | **green** |
| `WhatBreaks` catalogue rendering (Phase C bead 6) | Static catalogue — server-side table read | n/a | n/a | none | $0 | n/a | **$0.00** | $0.00 | **green** |
| `ProbeResult` message tail (`secrets-evidence.jsx:123-128`) | Verbatim provider error string | n/a | n/a | none | $0 | n/a | **$0.00** | $0.00 | **green** |
| `StampRow` serif note (`secrets-pages.jsx:66-68`) | Stored audit note written at audit-write time | n/a | n/a | none | $0 | n/a | **$0.00** | $0.00 | **green** |
| Page header voice paragraph (`secrets-passport.jsx:77-87`) | Templated string from KPI counts | n/a | n/a | none | $0 | n/a | **$0.00** | $0.00 | **green** |
| Empty-state serif lines | Hard-coded literals | n/a | n/a | none | $0 | n/a | **$0.00** | $0.00 | **green** |
| Spine subline copy (`secrets-spine.jsx:309-315`) | Templated string from state catalogue | n/a | n/a | none | $0 | n/a | **$0.00** | $0.00 | **green** |
| `+ verify cmd` expander (`secrets-evidence.jsx:239-242`) | Hard-coded shell command literal | n/a | n/a | none | $0 | n/a | **$0.00** | $0.00 | **green** |
| LLM-key probe (1-token completion against Anthropic/OpenAI/Gemini) | `provider-api-cost` — user-initiated click only | ~10 | 1 | (provider) | ~$0.00005 | ~5/day | **~$0.0003** | $0.03 | **green** (provider cost, not Butlers inference) |

#### Red verdicts

None.

#### Recommended de-scopes before spec phase

None. **One explicit guardrail to lock into the spec:** the spec MUST forbid "smart explanation" / "narrative caption" features on `/secrets` surfaces. Future beads proposing LLM-elaborated scope explanations, audit summaries, or "would-break" narratives must be rejected by reference to this brief. (Captured as the last bullet of Section 0's "What we are deliberately NOT doing".)

### Manifesto / identity preservation

| Butler | Manifesto file:line cited | What the redesign does that touches identity | Verdict |
|---|---|---|---|
| general | `roster/general/MANIFESTO.md:5-15` ("trusted place where anything goes", "Reliability") | System secrets page surfaces shared infra credentials; renders mono titles + serif description from `SYSTEM_SECRETS[*].description`. No voice/personality asserted. Manifesto silent on per-credential UI. | identity preserved |
| messenger | `roster/messenger/MANIFESTO.md:11-18, 25` ("delivery execution", "does not classify messages or perform routing decisions") | Flattens Telegram + WhatsApp + Email into one User-tab row template. Manifesto declares no per-channel UI chrome contract. | identity preserved |
| qa | `roster/qa/MANIFESTO.md:172-173` (explicitly directs operators to `/secrets` to provision `BUTLERS_QA_GH_TOKEN`) | Redesign keeps `/secrets` as the single inventory + reauth surface; spine groups System secrets including QA's GH token. 1-call probe semantics align with `ProbeResult`. | identity preserved |
| health | `roster/health/MANIFESTO.md:31-32` ("Companion, Not a Doctor"; "Your health data is yours") | Google OAuth + Home Assistant token rendered as PageUser with scope inventory, fingerprint, last-verified — owner-controlled. Manifesto silent on per-credential UI. | identity preserved |
| education | `roster/education/MANIFESTO.md` (whole) | LLM keys consumed; no per-credential UI claim. | identity preserved |
| relationship | `roster/relationship/MANIFESTO.md:44-65` (Dunbar model — owner-centric; no credential-visibility axis declared) | IdentityChip + Spine identity switcher read identity data from `IDENTITIES` source. Manifesto sets no constraint either way. (Member-scope claim resolved as projection lens per §0; matches existing single-owner doctrine.) | identity preserved |
| chronicler | `roster/chronicler/MANIFESTO.md:14-15, 24-25, 60-61` ("evidence with provenance", "preserve source provenance on every row", "never claim certainty I do not have") | StampGlyph + StampRow + FingerprintRow render action-typed glyphs with full provenance (actor + note + ts). Serif italic note is audit text, not LLM elaboration. | identity preserved |
| home | `roster/home/MANIFESTO.md:39-40` ("transparent about the automations") | `home_assistant_token` renders as PageUser with fingerprint + expires + scopes (`states.read`, `events.fire`). Transparent-by-design. | identity preserved |
| lifestyle | `roster/lifestyle/MANIFESTO.md:36-37` ("stays in its lane") | Spotify/Steam/OwnTracks as User-tab rows. Voice paragraph reads `provider.brief` + "Feeds the chronicler butler" — consistent with lifestyle's data-handoff role. | identity preserved |
| finance | `roster/finance/MANIFESTO.md` (whole) | Depends on Gmail plumbing; not directly mentioned in `secrets-data.jsx`. No per-credential UI claim. | identity preserved |
| travel | `roster/travel/MANIFESTO.md` (whole) | Same as finance. | identity preserved |

#### Drift write-ups

**No `identity drift flagged` rows.** One doctrine-gap was surfaced (owner-vs-member scope) and **has been resolved by user decision**: the Vision's member-scope claim is softened to projection-lens semantics, matching existing single-owner doctrine in `about/heart-and-soul/security.md`. See §0 "Primary audience" for the binding language. No manifesto or doctrine document needs to change for v1.

#### Recommended manifesto updates

None required for v1. (If a future RFC under `about/legends-and-lore/` introduces a household-member privilege tier, this redesign is forward-compatible — the same `?identity=<id>` URL state will then bind to a session principal rather than to a projection lens.)

### Intent compliance

No red verdicts and no drift verdicts to cross-reference. The single intent-adjacent decision — member scope — was resolved at brief-synthesis time by softening the Vision (per user choice). Intent and current implementation plan are now consistent. The forbid-LLM-narration guardrail is captured both in §0 ("What we are deliberately NOT doing") and as a Recommended de-scope above; the spec phase MUST surface this as a binding constraint.

---

## 5. Open questions

Consolidated from Phases A, B, C, D. Each entry tagged with originating phase and citation. These are the items `/project-direction` Phase 1 (doctrine) and Phase 2 (spec) must resolve before bead creation.

1. **[Phase A / BRIEF.md:419-423] Probe safety for paid LLM keys.** For Anthropic / OpenAI / Gemini probes, is a 1-token completion (~$0.00005) acceptable, or should probes use a `models.list`-style free endpoint? Phase D rates current proposal `green` either way; user decision needed for spec.
2. **[Phase A / BRIEF.md:425-429] Audit retention surface.** Inline last-5 + `open /audit ↗` link to `/audit?key=<key>` is the default plan; confirm the param name (`key`) before extending the audit router.
3. **[Phase A / BRIEF.md:429-434] Webhook secret rotation external-reconfig instructions.** Where in the PageUser does the redesign surface "you also need to update OwnTracks/Telegram with the new webhook"? Current plaque says "secret rotates with token" — confirm sufficient.
4. **[Phase A / BRIEF.md:436-438] Identity switcher chrome for members.** Per projection-lens semantics (now binding §0), the switcher is always owner-facing. Confirm: hide entirely when only one identity is in scope, or always render the chip even when collapsed?
5. **[Phase A / prompts/00-foundation.md §5.3] Focus-key URL encoding round-trip.** `u:provider`, `s:KEY`, `c:id` must round-trip through `parseFocus`/`encodeFocus` cleanly through identity-switch and sort-change. Confirm encoding is URL-safe (colon vs encoded variant).
6. **[Phase A / HANDOFF.md §2.5] Per-kind page variations.** Confirm exact field deltas for oauth/token/apikey/webhook PageUser variants before Phase C bead 4 freezes the response shape.
7. **[Phase A] Spine pin behaviour for "needs-hand" group.** Always pinned and severity-sorted regardless of `?sort=` mode. Backend tag or client-side computed (`lastVerified < 7d AND state != ok`)? Decision drives bead 3 query shape.
8. **[Phase A] `WhatBreaks` empty-state behaviour.** When state=`never_set` and the catalogue cannot pre-populate dependent features, omit the block entirely or show "no features depend on this"?
9. **[Phase A] Tweaks persistence mechanism.** Reveal-mode / default-sort / show-verify-cmd / voice-paragraph toggles persist via the bundle's `EDITMODE-BEGIN`/`EDITMODE-END` block. Confirm storage (`localStorage` vs URL fragment vs server-side user prefs) — check ingestion/entity precedent first.
10. **[Phase B] Font verification.** Inter Tight / Source Serif 4 / JetBrains Mono presumed loaded by prior Dispatch rollouts. Verify before bead 4 frontend work.
11. **[Phase B] Color-token reconciliation.** Dashboard `index.css` may or may not name `--bg/--fg/--mfg/--dim/--border` directly. Reconcile against Dispatch spec.
12. **[Phase B] Member-view access control implementation.** Now resolved as projection-lens (§0). Confirm spec phase encodes this as a documented invariant: backend never enforces identity-scoped access; switcher state is purely a view filter.
13. **[Phase C] OAuth per-provider routing model.** Current `oauth.py` is Google-specific. Bead 7 generalizes. Decision needed: live under `src/butlers/api/routers/oauth.py` with `<provider>` path, or new `src/butlers/api/routers/secrets.py` namespace? Affects router auto-discovery wiring.
14. **[Phase C] Audit `?key=` filter — target normalization.** What format does `public.audit_log.target` use today, and does it match the redesign's credential-key encoding (`u:google`, `s:BUTLER_TELEGRAM_TOKEN`, `c:claude`)? May need a normalization function.
15. **[Phase D] LLM-narration guardrail enforcement.** Spec must encode the §0 "no smart elaboration on `/secrets`" rule. Suggest a lint rule or a doctrine ADR under `about/heart-and-soul/`.
16. **[Phase D] Pricing reference `last_verified` check.** Confirm `references/llm-pricing.md` is < 60 days old before spec phase; if not, refresh from anthropic.com/pricing.

---

## 6. Handoff to `/project-direction`

This brief is the input to a `/project-direction` run with **feature evaluation focus** scoped to `secrets`.

Concrete invocation:

```
/project-direction --focus=feature \
  --brief=docs/redesigns/2026-05-25-secrets-brief.md \
  --bundle=pr/overview/secrets-redesign/ \
  --binding-design-language=pr/overview/secrets-redesign/DESIGN_LANGUAGE.md \
  --binding-design-intent=docs/redesigns/2026-05-25-secrets-brief.md#0-design-intent \
  --red-flag-policy=descope-or-escalate
```

Carry-forward instructions:

- `pr/overview/secrets-redesign/DESIGN_LANGUAGE.md` is **binding**. Every spec section must preserve it.
- Section 0 of this brief is **binding**. Spec drift away from intent fails reconciliation.
- The "no LLM narration on `/secrets`" guardrail is part of intent and must be encoded as a spec invariant.
- The projection-lens semantics for the identity switcher are **binding** for v1; backend must not enforce identity-scoped access (no auth boundary).
- The WhatBreaks catalogue resolution is **Option B** (server-side `public.provider_feature_catalogue` + endpoint).
- All `red`-verdict LLM features (none in this brief) must be de-scoped or escalated before being specced.
- All `identity drift flagged` items (none) must be resolved before being specced.
- After `/project-direction` Phase 3 produces the beads graph, Phase G of `butlers-redesign-prompt` will split out the backend epic (`secrets redesign — backend contracts`) per §3.
