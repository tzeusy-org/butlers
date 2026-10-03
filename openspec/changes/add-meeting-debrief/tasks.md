## 1. Slice 1: table, selection job, answer tools

- [x] 1.1 Migration `rel_036` adds `relationship.meeting_debriefs`.
- [x] 1.2 `butlers.jobs.meeting_debrief` selection with owner-RSVP, solo, transparent, posture and idempotence rules.
- [x] 1.3 `meeting_debrief_pending` / `meeting_debrief_answer` tools and the `meeting-debrief` skill.
- [x] 1.4 `create_commitment(sphere=...)` metadata.

## 2. Slice 2: batched prompt and back-off

- [x] 2.1 One batched insight per run, `prompted_at` only on acceptance.
- [x] 2.2 Weekly cadence after 3 consecutive unanswered prompts, announced once.

## 3. Deferred

- [ ] 3.1 Dashboard Debrief card with a pinned reply.
- [ ] 3.2 "Work commitments" filter on the condition ledger.
- [ ] 3.3 Sphere derived from endpoint custody.
