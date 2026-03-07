# CRUD-to-SPO Predicate Taxonomy and Migration Patterns

## Overview

This document defines the full predicate taxonomy, metadata schemas, entity resolution contract, aggregation query patterns, and backward-compatible wrapper API contract for migrating domain CRUD tables to bitemporal SPO facts (bu-ddb epic).

All predicates defined here MUST be seeded into the `predicate_registry` table as part of each phase migration.

---

## Part 1: Entity Resolution Contract

### 1.1 Principle — No Bare String Subjects

**ALL facts stored via domain migration wrappers MUST carry a resolved `entity_id` UUID.** The string `"user"` MUST NOT appear as the primary identifier for any migrated fact. The `subject` field is retained as a human-readable label but is NOT the identity key.

### 1.2 Resolution Cascade

| Data type | `entity_id` source | `subject` field value |
|---|---|---|
| Self-data (health, finance) | Owner entity: `SELECT entity_id FROM shared.contacts WHERE roles @> '["owner"]' LIMIT 1` | Owner name (e.g. "Alice") or `"owner"` |
| Contact-data (relationship) | `shared.contacts.entity_id` FK for the contact; if NULL, call `memory_entity_create()` and backfill | Contact's canonical name |
| HA device (home) | Entity looked up by `canonical_name = ha_entity_id`; create with `entity_type='other'` if not found | HA entity ID string (e.g. "sensor.bedroom_temp") |
| Unresolved actors (any domain) | `memory_entity_create(entity_type='other', name=<best_available_string>)` | Best available name string |

### 1.3 Owner Entity Bootstrap

The owner entity MUST be resolved at daemon startup via:
```sql
SELECT entity_id FROM shared.contacts WHERE roles @> '["owner"]' LIMIT 1
```

This UUID is cached in the butler's in-memory state and reused for all self-data facts. If the owner contact has no `entity_id`, the butler MUST call `memory_entity_create()` and update `shared.contacts.entity_id` before processing any domain facts.

### 1.4 Contact Entity Resolution for Relationship Data

For each relationship fact, the resolution pipeline is:
1. Resolve `contact_id` → `shared.contacts`
2. Read `shared.contacts.entity_id`; if non-NULL, use it
3. If NULL: call `memory_entity_create(entity_type='person', name=contact.name)` → get `entity_uuid`; UPDATE `shared.contacts SET entity_id = entity_uuid WHERE id = contact_id`
4. Use `entity_uuid` as `entity_id` for the fact

### 1.5 HA Device Entity Resolution

For each distinct HA entity ID string (e.g. `"sensor.bedroom_temp"`):
1. SELECT from `entities WHERE canonical_name = ha_entity_id AND entity_type = 'other'`
2. If found: use `entity.id`
3. If not found: call `memory_entity_create(entity_type='other', name=ha_entity_id, metadata={"entity_class": "ha_device"})` → use returned UUID

---

## Part 2: Scope Conventions

Each domain uses a fixed `scope` value to namespace facts and prevent predicate collisions:

| Domain | `scope` |
|---|---|
| Health butler | `health` |
| Relationship butler | `relationship` |
| Finance butler | `finance` |
| Home butler | `home` |

Facts created by the memory module's general consolidation engine use `scope='global'`.

---

## Part 3: Predicate Taxonomy

### 3.1 Health Domain Predicates (Phase 1)

All health facts use `entity_id = owner_entity_id` and `scope = 'health'`.

#### Temporal Predicates (is_temporal = true)

| Predicate | Description | expected_subject_type | valid_at source |
|---|---|---|---|
| `measurement_weight` | Body weight measurement | `person` | `measured_at` |
| `measurement_blood_pressure` | Blood pressure reading | `person` | `measured_at` |
| `measurement_heart_rate` | Heart rate measurement | `person` | `measured_at` |
| `measurement_blood_sugar` | Blood glucose measurement | `person` | `measured_at` |
| `measurement_temperature` | Body temperature measurement | `person` | `measured_at` |
| `symptom` | Symptom occurrence event | `person` | `occurred_at` |
| `took_dose` | Medication dose taken or skipped | `person` | `taken_at` |

