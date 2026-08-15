# Tasks

This change is **spec-only**. The tasks below are spec-authoring tasks. The
implementation work is captured as a bead-creation handoff in §5 (to be run
by the operator AFTER this change ratifies).

## 0. Spotify authority reconciliation amendment (owner-approved; spec-only)

- [x] 0.1 Amend the active carrier's proposal, design, tasks, and
      `connector-oauth-scope-surface` delta so they state: connector-owned
      Spotify PKCE is the only production authorization flow; identity-bound
      access and refresh tokens are RFC 0006 Tier 2 owner `public.entity_info`
      credentials resolved via `resolve_owner_entity_info()`; the system-level
      app client ID remains Tier 1; and Passport is a content-blind,
      connector-owned projection rather than a secret authority.

- [x] 0.2 Amend the canonical ingestion, Passport, Spotify setup, Spotify
      connector/module, and core-credentials specifications atomically. The
      canonical contract routes Spotify recovery through
      `/secrets?focus=u:spotify`, delegates its action to
      `POST /api/connectors/spotify/oauth/start`, and uses
      `GET /api/connectors/spotify/oauth/callback`; only the connector flow
      writes the secured owner `public.entity_info` token rows, while the
      Passport projection creates no editable User credential or token mirror.

- [x] 0.3 Add a causal documentation-contract regression that reads RFC 0006
      first and fails if the active carrier or canonical specs reintroduce
      Tier 1 Spotify token authority, generic OAuth Spotify ownership, or omit
      the serialized downstream plan.

- [x] 0.4 Record the binding implementation order without mutating Beads:
      after this reconciliation merges, `bu-fj7lx` implements the
      content-blind connector-owned Passport projection; only then
      `bu-3ifcj` removes the generic OAuth Spotify production exemplar and
      repository cruft. The cleanup uses a synthetic generalized-provider
      fixture and leaves no compatibility alias, shim, or production registry
      entry.

- [x] 0.5 Correct the implementation handoff to the real generic raw-editor
      and backend seams. `PassportAddPanel` under `/secrets`, not EntityDetail,
      consumes `ENTITY_INFO_TYPES`. `bu-fj7lx` must add a server-side Spotify
      fence in `src/butlers/api/routers/secrets_v2.py` across inventory,
      detail/read, rotate, disconnect, probe, and reauthorize before it may be
      dispatched as complete. It must also fence the generic Relationship
      entity-info authority in `roster/relationship/api/router.py` across
      `GET /api/relationship/owner/entity-info`,
      `GET /api/relationship/entities/{entity_id}`,
      `POST /api/relationship/entities/{entity_id}/info`,
      `PATCH /api/relationship/entities/{entity_id}/info/{info_id}`,
      `DELETE /api/relationship/entities/{entity_id}/info/{info_id}`,
      `GET /api/relationship/entities/{entity_id}/secrets/{info_id}`, and
      `GET /api/relationship/entities/{entity_id}/linked-contacts`. Lists omit
      Spotify types at the SQL boundary; create rejects the requested type
      before database access; ID-addressed mutation/reveal uses only a
      metadata type discriminator before the stable non-disclosing 404 and
      never selects or reveals `value`. It must also implement the typed
      `expired | rotation-needed` → `needs_reauth` convergence before the
      dashboard recovery resolver.

## 1. Spec authoring — core capability

- [x] 1.1 Draft `specs/connector-oauth-scope-surface/spec.md` with the
      following ADDED requirement blocks:
  - Scope declaration manifest schema (per Decision 1).
  - Observed-scope storage (additive columns on `connector_registry` per
    Decision 2).
  - Drift taxonomy with five classes (per Decision 3).
  - `auth.status` enum with six values (per Decision 4).
  - Reauth endpoint contract for generic OAuth providers other than Spotify
    (per Decision 5).
  - Reauth endpoint contract for non-OAuth providers (per Decision 6).
  - Re-introspection cadence (per Decision 7).
  - Audit trail (per Decision 8).
  - Per-connector applicability matrix (per Decision 6).
  - State token round-trip contract.

- [x] 1.2 Each ADDED requirement has at minimum one `#### Scenario:` block
      with WHEN/THEN/AND clauses, per OpenSpec schema.

- [x] 1.3 Include `## Source References` footer listing the doctrine principles
      and existing-spec dependencies, per `openspec/config.yaml:9-15`.

## 2. Spec authoring — delta against `connector-base-spec`

- [x] 2.1 Add `## ADDED Requirements` block to
      `specs/connector-oauth-scope-surface/spec.md` (or a sibling spec dir
      `specs/connector-base-spec/spec.md` containing only `## MODIFIED
      Requirements` deltas) that adds the four columns
      (`observed_scopes`, `observed_scopes_fetched_at`,
      `required_scopes_version`, `auth_status`) to `connector_registry` and
      extends `ConnectorDetail` Pydantic with the `auth` and `scopes` blocks.

