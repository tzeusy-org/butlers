# butler-secrets

## Purpose

The `butler-secrets` capability defines the operational `/secrets` dashboard page: a single **passport-book** surface that replaces the deprecated 3-tab shell (System / User / CLI runtimes) wrapping a flat `SecretsTable` of `••••••••` rows with a per-row eye-toggle reveal.

The old surface had two structural pains: it was **opaque without leaking** (the only diagnostic was revealing the value) and had **flat rhythm** (a silently-expired Google OAuth and a healthy Telegram token rendered at identical weight). The passport-book IA fixes both with an **evidence-over-value** contract — fingerprint, last-verified timestamp, scope inventory, probe result, provider-side state, and WhatBreaks — so the owner can assess any credential without revealing its value.

This capability declares the `/secrets` page contract: passport-book IA, the evidence-over-value affordance contract, projection-lens identity-switcher semantics (including connected Google accounts as identity lenses), deep-link focus routing, OAuth per-provider unification, cross-page reauth bookkeeping, the inventory-vs-channel-health scope boundary, and the binding "no LLM narration on `/secrets` surfaces" invariant. It does **not** modify the three-tier authority model declared in `about/heart-and-soul/security.md`.

## Requirements

### Requirement: Passport-Book Information Architecture
The dashboard SHALL replace the existing 3-tab `/secrets` shell with a single passport-book route at `/secrets` consisting of a left **spine** index and a right **page** editorial body. The page SHALL NOT render any of the deprecated patterns: tab strip, `SecretsTable` (`••••••••` row with eye-toggle as the primary affordance), six bespoke provider Setup cards, separate `CLIAuthCard`, embedded `EntityPicker` in the page header, or prototype tweaks chrome.

The page is rendered in the binding **Dispatch** design language captured at `docs/redesigns/secrets-design-language.md`. Every visual decision in the spec MUST preserve that language — typography (Inter Tight 500 display, Source Serif 4 voice, JetBrains Mono code/eyebrow), spacing (4px multiples; 48px × 56px page padding; 56px section gutter; `1.4fr 1fr` two-column editorial grid), colour (oklch tokens; `--red/--amber/--green` only when state demands), motion (briefing cross-fade, sidebar chevron, theme fade, tooltip; nothing else).

#### Scenario: Single-route surface
- **WHEN** a user navigates to `/secrets`
- **THEN** the page renders a two-column layout: left spine (sticky, scrollable index), right page (editorial body for the focused credential)
- **AND** there is no tab strip, no `<Tabs>` shell, and no horizontal navigation between System / User / CLI families
- **AND** the page header contains the title "Secrets", a mono eyebrow, and the identity chip (when more than one identity is in scope)

#### Scenario: Spine grouping order
- **WHEN** the spine renders
- **THEN** rows are grouped in this exact order: `needs-hand` (pinned), `CLI runtimes`, `System`, `User`
- **AND** the `needs-hand` group is always pinned and severity-sorted regardless of the `?sort=` mode
- **AND** group eyebrows render in mono 10px uppercase with tracking 0.14em

#### Scenario: Empty `needs-hand` group
- **WHEN** every credential is healthy (no row has state in {`expired`, `revoked`, `failed`, `warn`, `scope_mismatch`, `expiring_soon`})
- **THEN** the spine omits the `needs-hand` group entirely (no empty-state stub) and the page renders **zero red and zero amber pixels**

### Requirement: Evidence-Over-Value Affordance Contract
Each credential row in the spine SHALL surface a 6px state dot, a 2px left-edge sliver (coloured only when state demands), a mono label, and a mono subline. The masked-value blob (`••••••••`) is FORBIDDEN as the only proof a credential exists. Each credential page SHALL render the following evidence — in order — without any LLM-generated text:

