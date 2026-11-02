# General Butler Role

## Purpose
The General butler (port 41101) is the flexible catch-all assistant for freeform data that does not belong to any specialist domain.

## Requirements

### Requirement: General Butler Identity and Runtime
The general butler SHALL handle ad-hoc user requests without specialist schema assumptions.

#### Scenario: Identity and port
- **WHEN** the general butler is running
- **THEN** it operates on port 41101 with description "Flexible catch-all assistant for freeform data"
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `general` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the general butler starts
- **THEN** it loads modules: `general` (the custom module that registers the collection and item management tools), `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), and `memory`

### Requirement: General Butler Tool Surface
The general butler SHALL provide collection and item management tools for organizing freeform data.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the general butler
- **THEN** it has access to: `collection_create`, `collection_list`, `collection_delete`, `item_create`, `item_get`, `item_update`, `item_delete`, `item_search`, `collection_export`, and calendar tools

### Requirement: General Butler Schedules
The general butler SHALL run memory maintenance, briefing aggregation, and a daily end-of-day preparation prompt that incorporates cross-butler data.

#### Scenario: Scheduled task inventory
- **WHEN** the general butler daemon is running
- **THEN** it executes: `memory_consolidation` (0 */6 * * *, job), `memory_episode_cleanup` (0 4 * * *, job), `collect_briefing_contributions` (58 6 * * *, job: 14:58 SGT; aggregates specialist briefing contributions into combined payload), and `eod-tomorrow-prep` (0 15 * * *, prompt-based: 23:00 SGT end-of-day briefing reviewing tomorrow's calendar AND incorporating cross-butler specialist summaries, sent via Telegram)

#### Scenario: EOD briefing reads combined contributions
- **WHEN** the `eod-tomorrow-prep` prompt executes
- **THEN** it reads `state_get('briefing/combined/<today-SGT>')` to obtain the aggregated specialist data
- **AND** incorporates sections with `has_updates=true` into the briefing message
- **AND** omits sections with `has_updates=false` entirely

#### Scenario: EOD briefing without specialist data
- **WHEN** the `eod-tomorrow-prep` prompt executes and `briefing/combined/<today>` is absent or empty
- **THEN** the briefing degrades gracefully to calendar-only format (current behavior preserved)

### Requirement: General Butler Skills
The general butler SHALL have a data organization skill plus shared skills.

#### Scenario: Skill inventory
- **WHEN** the general butler operates
- **THEN** it has access to `data-organizer` (collection and entity organization patterns, JSONB query patterns, data hygiene) plus shared skills `butler-memory` and `butler-notifications`

### Requirement: General Memory Taxonomy
The general butler SHALL use a flexible memory taxonomy for broad knowledge capture.

#### Scenario: Memory classification
- **WHEN** the general butler extracts facts
- **THEN** it uses flexible subjects like topic names or "user"; predicates like `goal`, `preference`, `resource`, `idea`, `note`, `deadline`, `status`; permanence `standard` for most general knowledge, `volatile` for temporary notes, `stable` for long-term preferences

### Requirement: EOD Briefing Multi-Domain Format
The EOD briefing message SHALL follow a structured multi-domain format when specialist contributions are available. The message SHALL be under 500 words for mobile readability.

#### Scenario: Full briefing with specialist sections
- **WHEN** the combined briefing payload has contributions with `has_updates=true`
- **THEN** the message includes a calendar timeline section followed by a "Today's Highlights" section grouping specialist summaries by domain — the domain label set is exactly: Learning (education), Finance, Health, Home, Lifestyle, Relationships, Travel (one label per butler in `SPECIALIST_BUTLERS`)
- **AND** only domains with `has_updates=true` appear in the output

#### Scenario: Cross-domain heads-up flags
- **WHEN** multiple specialist contributions contain high-priority highlights
- **THEN** the briefing MAY include a "Heads-up" section at the end highlighting cross-domain correlations (e.g., travel departure conflicting with a medical appointment)

#### Scenario: Calendar-only fallback
- **WHEN** no specialist contributions have `has_updates=true`
- **THEN** the briefing renders the calendar timeline only, matching the existing format

### Requirement: Owner Projects
General SHALL hold owner projects: owner-authored, time-bounded charters that group goals, obligations and links; a project grants no authority. A project is a Project, Responsibility or Decision with a title, a horizon at most 180 days ahead, and state `draft`, `closed` or `expired`. Its charter (title, horizon, objective of at most 1000 characters, done condition, and typed constraints: an optional descriptive budget `{amount, currency}` and up to 10 `must_keep` entries) is stored as insert-only versions with the authenticated owner actor. Only the owner's verified dashboard session writes projects, through `/api/general/projects`; owner control without a dashboard session (an API key, connector callback or runtime session) may read but never write, no MCP tool reaches the project tables, and no draft-to-charter promotion path exists. Authoring or viewing a project creates no scheduled task, goal, research call, wake or parked effect.

#### Scenario: Create is replay-safe and deduplicated
- **WHEN** the owner creates a project with an `idempotency_key`
- **THEN** charter version 1 is written and the project is a `draft`
- **AND** replaying the same key and request returns the same project (`meta.outcome = "replayed"`), while the same key with a different request returns 409 `IDEMPOTENCY_KEY_REUSED`
- **AND** a create whose normalized title (case and whitespace folded) and horizon match an existing draft returns that draft (`meta.outcome = "deduplicated"`) instead of a twin; nothing from that request is stored, so the Projects tab says so, keeps the form text, and offers to open the existing draft

#### Scenario: Charter edits use compare-and-swap
- **WHEN** the owner edits a draft's charter with `expected_version`
- **THEN** version `expected_version + 1` is inserted and earlier versions are never modified (UPDATE, DELETE and TRUNCATE of versions are refused)
- **AND** a changed edit from a stale version returns 409 `VERSION_CONFLICT`, so two concurrent edits from one version commit exactly one successor
- **AND** an edit identical to the current charter writes nothing and succeeds (`meta.outcome = "unchanged"`)

#### Scenario: Close and expiry are terminal
- **WHEN** the owner closes a draft at its current version, or a draft's horizon passes
- **THEN** the project reads as `closed` or `expired` (expiry takes effect exactly at the horizon on every read, and the next write persists it)
- **AND** a closed or expired project is frozen: charter edits, close and link changes return 409 `PROJECT_NOT_DRAFT`
- **AND** the freeze holds in the database too: inserting a charter version or inserting or deleting a link for a closed, expired or past-horizon project is refused whatever the caller

#### Scenario: Typed links reference records without copying them
- **WHEN** the owner links a record to a draft
- **THEN** the link stores only its kind (`deadline`, `owner_condition`, `trip`, `drive_file` or `calendar_event`) and an opaque record id (a canonical UUID for owner conditions, trips and calendar events, `<butler>:<canonical UUID>` for a deadline, a bare Drive file id for a Drive file), never a URL or record content, at most 50 per project
- **AND** adding an existing link is a no-op, and removing a link or closing the project never changes the referenced record

#### Scenario: The projects page resolves links through canonical readers
- **WHEN** the owner opens a project on the General butler's Projects tab
- **THEN** each link group resolves through that record's own owner-authorized reader (trips through the Travel trip reader, calendar events through the calendar workspace entry reader, owner conditions through the owner condition ledger, deadlines through the owning butler's owner-only deadline reader, Drive files through General's owner-only Drive file reader), never General cross-schema SQL
- **AND** a failing reader marks only its own group unavailable while the project and other groups stay visible, and an unknown or deleted record reads as not found
- **AND** the deadline reader never returns the task prompt, and the Drive file reader returns only the file's name, type and modified time (no link, owners, sharing or content) through General's own Drive module, with any Drive failure answered as a content-blind unavailable
- **AND** record text renders as inert data, and the page has no Arm or Run control
