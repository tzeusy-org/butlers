## 1. Specification

- [x] 1.1 Add the Dispatch surface primitive requirement and scenarios.
- [x] 1.2 Define Section as the default, Tile's narrow independent-state role, quiet behavior,
      and Card retirement with no alias.
- [x] 1.3 Run strict OpenSpec and overwrite-safety validation.

## 2. Primitives and migration

- [x] 2.1 Add semantic Section and Tile primitives with Eyebrow anatomy.
- [x] 2.2 Migrate all production `ui/card` consumers and delete `ui/card.tsx`.
- [x] 2.3 Preserve each consumer's loading, error, empty, and retry branches.

## 3. State and lint discipline

- [x] 3.1 Route connector, Google Health, and topology state signals through StateDot or
      `stateColorVar`.
- [x] 3.2 Reject retired Card imports and literal CSS-token fallbacks with narrow ESLint selectors.
- [x] 3.3 Remove all production literal `var(--token, fallback)` references.

## 4. Verification and handoff

- [x] 4.1 Extend the named visual-role, connector, Google Health, topology, and StateDot seams;
      add focused Section and Tile render tests.
- [x] 4.2 Run affected Vitest nodes/files, frontend lint, `npm run knip`, build, `make check-guards`,
      and `make test-plan BASE=origin/main`.
- [x] 4.3 Push the exact head, open PR #4231, and use terminal hosted CI for broad evidence.
