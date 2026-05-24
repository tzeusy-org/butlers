# Secrets redesign — Claude Code hand-off

> Read this end-to-end before writing any code. The prototype at
> `secrets-redesign/Secrets.html` is the authoritative reference for
> visuals, behaviour, and data shapes. When this document and the
> prototype disagree, **the prototype is the spec**.

---

## 1. Orientation

You are shipping a redesigned **`/secrets`** page in `frontend/`. The
existing `frontend/src/pages/SecretsPage.tsx` is a 3-tab shell
(`System / User / CLI runtimes`) wrapping a generic
`SecretsTable`. The redesign replaces all three tabs with a single
**passport-book** surface:

- A left **spine** indexes every credential the system holds —
  pinned `needs hand` group, then CLI runtimes, then system secrets,
  then user integrations.
- A right **page** opens the focused credential in editorial depth:
  heading + state plaque, dense KV band, scopes (when applicable),
  *what breaks*, probe result, audit stamps, cross-references,
  commit footer.

The redesign does **not** migrate storage. System secrets stay in
`butler_secrets`. User secrets stay on `entity_info`. CLI runtimes
stay where they live now. The redesign is presentational + adds a
small number of new endpoints (see §5).

This is a designed surface in the Dispatch language. **Read
`DESIGN_LANGUAGE.md` before writing CSS.** When porting the
prototype's inline styles to your component framework, do not paper
over the visual language with shadcn defaults. The Dispatch atoms
already in `frontend/src/components/ui/` are your starting point.

---

## 2. The decisions already taken

These are settled. Do not re-litigate during implementation:

1. **One surface, no tabs.** The three families (CLI / System / User
   integrations) become spine sections, not tabs.
2. **`/secrets` is the source of truth.** `/ingestion/connectors`
   deep-links here for reauth; both pages can trigger reauth.
3. **Mixed visibility.** The owner sees every household member's
   user secrets via the identity switcher in the spine. Members see
   only their own; the System and CLI sections are owner-only.
4. **Reveal is opt-in, evidence is default.** Fingerprint + scopes +
   last-verified + provider state are the primary affordances. The
   value-reveal button still works, but it is no longer the page's
   centre of gravity.
5. **Per-kind pages.** OAuth, token, apikey, and webhook providers
   each get a slightly different KV band and footer. See the
   prototype's `secrets-pages.jsx`.
6. **The serif voice paragraph is a single sentence.** State is
   communicated by the plaque + what-breaks + KV band — *not* by
   conditional sentences glued onto the voice line. The Tweaks panel
   exposes a toggle to hide the voice line entirely.
7. **Sticky-tab sub-nav stays out.** Sub-routes are URL params, not
   sticky tabs. The spine is the navigation.

---

## 3. The visual language — non-negotiables

(Beyond what's in `DESIGN_LANGUAGE.md`, these are the rules that get
violated most often when porting SaaS conventions.)

- **No cards.** No bordered+shadowed boxes for list items. Hairlines
  and rhythm. `padding: 24px` on a list item is card thinking; don't.
- **No drop shadows on anything.** One elevation.
- **No bold display weight.** Display is always weight 500; tight
  tracking does the work that weight would do (`letter-spacing:
  -0.025em`).
- **State colour is foreground or border only.** Never fill a row
  background with red. The 2px attention sliver on the left edge is
  the only acceptable colour-as-fill.
- **Butler hues never leak.** Butler category colour appears only on
  the letter-mark of the corresponding butler. Not on buttons. Not on
  hovers. Not on borders.
- **Status is never a word.** Not "Connected", not "Active", not
  "Linked". Use one of {dot, sliver, numeral, colour}. The state
  plaque IS the word, but it earns its place by being the headline.
- **No emoji.** Anywhere. Including empty states. Empty states are a
  single serif-italic sentence.
- **Tabular numerals on every number, always.** Add
  `font-variant-numeric: tabular-nums` globally to mono and numeric
  spans.

---

## 4. Routes and components

The redesign lives at one top-level route. Sub-state is in URL params.

| Route | Behaviour | Status |
|---|---|---|
| `/secrets` | The passport surface. Default focus is the most-severe credential for the active identity. | **REPLACE** existing `SecretsPage.tsx` |
| `/secrets?focus=<key>` | Focus a specific credential. `<key>` is `u:<provider>`, `s:<KEY>`, or `c:<id>` (see §5.3). | **NEW** |
| `/secrets?identity=<id>` | Switch identity (owner only). Defaults to the logged-in user. | **NEW** |
| `/secrets?sort=<mode>` | `severity \| recency \| alpha`. Optional. | **NEW** |
| `/secrets/connect/<provider>` | Begin the OAuth dance for `<provider>` against the active identity. Existing handler if any; otherwise new. | **NEW or EXTEND** |
| `/secrets/oauth/callback/<provider>` | The OAuth callback. Re-routes to `/secrets?focus=u:<provider>&toast=connected`. | **EXTEND existing handler** |
| `/ingestion/connectors/<provider>/<identity>` | Deep-link to the channel-side view (existing). The page footer links here under "elsewhere". | **UNCHANGED** |
| `/audit?key=<key>` | Existing audit log filtered to this credential. | **UNCHANGED** — confirm the param works |