1. **Heading + state plaque** — credential title, state label (one of {`ok`, `warn`, `expired`, `revoked`, `expiring_soon`, `scope_mismatch`, `failed`, `never_set`}), fingerprint pill (`sha256:7a3f…`, mono 11px).
2. **Dense KV band** — issued / expires / last verified / last used / source / target / category, all in mono with tabular numerals.
3. **Scopes inventory** (when applicable) — granted vs required scopes; missing scopes called out in `--amber`; over-grant noted dim.
4. **WhatBreaks list** — butler features that will silently fail if the credential is sick; severity pip per row; rendered from `public.provider_feature_catalogue` server-side, never from a static frontend JSON.
5. **Probe result** — most recent `TestResult`: ok/fail, HTTP code (when applicable), latency ms, pre-formatted timestamp (server-formatted, e.g. `"14:21 today"`), serif-italic message tail (verbatim provider error, never LLM-elaborated).
6. **Audit stamps** — last 10 `AuditEvent` rows: 1-char mono glyph + date/time + action + actor + serif note; an `open /audit-log ↗` link deep-links to `/audit-log?key=<credential-key>` (see Audit Deep-Link requirement).
7. **Cross-references** — for OAuth credentials, a link to the same provider on `/ingestion/connectors` (channel-side view) and an `Reauthorize` commit-pill button.
8. **Commit footer** — primary action (`Reauthorize`, `Rotate`, `Probe`, `Disconnect`, `Set`, `Override`, `Revoke`) rendered as the Dispatch commit-pill (foreground-on-background, never brand-coloured).

Explicit reveal actions SHALL ship and remain available where credential pages support value reveal. Removing prototype tweak controls MUST NOT impair the owner's ability to assess a row.

#### Scenario: Fingerprint never persisted
- **WHEN** any credential read endpoint returns a fingerprint
- **THEN** the fingerprint is computed on-read by hashing the secret value with SHA-256 in the application layer and truncating to the first 8 hex characters
- **AND** the fingerprint is NEVER stored in any DB column, file, or cache

#### Scenario: Fingerprint verify command exposure
- **WHEN** the `+ verify cmd` expander is toggled open on a credential page
- **THEN** the page renders a single mono line containing a hard-coded shell command literal of the form `echo -n '<value>' | sha256sum | cut -c1-8` (where `<value>` is a placeholder, never the real secret)
- **AND** no LLM call is made to generate or annotate this command

### Requirement: Severity Earns Visual Authority Only When State Demands
State colour (`--red`, `--amber`, `--green`) SHALL appear on `/secrets` ONLY when at least one credential is in a non-`ok` state. The page MUST NOT render decorative state colour on healthy rows. A day in which every credential is healthy SHALL render `/secrets` with **zero red, amber, or green pixels** (the only non-neutral pixels permitted are butler letter-mark hues, which appear only inside their letter-mark boundary per `about/heart-and-soul/design-language.md`).

State SHALL be expressed as one of {dot, sliver, numeral, colour} — never as a word. Status badges containing the words `"Connected"`, `"Active"`, `"Linked"` are FORBIDDEN.

#### Scenario: Calm-day rendering
- **WHEN** all credentials are in state `ok`
- **THEN** spine rows render with state dots in `--dim`, no slivers, no colour
- **AND** the page-body state plaque renders with the neutral foreground colour, no fill, no border

#### Scenario: Single-sick rendering
- **WHEN** exactly one credential is in state `expired`
- **THEN** only that one spine row has a 2px `--red` left-edge sliver and a `--red` state dot
- **AND** the `needs-hand` group is rendered, containing that one row pinned at the top
- **AND** all other rows render unchanged (no colour leakage)

### Requirement: One Row Template Across All Three Families
The User-tab's six bespoke provider Setup cards SHALL be replaced by one row template applicable to System, User (oauth / token / apikey / webhook variants), and CLI families. Per-provider oddities (OwnTracks webhook URL, Steam ID format, WhatsApp QR-link affordance) SHALL live in a provider-specific drawer opened from the row, not in divergent row chrome.

