## 1. Ordinary notification recovery compatibility

- [x] 1.1 Omit unset recovery fields from generated notify envelopes.
- [x] 1.2 Treat absent/null recovery as ordinary delivery and malformed non-null recovery as fail-closed recovery traffic.

## 2. Proof-bearing delivery

- [x] 2.1 Reject nested route errors and incomplete or mismatched Messenger receipts before sent logging or outbound history.
- [x] 2.2 Validate Telegram `ok=true` plus a positive integer provider `message_id` and propagate it as the delivery id.
- [x] 2.3 Record fixed content-blind failure categories and retain confirmed delivery across later bookkeeping failures.

## 3. Verification

- [x] 3.1 Add focused recovery-null, nested-error, malformed-receipt, Telegram response, timeout, and audit tests.
- [ ] 3.2 Run strict OpenSpec validation, overwrite checks, targeted tests, planner-selected gates, independent review, and hosted CI.
- [ ] 3.3 After landing and separate runtime authorization, restart affected services and run one traced Telegram canary.
