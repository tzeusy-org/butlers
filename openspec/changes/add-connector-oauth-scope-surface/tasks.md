# Tasks

This change is **spec-only**. The tasks below are spec-authoring tasks. The
implementation work is captured as a bead-creation handoff in §5 (to be run
by the operator AFTER this change ratifies).

## 1. Spec authoring — core capability

- [x] 1.1 Draft `specs/connector-oauth-scope-surface/spec.md` with the
      following ADDED requirement blocks:
  - Scope declaration manifest schema (per Decision 1).
  - Observed-scope storage (additive columns on `connector_registry` per
    Decision 2).
  - Drift taxonomy with five classes (per Decision 3).
  - `auth.status` enum with six values (per Decision 4).
  - Reauth endpoint contract for OAuth providers (per Decision 5).
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

## 3. Spec authoring — delta against `connector-lifecycle-ceremony`

- [x] 3.1 Place a `## MODIFIED Requirements` block in
      `specs/connector-lifecycle-ceremony/spec.md` that supersedes the
      "Reauth is blocked" scenario and the gate matrix entry for `reauth`
      from
      `openspec/changes/redesign-ingestion-dispatch-console/specs/connector-lifecycle-ceremony/spec.md:11-17,36-40`.

- [x] 3.2 Note in `proposal.md ## Impact > Cross-change coordination` and
      in the lifecycle-ceremony delta's preamble that the delta applies
      cleanly regardless of archive order, with the no-op fallback for the
      case where this change archives BEFORE `redesign-ingestion-dispatch-console`.

## 4. Spec authoring — verification

- [ ] 4.1 Run `openspec validate add-connector-oauth-scope-surface` and
      confirm clean output. Fix any structural drift (heading levels,
      requirement/scenario nesting, missing footer) before submitting for
      review.

- [ ] 4.2 Run `openspec show add-connector-oauth-scope-surface` and visually
      review the rendered structure.

- [ ] 4.3 Cross-check that no existing capability spec is contradicted:
  - `core-credentials/spec.md:52-99` — credential masking. Confirm no scope
    response field exposes a token.
  - `connector-lifecycle-ceremony/spec.md:103-109` — "No credentials in
    lifecycle API responses". Confirm reauth response shape only contains
    `auth_url`, `state`, `expires_in` — no token.
  - `google-multi-account-oauth/spec.md:84-145` — scope-set registry.
    Confirm the manifest schema in Decision 1 generalizes the existing
    Google scope-set pattern without conflict.
  - `google-account-registry/spec.md:150-162` — `granted_scopes` on
    `google_accounts`. Confirm `observed_scopes` on `connector_registry`
    does not duplicate or contradict; the two serve different layers
    (account-level vs. connector-instance-level).

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

## 5. Documentation + cleanup

- [ ] 5.1 No `roster/*/AGENTS.md` updates are required by this change (no
      butler behavior change).

- [ ] 5.2 No `CLAUDE.md` update is required (no new agent-facing conventions).

- [ ] 5.3 **Bead-creation handoff (MANUAL — run AFTER this change is ratified
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

- [ ] 5.4 Run `openspec validate add-connector-oauth-scope-surface` one
      final time before submitting for review.

- [ ] 5.5 Run `openspec archive add-connector-oauth-scope-surface` after the
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

- [ ] 6.6 `openspec validate add-connector-oauth-scope-surface --strict`
      returns clean.

- [ ] 6.7 Bead-creation command in §5.3 has been tested for shell-quoting
      sanity (the `--description` is one line; the `--deps` are
      comma-separated without spaces).