**Catch-all measurement rule:** Any measurement type not listed above uses predicate `measurement_{type}` where `{type}` is the normalized measurement type string from the source table. New measurement types are added to the registry on first use.

#### Metadata Schemas — Temporal Health Predicates

**measurement_* predicates:**
```json
{
  "value": "<JSONB — number or object like {\"systolic\": 120, \"diastolic\": 80}>",
  "unit": "<string — e.g. \"kg\", \"mmHg\", \"bpm\", \"mmol/L\", \"°C\">",
  "notes": "<string | null>"
}
```

`content` field: human-readable summary, e.g. `"Weight: 72.5 kg"`, `"Blood pressure: 120/80 mmHg"`.

**symptom predicate:**
```json
{
  "severity": "<integer 1-10>",
  "condition_id": "<UUID string | null — FK to the condition entity if known>",
  "notes": "<string | null>"
}
```

`content` field: symptom name, e.g. `"Headache"`, `"Chest tightness"`.

**took_dose predicate:**
```json
{
  "medication_id": "<UUID string — fact ID or entity ID of the medication>",
  "skipped": "<boolean>",
  "notes": "<string | null>"
}
```

`content` field: medication name, e.g. `"Metformin 500mg"`.

#### Property Predicates (is_temporal = false)

| Predicate | Description | expected_subject_type | Supersession key |
|---|---|---|---|
| `medication` | Current active medication | `person` | `(entity_id, scope, predicate)` + content = medication name |
| `condition` | Diagnosed health condition | `person` | `(entity_id, scope, predicate)` + content = condition name |
| `research` | Saved health research item | `person` | `(entity_id, scope, predicate)` + content = research title |

**medication predicate metadata:**
```json
{
  "name": "<string>",
  "dosage": "<string — e.g. \"500mg\">",
  "frequency": "<string — e.g. \"twice daily\">",
  "schedule": "<list of strings — e.g. [\"08:00\", \"20:00\"]>",
  "active": "<boolean>",
  "notes": "<string | null>"
}
```

`content` field: `"{name} {dosage} {frequency}"`, e.g. `"Metformin 500mg twice daily"`.

**condition predicate metadata:**
```json
{
  "name": "<string>",
  "status": "<string — e.g. \"active\", \"managed\", \"resolved\">",
  "diagnosed_at": "<ISO 8601 date string | null>",
  "notes": "<string | null>"
}
```

`content` field: `"{name}: {status}"`, e.g. `"Type 2 Diabetes: managed"`.

**research predicate metadata:**
```json
{
  "title": "<string>",
  "tags": "<list of strings>",
  "source_url": "<string | null>",
  "condition_id": "<UUID string | null>"
}
```

`content` field: the full research content text (summary or article body).

---

### 3.2 Relationship Domain Predicates (Phase 2)

All relationship facts use `entity_id = contact_entity_id` (resolved contact entity) and `scope = 'relationship'`.

#### Temporal Predicates (is_temporal = true)

| Predicate | Description | expected_subject_type | valid_at source |
|---|---|---|---|
| `interaction_call` | Phone or video call with contact | `person` | `occurred_at` |
| `interaction_meeting` | In-person meeting | `person` | `occurred_at` |
| `interaction_message` | Message exchange | `person` | `occurred_at` |
| `interaction_email` | Email exchange | `person` | `occurred_at` |
| `interaction_other` | Other interaction type | `person` | `occurred_at` |
| `life_event` | Significant life event for contact | `person` | `happened_at` |
| `contact_note` | Note about a contact (append-only) | `person` | `created_at` |
| `activity` | Activity feed entry for contact | `person` | `created_at` |

