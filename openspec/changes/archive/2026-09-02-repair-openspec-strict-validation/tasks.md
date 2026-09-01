## 1. Normalize normative requirement paragraphs

- [x] 1.1 Add a neutral `SHALL` sentence to each warning-only baseline requirement while preserving all existing scenario blocks verbatim.
- [x] 1.2 Validate the change with `openspec validate repair-openspec-strict-validation --strict`.
- [x] 1.3 Archive the change in a scratch copy and confirm all rebuilt specs remain strict-valid.

## 2. Verification

- [x] 2.1 Run `openspec validate --specs --strict` and confirm no warning-only failures remain.
- [x] 2.2 Run the repository OpenSpec overwrite guard and confirm no new baseline loss.
