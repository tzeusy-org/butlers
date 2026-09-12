## 1. Canonical CLI authority projection

- [x] 1.1 Add backend inventory regressions proving canonical CLI state wins
  over same-key stale or failed mirrors in conceptual state and CLI-family KPI
  counts.
- [x] 1.2 Add backend inventory regression proving mirror-only keys remain
  visible and retain most-severe fallback aggregation.
- [x] 1.3 Implement canonical-key precedence in backend inventory aggregation
  without rewriting or deleting raw per-source System rows.
- [x] 1.4 Add matching frontend inventory-adapter regressions for
  canonical-plus-mirror and mirror-only inputs, then implement the same
  canonical-key precedence rule.

## 2. CLI Test query refresh

- [x] 2.1 Add frontend hook regressions proving a completed healthy or failed
  CLI Test invalidates the Secrets inventory and CLI-provider status queries.
- [x] 2.2 Implement both query invalidations after an HTTP success response
  while preserving current transport/API failure behavior.

## 3. Spotify passport presentation

- [x] 3.1 Add frontend regressions for loading, connected, unconfigured,
  authorization-needed, needs-reauth, connector error, and query failure.
- [x] 3.2 Implement the closed connector-status-to-passport mapping, including
  explicit `checking` and `authorization_needed` presentation states, labels,
  and severity ranks.
- [x] 3.3 Verify authorization-needed and failed states enter `needs-hand`,
  checking never enters `stale`, and no generic probe or audit surface is added
  to `u:spotify`.

## 4. Verification and review

- [x] 4.1 Run focused backend inventory tests and frontend inventory, hook, and
  Spotify projection tests.
  - Completed backend: `uv run pytest tests/api/test_secrets_v2_inventory.py tests/api/test_secrets_v2_probe_live_spotify.py -q` (141 passed).
  - Completed Task 3 frontend: `npx vitest run src/hooks/use-secrets-inventory.test.ts src/hooks/use-cli-auth.test.ts` from `frontend/` (2 files, 36 tests passed).
  - Completed Task 4 frontend: `npx vitest run src/components/secrets/passport/spotify-projection-state.test.ts src/components/secrets/passport/passport.test.tsx src/components/secrets/passport/secrets-fe5.test.tsx src/hooks/use-secrets-inventory.test.ts --reporter=dot` from `frontend/` (4 files, 191 tests passed).
- [x] 4.2 Run frontend lint, TypeScript build, Knip, and the existing
  content-blind and generic-Spotify-prohibition regression coverage.
  - Completed: `npm run lint`, `npm run lint:emdash`, `npm run lint:query-coercion`, `npm run knip`, and `npm run build` pass. The exact local merge-base `npm run test` run passed; branch-local runs timed out only under contention at `route-chunk-registry.test.ts`'s unchanged 20-second limit. Hosted clean-runner adjudication for PR #3850 at exact head `26a67c082` passed the full frontend job (including Test, 7m37s), frontend-e2e (3m49s), and all fast guards without timeout, worker, or test changes.
- [x] 4.3 Validate this OpenSpec change strictly, run the spec-overwrite ratchet,
  and review the final diff for authority-boundary or browser-visible data
  expansion.
  - Completed: `openspec validate repair-secrets-authority-projections --strict`, `make check-spec-overwrites`, and `git diff --check origin/main...HEAD`; authority-boundary review found no browser-visible credential-data expansion.
