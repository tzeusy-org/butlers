## Context

Identity in the butler system is currently fragmented across three ad-hoc mechanisms:

1. **Tool-name prefixes** (`user_*/bot_*`): The I/O model convention infers identity from tool names. A `user_telegram_send_message` tool implies "acting as the user's personal account." This is enforced through `ToolIODescriptor`, four Module ABC methods, daemon validators, name compliance scanners, and a safety-net heuristic — ~1,200 lines of tests and significant daemon complexity, all to approximate "who is this message for?"

2. **Raw credential lookup**: `notify()` resolves the owner's Telegram chat ID by calling `credential_store.resolve("BUTLER_TELEGRAM_CHAT_ID")`. There is no contact-based resolution — recipients are either hardcoded secret keys or freeform strings.

3. **Unlinked source logging**: The Switchboard logs `source_id` (e.g., a Telegram chat ID) in `routing_log` but never resolves it to a known contact. Every inbound message is treated identically regardless of sender.

Meanwhile, the contacts table and contact_info table already exist with the right shape for identity resolution — they just aren't wired into the identity path.

### Current schema layout

- `contacts` lives in the `relationship` schema (defined across rel_001 through rel_008)
- `shared.contact_info` lives in `shared` (moved there in contacts_002), with no FK to contacts (cross-schema)
- `butler_secrets` is per-butler-schema (core_001), holding owner credentials like `BUTLER_TELEGRAM_CHAT_ID`
- `entities` lives in the memory butler's schema (mem_002), linked from contacts via `entity_id` FK

### Constraints

- Each butler has its own PostgreSQL schema; cross-schema FK constraints require explicit schema qualification
- Butler roles have `search_path = {own_schema}, shared, public`
- The relationship butler queries `contacts` with unqualified names (resolves via search_path)
- 17+ tables in the relationship schema hold `contact_id` FKs with `ON DELETE CASCADE`
- `shared.contact_info` intentionally omits FK to contacts due to cross-schema limitation — app-layer integrity enforced

## Goals / Non-Goals

**Goals:**

- Single source of truth for "who is this person" across all butlers, resolved from channel identifiers
- Owner identity bootstrapped at first boot, filled in by user via dashboard
- Role-based approval gating (owner contacts bypass, non-owner contacts require approval) replacing the tool-name-based heuristic
- Switchboard can distinguish owner vs. third-party messages and inject identity context into routed prompts
- `notify()` can resolve a contact's channel identifiers for outbound delivery
- Owner channel credentials (Telegram chat ID, email, OAuth tokens) stored on the owner's contact record rather than scattered across `butler_secrets`
- `entity_id` is the canonical cross-butler subject identifier for facts, health records, etc.

**Non-Goals:**

- Multi-user / multi-owner support (single owner per deployment)
- Authentication / session management for the dashboard (existing auth model unchanged)
- Automated role assignment from ingestion (roles are dashboard-only to prevent injection from untrusted sources)
- Bi-directional contact sync (write-back to Google/Apple) — remains out of scope
- Real-time push notifications for identity changes

## Decisions

### D1: Move `contacts` table to `shared` schema

**Decision:** Migrate `contacts` from the `relationship` schema to `shared.contacts`.

**Rationale:** The Switchboard, approval gate, and `notify()` all need to resolve contacts. Under per-butler schema isolation, only the relationship butler can query its own `contacts` table. Moving to `shared` makes contacts universally accessible via `search_path` (every butler already has `shared` in their path).

**Alternative considered:** Cross-schema views or a dedicated "identity" microservice. Rejected — adds latency and complexity for what is fundamentally a lookup table. The contacts table is small (hundreds to low thousands of rows), read-heavy, and needed by every butler.