**interaction_* predicates metadata:**
```json
{
  "type": "<string — original interaction_type value>",
  "notes": "<string | null>"
}
```

`content` field: summary of the interaction, e.g. `"Caught up over coffee, discussed new job"`.

**life_event predicate metadata:**
```json
{
  "life_event_type": "<string — e.g. \"birth\", \"marriage\", \"new_job\", \"graduation\">",
  "description": "<string | null>"
}
```

`content` field: summary, e.g. `"Got married to Sarah"`.

**contact_note predicate:** No supersession (append-only temporal). Each note is a separate fact with its own `valid_at`.

```json
{
  "emotion": "<string | null — e.g. \"happy\", \"concerned\">"
}
```

`content` field: full note content text.

**activity predicate metadata:**
```json
{
  "type": "<string — activity type>"
}
```

`content` field: activity description.

#### Property Predicates (is_temporal = false)

| Predicate | Description | expected_subject_type | Notes |
|---|---|---|---|
| `<dynamic>` | quick_facts key→predicate migration | `person` | Predicate = the key string itself |
| `gift` | Gift given to or received from contact | `person` | |
| `loan` | Loan with contact (money lent or borrowed) | `person` | |
| `contact_task` | Task related to a contact | `person` | |
| `reminder` | Reminder about a contact | `person` | |

**quick_facts migration:** The `quick_facts` table stores `(contact_id, key, value)` — already SPO-shaped. Migration: `predicate = key`, `content = value`, `entity_id = contact_entity_id`. Keys become predicates directly; no fixed predicate name. These facts use `is_temporal = false` and support supersession by `(entity_id, scope, predicate)`.

**gift predicate metadata:**
```json
{
  "occasion": "<string | null — e.g. \"birthday\", \"christmas\">",
  "status": "<string — e.g. \"idea\", \"purchased\", \"given\">"
}
```

`content` field: gift description, e.g. `"Noise-cancelling headphones"`.

**loan predicate metadata:**
```json
{
  "amount": "<string — NUMERIC as string, e.g. \"50.00\">",
  "currency": "<string — ISO-4217, e.g. \"USD\">",
  "direction": "<string — \"lent\" or \"borrowed\">",
  "settled": "<boolean>",
  "settled_at": "<ISO 8601 datetime string | null>"
}
```

`content` field: loan description, e.g. `"Lent $50 for concert tickets"`.

**contact_task predicate metadata:**
```json
{
  "description": "<string | null>",
  "completed": "<boolean>",
  "completed_at": "<ISO 8601 datetime string | null>"
}
```

`content` field: task title, e.g. `"Send birthday card"`.

**reminder predicate metadata:**
```json
{
  "reminder_type": "<string — e.g. \"one_time\", \"recurring\">",
  "cron": "<string | null — cron expression for recurring>",
  "due_at": "<ISO 8601 datetime string | null>",
  "dismissed": "<boolean>"
}
```

`content` field: reminder message, e.g. `"Call Mom about Thanksgiving plans"`.

---

### 3.3 Finance Domain Predicates (Phase 3)

All finance facts use `entity_id = owner_entity_id` and `scope = 'finance'`.

#### Temporal Predicates (is_temporal = true)

| Predicate | Description | expected_subject_type | valid_at source |
|---|---|---|---|
| `transaction_debit` | Money leaving owner's account | `person` | `posted_at` |
| `transaction_credit` | Money entering owner's account | `person` | `posted_at` |

**transaction_* predicates metadata:**
```json
{
  "account_id": "<UUID string | null — fact ID of the account fact>",
  "source_message_id": "<string | null — ingestion source message ID for dedup>",
  "merchant": "<string | null>",
  "description": "<string | null>",
  "amount": "<string — NUMERIC(14,2) as string, e.g. \"1234.99\">",
  "currency": "<string — ISO-4217, e.g. \"USD\">",
  "category": "<string | null — e.g. \"groceries\", \"transport\">",
  "payment_method": "<string | null>",
  "receipt_url": "<string | null>",
  "external_ref": "<string | null>",
  "extra": "<JSONB object | null — connector-specific fields>"
}
```

