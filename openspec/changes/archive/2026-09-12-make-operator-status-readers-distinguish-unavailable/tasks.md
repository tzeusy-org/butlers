## 1. Operator reader states

- [x] 1.1 Render audit query failure as a named, retryable degraded state without replacing successful-empty history semantics.
- [x] 1.2 Retain cached privileged audit rows with degraded labeling when their refresh fails.
- [x] 1.3 Gate Telegram setup on a successful status response and render loading and unavailable states without credential inference or disclosure.

## 2. Focused regression coverage

- [x] 2.1 Cover audit no-history, unavailable, and cached-degraded states in the Permissions page tests.
- [x] 2.2 Cover Telegram loading, unavailable/retry, and successful-unready states in rendered component tests.

## 3. Verification

- [x] 3.1 Run strict OpenSpec validation for both deltas.
- [x] 3.2 Run focused frontend tests plus scoped lint, format, and diff checks.