Component file structure (suggested — match your project conventions):

```
frontend/src/pages/
  SecretsPage.tsx                ← top-level, reads URL params, mounts <Spine /> + <Page />
frontend/src/components/secrets/
  Spine.tsx                       ← left index
  SpineRow.tsx
  SpineSearch.tsx
  SortPicker.tsx
  IdentitySwitcher.tsx
  pages/
    UserPage.tsx                  ← integration page (per-kind variation inside)
    SystemPage.tsx
    CliPage.tsx
  evidence/
    Fingerprint.tsx
    WhatBreaks.tsx
    ProbeResult.tsx
    ScopeBalance.tsx
    VisaRow.tsx
    StampGlyph.tsx
    StampRow.tsx
    HeadingBand.tsx
    StatePlaque.tsx
  TweaksPanel.tsx                 ← if not already factored
```

---

## 5. The API surface

### 5.1 GET endpoints (canonical reads)

| Endpoint | Returns | Notes |
|---|---|---|
| `GET /api/secrets/inventory?identity=<id>` | `{ cli: CliRuntime[], system: SystemSecret[], user: UserSecret[] }` | One round trip for the whole page. Owner-only for non-self identity. |
| `GET /api/secrets/user/<provider>?identity=<id>` | `UserSecret` (single) | For deep-link refresh. |
| `GET /api/secrets/system/<key>` | `SystemSecret` (single) | Owner-only. |
| `GET /api/secrets/cli/<id>` | `CliRuntime` (single) | Owner-only. |
| `GET /api/secrets/audit/<scope>/<key>` | `AuditEvent[]` | `<scope>` is `user|system|cli`. Last 50 events. The page shows the first 10; the rest is `/audit?key=…`. |

### 5.2 Mutation endpoints

| Endpoint | Action |
|---|---|
| `POST /api/secrets/user/<provider>/reauthorize?identity=<id>` | Begin OAuth dance; returns redirect URL. |
| `POST /api/secrets/user/<provider>/rotate?identity=<id>` | Replace the value, keep the slot. Body: `{ value: string \| OAuthGrant }`. Writes audit. |
| `POST /api/secrets/user/<provider>/disconnect?identity=<id>` | Clear credential. Writes audit. Soft delete. |
| `POST /api/secrets/user/<provider>/probe?identity=<id>` | 1-call probe; returns `TestResult`. |
| `POST /api/secrets/system/<key>` | Set value; body `{ value, target }`. `target=shared` or `<butler>` for a local override. |
| `POST /api/secrets/system/<key>/probe` | 1-call probe. |
| `DELETE /api/secrets/system/<key>?target=<t>` | Clear (shared or one override). |
| `POST /api/secrets/cli/<id>` | Set token; body `{ value }`. |
| `POST /api/secrets/cli/<id>/rotate` | Issue new token; returns the new fingerprint (and *the value once*, for the user to copy). |
| `POST /api/secrets/cli/<id>/revoke` | Revoke. Writes audit. |

### 5.3 Data shapes

These mirror `secrets-data.jsx` in the prototype. Treat that file as
the contract. **TypeScript types belong in
`frontend/src/api/types/secrets.ts`.**