`content` field: `"{merchant} {amount} {currency}"`, e.g. `"Whole Foods 47.32 USD"`.

**Deduplication check:** Before inserting a transaction fact, query:
```sql
SELECT id FROM facts
WHERE entity_id = $owner_entity_id
  AND predicate IN ('transaction_debit', 'transaction_credit')
  AND validity = 'active'
  AND scope = 'finance'
  AND metadata->>'source_message_id' = $source_message_id
  AND metadata->>'merchant' = $merchant
  AND metadata->>'amount' = $amount
  AND valid_at = $posted_at
LIMIT 1
```
If a row is found, skip insert and return the existing fact ID.

#### Property Predicates (is_temporal = false)

| Predicate | Description | expected_subject_type | Notes |
|---|---|---|---|
| `account` | Financial account (bank, credit card, etc.) | `person` | content = stable identifier for dedup |
| `subscription` | Recurring subscription | `person` | |
| `bill` | Upcoming or recurring bill | `person` | |

**account predicate metadata:**
```json
{
  "institution": "<string — bank/card issuer name>",
  "type": "<string — e.g. \"checking\", \"savings\", \"credit\">",
  "name": "<string | null — account nickname>",
  "last_four": "<string | null — last 4 digits>",
  "currency": "<string — ISO-4217>",
  "extra": "<JSONB object | null>"
}
```

`content` field: `"{institution} {type} ****{last_four}"`, e.g. `"Chase checking ****4242"`. Content serves as a stable identifier for supersession differentiation between multiple accounts.

**subscription predicate metadata:**
```json
{
  "service": "<string>",
  "amount": "<string — NUMERIC as string>",
  "currency": "<string — ISO-4217>",
  "frequency": "<string — e.g. \"monthly\", \"annual\">",
  "next_renewal": "<ISO 8601 date string | null>",
  "status": "<string — e.g. \"active\", \"cancelled\", \"paused\">",
  "auto_renew": "<boolean>",
  "payment_method": "<string | null>",
  "account_id": "<UUID string | null>",
  "source_message_id": "<string | null>"
}
```

`content` field: `"{service} {amount}/{frequency}"`, e.g. `"Netflix 15.99/monthly"`.

**bill predicate metadata:**
```json
{
  "payee": "<string>",
  "amount": "<string — NUMERIC as string>",
  "currency": "<string — ISO-4217>",
  "due_date": "<ISO 8601 date string>",
  "frequency": "<string | null — e.g. \"monthly\", \"one_time\">",
  "status": "<string — e.g. \"pending\", \"paid\", \"overdue\">",
  "payment_method": "<string | null>",
  "account_id": "<UUID string | null>",
  "statement_period_start": "<ISO 8601 date string | null>",
  "statement_period_end": "<ISO 8601 date string | null>",
  "paid_at": "<ISO 8601 datetime string | null>",
  "source_message_id": "<string | null>"
}
```

`content` field: `"{payee} {amount} due {due_date}"`, e.g. `"PG&E 123.45 due 2026-03-15"`.

---

### 3.4 Home Domain Predicates (Phase 4)

HA device facts use `entity_id = ha_device_entity_id` (resolved or created HA device entity) and `scope = 'home'`.

#### Property Predicates (is_temporal = false, with supersession)

| Predicate | Description | expected_subject_type | Notes |
|---|---|---|---|
| `ha_state` | Current state of a Home Assistant entity | `other` | Superseded on each snapshot cycle |

**ha_state predicate metadata:**
```json
{
  "attributes": "<JSONB object — full HA attributes dict>",
  "entity_id_ha": "<string — HA entity ID string, e.g. \"sensor.bedroom_temp\">"
}
```

