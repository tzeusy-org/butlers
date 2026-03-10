## 1. Spec Sync

- [ ] 1.1 Archive this change via `openspec sync` to merge delta specs into the main `openspec/specs/module-memory/spec.md` and `openspec/specs/entity-identity/spec.md`

## 2. Butler Skill Prompt Updates

- [ ] 2.1 Update the shared `butler-memory` skill (used by all butlers with memory module) to include the resolve-or-create-transitory protocol: resolve → if empty, create with `metadata.unidentified=true` → use `entity_id`
- [ ] 2.2 Update the finance butler's email-processing skill/prompt to follow the resolve-or-create pattern for merchants and organizations found in emails
- [ ] 2.3 Update the health butler's skill/prompt to follow the resolve-or-create pattern for providers, clinics, and other entities mentioned in health data
- [ ] 2.4 Update the relationship butler's skill/prompt to follow the resolve-or-create pattern for mentioned people not in the preamble
- [ ] 2.5 Update the home butler's skill/prompt to follow the resolve-or-create pattern for HA devices and service providers

## 3. CRUD-to-SPO Predicate Taxonomy Alignment

- [ ] 3.1 Update `openspec/changes/crud-to-spo-migration/specs/predicate-taxonomy.md` §1.2 "Resolution Cascade" — add `metadata.unidentified=true` requirement to the "Unresolved actors" row and add a new row for "Unknown organization/place from ingestion"
- [ ] 3.2 Update §1.4 "Contact Entity Resolution for Relationship Data" to include the `unidentified` metadata flag when creating entities for contacts with no `entity_id`

## 4. Backfill Existing String-Anchored Facts

- [ ] 4.1 Write a diagnostic query to find all active facts where `entity_id IS NULL` and `subject` is not a known generic label (like "Owner") — these are the string-anchored facts that need backfill
- [ ] 4.2 Write a migration script that for each unique `(subject, scope)` pair with `entity_id IS NULL`: creates a transitory entity (`metadata.unidentified=true, source="backfill"`), then updates all matching facts to set `entity_id` to the new entity
- [ ] 4.3 Run the backfill and verify affected facts now appear linked in `/entities`

## 5. Verification

- [ ] 5.1 Test the end-to-end flow: process an email with a new merchant name → verify transitory entity appears in `/entities` "Unidentified Entities" section → merge it into a confirmed entity → verify facts are re-pointed
- [ ] 5.2 Test idempotency: process the same merchant name twice → verify no duplicate entity is created (second call resolves the existing transitory entity)
- [ ] 5.3 Test entity type inference: verify organizations, people, and places get correct `entity_type` values on the transitory entity