#### Scenario: Row-template uniformity
- **WHEN** the spine renders any credential from any family
- **THEN** the row has the same vertical rhythm (10px vertical padding), same column layout (sliver | dot | label | subline | right-aligned glyph), and same hairline separators
- **AND** the rendered HTML structure of a System row is identical (modulo `data-*` attributes and content) to the rendered HTML structure of a User row or CLI row

#### Scenario: One spine row per credential concept
- **WHEN** the inventory contains multiple raw rows that resolve to the same credential concept, such as multiple `entity_info.type` rows for one User provider or the same System `key` across butler schemas
- **THEN** the spine renders exactly one row for that credential concept
- **AND** the row preserves the highest-severity state and the relevant shared/local source-target provenance

#### Scenario: Provider drawer for oddities
- **WHEN** a User OAuth row for `owntracks` is expanded
- **THEN** the drawer renders the webhook URL and the regen-secret affordance; no other provider's row renders these fields
- **AND** the drawer is implemented as a per-provider component dispatched by `provider` slug, not by branching the row template

### Requirement: Owner-Default Inventory Surfaces Primary Google Account

The owner-default `/secrets` inventory (`GET /api/secrets/inventory` without `?identity=`) SHALL include the primary Google account's `google_oauth_refresh` credential entry in the `user` array. This entry SHALL be present whenever at least one Google account with `is_primary = true` exists in `public.google_accounts`, regardless of `status`. This includes `status = 'expired'` and `status = 'revoked'` accounts so the owner can reach the scope-set picker and reauth CTA without needing a manual `?identity=` parameter; hiding a revoked primary would make the reauthorize flow unreachable.

Including the primary Google account credential in the owner-default inventory makes the scope-set picker (including `Google Health`) reachable at `/secrets?focus=u:google` WITHOUT requiring the owner to first discover or manually specify an `?identity=<entity_id>` parameter.

The behavioral outcome MUST be that `u:google` appears in the spine and `PageGoogleAccounts` is reachable from the owner-default `/secrets` view, regardless of whether the primary account's status is `active` or `expired`. The backend join strategy (how to resolve the credential from `public.google_accounts` and `public.entity_info`) is an implementation detail owned by bead `bu-2kejb`.

This requirement is **co-owned** with the `dashboard-google-accounts` spec (§Multi-Account Leak Prevention), which binds the leak-prevention invariant: the owner-default projection SHALL surface ONLY the primary account's credential. Implementation is owned by bead `bu-2kejb`.

#### Scenario: Owner-default inventory includes primary Google account credential

- **WHEN** `GET /api/secrets/inventory` is called without `?identity=`
- **AND** a primary active Google account exists in `public.google_accounts`
- **THEN** the response `user` array SHALL contain a `google_oauth_refresh` entry corresponding to the primary account's companion entity
- **AND** the spine at `/secrets` SHALL render a `u:google` row without requiring any `?identity=` parameter
- **AND** the owner can navigate to `/secrets?focus=u:google` and reach `PageGoogleAccounts` with the scope-set picker and Google Health status card

#### Scenario: No Google account connected — no google_oauth_refresh in owner-default

- **WHEN** `GET /api/secrets/inventory` is called without `?identity=`
- **AND** no Google account exists in `public.google_accounts` (or none with `is_primary = true`)
- **THEN** the response `user` array SHALL NOT contain a `google_oauth_refresh` entry
- **AND** the spine at `/secrets` SHALL NOT render a `u:google` row in the owner-default view

#### Scenario: Only the primary account appears in owner-default — non-primary excluded