- [x] 2.2 Cite the existing `connector-base-spec/spec.md:319-348,381-419` as
      the extension target.

## 3. Spec authoring — durable delta against `dashboard-ingestion-dispatch-console`

- [x] 3.1 Place an `## ADDED Requirements` block in
      `specs/dashboard-ingestion-dispatch-console/spec.md` that adds the
      generic-OAuth, Spotify, and non-OAuth reauth authority split to the
      existing canonical ingestion recovery resolver.

- [x] 3.2 Remove the orphaned
      `specs/connector-lifecycle-ceremony/spec.md` modified delta. Record in
      proposal and design that the historical lifecycle artifact is context
      only; this delta targets the live canonical spec and therefore survives
      this change's own archive without a second-change ordering fallback.

## 4. Spec authoring — verification

- [x] 4.1 Run `openspec validate add-connector-oauth-scope-surface` and
      confirm clean output. Fix any structural drift (heading levels,
      requirement/scenario nesting, missing footer) before submitting for
      review.

- [x] 4.2 Run `openspec show add-connector-oauth-scope-surface` and visually
      review the rendered structure.

- [x] 4.3 Cross-check that no existing capability spec is contradicted:
  - `core-credentials/spec.md:52-99` — credential masking. Confirm no scope
    response field exposes a token.
  - `connector-oauth-scope-surface/spec.md` — its response-shape and audit
    requirements keep credentials out of the generic OAuth response while the
    Spotify path retains RFC 0006 Tier 2 owner `entity_info` token authority.
  - `google-multi-account-oauth/spec.md:84-145` — scope-set registry.
    Confirm the manifest schema in Decision 1 generalizes the existing
    Google scope-set pattern without conflict.
  - `google-account-registry/spec.md:150-162` — `granted_scopes` on
    `google_accounts`. Confirm `observed_scopes` on `connector_registry`
    does not duplicate or contradict; the two serve different layers
    (account-level vs. connector-instance-level).
  - `dashboard-ingestion-dispatch-console/spec.md`,
    `butler-secrets/spec.md`, `dashboard-spotify-setup/spec.md`,
    `connector-spotify/spec.md`, `module-spotify/spec.md`, and
    `core-credentials/spec.md` — confirm
    Spotify is connector-owned PKCE with access and refresh tokens stored in
    owner `public.entity_info` and resolved via `resolve_owner_entity_info()`;
    `u:spotify` is content-blind presentation only; and no generic OAuth
    Spotify route, registry, credential mirror, or second secret authority
    remains normative.

- [ ] 4.4 Confirm the per-connector applicability matrix (Decision 6) covers
      every connector type currently in `openspec/specs/connector-*/`:
  - `connector-discord` — OAuth (planned)
  - `connector-filtered-events` — internal, no auth surface
  - `connector-gmail` — OAuth (Google)
  - `connector-google-calendar` — OAuth (Google)
  - `connector-google-drive` — OAuth (Google)
  - `connector-google-health` — OAuth (Google)
  - `connector-home-assistant` — long-lived access token (non-OAuth)
  - `connector-live-listener` — internal (no auth surface)
  - `connector-owntracks` — bearer token (non-OAuth)
  - `connector-spotify` — OAuth (Spotify)
  - `connector-steam` — API key (non-OAuth)
  - `connector-telegram-bot` — bot token (non-OAuth)
  - `connector-telegram-user-client` — TDLib session (non-OAuth)
  Every entry must be classified in the spec.

- [x] 4.5 Add a static archive-survival regression that proves the active
      lifecycle-authority delta is named for an existing canonical spec and
      that the orphaned lifecycle-ceremony delta is absent.

## 5. Documentation + cleanup

- [ ] 5.1 No `roster/*/AGENTS.md` updates are required by this change (no
      butler behavior change).

- [ ] 5.2 No `CLAUDE.md` update is required (no new agent-facing conventions).

