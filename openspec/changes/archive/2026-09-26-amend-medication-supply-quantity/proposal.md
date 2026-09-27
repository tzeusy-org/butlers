## Why

The Health butler already stores an owner-recorded medication quantity and its
last-recorded timestamp, and the refill insight job correctly refuses to infer
a supply when those facts are absent. The dashboard API exposes that server
truth, but the client does not send or render it, so the owner cannot provide a
real initial fill or refill and an unknown supply is visually silent.

## What Changes

- Define an additive Health API contract for optional, strictly positive whole-
  number `quantity` on medication create and update requests.
- Define omission as an explicit unknown/null response state; no default supply
  or zero is introduced.
- Wire the existing MedicationForm and typed client models through create,
  edit, and refill (quantity update) paths.
- Render `Supply: unknown` when the server has no quantity, or the exact
  owner-recorded count when it does.
- Refuse zero, negative, decimal, boolean, and malformed quantity input with
  typed 422 API validation and inline client validation.

## Deliberately Out of Scope

- Medication inference, adherence policy, provider synchronization, credential
  access, live health actions, or inventory forecasting redesign.
- New tables, columns, facts, or refill workflows. Updating quantity continues
  to use the existing `medication_update` fact path and timestamp anchor.

## Impact

Affected capability specs:

- `butler-health` — dashboard medication API quantity contract.
- `dashboard-domain-pages` — Medications page form and honest supply display.

Affected implementation surfaces:

- `roster/health/api/models.py`
- `frontend/src/api/types.ts`
- `frontend/src/components/health/MedicationForm.tsx`
- `frontend/src/components/health/MedicationTracker.tsx`
