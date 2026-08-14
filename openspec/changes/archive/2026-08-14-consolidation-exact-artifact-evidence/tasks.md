## 1. Evidence contract regressions

- [x] 1.1 Add focused prompt/parser regressions for rendered episode IDs and
  per-artifact `evidence_episode_ids` retention.
- [x] 1.2 Add focused consolidation regressions proving absent, malformed,
  duplicate, and foreign evidence reaches the existing group failure path
  before any artifact persistence.
- [x] 1.3 Add executor regressions proving each fact/rule links only its
  validated evidence and rolls back its write when an evidence link fails.

## 2. Consolidation implementation

- [x] 2.1 Extend the prompt and parsed artifact shapes with exact episode
  evidence without changing confirmation semantics.
- [x] 2.2 Preflight-validate all artifact evidence against the claimed group
  before action execution, routing invalid output through the current group
  failure lifecycle.
- [x] 2.3 Persist each valid fact/rule and only its `derived_from` links in one
  transaction while retaining persisted-target and stale-target behavior.

## 3. Verification and handoff

- [x] 3.1 Run strict OpenSpec validation and focused memory tests, then the
  relevant lint/format gates.
- [x] 3.2 Review the final diff for scope boundaries and commit, push, and open
  a clean draft pull request with verified head/base metadata.
