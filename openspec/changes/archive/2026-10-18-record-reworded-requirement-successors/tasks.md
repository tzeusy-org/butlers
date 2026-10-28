## 1. Provenance and overlap checks

- [x] 1.1 Compare each archived predecessor body with its live successor and confirm none is restored.
- [x] 1.2 Recheck the active changes touching the same capabilities for conflicts.

## 2. OpenSpec archive

- [x] 2.1 Validate the exact `RENAMED`-only delta in strict mode.
- [x] 2.2 Archive normally and prove the canonical specs are byte-identical before and after.

## 3. Ratchet and final evidence

- [x] 3.1 Delete exactly the healed predecessor keys from the archived-requirements ratchet by hand.
- [x] 3.2 Run the archived-requirements and spec-overwrite guards and inspect the final diff.
