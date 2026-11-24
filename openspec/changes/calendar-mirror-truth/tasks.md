## 1. Slice B: mirror truth and fleet default target

- [x] 1.1 Link id and provider id in separate columns, with a per-row mirror state (core_377)
- [x] 1.2 Mirror push records failures, retries them and skips unchanged tasks
- [x] 1.3 Sync health reports mirror counts
- [x] 1.3a Dashboard source freshness carries the counts and shows the scheduler source failed
- [x] 1.3b Delete retires a mirrored task until the provider copy is gone (`delete_requested_at`)
- [x] 1.3c Idempotent creates, backoff and parking, 410 as deleted
- [x] 1.4 `calendar_set_primary` is followed by every calendar butler
- [ ] 1.5 Deadman alert on a long-failing mirror (follow-up)
