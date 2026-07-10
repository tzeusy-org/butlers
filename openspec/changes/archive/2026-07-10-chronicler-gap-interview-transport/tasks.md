## 1. Document the shipped resolve endpoint (spec-only)

- [x] 1.1 MODIFY `chronicler-api` *Chronicler Corrections* to add
  `POST /api/chronicler/gap-interview/resolve` and its idempotent,
  always-200-with-status contract (carrying forward all existing scenarios).

## 2. Document the shipped telegram cgi: ingress (spec-only)

- [x] 2.1 MODIFY `connector-telegram-bot` *Update Type Handling* to carve out
  the additive `cgi:` gap-interview callback exception and its routing scenario
  (preserving the drop behavior for all non-`cgi:` callbacks).

## 3. Validate and archive

- [x] 3.1 `openspec validate chronicler-gap-interview-transport --strict`.
- [x] 3.2 Archive in-PR (`openspec archive`) so the deltas fold into the
  canonical specs.