```ts
type ProviderKind = 'oauth' | 'token' | 'apikey' | 'webhook';

interface Provider {
  id: string;
  label: string;        // display name
  glyph: string;        // single uppercase letter
  kind: ProviderKind;
  authority: string;    // issuing host
  brief: string;        // one serif sentence; the page's first voice line
  cadence: string;      // human label: "on demand · refreshes hourly"
}

type State =
  | 'ok'
  | 'expiring'
  | 'expired'
  | 'revoked'
  | 'scope_mismatch'
  | 'rotating'
  | 'never_set';

interface TestResult {
  ok: boolean;
  code: number | null;
  latencyMs: number;
  at: string;           // "14:21 today" — server pre-formats. UI never formats.
  message?: string;     // serif-italic tail on failure
}

interface BreakEntry {
  butler: string | '*'; // '*' = every butler that talks to a model
  feature: string;      // user-friendly feature name
  severity: 'high' | 'medium' | 'low';
}

interface AuditEvent {
  ts: string;           // "YYYY-MM-DD HH:MM"
  actor: 'system' | string;
  action: 'verified' | 'rotated' | 'failed' | 'revoked' | 'connected'
        | 'disconnected' | 'warned' | 'overrode' | 'attempted' | 'set';
  note: string;
}

interface UserSecret {
  provider: string;
  identity: string;
  state: State;
  fingerprint: string | null;     // "sha256:7a3f9e2c" (8-char prefix; never the value)
  issued: string | null;          // "YYYY-MM-DD"
  expires: string | null;
  lastVerified: string | null;
  lastUsed: string | null;
  scopesRequired: string[];
  scopesGranted: string[];
  feeds: string[];                // butler names
  webhook?: string;               // present for kind='webhook'
  failureTail?: string;
  breaks: BreakEntry[];
  test: TestResult | null;
  audit: AuditEvent[];
}

interface SystemSecret {
  key: string;
  category: 'core' | 'telegram' | 'google' | 'gemini' | 'email' | 'home_assistant' | string;
  rowState: 'shared' | 'local' | 'missing';
  fingerprint: string | null;
  description: string;
  source: string;
  target: 'shared' | string;       // butler id if local
  lastVerified: string | null;
  usedBy: string[];                // '*' = all butlers
  plainValue?: string;             // when the value is plain text (e.g. sender address)
  breaks: BreakEntry[];
  test: TestResult | null;
  audit: AuditEvent[];
}

interface CliRuntime {
  id: string;
  label: string;
  fingerprint: string | null;
  state: State;
  issued: string | null;
  expires: string | null;
  lastUsed: string | null;
  scopesRequired: string[];
  scopesGranted: string[];
  test: TestResult | null;
}
```

### 5.4 The `breaks` array (this is the dramatic anchor — get it right)

When a credential is sick, the **what breaks** block on the page lists
the butler features that will silently fail. This is computed
server-side from the catalogue of features that reference the
credential's `provider` + `identity` (or `key` for system secrets).

Behaviour:

- For an **expired / revoked** credential: severity `high` items
  render in red (`var(--red)`) and the block heading says
  *"what breaks"*.
- For a **scope-mismatch** credential: only features that depend on
  the missing scope(s) are listed; the block still says *"what
  breaks"*, but in amber.
- For an **expiring** credential: same list as ok, but the heading
  becomes *"what will break"* (future tense). Amber.
- For an **ok** credential: heading is *"what would break"*. No
  colour. This block is still shown — it's how the user understands
  why this credential matters.
- For a **never-set** credential: only show if the catalogue can
  pre-populate the dependent features. Otherwise omit entirely.

If your server can't compute this on day one, ship the list of
*provider × butler-feature* pairings as a static catalogue in the
frontend keyed by provider id, and merge with the credential's state
at render. See `secrets-data.jsx` for the sample shape.

---

## 6. Implementation plan

Each phase ends with a working route and a screenshot review.

### Phase A — Foundation (1 PR)

- TypeScript types in `frontend/src/api/types/secrets.ts`.
- API client functions in `frontend/src/api/client.secrets.ts` (or
  wherever your client lives). Read endpoints first; mutations later.
- New route `/secrets` mounted; `SecretsPage.tsx` redirects there
  for now. (Keep old behind a feature flag for one cycle.)
- Sidebar item already exists; confirm the icon and label match.
- Foundation prompt: [`prompts/00-foundation.md`](prompts/00-foundation.md)

### Phase B — Spine (1 PR)

- `<Spine>` with search, sort, identity switcher, pinned `needs hand`
  group.
- URL state: `?identity=`, `?sort=`, `?focus=`. Pushes to history on
  selection (back button works).
- Implements `buildSpineEntries` in TS — see prototype.
- Spine prompt: [`prompts/01-spine.md`](prompts/01-spine.md)

### Phase C — User page (2 PRs)

- `<UserPage>` with all states (ok / expired / scope_mismatch /
  expiring / never_set / revoked / rotating).
- Per-kind branches for oauth / token / apikey / webhook.
- All evidence atoms (`Fingerprint`, `ScopeBalance`, `WhatBreaks`,
  `ProbeResult`, `VisaRow`, `StampGlyph`, `StampRow`).
- Footer commits: `re-authorize / rotate / test / reveal value /
  disconnect`. Mutation calls wired.
- User-page prompt: [`prompts/02-page-user.md`](prompts/02-page-user.md)

### Phase D — System page (1 PR)

- `<SystemPage>` reusing all evidence atoms.
- `shared` vs `local` rowState handling; the `override · per butler`
  affordance.
- Plain-text-value branch (e.g. `GMAIL_SENDER_ADDRESS`).
- System-page prompt: [`prompts/03-page-system.md`](prompts/03-page-system.md)

### Phase E — CLI page (1 PR)

- `<CliPage>` with the how-to-use snippet and rotate flow that
  returns the value once.
