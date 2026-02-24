## 1. I/O Model Teardown

Remove the entire `user_*/bot_*` convention, validators, descriptors, and tests. No fallbacks, no legacy name support — delete all of it.

- [ ] 1.1 Remove `ToolIODescriptor` dataclass, `user_inputs()`, `user_outputs()`, `bot_inputs()`, `bot_outputs()` from `Module` ABC in `src/butlers/modules/base.py`
- [ ] 1.2 Remove `_validate_tool_name()`, `_validate_module_io_descriptors()`, `ModuleToolValidationError`, `_with_default_gated_user_outputs()`, `_is_user_send_or_reply_tool()`, `_CHANNEL_EGRESS_ACTIONS`, and all channel egress filtering logic from `src/butlers/daemon.py`
- [ ] 1.3 Remove `_default_identity_for_tool()` and `_build_source_metadata()` from `src/butlers/modules/pipeline.py`
- [ ] 1.4 Remove all four descriptor methods from `src/butlers/modules/telegram.py`; rename tools from `user_telegram_*`/`bot_telegram_*` to plain `telegram_send_message`, `telegram_reply_to_message`, etc.
- [ ] 1.5 Remove all four descriptor methods from `src/butlers/modules/email.py`; rename tools from `user_email_*`/`bot_email_*` to plain `email_send_message`, `email_reply_to_thread`, `email_search_inbox`, `email_read_message`, etc.
- [ ] 1.6 Remove `[modules.telegram.user]`/`[modules.telegram.bot]` config split from `roster/messenger/butler.toml`; flatten to single `[modules.telegram]` and `[modules.email]` sections
- [ ] 1.7 Delete test files: `tests/modules/test_io_tooling_refactor.py`, `tests/daemon/test_approval_defaults.py`, `tests/modules/test_module_tool_naming_validation.py`, `tests/test_tool_name_compliance.py`
- [ ] 1.8 Update remaining test files that reference `user_*/bot_*` tool names: `tests/daemon/test_channel_egress_ownership.py`, `tests/test_tool_gating.py`, `tests/modules/test_module_telegram.py`, `tests/modules/test_module_email.py`
- [ ] 1.9 Delete `docs/modules/io_model.md`
- [ ] 1.10 Remove I/O model references from `README.md`, `AGENTS.md` (lines ~574–665), `docs/architecture/system-architecture.md`, `docs/roles/messenger_butler.md`, `docs/roles/switchboard_butler.md`, `docs/roles/base_butler.md`, `docs/modules/approval.md`, `docs/modules/calendar.md`
- [ ] 1.11 Remove I/O model references from OpenSpec specs: `v1-mvp-spec/specs/switchboard/spec.md`, `v1-mvp-spec/specs/butler-daemon/spec.md`, `v1-mvp-spec/tasks.md`
- [ ] 1.12 Update `roster/messenger/CLAUDE.md` to remove `bot_telegram_*`/`bot_email_*` tool references; use new plain names
- [ ] 1.13 Run lint and full test suite; fix any remaining references to old tool names

## 2. Schema Migration — Contacts to Shared

Move `contacts` to `shared` schema and add new columns. Single Alembic migration in `core/`.

- [ ] 2.1 Create Alembic migration: `ALTER TABLE relationship.contacts SET SCHEMA shared`
- [ ] 2.2 In same migration: add `roles TEXT[] NOT NULL DEFAULT '{}'` column to `shared.contacts`
- [ ] 2.3 In same migration: add `secured BOOLEAN NOT NULL DEFAULT false` column to `shared.contact_info`
- [ ] 2.4 In same migration: replace `idx_shared_contact_info_type_value` non-unique index with `UNIQUE(type, value)` constraint on `shared.contact_info`
- [ ] 2.5 In same migration: add FK `shared.contact_info(contact_id) REFERENCES shared.contacts(id) ON DELETE CASCADE`
- [ ] 2.6 In same migration: re-create all 17+ FK constraints from relationship-schema tables (relationships, interactions, notes, important_dates, gifts, loans, groups, contact_labels, quick_facts, addresses, life_events, tasks, activity_feed, reminders, group_members) to reference `shared.contacts(id)`
- [ ] 2.7 In same migration: grant `SELECT, INSERT, UPDATE, DELETE` on `shared.contacts` to all butler roles (`butler_switchboard_rw`, `butler_general_rw`, `butler_health_rw`, `butler_relationship_rw`)
- [ ] 2.8 Write downgrade path for the migration (reverse schema move, drop columns, restore indexes)
- [ ] 2.9 Run migration against a staging DB dump; verify all FK constraints, indexes, and grants are correct