- **WHEN** `GET /api/secrets/inventory` is called without `?identity=`
- **AND** multiple Google accounts exist (at least one primary, at least one non-primary with `is_primary = false`)
- **THEN** the response `user` array SHALL contain exactly one `google_oauth_refresh` entry (the primary account's)
- **AND** non-primary accounts' `google_oauth_refresh` entries SHALL NOT appear in the owner-default response
- **AND** the owner MUST use `?identity=<non_primary_entity_id>` to access a non-primary account's credential details

### Requirement: Projection-Lens Identity Switcher
The identity switcher in the page header SHALL be a **projection lens** over the owner's view of household-member credential data. Switching identity SHALL re-project the User-tab credentials associated with the selected member entity, but every action (rotate, reauthorize, disconnect, probe, set, override, revoke) SHALL run with owner privilege. The backend MUST NOT enforce identity-scoped access in v1.

This matches the existing single-owner doctrine in `about/heart-and-soul/security.md:7-8, 18-20` ("user-federated. One user. One instance.", "no access control within the system that restricts the owner"). A future RFC under `about/legends-and-lore/` may introduce a household-member privilege tier; this surface is forward-compatible because the same `?identity=<id>` URL state will then bind to a session principal rather than to a projection lens.

The identity switcher chip SHALL include connected Google accounts as selectable identity lenses, in addition to household-member entities. Selecting a Google account entity in the switcher SHALL re-project the User-tab credentials to show that account's `google_oauth_refresh` entry (and any other credentials anchored on that companion entity). This enables the owner to access non-primary Google account credentials through the same projection-lens affordance without navigating to a separate management screen.

#### Scenario: Identity switch re-projects view
- **WHEN** the owner clicks the identity chip and selects a household member entity
- **THEN** the URL updates to `/secrets?identity=<member-id>`
- **AND** the User-tab portion of the spine re-renders to show only credentials associated with that member entity
- **AND** the CLI and System groups in the spine remain unchanged (those families are not identity-scoped)
- **AND** any mutation triggered from the page (rotate, reauthorize, etc.) is dispatched with owner privilege regardless of `?identity=` state

#### Scenario: Identity switch to a non-primary Google account
- **WHEN** the owner clicks the identity chip and selects a Google account entity (e.g. the companion entity for `tzeuse@gmail.com`)
- **THEN** the URL updates to `/secrets?identity=<google_account_entity_id>`
- **AND** the User-tab spine re-renders to show the `google_oauth_refresh` credential for that non-primary account
- **AND** `PageGoogleAccounts` renders with that account's scope-set picker and connector health data
- **AND** the CLI and System groups remain unchanged

#### Scenario: Backend ignores identity-scoped access enforcement
- **WHEN** any `/api/secrets/*` mutation endpoint receives a request with `?identity=<member-id>`
- **THEN** the endpoint validates the credential exists and mutates it
- **AND** the endpoint does NOT check whether the caller has permission to act on the member's credential (no member-level authorization in v1)

#### Scenario: Single-identity scope hides chip
- **WHEN** no household-member entities have user credentials registered
- **AND** at most one Google account is connected (zero or one)
- **THEN** there is no alternative identity lens available beyond the owner-default view
- **AND** the identity chip SHALL be hidden from the page header
- **AND** the `?identity=` URL parameter is ignored if present
- **AND** the spine renders the User group as if no switcher exists

#### Scenario: Identity chip visible when multiple Google accounts connected
- **WHEN** two or more Google accounts are connected (regardless of household-member entities)
- **THEN** the identity chip SHALL be visible in the page header
- **AND** the chip dropdown SHALL list all connected Google account entities as selectable identity lenses
- **AND** the owner-default view (no `?identity=`) SHALL show only the primary account's credentials per §Owner-Default Inventory Surfaces Primary Google Account

### Requirement: Deep-Link Focus Routing
The `/secrets` page SHALL accept a `?focus=<key>` URL parameter that opens the right-page editorial for the specified credential. The focus-key format SHALL be:

- `u:<provider>` for User credentials (e.g. `u:google`, `u:owntracks`)
- `s:<KEY>` for System secrets (e.g. `s:BUTLER_TELEGRAM_TOKEN`)
- `c:<id>` for CLI runtimes (e.g. `c:claude`)

Focus keys SHALL be URL-safe (no encoded colons; the `:` separator is permitted in URL fragments and query values per RFC 3986 §3.4).

#### Scenario: Deep-link to a User credential
- **WHEN** the owner clicks a link of the form `/secrets?focus=u:google` from `/ingestion/connectors` or anywhere else
- **THEN** the spine renders with the `u:google` row highlighted
- **AND** the right page renders the focused User credential editorial body
- **AND** the page does NOT render a tab top, a flat table, or any deprecated chrome

#### Scenario: Unknown focus key
- **WHEN** the `?focus=` parameter references a credential that does not exist
- **THEN** the spine renders normally
- **AND** the right page renders the empty-state serif line (no LLM call; static literal)
- **AND** a `--amber` toast surfaces with a templated message (no LLM elaboration)

### Requirement: No Prototype Tweaks Chrome
The `/secrets` page SHALL NOT render a Tweaks button, Tweaks panel, or localStorage-backed `secrets.tweaks.*` preference surface. Default behavior is fixed in product code: spine sort defaults to `severity`, `?sort=` may still update the current URL-backed sort mode, verify commands remain hidden by default, reveal actions remain visible where credential pages support explicit reveal, and the voice paragraph renders when credentials need attention.

#### Scenario: Stale tweak storage is ignored
- **WHEN** a browser has stale `secrets.tweaks.revealMode = never` localStorage from an older build
- **THEN** `/secrets` does not render any Tweaks trigger or panel
- **AND** the stale localStorage value does not hide explicit reveal actions

### Requirement: No-LLM-Narration Invariant on `/secrets` Surfaces (binding)
The `/secrets` surfaces (spine, page, drawers, modals, toasts) MUST NOT trigger LLM inference. Every text fragment rendered on these surfaces SHALL be one of:

1. **Stored prose** — a static catalogue entry (e.g. `provider.brief` from `public.provider_feature_catalogue`).
2. **Templated string** — a template interpolating server-data values (e.g. `"Feeds the {feeds} butler"`, `"{kpi.healthy} healthy, {kpi.expiring} expiring"`).
3. **Verbatim provider error tail** — the raw error message returned by the probed external API, displayed serif-italic with no transformation other than truncation.
4. **Hard-coded literal** — empty-state lines, button labels, eyebrows.

This is a binding spec invariant. Any future change, bead, or task that proposes LLM-elaborated scope explanations, audit summaries, "would-break" narratives, smart-explanation captions, or any other generative text on `/secrets` MUST be rejected by reference to this requirement and to brief §0 ("What we are deliberately NOT doing") + brief §4 ("Recommended de-scopes").

Rationale: brief §4 LLM-cost feasibility verified every `/secrets` affordance as `green` (zero LLM cost) on the explicit assumption that none of them ever calls an LLM. Adding LLM narration would break the cost guarantee, the read-mostly observability doctrine (`about/heart-and-soul/design-language.md:25-43`), and the determinism contract (`about/heart-and-soul/vision.md:81-84`).

#### Scenario: Voice paragraph is stored prose
- **WHEN** the page-header voice paragraph renders ("Inventory of every credential the system holds. {kpi.summary}.")
- **THEN** the paragraph is constructed by string interpolation of server-computed counts
- **AND** no LLM call is dispatched for this paragraph

#### Scenario: WhatBreaks list is server-data
- **WHEN** the WhatBreaks list renders on a credential page
- **THEN** the rows are sourced from `GET /api/secrets/breaks-catalogue?provider=<p>` server-side (which reads `public.provider_feature_catalogue`)
- **AND** the row text is the stored `feature` label, never an LLM elaboration

#### Scenario: Probe error tail is verbatim
- **WHEN** a probe fails and the page renders the probe-result message
- **THEN** the message is the raw `error` string returned by the external provider's API
- **AND** the message is NOT summarised, paraphrased, translated, or annotated by an LLM

### Requirement: Cross-Page Reauth Bookkeeping
OAuth dances initiated from `/secrets` SHALL return to `/secrets?focus=u:<provider>&toast=connected` on success. OAuth dances initiated from `/ingestion/connectors` SHALL return to `/ingestion/connectors`. The OAuth callback handler SHALL inspect a `page_of_origin` parameter carried through the state token to determine the return destination.

This requirement is **co-owned** with the in-flight `complete-ingestion-redesign-parity` change. This spec defines only the `/secrets` side of the contract; the `/ingestion/connectors` side is specified there.

#### Scenario: `/secrets`-initiated reauth returns to `/secrets`
- **WHEN** the owner clicks `Reauthorize` on `/secrets?focus=u:google` and completes the Google OAuth dance
- **THEN** the OAuth callback redirects the browser to `/secrets?focus=u:google&toast=connected`
- **AND** the spine re-renders with the `u:google` row in state `ok` and a green `--green` toast confirms the reauthorization

#### Scenario: Cross-page reauth returns to origin
- **WHEN** the owner clicks `Reauthorize` from `/ingestion/connectors` and completes the dance
- **THEN** the callback redirects to `/ingestion/connectors` (NOT to `/secrets`)
- **AND** the `/ingestion/connectors` view reflects the new credential state

### Requirement: Inventory ≠ Channel-Health Dashboard (Scope Boundary)
`/secrets` SHALL be the credential inventory surface. `/ingestion/connectors` SHALL remain the channel-side view of the same OAuth credential (throughput, scope, route, recent events). Both surfaces SHALL be capable of triggering a reauthorization dance and SHALL reflect identical source-of-truth state for any given credential. The two surfaces MUST NOT diverge in their representation of credential state — both read from the same DB-backed cache.

This requirement defines a scope boundary, not a UI contract; the UI contract for `/ingestion/connectors` is owned by other changes.

#### Scenario: Single source of truth for state
- **WHEN** a Google OAuth token expires at time T
- **THEN** within the same page-load cycle, both `/secrets` (User row for `u:google`, spine) and `/ingestion/connectors` (the Google connector card) reflect state `expired`
- **AND** neither surface caches a stale `ok` state past the standard TanStack Query refresh interval

## Source References

- Non-Negotiable Rule 1 (user-federated, one user one instance) — `about/heart-and-soul/vision.md:60-63`
- Determinism contract (read-mostly observability surface) — `about/heart-and-soul/vision.md:81-84`
- Security model — single-owner doctrine — `about/heart-and-soul/security.md:7-8, 18-20`
- Read-mostly observability / design language tokens — `about/heart-and-soul/design-language.md:25-43`
- Binding integration brief (§0 design intent, §3 backend contract, §4 LLM-cost de-scopes, §5 Q8/Q13) — `docs/redesigns/2026-05-25-secrets-brief.md`
- Passport-book design language (Dispatch) — `docs/redesigns/secrets-design-language.md`
- `_fetch_user_secrets` owner-default join (current behavior being extended) — `src/butlers/api/routers/secrets_v2.py:701-721`
- `{google_account}` companion entity model and exclusion from entity resolution — `openspec/specs/google-account-registry/spec.md:71-89`
- Primary account is_primary constraint — `openspec/specs/google-account-registry/spec.md:32-33`
- Co-owning leak-prevention invariant — `openspec/changes/google-health-secrets-surface/specs/dashboard-google-accounts/spec.md:§Multi-Account Leak Prevention`
- Response envelope contract (`ApiResponse<T>` / `PaginatedResponse<T>`) — RFC 0007 §Response Envelope
- Focus-key URL safety — RFC 3986 §3.4
- Cross-page reauth co-ownership — `openspec/changes/complete-ingestion-redesign-parity`
- Systemic auth_status taxonomy (cross-link, NOT re-specced here) — `openspec/changes/add-connector-oauth-scope-surface/proposal.md:43-72`
- Implementation bead for backend join — `bu-2kejb`
- OpenSpec config rule on Source References footer — `openspec/config.yaml:9-15`