- [x] 5.3 **Serialized Spotify implementation handoff (NO Beads mutation in
      this change):** after this reconciliation merges, implement the two
      already-created beads in order:
  1. `bu-fj7lx` adds the content-blind connector-owned Passport projection at
     `/secrets?focus=u:spotify`, with fixed `listening-history` capability
     evidence and connector-endpoint actions only. It must not create a User
     credential editing surface or token mirror; the connector-owned callback
     remains the only writer of the Tier 2 owner `public.entity_info` rows. Its
     required backend/API scope includes the `secrets_v2.py` server-side fence
     for generic inventory, detail/read, rotate, disconnect, probe, and
     reauthorize; the generic Relationship entity-info fences at
     `GET /api/relationship/owner/entity-info`,
     `GET /api/relationship/entities/{entity_id}`,
     `POST /api/relationship/entities/{entity_id}/info`,
     `PATCH /api/relationship/entities/{entity_id}/info/{info_id}`,
     `DELETE /api/relationship/entities/{entity_id}/info/{info_id}`,
     `GET /api/relationship/entities/{entity_id}/secrets/{info_id}`, and
     `GET /api/relationship/entities/{entity_id}/linked-contacts`; and the
     typed `expired | rotation-needed` → `needs_reauth` convergence that
     preserves generic Google OAuth and unsupported non-OAuth behavior.
     Collection queries omit Spotify types; create rejects their type before
     DB access; ID-addressed operations perform only a metadata discriminator
     before a stable non-disclosing 404. Frontend omission from
     `PassportAddPanel` is not sufficient, and the connector callback remains
     the sole writer.
  2. `bu-3ifcj` follows `bu-fj7lx` and removes the generic OAuth Spotify
     production registry, route, configuration, UI, documentation, and test
     exemplar. It preserves a synthetic generalized-provider fixture only and
     leaves no compatibility alias, shim, or production registry entry.

  Before dispatching `bu-3ifcj`, the coordinator SHALL run
  `bd dep add bu-3ifcj bu-fj7lx --type blocks` to materialize the required
  blocker. A `discovered-from` relation is not a substitute for that `blocks`
  prerequisite. This spec-only change records the post-ratification tracker
  action and does not execute it.

- [ ] 5.4 **Bead-creation handoff (MANUAL — run AFTER this change is ratified
      and archived):** the operator runs the following `bd create` command to
      file the implementation bead that unblocks
      `bu-1f91v.11`:

      ```bash
      bd create "Implement connector-oauth-scope-surface spec + unblock bu-1f91v.11 (reauth endpoint)" \
        --description "Spec ratified in openspec/changes/archive/<archive-id>-add-connector-oauth-scope-surface/. Implements the connector-oauth-scope-surface capability: additive connector_registry columns, per-connector scope manifest registration, OAuth introspection on token refresh + 6h cadence task, reauth endpoint contract (replacing the HTTP 503 stub from bu-1f91v.11), audit emissions, per-connector applicability matrix, ScopeList and ReauthCallout API surface. See spec for full requirement list." \
        -t feature \
        -p 1 \
        --labels redesign-ingestion-dispatch-console \
        --deps blocks:bu-1f91v.11,discovered-from:bu-1f91v.11 \
        --json
      ```

      Why this is manual: the bead's `--deps blocks:bu-1f91v.11` makes it
      meaningful only once this spec is ratified (otherwise the bead would
      be created with a forward reference to spec work that is itself
      blocking). File it once the change archives, not before.

- [x] 5.5 Run `openspec validate add-connector-oauth-scope-surface` one
      final time before submitting for review.

- [ ] 5.6 Run `openspec archive add-connector-oauth-scope-surface` after the
      change is ratified (moves the change directory to
      `openspec/changes/archive/`).

## 6. Acceptance criteria (must pass before archive)

- [ ] 6.1 The spec is implementable cold by someone who has not been in this
      conversation. (Self-test: re-read the spec and check that every
      requirement has a scenario and every scenario references concrete file
      paths or capability names rather than handwaved concepts.)

- [ ] 6.2 No requirement contradicts an existing spec in
      `openspec/specs/`. (Cross-reference list in §4.3 has been walked.)

- [ ] 6.3 Every connector type currently in the project has a defined
      `auth.status` resolver in the per-connector applicability matrix.
      (Verified against §4.4 list.)

- [ ] 6.4 No credential, token, or refresh-token value appears in any
      response shape defined by the spec. Spot-check all JSON shapes in
      requirement scenarios.

- [ ] 6.5 The `## Source References` footer is present and lists doctrine
      principles by rule number plus all cited specs.

- [x] 6.6 `openspec validate add-connector-oauth-scope-surface --strict`
      returns clean.

- [ ] 6.7 Bead-creation command in §5.4 has been tested for shell-quoting
      sanity (the `--description` is one line; the `--deps` are
      comma-separated without spaces).

- [x] 6.8 The active carrier and canonical specs agree that Spotify is
      connector-owned PKCE with RFC 0006 Tier 2 owner `entity_info` access and
      refresh token authority resolved via `resolve_owner_entity_info()`;
      Passport is content-blind projection-only; and `bu-fj7lx` precedes
      `bu-3ifcj` cleanup with a synthetic generalized-provider fixture and no
      compatibility alias, shim, or production registry entry.
