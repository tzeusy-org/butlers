# butler-secrets

## ADDED Requirements

### Requirement: Passport-Book Information Architecture
The dashboard SHALL replace the existing 3-tab `/secrets` shell with a single passport-book route at `/secrets` consisting of a left **spine** index, a right **page** editorial body, and a top-right **tweaks** panel. The page SHALL NOT render any of the deprecated patterns: tab strip, `SecretsTable` (`••••••••` row with eye-toggle as the primary affordance), six bespoke provider Setup cards, separate `CLIAuthCard`, or embedded `EntityPicker` in the page header.

The page is rendered in the binding **Dispatch** design language captured at `pr/overview/secrets-redesign/DESIGN_LANGUAGE.md`. Every visual decision in the spec MUST preserve that language — typography (Inter Tight 500 display, Source Serif 4 voice, JetBrains Mono code/eyebrow), spacing (4px multiples; 48px × 56px page padding; 56px section gutter; `1.4fr 1fr` two-column editorial grid), colour (oklch tokens; `--red/--amber/--green` only when state demands), motion (briefing cross-fade, sidebar chevron, theme fade, tooltip; nothing else).

#### Scenario: Single-route surface
- **WHEN** a user navigates to `/secrets`
- **THEN** the page renders a two-column layout: left spine (sticky, scrollable index), right page (editorial body for the focused credential)
- **AND** there is no tab strip, no `<Tabs>` shell, and no horizontal navigation between System / User / CLI families
- **AND** the page header contains the title "Secrets", a mono eyebrow, the identity chip (when more than one identity is in scope), and the tweaks panel trigger

#### Scenario: Spine grouping order
- **WHEN** the spine renders
- **THEN** rows are grouped in this exact order: `needs-hand` (pinned), `CLI runtimes`, `System`, `User`
- **AND** the `needs-hand` group is always pinned and severity-sorted regardless of the `?sort=` mode
- **AND** group eyebrows render in mono 10px uppercase with tracking 0.14em

#### Scenario: Empty `needs-hand` group
- **WHEN** every credential is healthy (no row has state in {`expired`, `revoked`, `failed`, `scope_mismatch`, `expiring_soon`})
- **THEN** the spine omits the `needs-hand` group entirely (no empty-state stub) and the page renders **zero red and zero amber pixels**

### Requirement: Evidence-Over-Value Affordance Contract
Each credential row in the spine SHALL surface a 6px state dot, a 2px left-edge sliver (coloured only when state demands), a mono label, and a mono subline. The masked-value blob (`••••••••`) is FORBIDDEN as the only proof a credential exists. Each credential page SHALL render the following evidence — in order — without any LLM-generated text:

1. **Heading + state plaque** — credential title, state label (one of {`ok`, `expired`, `revoked`, `expiring_soon`, `scope_mismatch`, `failed`, `never_set`}), fingerprint pill (`sha256:7a3f…`, mono 11px).
2. **Dense KV band** — issued / expires / last verified / last used / source / target / category, all in mono with tabular numerals.
3. **Scopes inventory** (when applicable) — granted vs required scopes; missing scopes called out in `--amber`; over-grant noted dim.
4. **WhatBreaks list** — butler features that will silently fail if the credential is sick; severity pip per row; rendered from `public.provider_feature_catalogue` server-side, never from a static frontend JSON.
5. **Probe result** — most recent `TestResult`: ok/fail, HTTP code (when applicable), latency ms, pre-formatted timestamp (server-formatted, e.g. `"14:21 today"`), serif-italic message tail (verbatim provider error, never LLM-elaborated).
6. **Audit stamps** — last 10 `AuditEvent` rows: 1-char mono glyph + date/time + action + actor + serif note; an `open /audit-log ↗` link deep-links to `/audit-log?key=<credential-key>` (see Audit Deep-Link requirement).
7. **Cross-references** — for OAuth credentials, a link to the same provider on `/ingestion/connectors` (channel-side view) and an `Reauthorize` commit-pill button.
8. **Commit footer** — primary action (`Reauthorize`, `Rotate`, `Probe`, `Disconnect`, `Set`, `Override`, `Revoke`) rendered as the Dispatch commit-pill (foreground-on-background, never brand-coloured).

