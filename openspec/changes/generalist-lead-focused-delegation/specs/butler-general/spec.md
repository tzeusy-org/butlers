## MODIFIED Requirements

### Requirement: General Butler Identity and Runtime
The general butler SHALL handle ad-hoc user requests without specialist schema assumptions. It SHALL NOT spawn reasoning sessions or hold a runtime adapter or session pool; ad-hoc requests are goals whose invocations use General capabilities, and the model for each invocation is resolved per invocation by role and tier.

Source: RFC 0038 D2 and D16
Scope: v1-mandatory

#### Scenario: Identity and port
- **WHEN** the general butler is running
- **THEN** it operates on port 41101 with description "Flexible catch-all assistant for freeform data"
- **AND** it runs no runtime adapter and holds no concurrent-session limit, because it spawns no reasoning session
- **AND** its database schema is `general` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the general butler starts
- **THEN** it loads modules: `general` (the custom module that registers the collection and item management tools), `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), `memory`, and `steam` (lifestyle gaming-activity capture available to the catch-all butler)

### Requirement: General Butler Tool Surface
The general butler SHALL provide collection and item management tools for organizing freeform data, registered as General capabilities with the control plane.

ID: REQ-butler-general-001
Source: RFC 0038 D2 and D7; RFC 0037 ordinary capture; compose-general-capture-with-private-custody fold-in
Scope: v1-mandatory

#### Scenario: Tool inventory
- **WHEN** the general butler registers its capabilities with the control plane
- **THEN** it registers: `collection_create`, `collection_list`, `collection_delete`, `item_create`, `item_get`, `item_update`, `item_delete`, `item_search`, `collection_export`, and calendar tools

#### Scenario: Adopted ordinary capture availability remains configured and private-safe
- **WHEN** the General capture target is implemented and its existing configured group/module authority admits the capability
- **THEN** ordinary capture, explicit ordinary collection declaration and bounded capture search SHALL be available through their documented owner/source boundary
- **AND** existing ordinary collection/item creation semantics remain compatible, private-only names cannot resolve private identities, and no record-bearing custody tool is introduced
- **AND** disabled groups, missing source authority or unavailable privacy fencing SHALL NOT be bypassed to expose these tools or their data

### Requirement: General Butler Schedules
The general butler SHALL run memory maintenance, briefing aggregation, and a daily end-of-day preparation obligation that incorporates cross-butler data. When `eod-tomorrow-prep` is due, the general daemon SHALL submit an obligation admission whose grant is no larger than the capability set declared for that obligation in git, and the obligation's governing brief SHALL replace the prompt previously sent to a General session. Memory episode cleanup and briefing aggregation SHALL remain deterministic jobs. The `memory_consolidation` job SHALL only submit consolidation obligation admissions with their declared capability set (`REQ-module-memory-005`), because consolidation reasons with a model (RFC 0038 D2 and D18).

Source: RFC 0038 D2, D5 and D18
Scope: v1-mandatory

#### Scenario: Scheduled task inventory
- **WHEN** the general butler daemon is running
- **THEN** it executes: `memory_consolidation` (0 */6 * * *, job that submits consolidation obligation admissions), `memory_episode_cleanup` (0 4 * * *, job), `collect_briefing_contributions` (58 6 * * *, job: 14:58 SGT; aggregates specialist briefing contributions into combined payload), and `eod-tomorrow-prep` (0 15 * * *, obligation: 23:00 SGT end-of-day briefing reviewing tomorrow's calendar AND incorporating cross-butler specialist summaries, sent via Telegram)

#### Scenario: EOD briefing reads combined contributions
- **WHEN** the `eod-tomorrow-prep` obligation goal runs
- **THEN** it reads `state_get('briefing/combined/<today-SGT>')` to obtain the aggregated specialist data
- **AND** incorporates sections with `has_updates=true` into the briefing message
- **AND** omits sections with `has_updates=false` entirely

#### Scenario: EOD briefing without specialist data
- **WHEN** the `eod-tomorrow-prep` obligation goal runs and `briefing/combined/<today>` is absent or empty
- **THEN** the briefing degrades gracefully to calendar-only format (current behavior preserved)

#### Scenario: EOD occurrence is admitted as an obligation goal
- **WHEN** the 15:00 UTC `eod-tomorrow-prep` occurrence fires
- **THEN** the general daemon submits an obligation admission instead of spawning a session, and the admitted goal holds only the obligation's declared capability set
