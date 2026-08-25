# Secrets Authority and Projection State Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Secrets passport display canonical Codex CLI health and connector-derived Spotify state without widening either credential authority.

**Architecture:** The backend and frontend will both give an existing canonical `cli[]` row precedence over same-key legacy `system[]` mirrors, retaining mirror-only fallback behavior. The CLI Test mutation will refresh inventory immediately. Spotify remains a presentation-only connector projection, but its spine state will come from the existing content-blind connector status query using explicit `checking` and `authorization_needed` presentation states.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, React 19, TypeScript, TanStack Query, Vitest, pytest, OpenSpec 1.9.

## Global Constraints

- Tier 1 `cli-auth/*` state is authoritative in the shared system-global credential pool; do not delete or rewrite per-butler compatibility rows.
- Spotify OAuth token material remains connector-owned Tier 2 state; generic Secrets inventory/detail/mutation/probe routes stay unavailable for Spotify.
- Browser responses remain content-blind: no credential values, provider error text, account identifiers, raw scopes, probe messages, or audit notes.
- A dashboard CLI Test is isolated evidence and must not claim daemon-routed success or alter breaker history.
- Preserve mirror-only fallback: legacy `cli-auth/*` system rows remain visible only when the canonical `cli[]` key is absent.
- Follow TDD: each production-code task starts with a focused failing regression and records the expected red failure before implementation.
- Use the established repository toolchain only: `uv`, pytest, Ruff, npm, Vitest, TypeScript, Knip, and strict OpenSpec validation.

---

### Task 1: Add the authority and projection contract

**Files:**
- Create: `openspec/changes/repair-secrets-authority-projections/.openspec.yaml`
- Create: `openspec/changes/repair-secrets-authority-projections/proposal.md`
- Create: `openspec/changes/repair-secrets-authority-projections/design.md`
- Create: `openspec/changes/repair-secrets-authority-projections/tasks.md`
- Create: `openspec/changes/repair-secrets-authority-projections/specs/dashboard-api/spec.md`
- Create: `openspec/changes/repair-secrets-authority-projections/specs/butler-secrets/spec.md`

**Interfaces:**
- Consumes: existing `GET /api/secrets/inventory`, `POST /api/cli-auth/{provider}/test`, and `GET /api/connectors/spotify/status` contracts.
- Produces: additive requirements named `Canonical CLI Authority Projection` and `Connector Status Drives Spotify Passport State`.

- [ ] **Step 1: Scaffold the OpenSpec change**

Create `.openspec.yaml` with:

```yaml
schema: spec-driven
created: 2026-08-24
```

Write `proposal.md`, `design.md`, and `tasks.md` from the approved design in `docs/plans/2026-08-24-secrets-authority-state-repair-design.md`. Keep the change limited to inventory projection, query invalidation, and Spotify presentation state.

- [ ] **Step 2: Add the dashboard API requirement**

In `specs/dashboard-api/spec.md`, add:

```markdown
## ADDED Requirements

### Requirement: Canonical CLI Authority Projection

The Secrets inventory SHALL use a canonical shared CLI row as the health authority for a CLI credential key whenever that row exists. Same-key per-butler system rows are compatibility mirrors and SHALL NOT override the canonical state or inflate CLI-family failing/unverified counts. When no canonical CLI row exists, per-butler mirrors MAY supply the legacy display fallback and SHALL retain most-severe aggregation.

#### Scenario: Canonical CLI health overrides stale mirrors

- **WHEN** `cli[]` contains a credential key and `system[]` contains same-key `cli-auth` mirrors
- **THEN** CLI-family state and KPI counts use the canonical `cli[]` row only
- **AND** the raw per-source System evidence remains available without rewriting credential data

#### Scenario: Legacy mirror remains visible without canonical state

- **WHEN** no canonical `cli[]` row exists for a `cli-auth` key
- **THEN** same-key per-butler mirrors remain eligible for the CLI display family
- **AND** their most severe state determines the fallback display state
```