`content` field: state value string as returned by HA, e.g. `"on"`, `"22.5"`, `"unavailable"`.

`valid_at` usage: Although `ha_state` is a property fact (supersession on update), the `valid_at` field SHOULD be set to the `last_updated` timestamp from HA to record when the state was observed. This allows temporal queries on the superseded chain if needed.

---

## Part 4: Aggregation Query Patterns

### 4.1 nutrition_summary

**Original query:** SQL SUM/GROUP BY on dedicated `meals` table with typed `calories`, `protein_g` columns.

**Migrated query:**
```sql
SELECT
  SUM((metadata->>'estimated_calories')::NUMERIC)  AS total_calories,
  SUM((metadata->'macros'->>'protein_g')::NUMERIC)  AS total_protein_g,
  SUM((metadata->'macros'->>'carbs_g')::NUMERIC)    AS total_carbs_g,
  SUM((metadata->'macros'->>'fat_g')::NUMERIC)      AS total_fat_g,
  COUNT(*)                                           AS meal_count
FROM facts
WHERE entity_id = $owner_entity_id
  AND predicate IN ('meal_breakfast', 'meal_lunch', 'meal_dinner', 'meal_snack')
  AND validity = 'active'
  AND scope = 'health'
  AND valid_at BETWEEN $start_date AND $end_date
```

**Recommended index:** Partial B-tree on `(entity_id, predicate, valid_at)` WHERE `predicate IN ('meal_breakfast','meal_lunch','meal_dinner','meal_snack') AND validity = 'active'`. (Also covered by the broader `meal_%` partial index from bu-ddb.6.)

**Response shape (preserved):**
```json
{
  "period": {"start": "<ISO date>", "end": "<ISO date>"},
  "total_calories": 1850,
  "macros": {"protein_g": 120.5, "carbs_g": 210.0, "fat_g": 65.3},
  "meal_count": 3
}
```

---

### 4.2 spending_summary

**Original query:** SQL SUM/GROUP BY on dedicated `transactions` table with typed `amount NUMERIC` column.

**Migrated query:**
```sql
SELECT
  metadata->>'category'                       AS category,
  SUM((metadata->>'amount')::NUMERIC)         AS total,
  COUNT(*)                                    AS count
FROM facts
WHERE entity_id = $owner_entity_id
  AND predicate IN ('transaction_debit', 'transaction_credit')
  AND validity = 'active'
  AND scope = 'finance'
  AND valid_at BETWEEN $start_date AND $end_date
GROUP BY metadata->>'category'
ORDER BY total DESC
```

**Recommended index:** Partial B-tree on `(entity_id, predicate, valid_at)` WHERE `predicate IN ('transaction_debit','transaction_credit') AND validity = 'active'`. (Covered by `transaction_%` partial index from bu-ddb.6.)

**Grand total query variant:**
```sql
SELECT
  predicate,
  SUM((metadata->>'amount')::NUMERIC) AS total,
  metadata->>'currency'               AS currency
FROM facts
WHERE entity_id = $owner_entity_id
  AND predicate IN ('transaction_debit', 'transaction_credit')
  AND validity = 'active'
  AND scope = 'finance'
  AND valid_at BETWEEN $start_date AND $end_date
GROUP BY predicate, metadata->>'currency'
```

**Response shape (preserved):**
```json
{
  "period": {"start": "<ISO date>", "end": "<ISO date>"},
  "total_spent": 1234.56,
  "currency": "USD",
  "by_category": [
    {"category": "groceries", "total": 342.10, "count": 8},
    {"category": "transport", "total": 89.50, "count": 5}
  ]
}
```

---

### 4.3 trend_report (health measurements)

**Original query:** SQL SELECT with ordering on dedicated `measurements` table.

