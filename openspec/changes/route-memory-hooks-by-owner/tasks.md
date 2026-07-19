## 1. Owner-keyed session runtime

- [x] 1.1 Add failing core regressions for owner-specific dispatch, absent-owner
  safe defaults, and stale-runtime identity preservation.
- [x] 1.2 Replace global context and episode-store slots with an owner-keyed,
  identity-safe paired runtime registry.
- [x] 1.3 Wire `MemoryModule` startup and shutdown to register and unregister
  its daemon-owned session runtime.

## 2. Regression coverage and verification

- [x] 2.1 Add a multi-daemon module regression covering General, Travel, and a
  Chronicler-style private memory pool.
- [x] 2.2 Run focused core/module/schema-isolation/spec/lint verification and
  record results.

### Verification evidence

- Focused owner-routing, Spawner, module, schema-isolation, dependency, daemon,
  and maintenance tests passed after rebasing onto `main`.
- `make lint`, `uv run ruff format --check src/ tests/ roster/ conftest.py -q`,
  and `openspec validate route-memory-hooks-by-owner --strict` passed.
- `make test-qg` passed: 11,234 passed, 5 skipped.
