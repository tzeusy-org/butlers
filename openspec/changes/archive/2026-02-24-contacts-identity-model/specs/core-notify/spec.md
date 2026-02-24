## MODIFIED Requirements

### Requirement: Notify tool registration
Every butler daemon SHALL register a `notify(channel, message, contact_id?, recipient?, subject?, intent?, emoji?, request_context?)` MCP tool during startup. Runtime instances MUST be able to call this tool to send outbound notifications. The tool MUST be available in every butler's MCP tool surface regardless of which modules are enabled.

#### Scenario: Tool available to runtime instance
- **WHEN** a runtime instance spawned by a butler lists available MCP tools
- **THEN** the `notify` tool MUST appear in the tool list with parameters `channel` (required string), `message` (required string), `contact_id` (optional UUID string), `recipient` (optional string), `subject` (optional string), `intent` (optional string), `emoji` (optional string), and `request_context` (optional object)

#### Scenario: Tool registered at startup
- **WHEN** a butler daemon starts up
- **THEN** the `notify` tool MUST be registered as a core MCP tool before the butler accepts any triggers

---

### Requirement: Default recipient
The `notify` tool accepts `contact_id` (UUID) and `recipient` (string) as optional parameters for specifying the target. Resolution priority SHALL be: (1) if `contact_id` is provided, resolve the target's channel identifier from `shared.contact_info`; (2) if `recipient` string is provided, use it as-is; (3) if neither is provided, resolve the owner contact's channel identifier from `shared.contact_info`.

#### Scenario: Contact-based recipient resolution
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Your dental appointment is tomorrow', contact_id='abc-123')`
- **AND** contact `abc-123` has a `contact_info` entry with `type='telegram'`, `value='12345'`, `is_primary=true`
- **THEN** the butler daemon MUST resolve the Telegram chat ID to `12345` and deliver to that recipient

#### Scenario: Contact-based resolution with multiple entries
- **WHEN** a contact has two `contact_info` entries of `type='email'`: one with `is_primary=true` and one with `is_primary=false`
- **AND** `notify(channel='email', message='...', contact_id=...)` is called
- **THEN** the daemon MUST use the `is_primary=true` entry's value
- **AND** if no primary entry exists, the daemon MUST use the first entry of matching type

#### Scenario: Omitted contact_id and recipient defaults to system owner
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Alert')` without `contact_id` or `recipient`
- **THEN** the butler daemon MUST resolve the owner contact (the contact with `'owner' = ANY(roles)`)
- **AND** MUST look up the owner's `contact_info` entry of the matching channel type
- **AND** MUST deliver to that resolved identifier

#### Scenario: Explicit recipient string provided
- **WHEN** a runtime instance calls `notify(channel='email', message='Report', recipient='user@example.com')`
- **THEN** the butler daemon MUST forward the call to the Switchboard with `recipient='user@example.com'`
- **AND** `contact_id`-based resolution MUST NOT be attempted

---

### Requirement: Missing channel identifier fallback
When `contact_id` is provided but the contact has no `contact_info` entry for the requested channel, the `notify` tool MUST NOT silently fail. Instead, it MUST park the notification as a `pending_action` in the approval system and notify the owner to provide the missing channel identifier.

#### Scenario: Contact missing Telegram identifier
- **WHEN** a runtime instance calls `notify(channel='telegram', message='Reminder', contact_id='abc-123')`
- **AND** contact `abc-123` has no `contact_info` entry with `type='telegram'`
- **THEN** the notify tool MUST create a `pending_action` with `tool_name='notify'`, `status='pending'`, and `agent_summary` explaining that contact "Chloe" has no Telegram identifier on file
- **AND** the tool MUST return `{"status": "pending_missing_identifier", "action_id": "...", "message": "Cannot deliver telegram notification to Chloe -- no telegram identifier on file. Add it at /contacts/abc-123."}`

#### Scenario: Owner notified of missing identifier
- **WHEN** a notification is parked due to a missing channel identifier
- **THEN** the owner MUST be notified via their preferred channel with the missing identifier details and a link to the contact's page

---

## ADDED Requirements

### Requirement: Role-based approval gating for notify

The `notify` tool SHALL apply approval gating based on the target contact's roles. Notifications to contacts with `'owner'` in their roles MUST bypass the approval gate. Notifications to contacts without the `'owner'` role MUST be subject to the approval gate (checking standing rules, else pending).

#### Scenario: Notification to owner bypasses approval

- **WHEN** `notify(channel='telegram', message='Alert')` is called with no contact_id (defaults to owner)
- **THEN** the notification MUST be delivered without requiring approval

#### Scenario: Notification to non-owner requires approval

- **WHEN** `notify(channel='telegram', message='Reminder', contact_id='abc-123')` is called
- **AND** contact `abc-123` has `roles = []` (non-owner)
- **THEN** the notification MUST be checked against standing approval rules
- **AND** if no rule matches, it MUST be parked as a pending action

#### Scenario: Standing rule auto-approves non-owner notification

- **WHEN** a standing approval rule exists matching `tool_name='notify'` with constraint `contact_id='abc-123'`
- **AND** `notify(channel='telegram', message='Hi', contact_id='abc-123')` is called
- **THEN** the notification MUST be auto-approved and delivered immediately

#### Scenario: Unresolvable target requires approval

- **WHEN** `notify(channel='telegram', message='Hi', recipient='unknown@example.com')` is called
- **AND** reverse-lookup of `('email', 'unknown@example.com')` returns no contact
- **THEN** the notification MUST require approval (conservative default)