**Migrated query:**
```sql
SELECT
  valid_at                          AS measured_at,
  metadata->>'value'                AS value,
  metadata->>'unit'                 AS unit,
  content                           AS label
FROM facts
WHERE entity_id = $owner_entity_id
  AND predicate = $measurement_predicate
  AND validity = 'active'
  AND scope = 'health'
  AND valid_at >= NOW() - INTERVAL '30 days'
ORDER BY valid_at ASC
```

**Recommended index:** Partial B-tree on `(entity_id, predicate, valid_at)` WHERE `predicate LIKE 'measurement_%' AND validity = 'active'`. (Covered by `measurement_%` partial index from bu-ddb.6.)

**Response shape (preserved):**
```json
{
  "measurement_type": "weight",
  "unit": "kg",
  "period_days": 30,
  "data_points": [
    {"measured_at": "<ISO datetime>", "value": 72.5},
    {"measured_at": "<ISO datetime>", "value": 72.1}
  ],
  "trend": "decreasing",
  "min": 72.1,
  "max": 73.0,
  "avg": 72.4
}
```

---

### 4.4 Index Recommendations (bu-ddb.6)

All indexes belong to a migration that runs AFTER `facts.valid_at` is non-NULL-able and all domain predicate seeds have landed.

```sql
-- GIN index for JSONB containment and key existence queries
CREATE INDEX CONCURRENTLY idx_facts_metadata_gin
  ON facts USING GIN (metadata);

-- Partial B-tree for meal aggregation (nutrition_summary)
CREATE INDEX CONCURRENTLY idx_facts_meal_temporal
  ON facts (entity_id, predicate, valid_at)
  WHERE predicate LIKE 'meal_%' AND validity = 'active';

-- Partial B-tree for transaction aggregation (spending_summary)
CREATE INDEX CONCURRENTLY idx_facts_transaction_temporal
  ON facts (entity_id, predicate, valid_at)
  WHERE predicate LIKE 'transaction_%' AND validity = 'active';

-- Partial B-tree for measurement trend queries (trend_report)
CREATE INDEX CONCURRENTLY idx_facts_measurement_temporal
  ON facts (entity_id, predicate, valid_at)
  WHERE predicate LIKE 'measurement_%' AND validity = 'active';
```

**Performance target:** Aggregation queries over 10,000 facts in scope MUST complete in < 100ms with these indexes in place (bu-ddb.6 acceptance criterion).

---

## Part 5: Wrapper API Contract — Backward-Compatible Response Shapes

Each migrated tool MUST return the same response structure as the original CRUD-table implementation. The following tables document the field-level mapping from fact to legacy response.

### 5.1 Health Wrapper Mappings

#### measurement_log → store_fact(predicate=measurement_{type}, valid_at=measured_at)

**Input (unchanged):** `type`, `value`, `unit`, `measured_at`, `notes`
**Stored fact:** `predicate = "measurement_{type}"`, `valid_at = measured_at`, `content = "{type}: {value} {unit}"`, `metadata = {value, unit, notes}`
**Response (unchanged):**
```json
{"id": "<fact_uuid>", "type": "<type>", "value": "<value>", "unit": "<unit>", "measured_at": "<ISO datetime>", "notes": "<notes|null>"}
```
**Mapping:** `id ← fact.id`, `measured_at ← fact.valid_at`, `value ← fact.metadata->>'value'`, `unit ← fact.metadata->>'unit'`

#### measurement_history → query facts with predicate = measurement_{type}

**Response (unchanged):**
```json
{"measurements": [{"id": "<uuid>", "type": "<type>", "value": "<value>", "unit": "<unit>", "measured_at": "<ISO datetime>"}]}
```
**Mapping:** Each fact row → `{id: fact.id, type: predicate[12:], value: metadata->>'value', unit: metadata->>'unit', measured_at: valid_at}`

#### symptom_log → store_fact(predicate='symptom', valid_at=occurred_at)

