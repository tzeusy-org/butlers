## 1. Summary API

- [x] 1.1 Add an internal owner-cancellation boolean to the shared session-summary projection and DTO without exposing raw error text.
- [x] 1.2 Return the additive summary field from the global and butler-scoped list routes while preserving their pagination contracts.

## 2. Sessions table

- [x] 2.1 Add the frontend summary type field and render it as `Cancelled` in `SessionTable` without changing generic failed or running states.

## 3. Verification

- [x] 3.1 Add focused backend coverage for canonical cancellation, generic failure, and non-terminal summaries across both list routes.
- [x] 3.2 Add focused frontend table coverage for cancelled, failed, and non-terminal status rendering.
- [x] 3.3 Run targeted backend and frontend tests, formatter/lint/type checks, and strict OpenSpec validation.
