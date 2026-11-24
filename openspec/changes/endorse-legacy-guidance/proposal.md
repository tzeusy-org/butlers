## Why

Held rules stay out of active guidance until the owner endorses them. The Memory register can list held rows, but reviewing their provenance and endorsing them currently requires leaving the register or using the API. Legacy rows also lack a way to approve a finite batch after inspecting the exact guidance.

## What Changes

- Show stored provenance and an individual endorsement action for held guidance.
- Let the owner review and confirm up to 100 explicitly chosen legacy rules in one source.
- Return committed receipts and preserve existing authority stamps, visibility rules, and catalog exclusions.

## Scope

This change adds an owner dashboard interaction to existing endorsement authority. It does not add authority for sessions, grant bulk endorsement for stamped rules, classify legacy material, or enable an unapproved reader rollback.

## Impact

Memory House-Ledger UI, owner dashboard Memory API, and the rule endorsement transaction. No migration or new data store.