- CLI-page prompt: [`prompts/04-page-cli.md`](prompts/04-page-cli.md)

### Phase F — Tweaks panel (½ PR)

- Reveal mode (`eye | hover | never`), default sort, show-verify-cmd,
  voice-paragraph toggle.
- Persists via `EDITMODE-BEGIN` / `EDITMODE-END` block in
  `SecretsPage.tsx` (or wherever the host expects).
- Tweaks prompt: [`prompts/05-tweaks-and-state.md`](prompts/05-tweaks-and-state.md)

### Phase G — Cleanup (½ PR)

- Delete the old `SecretsPage.tsx` 3-tab shell.
- Delete the per-provider `SetupCard` components (Google / Spotify /
  Home Assistant / WhatsApp / OwnTracks / Steam). Their OAuth
  click-handlers move to the new `<UserPage>` connect/reauthorize
  commits.
- Delete `SecretsTable.tsx` (the table is replaced by the spine + page).
- Remove the old `Tabs` shell and any tests that depended on it.
- Verify `/ingestion/connectors/*` deep-links still arrive at the
  right page.

---

## 7. Acceptance criteria

The page passes when:

- [ ] **Quiet day reads quiet.** On an all-healthy inventory the page
      has no red or amber pixels. The "needs hand" pinned group
      collapses to a single empty line: `*nothing waiting.*` (serif
      italic).
- [ ] **Sick credentials read sick.** The Spotify (expired) page
      renders with: red plaque, red failure-tail, red sliver on its
      spine row, red severity rows in "what breaks".
- [ ] **Evidence is the default.** A user can determine whether a
      credential is correct without ever clicking *reveal value*.
- [ ] **One round trip.** Page load fires one `GET
      /api/secrets/inventory?identity=…`; per-credential drill-down
      uses the in-memory cache plus background revalidation.
- [ ] **The typography test:** print the User-page for Spotify on
      letterhead. Does the page read as a *passport stamp* — issuing
      authority, passport number, visa permissions, audit stamps?
      If it reads like a SaaS settings panel, the redesign isn't done.
- [ ] **Reauth round-trip:** clicking *re-authorize* on Spotify ends
      back at `/secrets?focus=u:spotify&toast=connected` and the
      plaque has flipped from `expired` to `healthy`.
- [ ] **Identity switching works** without a full page reload. URL
      updates. Spine repopulates.
- [ ] **Tweaks persist** across reload via the EDITMODE block.
- [ ] **No console errors.** No layout shift on initial load.

---

## 8. Out of scope (do not add these without an explicit ask)

- **Bulk operations.** No multi-select. One credential at a time.
- **Provider logos / brand assets.** The letter-mark is the only
  visual identifier. Never load a remote SVG.
- **Webhook payload inspector.** For OwnTracks/Telegram, the page
  shows the incoming URL and signing fingerprint; it does *not*
  preview payloads. That belongs on `/ingestion/connectors`.
- **OAuth scope picker UI.** Scopes are determined by what the
  butler catalogue requires. Users don't pick.
- **Export / download credentials.** No "export encrypted bundle"
  affordance. If a user needs to back up secrets, they back up the
  database.
- **Audit retention controls.** The page shows the last 10 events
  inline; full history lives at `/audit`. Do not add retention knobs.
- **Onboarding tour, "what's new" tooltips, version badges.** Ever.

---

## 9. Open questions to confirm before shipping

These are sized to a Slack message each. Resolve before merging
Phase G:

1. **Probe safety.** For Anthropic / OpenAI / Gemini, a 1-token
   completion costs essentially nothing but it does cost. Acceptable?
   If not, downgrade to a `models.list` style endpoint that doesn't
   bill.
2. **Member view scope.** A household member viewing `/secrets`:
   should they see System (read-only) too, or just their User tab?
   *Current default:* their User tab only.
3. **Audit history retention here vs. on `/audit`.** Currently the
   page shows the last 10 events inline; clicking *open /audit ↗*
   filters the audit log. Confirm `/audit?key=` works (or which
   param name to use).
4. **Webhook secret rotation.** When the webhook signing token
   rotates, OwnTracks (and any other webhook integration) needs to
   be reconfigured externally. Where does the page surface that
   instruction? The current plaque says *"secret rotates with
   token"*; is that enough?
5. **Identity switcher for members.** Members see only themselves —
   should the switcher chip render at all (collapsed to a static
   chip), or be hidden entirely?

---

## 10. After shipping

Write `RECONCILIATION.md` in this folder auditing what landed against
the pack:

- What's done.
- What's missing (and why — deferred / out-of-scope / blocked).
- What refinements happened during implementation that should
  propagate back into `prompts/`.

Mirror the shape of `entity-redesign/RECONCILIATION.md`.