- [ ] **Step 3: Add the Passport interaction requirement**

In `specs/butler-secrets/spec.md`, add:

```markdown
## ADDED Requirements

### Requirement: Connector Status Drives Spotify Passport State

The presentation-only `u:spotify` projection SHALL derive its spine state from the closed response of `GET /api/connectors/spotify/status`. It SHALL NOT use generic credential `warn` as a standing state and SHALL NOT expose a generic Secrets probe action.

#### Scenario: Spotify projection maps closed connector status

- **WHEN** Spotify status is loading, connected, unconfigured, authorization-needed, needs-reauth, failed, or unavailable
- **THEN** the projection renders respectively as checking, healthy, not-set, authorization-needed, authorization-needed, failed, or failed
- **AND** authorization-needed and failed states appear in `needs hand`
- **AND** checking never appears in `stale`

#### Scenario: CLI Test refreshes persisted evidence

- **WHEN** a CLI Test request completes with an HTTP success response
- **THEN** Passport invalidates the Secrets inventory and CLI provider queries
- **AND** the persisted healthy or failed outcome becomes visible without a page reload
```

- [ ] **Step 4: Validate the contract**

Run:

```bash
openspec validate repair-secrets-authority-projections --strict
make check-spec-overwrites
```

Expected: strict validation succeeds and the overwrite ratchet reports no new loss.

- [ ] **Step 5: Commit the contract**

```bash
git add openspec/changes/repair-secrets-authority-projections
git commit -m "spec: define secrets authority projections"
```

---

### Task 2: Make canonical CLI state authoritative in backend aggregation

**Files:**
- Modify: `src/butlers/api/routers/secrets_v2.py:2576-2605`
- Test: `tests/api/test_secrets_v2_inventory.py`

**Interfaces:**
- Consumes: `_dedupe_display_families(cli_secrets, system_secrets, user_secrets)` and `_is_cli_auth_system_secret`.
- Produces: canonical-key filtering before `_dedupe_most_severe` builds the conceptual CLI family.

- [ ] **Step 1: Write the canonical-precedence regression**

Add an API-level test using the file's existing `_make_system_row` and `_make_db_manager` fixtures. Supply the healthy canonical row through `cli_rows` and the stale mirror through a named butler's `system_rows`, then assert the CLI KPI family ignores the mirror:

```python
def test_inventory_family_counts_use_canonical_cli_health_over_stale_mirrors():
    canonical = _make_system_row(
        key="cli-auth/codex",
        category="cli-auth",
        last_test_ok=True,
        last_verified=_NOW,
    )
    stale_mirror = _make_system_row(
        key="cli-auth/codex",
        category="cli-auth",
        last_test_ok=True,
        last_verified=_NOW - timedelta(days=2),
    )
    client = _build_app(
        _make_db_manager(
            butler_names=["travel"],
            system_rows=[stale_mirror],
            cli_rows=[canonical],
        )
    )

    response = client.get("/api/secrets/inventory")

    assert response.status_code == 200, response.text
    assert response.json()["meta"]["failing_count_by_family"]["cli"] == 0
    assert response.json()["meta"]["unverified_count_by_family"]["cli"] == 0
```

- [ ] **Step 2: Verify the regression is red**

Run:

```bash
uv run pytest tests/api/test_secrets_v2_inventory.py -k "canonical_cli_health or relocate_per_butler_cli_auth" -q
```

Expected: the canonical-precedence assertion fails because the stale mirror currently wins; existing mirror-only fallback coverage remains green.

- [ ] **Step 3: Implement canonical-key filtering**

Update `_dedupe_display_families` with the equivalent of:

```python
canonical_cli_keys = {secret.key for secret in cli_secrets}
cli_from_system = [
    secret
    for secret in deduped_system
    if _is_cli_auth_system_secret(secret) and secret.key not in canonical_cli_keys
]
```