## 3. Owner Bootstrap

Seed owner contact on first startup.

- [ ] 3.1 Implement `_ensure_owner_contact()` in daemon startup (after DB provisioning): `INSERT INTO shared.contacts (name, roles) VALUES ('Owner', '{owner}') ON CONFLICT DO NOTHING` with conflict detection on `'owner' = ANY(roles)` (use a partial unique index or advisory lock for concurrency safety)
- [ ] 3.2 Add partial unique index on `shared.contacts` for owner uniqueness: `CREATE UNIQUE INDEX ix_contacts_owner_singleton ON shared.contacts ((true)) WHERE 'owner' = ANY(roles)` (in the same Alembic migration from section 2)
- [ ] 3.3 Write tests: first startup creates owner, subsequent startups are no-ops, concurrent startups create exactly one owner

## 4. Credential Migration

Move owner channel identifiers from `butler_secrets` to `shared.contact_info`.

- [ ] 4.1 In the Alembic migration (section 2): insert `contact_info` rows for each mapped secret key (BUTLER_TELEGRAM_CHAT_ID → type=telegram, USER_EMAIL_ADDRESS → type=email, USER_EMAIL_PASSWORD → type=email_password with secured=true, GOOGLE_REFRESH_TOKEN → type=google_oauth_refresh with secured=true, TELEGRAM_API_HASH → type=telegram_api_hash with secured=true, TELEGRAM_API_ID → type=telegram_api_id with secured=true, TELEGRAM_USER_SESSION → type=telegram_user_session with secured=true, USER_TELEGRAM_TOKEN → type=telegram_bot_token with secured=true) linked to the owner contact
- [ ] 4.2 Refactor `credential_store.resolve()` to query owner contact's `contact_info` for identity-bound credentials; remove legacy `butler_secrets` fallback for migrated keys
- [ ] 4.3 Rename secret key references throughout codebase: `BUTLER_TELEGRAM_CHAT_ID` → `TELEGRAM_CHAT_ID`; remove all references to the old key names
- [ ] 4.4 Update `_resolve_default_notify_recipient()` in `daemon.py` to query owner contact's `contact_info` instead of `credential_store.resolve("BUTLER_TELEGRAM_CHAT_ID")`
- [ ] 4.5 Write tests for credential resolution from contact_info

## 5. Reverse-Lookup and Identity Resolution

Core identity resolution function used by Switchboard and notify().

- [ ] 5.1 Implement `resolve_contact_by_channel(pool, type, value) -> Optional[ResolvedContact]` as a shared utility in `src/butlers/identity.py` (or similar); queries `shared.contact_info JOIN shared.contacts`; returns `(contact_id, name, roles, entity_id)` or None
- [ ] 5.2 Write tests: known contact resolves with roles, owner resolves with owner role, unknown returns None, unique constraint prevents duplicates

## 6. Switchboard Identity Injection

Switchboard resolves sender identity and injects preamble into routed prompts.

