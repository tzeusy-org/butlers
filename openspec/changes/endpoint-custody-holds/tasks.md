## 1. Hold record and owner-resolution gate (slice 1)

- [x] 1.1 Add `public.custody_holds` with digest target key, monotonic generation, one active hold
      per target, append-only history and owner-only row level security (core_293).
- [x] 1.2 Add the content-blind `public.custody_target_held` read and make
      `public.resolve_owner_triple` refuse owner for an actively held matched fact.
- [x] 1.3 Refuse downgrade while a hold is active or unprovable.
- [x] 1.4 Real-PostgreSQL tests: held email, Telegram and WhatsApp-by-phone stop resolving and
      release restores; runtime role cannot read, forge, release or delete; history cannot rewind;
      downgrade refusal and reapply.

## 2. Remaining slices

- [ ] 2.1 Admitted Switchboard hold engine: cheap owner-resolved LOCK, protected dashboard/host
      hold, release and replaced, command/case/receipt atomicity (REQ-001, -002, -003, -004).
- [ ] 2.2 Direct `resolve_contact_by_channel` routing, memory and session-recheck demotion
      (REQ-005, needs the D3 `endpoint_held` amendment).
- [ ] 2.3 Approved-executor and Messenger egress fences with owner-default fallback (REQ-006
      executor half, REQ-007).
- [ ] 2.4 Security question and bound answer, Finance and recovery evidence, release and parked
      recovery, protected owner surface (REQ-008 to -011).