**Response (unchanged):**
```json
{"id": "<fact_uuid>", "symptom": "<name>", "severity": <1-10>, "occurred_at": "<ISO datetime>", "notes": "<notes|null>"}
```
**Mapping:** `id ← fact.id`, `symptom ← fact.content`, `severity ← (fact.metadata->>'severity')::INT`, `occurred_at ← fact.valid_at`

#### medication_add → store_fact(predicate='medication', valid_at=NULL)

**Response (unchanged):**
```json
{"id": "<fact_uuid>", "name": "<name>", "dosage": "<dosage>", "frequency": "<frequency>", "active": true}
```
**Mapping:** `id ← fact.id`, unpack from `fact.metadata`

#### condition_add → store_fact(predicate='condition', valid_at=NULL)

**Response (unchanged):**
```json
{"id": "<fact_uuid>", "name": "<name>", "status": "<status>", "diagnosed_at": "<date|null>"}
```
**Mapping:** `id ← fact.id`, unpack from `fact.metadata`

---

### 5.2 Relationship Wrapper Mappings

#### interaction_log → store_fact(predicate='interaction_{type}', valid_at=occurred_at)

**Response (unchanged):**
```json
{"id": "<fact_uuid>", "contact_id": "<uuid>", "type": "<type>", "summary": "<summary>", "occurred_at": "<ISO datetime>"}
```
**Mapping:** `id ← fact.id`, `summary ← fact.content`, `occurred_at ← fact.valid_at`, `type ← fact.metadata->>'type'`

#### fact_set (quick_facts) → store_fact(predicate=key, content=value, valid_at=NULL)

**Response (unchanged):**
```json
{"contact_id": "<uuid>", "key": "<key>", "value": "<value>"}
```
**Mapping:** `key ← fact.predicate`, `value ← fact.content`

#### gift_add → store_fact(predicate='gift', valid_at=NULL)

**Response (unchanged):**
```json
{"id": "<fact_uuid>", "contact_id": "<uuid>", "description": "<desc>", "occasion": "<occasion|null>", "status": "<status>"}
```
**Mapping:** `id ← fact.id`, `description ← fact.content`, unpack `occasion`, `status` from `fact.metadata`

#### loan_create → store_fact(predicate='loan', valid_at=NULL)

**Response (unchanged):**
```json
{"id": "<fact_uuid>", "contact_id": "<uuid>", "amount": "<amount>", "currency": "<currency>", "direction": "<lent|borrowed>", "settled": false}
```
**Mapping:** `id ← fact.id`, `description ← fact.content`, unpack all fields from `fact.metadata`

---

### 5.3 Finance Wrapper Mappings

#### record_transaction → store_fact(predicate='transaction_{direction}', valid_at=posted_at)

**Response (unchanged):**
```json
{"id": "<fact_uuid>", "merchant": "<merchant>", "amount": "<amount>", "currency": "<currency>", "direction": "<debit|credit>", "category": "<category|null>", "posted_at": "<ISO datetime>"}
```
**Mapping:** `id ← fact.id`, `posted_at ← fact.valid_at`, unpack all fields from `fact.metadata`

#### list_transactions → query facts with predicate IN ('transaction_debit', 'transaction_credit')

**Response (unchanged):**
```json
{"transactions": [{"id": "<uuid>", "merchant": "<merchant>", "amount": "<amount>", "direction": "<debit|credit>", "posted_at": "<ISO datetime>", "category": "<category|null>"}]}
```
**Mapping:** Each fact row maps to one transaction entry.

#### track_subscription → store_fact(predicate='subscription', valid_at=NULL)

**Response (unchanged):**
```json
{"id": "<fact_uuid>", "service": "<service>", "amount": "<amount>", "currency": "<currency>", "frequency": "<frequency>", "status": "active", "next_renewal": "<date|null>"}
```
**Mapping:** `id ← fact.id`, unpack from `fact.metadata`

