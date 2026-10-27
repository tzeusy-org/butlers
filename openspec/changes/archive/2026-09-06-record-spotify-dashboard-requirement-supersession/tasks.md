## 1. Provenance and overlap checks

- [x] 1.1 Recheck the predecessor keys, successor headers, active deltas, and open PR file overlap at the dispatch head.
- [x] 1.2 Compare the archived `connector-spotify` predecessor bodies with the live successor authority and confirm that the old bodies are not restored.

## 2. OpenSpec archive

- [x] 2.1 Validate the exact `RENAMED`-only delta in strict mode.
- [x] 2.2 Run the archived-requirements and spec-overwrite guards before archive.
- [x] 2.3 Archive normally and prove that the canonical `dashboard-spotify-setup` spec is byte-identical before and after.

## 3. Ratchet and final evidence

- [x] 3.1 Delete exactly the two healed predecessor keys from the archived-requirements ratchet without regenerating any baseline.
- [x] 3.2 Run the named post-archive guards and inspect the final diff for the scoped archive and two-key deletion only.