**Migration approach:**
1. New Alembic migration in `core/` (not `relationship/`) since this is a shared-schema change
2. `ALTER TABLE relationship.contacts SET SCHEMA shared` — preserves data, indexes, and sequences
3. All 17+ relationship-schema tables keep their `contact_id` FKs but now reference `shared.contacts(id)`. Since `shared` is in every butler's `search_path`, the unqualified `contacts` name still resolves correctly for the relationship butler
4. The existing `shared.contact_info` cross-schema FK gap is closed — both tables are now in `shared`, so a real FK constraint can be added: `shared.contact_info(contact_id) REFERENCES shared.contacts(id) ON DELETE CASCADE`
5. `entity_id` FK from contacts → entities remains cross-schema (entities stays in memory schema). The conditional DO-block FK pattern from rel_008 is retained
6. Grant `SELECT, INSERT, UPDATE, DELETE` on `shared.contacts` to all butler roles (`butler_switchboard_rw`, `butler_general_rw`, `butler_health_rw`, `butler_relationship_rw`)

**Impact on existing queries:** The relationship butler's unqualified `SELECT * FROM contacts` continues to work because `shared` is in its `search_path`. No SQL changes needed in relationship tools. The API router's `shared.contact_info` joins become same-schema.

### D2: Add `roles TEXT[]` column to contacts

**Decision:** Add `roles TEXT[] NOT NULL DEFAULT '{}'` to `shared.contacts`.

**Rationale:** A text array is simple, queryable (`'owner' = ANY(roles)`), and extensible for future role values (`family`, `trusted`, etc.) without schema changes. A join table is overkill for a low-cardinality attribute on a small table.

**Role semantics:**
- `owner` — the system operator. Exactly one contact has this role. Messages from owner-role contacts bypass outbound approval gates.
- Future roles (`family`, `trusted`, etc.) can define graduated approval policies.
- `[]` (empty) — default for all non-owner contacts. Full approval gating applies.

**Write restriction:** Roles are ONLY modifiable through the dashboard API (authenticated). Butler runtime instances (LLM CLI sessions) must NOT be able to modify roles. The `contact_update` MCP tool explicitly excludes `roles` from its writable fields. This prevents an ingestion-sourced message like "Chloe is my family" from escalating privileges.

### D3: Owner bootstrap via seed contact

**Decision:** On first daemon startup (any butler), if no contact with `'owner' = ANY(roles)` exists in `shared.contacts`, create a seed contact with `roles = ['owner']`, `name = 'Owner'`, and empty channel identifiers.

**Rationale:** Avoids the chicken-and-egg problem of needing an owner GUID before the system exists. The owner fills in their actual identifiers (Telegram chat ID, email address, etc.) at `/butlers/contacts/{owner_id}` on the dashboard.

**Implementation:** A `_ensure_owner_contact()` function runs during `Butler.__init__()` after DB provisioning. Uses `INSERT ... ON CONFLICT DO NOTHING` keyed on `'owner' = ANY(roles)` to be idempotent across multiple butlers starting concurrently.

**Dashboard UX:** A persistent banner on `/butlers/` (the overview page) displays "Set up your identity at /contacts" until the owner contact has at least one `contact_info` entry of type `telegram` or `email`. The contacts detail page for the owner shows a guided setup flow for adding channel identifiers.

### D4: Reverse-lookup mechanism

**Decision:** A `resolve_contact_by_channel(type, value) → (contact_id, roles, entity_id)` function queries `shared.contact_info JOIN shared.contacts` to map a channel identifier to a contact and their role-set.

**Query:**
```sql
SELECT c.id, c.roles, c.entity_id, c.name, c.first_name, c.last_name
FROM shared.contact_info ci
JOIN shared.contacts c ON c.id = ci.contact_id
WHERE ci.type = $1 AND ci.value = $2
LIMIT 1
```

**Uniqueness:** Add `UNIQUE(type, value)` constraint on `shared.contact_info`. This means each channel identifier maps to exactly one contact. If the same person has two Telegram accounts, those are two `contact_info` rows with different values, both pointing to the same `contact_id`. A shared phone (e.g., family device) must be assigned to one contact — the owner can reassign via dashboard.

**Performance:** The existing `idx_shared_contact_info_type_value` index already covers this query. The UNIQUE constraint replaces the index.

### D5: Switchboard identity injection

**Decision:** On every inbound message, the Switchboard calls `resolve_contact_by_channel()` before routing. The resolved identity is injected as a structured preamble in the routed prompt.

