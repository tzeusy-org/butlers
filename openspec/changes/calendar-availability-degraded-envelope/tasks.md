## 1. Spec delta — dashboard-api

- [x] 1.1 Write `specs/dashboard-api/spec.md` MODIFYING "Calendar Workspace": add find-time endpoint mention to description, add "Find-time returns ranked open slots" and "Find-time fails open with available=false" scenarios, add "Workspace search degrades when all targeted schemas fail" scenario
- [x] 1.2 Verify the modified requirement header matches the canonical spec exactly (`### Requirement: Calendar Workspace`)

## 2. Spec delta — module-calendar

- [x] 2.1 Write `specs/module-calendar/spec.md` MODIFYING "Calendar Event Full-Text Search Query": add `available` signal rule scenario ("available signal is false only when all targeted schemas fail") and update requirement description to mention the `available` boolean
- [x] 2.2 Verify the modified requirement header matches the canonical spec exactly (`### Requirement: Calendar Event Full-Text Search Query`)

## 3. Validate and archive

- [x] 3.1 Run `openspec validate calendar-availability-degraded-envelope --strict` and fix any errors
- [ ] 3.2 PR merged → run `openspec archive calendar-availability-degraded-envelope` to sync deltas into canonical specs