Leave `visible_system` and the returned raw API families unchanged beyond conceptual KPI aggregation.

- [ ] **Step 4: Verify backend green**

Run:

```bash
uv run pytest tests/api/test_secrets_v2_inventory.py -q
uv run ruff check src/butlers/api/routers/secrets_v2.py tests/api/test_secrets_v2_inventory.py
uv run ruff format --check src/butlers/api/routers/secrets_v2.py tests/api/test_secrets_v2_inventory.py
```

Expected: all inventory tests and Ruff checks pass.

- [ ] **Step 5: Commit the backend repair**

```bash
git add src/butlers/api/routers/secrets_v2.py tests/api/test_secrets_v2_inventory.py
git commit -m "fix(secrets): prefer canonical CLI health"
```

---

### Task 3: Align frontend CLI projection and refresh after Test

**Files:**
- Modify: `frontend/src/hooks/use-secrets-inventory.ts:294-333`
- Modify: `frontend/src/hooks/use-secrets-inventory.test.ts`
- Modify: `frontend/src/hooks/use-cli-auth.ts:115-120`
- Create: `frontend/src/hooks/use-cli-auth.test.ts`

**Interfaces:**
- Consumes: `secretsInventoryKeys.all`, `cliAuthKeys.providers()`, canonical raw `cli` rows, and relocated System mirrors.
- Produces: `groupCliCredentials` input that contains mirrors only for keys absent from canonical `cli`; `useTestCLIAuthApiKey` invalidates persisted-state queries on HTTP success.

- [ ] **Step 1: Write the frontend canonical-precedence regression**

Extend `use-secrets-inventory.test.ts` with:

```typescript
it("keeps canonical CLI health over stale per-butler mirrors", () => {
  const result = adaptInventoryResponse({
    cli: [{
      key: "cli-auth/codex",
      category: "cli-auth",
      description: "Codex",
      state: "ok",
      fingerprint: "canonical",
      issued: null,
      expires: null,
      last_verified: "2026-08-24T14:16:06Z",
      test: { ok: true, code: null, at: "14:16 today", latency_ms: 374 },
    }],
    system: [
      makeSystem({
        key: "cli-auth/codex",
        category: "cli-auth",
        state: "warn",
        butler: "travel",
      }),
    ],
    user: [],
    identities: [],
  });
  expect(result.cli).toHaveLength(1);
  expect(result.cli[0]).toMatchObject({ id: "cli-auth/codex", state: "ok" });
});
```

Retain the existing test proving mirror-only rows are promoted.

- [ ] **Step 2: Write the CLI Test invalidation regression**

Create `use-cli-auth.test.ts` using the existing hook-test pattern: mock `useMutation`, `useQueryClient`, and `testCLIAuthApiKey`; capture the mutation options; invoke `onSuccess`; assert invalidation of both prefixes.

```typescript
expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: secretsInventoryKeys.all });
expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: cliAuthKeys.providers() });
```

- [ ] **Step 3: Verify both regressions are red**

Run:

```bash
cd frontend
npx vitest run src/hooks/use-secrets-inventory.test.ts src/hooks/use-cli-auth.test.ts
```

Expected: canonical state is `warn`, and the Test hook records no invalidations.

- [ ] **Step 4: Implement frontend authority precedence**

Before converting System mirrors, build canonical IDs from the adapted raw CLI rows and exclude same-key mirrors:

```typescript
const canonicalCliIds = new Set(cliCredentials.map((credential) => credential.id));
const cliFromSystem = systemCredentials
  .filter(isCliAuthSystemCredential)
  .filter((credential) => !canonicalCliIds.has(credential.key))
  .map(systemCliAuthToCliCredential);
```

Keep provider-managed System filtering and mirror-only fallback intact.

- [ ] **Step 5: Implement Test invalidation**

Update `useTestCLIAuthApiKey` to obtain `queryClient` and add:

```typescript
onSuccess: () => {
  void queryClient.invalidateQueries({ queryKey: secretsInventoryKeys.all });
  void queryClient.invalidateQueries({ queryKey: cliAuthKeys.providers() });
},
```

Do not treat `data.success === false` as an HTTP mutation error; the backend persists both outcomes.

- [ ] **Step 6: Verify frontend CLI green**

Run:

```bash
cd frontend
npx vitest run src/hooks/use-secrets-inventory.test.ts src/hooks/use-cli-auth.test.ts
npx eslint src/hooks/use-secrets-inventory.ts src/hooks/use-secrets-inventory.test.ts src/hooks/use-cli-auth.ts src/hooks/use-cli-auth.test.ts
```

Expected: focused Vitest and ESLint checks pass.

- [ ] **Step 7: Commit the frontend CLI repair**

```bash
git add frontend/src/hooks/use-secrets-inventory.ts \
  frontend/src/hooks/use-secrets-inventory.test.ts \
  frontend/src/hooks/use-cli-auth.ts \
  frontend/src/hooks/use-cli-auth.test.ts
git commit -m "fix(secrets): refresh canonical CLI state"
```

---

### Task 4: Derive Spotify projection state from connector status

**Files:**
- Modify: `frontend/src/components/secrets/passport/types.ts`
- Modify: `frontend/src/components/secrets/passport/constants.ts`
- Modify: `frontend/src/hooks/use-secrets-inventory.ts`
- Modify: `frontend/src/components/secrets/passport/DirectionPassport.tsx`
- Modify: `frontend/src/components/secrets/passport/passport.test.tsx`
- Modify: `frontend/src/components/secrets/passport/secrets-fe5.test.tsx`

**Interfaces:**
- Consumes: `useSpotifyStatus()` returning `state`, `connected`, and content-blind capability categories.
- Produces: `spotifyProjectionState(query): CredentialState`, plus `checking` and `authorization_needed` presentation states.

- [ ] **Step 1: Write state-mapping and grouping regressions**

Mock `useSpotifyStatus` for each closed state and render `DirectionPassport` focused on `u:spotify`. Assert the spine row state and group:

```typescript
it.each([
  ["connected", "ok", "integrations"],
  ["unconfigured", "never_set", "integrations"],
  ["authorization_needed", "authorization_needed", "needs hand"],
  ["needs_reauth", "authorization_needed", "needs hand"],
  ["error", "failed", "needs hand"],
])("maps Spotify %s to %s", (spotifyState, credentialState, group) => {
  vi.mocked(useSpotifyModule.useSpotifyStatus).mockReturnValue({
    data: { state: spotifyState, connected: spotifyState === "connected" },
    isLoading: false,
    isError: false,
    error: null,
  } as ReturnType<typeof useSpotifyModule.useSpotifyStatus>);
  const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />, [
    "/secrets?focus=u%3Aspotify",
  ]);
  expect(html).toContain(`data-credential-state="${credentialState}"`);
  expect(html).toContain(group);
  expect(html).not.toContain("probe · last test");
});
```

Add separate loading and query-error assertions: loading maps to `checking` outside `stale`; query error maps to `failed` without rendering raw error text.

- [ ] **Step 2: Verify Spotify regressions are red**

Run:

```bash
cd frontend
npx vitest run src/components/secrets/passport/passport.test.tsx \
  src/components/secrets/passport/secrets-fe5.test.tsx
```

Expected: every synthetic Spotify row is currently `warn` and remains in `stale`.

- [ ] **Step 3: Add explicit presentation states**

Extend `CredentialState`, `STATE_CATALOG`, and the adapter's `STATE_RANK` with:

```typescript
checking: { label: "checking…", tone: "dim", sliver: false, rank: 5 },
authorization_needed: {
  label: "authorization needed",
  tone: "amber",
  sliver: true,
  rank: 3,
},
```

Add `authorization_needed` to `NEEDS_HAND_STATES`; add neither state to `UNVERIFIED_STATES`.