**Flow:**
1. Message arrives (e.g., Telegram message from chat ID 12345)
2. Switchboard calls `resolve_contact_by_channel('telegram', '12345')`
3. **Known contact with `owner` role:** Route normally. Preamble: `[Source: Owner, via telegram]`
4. **Known contact without `owner` role:** Preamble: `[Source: Chloe (contact_id: abc-123, entity_id: def-456), via telegram]`. Downstream butlers use `entity_id` as the fact subject.
5. **Unknown sender (no match):** Create temporary contact (see D6). Preamble: `[Source: Unknown sender (temp_contact_id: ghi-789), via telegram — pending disambiguation]`. Message is still routed but facts are held against the temporary entity until disambiguated.

**Why entity_id in the preamble:** Downstream butlers (relationship, health) need a stable subject identifier for storing facts. Passing `entity_id` in the preamble lets them do `INSERT INTO facts (entity_id, predicate, object) VALUES ($entity_id, ...)` without their own contact resolution.

### D6: Unknown sender → temporary contact

**Decision:** When reverse-lookup returns no match, create a temporary contact with `metadata = {"needs_disambiguation": true, "source_channel": "telegram", "source_value": "12345"}` and a corresponding `contact_info` entry.

**Lifecycle:**
1. **Creation:** Switchboard creates temp contact on first unknown-sender message. Name is set from available channel metadata (e.g., Telegram display name) or `"Unknown ({channel} {value})"`.
2. **Notification:** Owner is notified via their preferred channel: "Received a message from {display_name} (Telegram). Who is this? Reply with a name or resolve at /contacts."
3. **Dashboard resolution:** At `/butlers/contacts`, a "Pending Identities" section lists all contacts with `metadata.needs_disambiguation = true`. For each, the owner can:
   - **Merge** into an existing contact (calls `entity_merge` for the linked entities, moves `contact_info` entries, deletes temp contact)
   - **Confirm as new** (removes `needs_disambiguation` flag, owner optionally fills in name/details)
   - **Ignore/block** (archives the temp contact with `listed = false`)
4. **Pre-disambiguation behavior:** Facts ingested from unresolved senders are stored against the temp contact's `entity_id`. After merge, `entity_merge` re-points all facts to the target entity.

### D7: Migrate owner credentials from secrets to contact_info

**Decision:** Owner channel identifiers currently stored as `butler_secrets` entries move to `shared.contact_info` rows linked to the owner contact, with a new `secured BOOLEAN DEFAULT false` column.

**Credential mapping:**

