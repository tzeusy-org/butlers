## Why

The butler system has no unified concept of "who is talking to me" or "who am I talking to." Identity is handled ad-hoc: the Switchboard logs raw `source_id` strings, the `notify()` tool accepts freeform `recipient` strings, and channel modules use a `user_*/bot_*` naming convention to infer identity from tool names. This means (a) the system cannot distinguish owner messages from third-party messages, (b) outbound notifications cannot resolve a contact's channel identifiers, (c) approval gating cannot be role-based, and (d) facts ingested from third parties get misattributed to the owner.

The `user_*/bot_*` I/O model (tool-name-based identity inference) is a workaround for this missing identity layer and should be replaced entirely by contacts-based identity resolution.

## What Changes

- **BREAKING**: Remove the `user_*/bot_*` tool naming convention, `ToolIODescriptor`, and all four `Module.user_inputs/user_outputs/bot_inputs/bot_outputs` methods. Tool names revert to plain `<channel>_<action>` (e.g., `telegram_send_message`). Identity is resolved from contacts, not tool prefixes.
- **BREAKING**: Rename owner-identity secrets from `BUTLER_TELEGRAM_CHAT_ID` → `TELEGRAM_CHAT_ID`, and normalize related keys (`TELEGRAM_API_HASH`, `TELEGRAM_API_ID`, `TELEGRAM_USER_SESSION`, `USER_TELEGRAM_TOKEN`). Owner channel credentials (email address, Telegram chat ID, OAuth tokens) move from the secrets store to the owner's contact record as secured `contact_info` entries (displayed asterisked, click-to-reveal on the dashboard).
- Add a `roles TEXT[]` column to the `contacts` table. On first boot, create a seed owner contact with `roles = ['owner']` and empty channel identifiers; the owner fills in their details at `/butlers/contacts`.
- Add a reverse-lookup capability: given `(type, value)` (e.g., `('telegram', '12345')`), resolve to a contact and their role-set. The Switchboard uses this on every inbound message to determine source identity.
- Switchboard injects explicit identity context into routed prompts: `"[Source: {contact.name} ({roles}), via {channel}]"` so downstream butlers attribute facts to the correct entity, not the owner by default.
- Unknown senders (reverse-lookup miss) create a temporary contact tagged `needs_disambiguation`. Owner is notified and can resolve the identity at `/butlers/contacts` (merging into an existing contact or confirming as new).
- `notify()` gains a `contact_id` parameter alongside the existing `recipient` string. When `contact_id` is provided, the function resolves the target's channel identifier from `contact_info`. When the lookup fails (no channel identifier for the requested channel), the notification is parked as a `pending_action` and the owner is notified to provide the missing identifier.
- Approval gating becomes role-based: notifications to contacts with `'owner'` in their roles bypass approval; notifications to non-owner contacts require approval. The `user_*_send*` name-based safety-net heuristic is removed.
- `entity_id` on contacts becomes a first-class cross-butler identifier. All butler subsystems (relationship facts, health facts, calendar events) reference `entity_id` as the subject, not arbitrary name strings.
- Frontend updates: contacts page gains a pending-identities queue for temporary contacts awaiting disambiguation; approvals page updated to show role-based gating context.

## Capabilities

### New Capabilities
- `contacts-identity`: Unified contacts-as-identity model — owner bootstrap, role-based identity, reverse-lookup from channel identifiers, temporary-identity lifecycle, secured credential storage on contact records
- `switchboard-identity`: Switchboard-level identity resolution on inbound messages — reverse-lookup, prompt injection with resolved identity context, unknown-sender handling and owner notification

### Modified Capabilities
- `core-notify`: `notify()` gains `contact_id` parameter, contact-based channel resolution, and missing-identifier fallback to `pending_action`
- `dashboard-approvals`: Approval UI surfaces role-based gating context (who is the target contact, what are their roles)
- `dashboard-relationship`: Contacts page adds pending-identities queue, secured credential display (asterisked, click-to-reveal), owner identity setup flow

## Impact

**Code — remove (I/O model teardown):**
- `docs/modules/io_model.md` — delete
- `src/butlers/modules/base.py` — remove `ToolIODescriptor`, `user_inputs()`, `user_outputs()`, `bot_inputs()`, `bot_outputs()`
- `src/butlers/daemon.py` — remove `_validate_tool_name()`, `_validate_module_io_descriptors()`, `ModuleToolValidationError`, `_with_default_gated_user_outputs()`, `_is_user_send_or_reply_tool()`, `_CHANNEL_EGRESS_ACTIONS`, channel egress filtering
- `src/butlers/modules/telegram.py`, `email.py` — remove descriptor methods, rename tools from `user_*/bot_*` to plain names
- `src/butlers/modules/pipeline.py` — remove `_default_identity_for_tool()`, `_build_source_metadata()`
- `tests/modules/test_io_tooling_refactor.py`, `tests/daemon/test_approval_defaults.py`, `tests/modules/test_module_tool_naming_validation.py`, `tests/test_tool_name_compliance.py` — delete
- `roster/messenger/butler.toml` — remove `[modules.telegram.user]`/`[modules.telegram.bot]` config split
- `AGENTS.md` — remove all identity-prefix contracts (lines ~574–665)
- Affected docs: `README.md`, `docs/architecture/system-architecture.md`, `docs/roles/messenger_butler.md`, `docs/roles/switchboard_butler.md`, `docs/roles/base_butler.md`, `docs/modules/approval.md`, `docs/modules/calendar.md`
- OpenSpec refs: `v1-mvp-spec/specs/switchboard/spec.md`, `v1-mvp-spec/specs/butler-daemon/spec.md`, `v1-mvp-spec/tasks.md`

**Code — add/modify (contacts identity model):**
- **BREAKING**: Migrate `contacts` table from `relationship` schema to `shared` schema. All butlers need direct access for identity resolution (Switchboard for reverse-lookup, all butlers for `notify()` recipient resolution). Relationship butler's FKs (`relationships`, `interactions`, `notes`, etc.) continue to reference `shared.contacts.id`.
- Alembic migration: move `contacts` to `shared`, add `roles TEXT[]` to `contacts`, add unique constraint on `contact_info(type, value)`, add `secured BOOLEAN DEFAULT false` to `contact_info`
- Owner bootstrap in daemon startup (create seed owner contact if none exists)
- Switchboard: reverse-lookup function, identity injection into routed prompts, unknown-sender temporary-contact creation
- `notify()` in `daemon.py`: add `contact_id` param, contact-based channel resolution
- Approval gate (`gate.py`): replace name-based heuristic with role-based target resolution
- Secret migration: move owner channel identifiers from `shared.secrets` to `contact_info` entries with `secured=true`

**Frontend:**
- `/butlers/contacts` — owner setup banner, pending-identities queue, secured field display
- `/butlers/approvals` — role context on pending actions

**Schema:**
- `contacts` moves from `relationship` schema to `shared` schema — all butlers need direct access for identity resolution (Switchboard for inbound reverse-lookup, all butlers for `notify()` recipient resolution, approval gate for role checks)
- `contact_info` remains in `shared` schema (already there) — joins with `shared.contacts` are now same-schema

**Dependencies:**
- Contacts sync (Google) must be updated to not overwrite `roles` or `secured` fields during sync
- All butler subsystems referencing people by name strings should migrate to `entity_id` references (relationship facts already do; health butler needs updating)