The reveal-eye SHALL ship and remain available but SHALL be reclassified as a Tweak (default `eye`, alternatives `hover` / `never`). Removing the eye from a row MUST NOT impair the owner's ability to assess that row.

#### Scenario: Fingerprint never persisted
- **WHEN** any credential read endpoint returns a fingerprint
- **THEN** the fingerprint is computed on-read via PostgreSQL `sha256(<secret_value>)::text` and truncated to the first 8 hex characters
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

#### Scenario: Provider drawer for oddities
- **WHEN** a User OAuth row for `owntracks` is expanded
- **THEN** the drawer renders the webhook URL and the regen-secret affordance; no other provider's row renders these fields
- **AND** the drawer is implemented as a per-provider component dispatched by `provider` slug, not by branching the row template

### Requirement: Projection-Lens Identity Switcher
The identity switcher in the page header SHALL be a **projection lens** over the owner's view of household-member credential data. Switching identity SHALL re-project the User-tab credentials associated with the selected member entity, but every action (rotate, reauthorize, disconnect, probe, set, override, revoke) SHALL run with owner privilege. The backend MUST NOT enforce identity-scoped access in v1.

This matches the existing single-owner doctrine in `about/heart-and-soul/security.md:7-8, 18-20` ("user-federated. One user. One instance.", "no access control within the system that restricts the owner"). A future RFC under `about/legends-and-lore/` may introduce a household-member privilege tier; this surface is forward-compatible because the same `?identity=<id>` URL state will then bind to a session principal rather than to a projection lens.

#### Scenario: Identity switch re-projects view
- **WHEN** the owner clicks the identity chip and selects a household member entity
- **THEN** the URL updates to `/secrets?identity=<member-id>`
- **AND** the User-tab portion of the spine re-renders to show only credentials associated with that member entity
- **AND** the CLI and System groups in the spine remain unchanged (those families are not identity-scoped)
- **AND** any mutation triggered from the page (rotate, reauthorize, etc.) is dispatched with owner privilege regardless of `?identity=` state

#### Scenario: Backend ignores identity-scoped access enforcement
- **WHEN** any `/api/secrets/*` mutation endpoint receives a request with `?identity=<member-id>`
- **THEN** the endpoint validates the credential exists and mutates it
- **AND** the endpoint does NOT check whether the caller has permission to act on the member's credential (no member-level authorization in v1)

#### Scenario: Single-identity scope hides chip
- **WHEN** only one identity (the owner) is in scope (no household-member entities have user credentials registered)
- **THEN** the identity chip is hidden from the page header
- **AND** the `?identity=` URL parameter is ignored if present
- **AND** the spine renders the User group as if no switcher exists

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

### Requirement: Tweaks-Panel State Persistence
The tweaks panel SHALL expose four toggles: `reveal-mode` (default `eye`, alternatives `hover` / `never`), `default-sort` (default `severity`, alternatives `recency` / `alpha`), `show-verify-cmd` (default `off`), `voice-paragraph` (default `on`). Tweak state SHALL persist via the same mechanism used by the in-flight ingestion-redesign and entity-redesign tweaks panels; if neither has shipped a persistence pattern by the time this change implements, `localStorage` keyed by `secrets.tweaks.*` is the default. (Resolution of brief §5 Q9 is deferred to design.md.)

#### Scenario: Tweak persists across sessions
- **WHEN** the owner sets `reveal-mode = never` and reloads the page
- **THEN** the tweak panel renders with `reveal-mode = never` and the eye-toggle is hidden from every row

### Requirement: No-LLM-Narration Invariant on `/secrets` Surfaces (binding)
The `/secrets` surfaces (spine, page, tweaks, drawers, modals, toasts) MUST NOT trigger LLM inference. Every text fragment rendered on these surfaces SHALL be one of:

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