- [ ] 6.1 Add identity resolution call in Switchboard's message ingestion path (before routing): call `resolve_contact_by_channel()` with the message's `source_channel` and `source_id`
- [ ] 6.2 Implement prompt preamble injection: `[Source: Owner, via {channel}]` for owner, `[Source: {name} (contact_id: {id}, entity_id: {eid}), via {channel}]` for known non-owner, `[Source: Unknown sender (contact_id: {tid}, entity_id: {eid}), via {channel} -- pending disambiguation]` for unknown
- [ ] 6.3 Implement temporary contact creation for unknown senders: create contact with `metadata.needs_disambiguation = true`, create `contact_info` entry, create memory entity via `entity_create`, link `entity_id`
- [ ] 6.4 Implement owner notification on unknown sender: notify owner via preferred channel with sender details and link to `/butlers/contacts/{temp_id}`
- [ ] 6.5 Add `contact_id UUID`, `entity_id UUID`, `sender_roles TEXT[]` columns to `routing_log` table (Alembic migration in switchboard schema); populate on every routed message
- [ ] 6.6 Write tests: owner message gets owner preamble, known contact gets identity preamble with entity_id, unknown sender creates temp contact and gets disambiguation preamble, second message from same unknown reuses existing temp contact

## 7. Notify Contact-Based Resolution

Add `contact_id` param and role-based gating to `notify()`.

- [ ] 7.1 Add `contact_id: UUID | None = None` parameter to `notify()` in `daemon.py`
- [ ] 7.2 Implement resolution priority: (1) contact_id → query `shared.contact_info WHERE contact_id = $1 AND type = $channel`, prefer `is_primary=true`; (2) recipient string → use as-is; (3) neither → resolve owner contact's channel identifier
- [ ] 7.3 Implement missing-identifier fallback: if contact_id provided but no matching contact_info entry, create `pending_action` with descriptive `agent_summary`, notify owner, return `{"status": "pending_missing_identifier", ...}`
- [ ] 7.4 Write tests: contact_id resolves to channel identifier, primary preferred over non-primary, missing identifier parks action, neither param defaults to owner

## 8. Role-Based Approval Gating

Replace name-based heuristic with role-based target resolution.

- [ ] 8.1 Refactor `gate_wrapper` in `src/butlers/modules/approvals/gate.py`: extract `contact_id` or `recipient` from `tool_args`, call `resolve_contact_by_channel()` or direct contact lookup, check if target has `'owner'` in roles
- [ ] 8.2 Implement gating logic: owner-targeted → auto-approve (no standing rule needed); non-owner-targeted → check standing rules, else pend; unresolvable target → require approval
- [ ] 8.3 Remove the old `_is_user_send_or_reply_tool()` name-based safety-net from the gate path (already removed from daemon in task 1.2, ensure gate.py has no residual references)
- [ ] 8.4 Update `approval_config.gated_tools` in `butler.toml` files: replace old `user_*` tool names with new plain tool names (e.g., `telegram_send_message`, `email_send_message`, `notify`)
- [ ] 8.5 Migrate existing standing approval rules in Alembic: `UPDATE approval_rules SET tool_name = regexp_replace(tool_name, '^(user|bot)_', '')` to rename old tool references; delete rules that no longer match valid tools
- [ ] 8.6 Write tests: notify to owner bypasses approval, notify to non-owner checks rules then pends, unresolvable target requires approval, standing rule auto-approves non-owner

## 9. Contacts Sync Guard

Ensure Google Contacts sync doesn't overwrite identity-managed fields.

- [ ] 9.1 Update `src/butlers/modules/contacts/sync.py` upsert logic: exclude `roles` from all UPDATE SET clauses on `shared.contacts`
- [ ] 9.2 Update sync upsert logic: exclude `secured` from UPDATE SET clauses on `shared.contact_info`; skip inserting `contact_info` rows where `(type, value)` already exists (ON CONFLICT DO NOTHING or explicit check)
- [ ] 9.3 Write tests: sync does not overwrite owner roles, sync does not flip secured flag, sync handles unique constraint on contact_info gracefully

## 10. MCP Tool Guard for Roles

Prevent runtime instances from modifying roles.

- [ ] 10.1 Update `contact_update` MCP tool in `roster/relationship/tools/contacts.py`: explicitly strip `roles` from writable fields before building UPDATE query
- [ ] 10.2 Write test: `contact_update` with `roles=['owner']` in args does not modify the contact's roles

## 11. Dashboard API Changes