#### track_bill → store_fact(predicate='bill', valid_at=NULL)

**Response (unchanged):**
```json
{"id": "<fact_uuid>", "payee": "<payee>", "amount": "<amount>", "currency": "<currency>", "due_date": "<ISO date>", "status": "pending"}
```
**Mapping:** `id ← fact.id`, unpack from `fact.metadata`

---

### 5.4 Home Wrapper Mappings

#### ha_entity_snapshot (internal persistence) → store_fact(predicate='ha_state', valid_at=last_updated, valid_at IS NOT NULL for audit)

No external tool exposes `ha_entity_snapshot` directly — the module persists it internally on HA state polling. The wrapper replaces the `INSERT/UPDATE INTO ha_entity_snapshot` with `store_fact()` with supersession. The state is retrieved via `memory_recall` by `(entity_id, predicate='ha_state', scope='home')`.

**Internal read pattern (replacing SELECT from ha_entity_snapshot):**
```sql
SELECT content AS state, metadata->>'attributes' AS attributes_json, valid_at AS last_updated
FROM facts
WHERE entity_id = $ha_device_entity_id
  AND predicate = 'ha_state'
  AND validity = 'active'
  AND scope = 'home'
ORDER BY created_at DESC
LIMIT 1
```

---

## Part 6: Predicate Registry Seed Summary

The following predicates MUST be seeded into `predicate_registry` as part of each phase migration. All rows use `is_edge = false` unless noted.

### Phase 1 — Health Predicates

| name | is_temporal | expected_subject_type | description |
|---|---|---|---|
| `measurement_weight` | true | person | Body weight temporal measurement |
| `measurement_blood_pressure` | true | person | Blood pressure reading (JSONB value with systolic/diastolic) |
| `measurement_heart_rate` | true | person | Heart rate measurement |
| `measurement_blood_sugar` | true | person | Blood glucose measurement |
| `measurement_temperature` | true | person | Body temperature measurement |
| `symptom` | true | person | Symptom occurrence event with severity |
| `took_dose` | true | person | Medication dose taken or skipped |
| `medication` | false | person | Current active medication (property fact) |
| `condition` | false | person | Diagnosed health condition (property fact) |
| `research` | false | person | Saved health research item (property fact) |

### Phase 2 — Relationship Predicates

| name | is_temporal | expected_subject_type | description |
|---|---|---|---|
| `interaction_call` | true | person | Phone or video call with contact |
| `interaction_meeting` | true | person | In-person meeting with contact |
| `interaction_message` | true | person | Message exchange with contact |
| `interaction_email` | true | person | Email exchange with contact |
| `interaction_other` | true | person | Other interaction type with contact |
| `life_event` | true | person | Significant life event for a contact |
| `contact_note` | true | person | Note about a contact (append-only) |
| `activity` | true | person | Activity feed entry |
| `gift` | false | person | Gift given to or received from contact |
| `loan` | false | person | Loan with a contact |
| `contact_task` | false | person | Task related to a contact |
| `reminder` | false | person | Reminder about a contact |

**Note on quick_facts:** The key-as-predicate pattern means individual quick_fact keys are NOT seeded individually. They are stored using whatever key string was used in the source table. No registry entry is created per key; they remain unregistered predicates (which is valid per the registry-is-advisory rule).

### Phase 3 — Finance Predicates

| name | is_temporal | expected_subject_type | description |
|---|---|---|---|
| `transaction_debit` | true | person | Money leaving owner account |
| `transaction_credit` | true | person | Money entering owner account |
| `account` | false | person | Financial account (bank, credit card, etc.) |
| `subscription` | false | person | Recurring subscription |
| `bill` | false | person | Upcoming or recurring bill |

### Phase 4 — Home Predicates

| name | is_temporal | expected_subject_type | description |
|---|---|---|---|
| `ha_state` | false | other | Current state of a Home Assistant entity (property fact with supersession) |