| Current secret key | New contact_info type | Secured |
|---|---|---|
| `BUTLER_TELEGRAM_CHAT_ID` → `TELEGRAM_CHAT_ID` | `telegram` | false (it's an identifier, not a secret) |
| `USER_EMAIL_ADDRESS` | `email` | false |
| `USER_EMAIL_PASSWORD` | `email_password` | true |
| `GOOGLE_REFRESH_TOKEN` | `google_oauth_refresh` | true |
| `TELEGRAM_API_HASH` | `telegram_api_hash` | true |
| `TELEGRAM_API_ID` | `telegram_api_id` | true |
| `TELEGRAM_USER_SESSION` | `telegram_user_session` | true |
| `USER_TELEGRAM_TOKEN` | `telegram_bot_token` | true |

**`secured` column:** Added to `shared.contact_info`. When `secured = true`:
- Dashboard API returns the value as `"••••••••"` by default
- A separate `GET /api/contacts/{id}/secrets/{info_id}` endpoint returns the actual value (click-to-reveal)
- MCP tools can read secured values (they need them for credential resolution)

**Credential resolution refactor:** `credential_store.resolve("TELEGRAM_CHAT_ID")` is updated to first check the owner contact's `contact_info` for a matching type, falling back to `butler_secrets` for non-identity credentials (API keys, webhook URLs, etc.). This is a phased migration — both paths work during transition.

### D8: `notify()` gains contact-based resolution

**Decision:** Add an optional `contact_id: UUID` parameter to `notify()`. When provided, resolve the recipient's channel identifier from `shared.contact_info`.

**Resolution priority:**
1. If `contact_id` is provided → query `shared.contact_info WHERE contact_id = $1 AND type = $channel AND is_primary = true` (fallback to any row of matching type if no primary)
2. If `recipient` string is provided → use as-is (backwards compatible)
3. If neither → resolve owner contact's channel identifier (replaces `BUTLER_TELEGRAM_CHAT_ID` lookup)

**Missing identifier fallback:** If `contact_id` is provided but no `contact_info` entry exists for the requested channel, the notification is parked as a `pending_action` with `agent_summary = "Cannot deliver {channel} notification to {contact.name} — no {channel} identifier on file. Add it at /contacts/{contact_id}."` The owner is notified.

### D9: Role-based approval gating

**Decision:** Replace the tool-name-based safety-net (`_is_user_send_or_reply_tool()`) with role-based target resolution in the approval gate.

**New gate logic (for outbound tools):**
1. Extract target identity from `tool_args` (e.g., `contact_id` or `recipient`)
2. If `contact_id` is present → resolve contact → check `roles`
3. If `recipient` string is present → reverse-lookup → check `roles`
4. If target has `'owner'` in roles → auto-approve (no standing rule needed)
5. If target is non-owner → require approval (check standing rules, else pend)
6. If target cannot be resolved → require approval (conservative default)

**Which tools are gated:** The existing `approval_config.gated_tools` mechanism is retained. The change is in how the gate *decides* — from name-prefix matching to role-based target resolution. The config still declares which tools go through the gate; the gate now has richer context for its decision.

**Backwards compatibility:** The `_with_default_gated_user_outputs()` function and all `user_*/bot_*` descriptor infrastructure is removed. Tools that should be gated are explicitly listed in `approval_config` (or `butler.toml`'s `[approvals]` section).

### D10: I/O model teardown sequencing

**Decision:** Remove the entire `user_*/bot_*` I/O model in a single coordinated change, not incrementally.

**Rationale:** The I/O model is deeply interconnected — tool naming validation, descriptor methods, approval defaults, compliance scanners, and channel egress filtering all reference each other. Partial removal would leave broken invariants. A clean sweep is safer.

**Sequencing within this change:**
1. Remove I/O model infrastructure (daemon validators, Module ABC methods, ToolIODescriptor)
2. Rename tools in telegram.py, email.py (drop `user_`/`bot_` prefixes)
3. Delete test files (4 files, ~1,200 lines)
4. Update approval gate to role-based (D9)
5. Update all docs, AGENTS.md, OpenSpec specs, README
6. Add contacts-identity infrastructure (D1–D8)

Steps 1–5 and step 6 can be developed in parallel branches if needed, but must be merged atomically.

### D11: `entity_id` as first-class cross-butler subject

**Decision:** All butler subsystems that store per-person facts must use `entity_id` (from the memory module's `entities` table) as the subject identifier, not name strings.

**Current state:** The relationship butler already does this — `contacts.entity_id` links to `entities`, and `facts.entity_id` is the subject FK. The health butler does NOT — it needs migration to associate health facts with `entity_id`.

**Cross-butler access:** `entities` lives in the memory schema. Butlers that need to reference entities either (a) hold a second pool to the memory schema (as the relationship butler does via `memory_pool`), or (b) entities could be moved to `shared` in a future change. For now, the existing `memory_pool` pattern is sufficient.

**Switchboard's role:** By including `entity_id` in the routed prompt preamble (D5), downstream butlers can store facts without performing their own contact resolution. The Switchboard does the lookup once; butlers consume the result.

## Risks / Trade-offs

**[Risk] Schema migration on live data** → Mitigation: `ALTER TABLE ... SET SCHEMA` is a metadata-only operation in PostgreSQL (no data copy). Combined with `search_path` resolution, existing unqualified queries continue to work. Test with a staging dump before production migration. Rollback: `ALTER TABLE shared.contacts SET SCHEMA relationship`.

**[Risk] FK cascade across schemas** → Mitigation: After moving `contacts` to `shared`, relationship-schema tables still FK to it. PostgreSQL supports cross-schema FK constraints — the migration explicitly re-creates them with schema-qualified references. The `shared.contact_info` FK gap is also closed.

**[Risk] Owner bootstrap race** → Mitigation: `INSERT ... ON CONFLICT` on a partial unique index `WHERE 'owner' = ANY(roles)` ensures exactly one owner contact regardless of concurrent butler startups. Alternatively, use an advisory lock.

**[Risk] Credential exposure via contact_info API** → Mitigation: `secured = true` entries are masked in API responses. The reveal endpoint requires dashboard auth. MCP tools (which run server-side) can read secured values directly from the DB without the API.

**[Risk] Temporary contacts accumulate** → Mitigation: A scheduled job (on the Switchboard's existing cron) archives temp contacts older than 30 days that haven't been disambiguated. Archived contacts have `listed = false` and their facts remain linked (recoverable).

**[Risk] I/O model removal breaks existing tool references** → Mitigation: Tool renames (`user_telegram_send_message` → `telegram_send_message`) are tracked in a rename map. The daemon logs a warning if a tool call uses a legacy name (for one release cycle). Standing approval rules referencing old tool names are migrated in the same Alembic migration.

**[Trade-off] Contacts in shared = weaker isolation** → Accepted. Contacts are inherently cross-cutting (identity is system-wide, not per-butler). The alternative (each butler maintaining its own contact cache) would create consistency problems. Write access is granted to all butler roles, but role modification is restricted to the dashboard API.

**[Trade-off] Single owner only** → Accepted for v1. Multi-owner support (e.g., family members who can each approve actions) is a future concern. The `roles TEXT[]` column supports it structurally but the approval logic assumes a single owner for now.

## Migration Plan

### Database migration (single Alembic revision in `core/`)

1. `ALTER TABLE relationship.contacts SET SCHEMA shared`
2. Add `roles TEXT[] NOT NULL DEFAULT '{}'` to `shared.contacts`
3. Add `secured BOOLEAN NOT NULL DEFAULT false` to `shared.contact_info`
4. Add `UNIQUE(type, value)` constraint on `shared.contact_info` (replace existing non-unique index)
5. Add FK: `shared.contact_info(contact_id) REFERENCES shared.contacts(id) ON DELETE CASCADE`
6. Re-create cross-schema FKs from relationship tables → `shared.contacts(id)` (17+ tables)
7. Grant `SELECT, INSERT, UPDATE, DELETE` on `shared.contacts` to all butler roles
8. Migrate owner credentials from `butler_secrets` to `shared.contact_info` (INSERT ... SELECT with type mapping)
9. Create seed owner contact if none exists (with `roles = ['owner']`)

### Rollback strategy

1. `ALTER TABLE shared.contacts SET SCHEMA relationship` (reverses step 1)
2. Drop added columns (roles, secured)
3. Revert FK changes
4. Restore credential rows to `butler_secrets`

All steps are individually reversible. The migration should include both upgrade and downgrade paths.

### Deployment order

1. Merge database migration — all butlers pick up new schema on restart
2. Merge I/O model teardown + tool renames — all butlers get new tool names
3. Merge Switchboard identity injection — inbound messages now resolve contacts
4. Merge notify() + approval gate changes — outbound path uses contacts
5. Merge frontend changes — dashboard shows owner setup, pending identities, secured fields

Steps 2–4 can be a single PR if the diff is manageable. Step 5 can follow independently.

## Open Questions

1. **`entities` table location:** Should `entities` also move to `shared` in this change? Currently cross-butler access uses a `memory_pool` pattern. Moving to `shared` would simplify joins but expands the shared-schema surface. Recommendation: defer to a follow-up change; the `memory_pool` pattern works for now and `entity_id` in the prompt preamble reduces the need for direct entity queries by other butlers.

2. **Standing approval rule migration:** Existing standing rules reference `user_telegram_send_message` etc. Should the Alembic migration auto-rename them, or should they be invalidated (forcing re-creation)? Recommendation: auto-rename via UPDATE with a rename map in the migration.

3. **Health butler entity_id migration:** The health butler currently doesn't use `entity_id` for health facts. Should this be part of this change or a follow-up? Recommendation: include as a task but don't block the core identity work on it.