Backend API updates for identity features.

- [ ] 11.1 Update `GET /api/butlers/relationship/contacts/:id` to include `roles` and `entity_id` in response; mask `contact_info` values where `secured = true`
- [ ] 11.2 Implement `GET /api/contacts/{id}/secrets/{info_id}` endpoint: return unmasked value for secured `contact_info` entries; validate info_id belongs to contact_id; return 404 otherwise
- [ ] 11.3 Implement `PATCH /api/contacts/{id}` endpoint: allow updating contact fields including `roles`; this is the sole write path for roles
- [ ] 11.4 Implement `GET /api/contacts/pending` endpoint (or query param on contacts list): return contacts with `metadata.needs_disambiguation = true`
- [ ] 11.5 Implement `POST /api/contacts/{id}/merge` endpoint: merge temp contact into target contact (move contact_info, call entity_merge, delete temp contact)
- [ ] 11.6 Implement `POST /api/contacts/{id}/confirm` endpoint: remove `needs_disambiguation` from metadata
- [ ] 11.7 Implement `GET /api/owner/setup-status` endpoint: return whether owner contact has at least one `telegram` or `email` contact_info entry (for the setup banner)
- [ ] 11.8 Update `GET /api/approvals/actions` response to include `target_contact` object (resolved from `contact_id` in action constraints)
- [ ] 11.9 Write tests for all new/modified API endpoints

## 12. Frontend Changes

Dashboard UI updates.

- [ ] 12.1 Add owner identity setup banner on `/butlers/` overview page: calls setup-status API, displays banner with link to owner contact when no identifiers configured
- [ ] 12.2 Update contact detail page header: show `roles` as colored role badges (e.g., "owner" in distinct color)
- [ ] 12.3 Update contact info section: show `secured` entries as masked values with click-to-reveal buttons; reveal calls `GET /api/contacts/{id}/secrets/{info_id}`
- [ ] 12.4 Add "Pending Identities" section above contacts table on `/butlers/relationship/contacts`: query pending contacts, show name/source channel/source value/created date, action buttons (Merge, Confirm as new, Archive)
- [ ] 12.5 Implement merge dialog: contact search input, select existing contact, confirm calls merge API
- [ ] 12.6 Update approvals actions table: add "Target Contact" column showing contact name and role badges
- [ ] 12.7 Update approvals action detail dialog: show target contact name, roles, link to contact page

## 13. Documentation

Update all docs to reflect the new contacts-identity model.

- [ ] 13.1 Create `docs/modules/contacts.md` documenting the unified contacts-as-identity model: shared schema, roles, reverse-lookup, secured entries, owner bootstrap
- [ ] 13.2 Update `docs/roles/switchboard_butler.md`: add identity disambiguation section covering reverse-lookup, prompt injection, unknown sender handling
- [ ] 13.3 Update `docs/modules/approval.md`: replace I/O model gating description with role-based gating; document owner-bypass and non-owner approval flow
- [ ] 13.4 Update egress documentation (`docs/roles/messenger_butler.md` or equivalent): document how `notify()` resolves channel identifiers from contact_info, how owner credentials are stored on the owner contact record
- [ ] 13.5 Update `AGENTS.md`: remove all I/O model contracts, add contacts-identity contracts (shared schema, reverse-lookup, role-based gating, owner bootstrap)
- [ ] 13.6 Update `CLAUDE.md`: update architecture section to reflect contacts in shared schema and removal of I/O model

## 14. Validation

Final integration verification.

- [ ] 14.1 Run full lint: `uv run ruff check src/ tests/ roster/ conftest.py --output-format concise`
- [ ] 14.2 Run full test suite: `uv run pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py -q --tb=short`
- [ ] 14.3 Verify Alembic migration runs cleanly on a fresh DB and on a DB with existing data
- [ ] 14.4 Verify no remaining references to `user_*/bot_*` tool names, `ToolIODescriptor`, `BUTLER_TELEGRAM_CHAT_ID`, or `io_model.md` in the codebase
