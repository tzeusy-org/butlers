## 1. Runtime-config validation

- [x] 1.1 Add `delegation` to `KNOWN_CORE_GROUPS` in
  `src/butlers/api/routers/runtime_config.py`.
- [x] 1.2 Update/add tests covering `delegation` accepted by
  `PATCH /api/butlers/{name}/runtime-config`.

## 2. Seed configuration

- [x] 2.1 Add `delegation` to Finance's `runtime_seed.core_groups` in
  `roster/finance/butler.toml`, preserving existing groups.
- [x] 2.2 Add `delegation` to Relationship's `runtime_seed.core_groups` in
  `roster/relationship/butler.toml`, preserving existing groups.

## 3. Guidance surface

- [x] 3.1 Add a shared `cross-butler-delegation` skill describing
  `delegate_ask`/`delegate_receive`/`delegate_answer`/`delegate_wake` usage,
  reachable by every butler-type roster's guidance discovery path (including
  Travel's local, non-shared path), without altering any bare `CLAUDE.md`
  include contract.

## 4. Verification

- [x] 4.1 Run lint/format and the focused runtime-config API, seed, and
  guidance/symlink tests.
- [x] 4.2 Run `openspec validate activate-delegation-core-group --strict`.
- [x] 4.3 Run the full test suite once before handoff.