- [ ] **Step 4: Implement status-derived Spotify state**

In `DirectionPassport`, call `useSpotifyStatus` and map only its closed state:

```typescript
function spotifyProjectionState(query: ReturnType<typeof useSpotifyStatus>): CredentialState {
  if (query.isLoading) return "checking";
  if (query.isError || !query.data) return "failed";
  switch (query.data.state) {
    case "connected": return "ok";
    case "unconfigured": return "never_set";
    case "authorization_needed":
    case "needs_reauth": return "authorization_needed";
    case "error": return "failed";
  }
}
```

Use that state for the synthetic `u:spotify` spine entry. Do not pass raw errors or token/account fields into the entry. Keep the existing `resolved.kind === "spotify"` connector drawer and its explicit no-probe contract unchanged.

- [ ] **Step 5: Verify Spotify green**

Run:

```bash
cd frontend
npx vitest run src/components/secrets/passport/passport.test.tsx \
  src/components/secrets/passport/secrets-fe5.test.tsx \
  src/hooks/use-secrets-inventory.test.ts
npx eslint src/components/secrets/passport/DirectionPassport.tsx \
  src/components/secrets/passport/types.ts \
  src/components/secrets/passport/constants.ts \
  src/components/secrets/passport/passport.test.tsx
```

Expected: status mappings, grouping, and no-generic-probe assertions pass.

- [ ] **Step 6: Commit the Spotify repair**

```bash
git add frontend/src/components/secrets/passport/types.ts \
  frontend/src/components/secrets/passport/constants.ts \
  frontend/src/hooks/use-secrets-inventory.ts \
  frontend/src/components/secrets/passport/DirectionPassport.tsx \
  frontend/src/components/secrets/passport/passport.test.tsx \
  frontend/src/components/secrets/passport/secrets-fe5.test.tsx
git commit -m "fix(secrets): derive Spotify projection health"
```

---

### Task 5: Integrated verification and branch completion

**Files:**
- Modify: `openspec/changes/repair-secrets-authority-projections/tasks.md` to check completed implementation and verification steps.
- Verify: all files changed by Tasks 1-4.

**Interfaces:**
- Consumes: the completed backend/frontend implementation and OpenSpec change.
- Produces: a merge-ready pushed branch and pull request with exact verification evidence.

- [ ] **Step 1: Run focused backend and contract gates**

```bash
uv run pytest tests/api/test_secrets_v2_inventory.py -q
uv run ruff check src/butlers/api/routers/secrets_v2.py tests/api/test_secrets_v2_inventory.py
uv run ruff format --check src/butlers/api/routers/secrets_v2.py tests/api/test_secrets_v2_inventory.py
openspec validate repair-secrets-authority-projections --strict
make check-spec-overwrites
```

Expected: all commands succeed.

- [ ] **Step 2: Run the actual frontend CI sequence**

From `frontend/`:

```bash
npm run lint
npm run lint:emdash
npm run lint:query-coercion
npm run knip
npm run build
npm run test
```

Expected: all six gates succeed in CI order; Knip does not mask build/test.

- [ ] **Step 3: Run final repository hygiene checks**

```bash
git diff --check origin/main...HEAD
git status --short
```

Expected: no whitespace errors and only intended tracked files.

- [ ] **Step 4: Mark the OpenSpec task checklist complete and commit**

Update the change's `tasks.md` checkboxes with the commands actually run, then:

```bash
git add openspec/changes/repair-secrets-authority-projections/tasks.md
git commit -m "docs: record secrets projection verification"
```

- [ ] **Step 5: Push and open the pull request**

```bash
git pull --rebase
git push
gh pr create --base main --head agent/fix-secrets-probe-state \
  --title "fix(secrets): honor credential authority state" \
  --body-file /tmp/secrets-authority-pr-body.md
git status --short --branch
```

The PR body must summarize both authority repairs, list exact tests, contain no secret values or session links, and state that no credential rows were mutated by the code change.
