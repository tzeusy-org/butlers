## Purpose

This capability specifies the future D19 dashboard regions owner experience. It preserves the complete source-bound functional contracts below, remains subject to RFC 0038 and owner-controlled M6 integration, and conveys no current runtime or capability authority.

## ADDED Requirements

### Requirement: Region identity and resident domain workspace

After the signed D19 design and the existing program M6 cutover, regions SHALL replace the Butlers index and butler-detail identity. A region SHALL expose domain records, standing obligations, schedules, capabilities, effect classes, domain guidance and recent related goals. Existing domain-specific workspaces and deep links keep their records, honest reads and accessible interactions; region membership confers no grant. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-regions-001
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Region overview

- **WHEN** the owner visits a region
- **THEN** the page shows its domain purpose, live record facts, obligations due and recently participating goals; no per-region reasoning agent, Sessions tab, trigger input, routing log, MCP call console or delegation panel is presented.

#### Scenario: Domain record drill-down

- **WHEN** the owner opens a calendar entry, episode, learning review, contact or other registered domain record
- **THEN** the established domain record ID, schema, validation, pagination, evidence and deep-link behavior remain; an episode retains the detail archetype and education review facts move to the Education region.

#### Scenario: Regional participation

- **WHEN** a goal used several regions
- **THEN** each region links the same goal ID and shows its relevant capability/effect receipts; regional row counts do not multiply the goal count.

#### Scenario: Resident action boundary

- **WHEN** a domain workspace already offers an owner action
- **THEN** the named existing form remains subject to its actual authenticated D7 control-plane boundary and exact communication approval; this design adds no generic editing or invocation console.


### Requirement: Read-only regional operator projection

After the signed D19 design and the existing program M6 cutover, each region SHALL offer a distinct read-only Operator projection for state, configuration, service failures, health, capability/schema metadata, skills/guidance and prompt provenance. The operator/resident split is retained under the owner’s item 2 B. This projection MUST NOT invoke tools, edit state/configuration/prompts, trigger work, manage grants or expose custody content. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-regions-002
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Inspect region operator facts

- **WHEN** the owner switches from region workspace to Operator
- **THEN** a URL-addressable read-only view exposes state/config and service facts with freshness and unavailable states; it contains no call console, Run, edit, enable, delete or grant controls.

#### Scenario: System infrastructure door

- **WHEN** a regional service is unhealthy
- **THEN** the region provides a read-only explanation and links to the existing System/Settings infrastructure surface; it never represents service health as a separate assistant’s state.

#### Scenario: Guidance and prompt provenance

- **WHEN** the owner inspects domain guidance
- **THEN** the projection shows known source digests, changed roster-relative names and matches_git/drifted/unknown truth, without expanding prompt authoring authority or disclosing effective prompt content routinely.

#### Scenario: Operator deep link and unavailable facts

- **WHEN** the owner bookmarks a fact or its read source fails
- **THEN** the link lands on the same read-only subsection with visible focus; unavailable stays named, and no absent fact becomes an all-clear.


### Requirement: Standing obligations and schedule truth

After the signed D19 design and the existing program M6 cutover, regions and Overview SHALL project declared subscriptions, commitments and schedules as standing obligations, including eligibility, explicit enabled state, finite next due instant and last occurrence goal. Existing schedule and cadence facts MUST remain truthful. Existing owner obligation edits belong to a named authenticated administer form outside the read-only Operator view. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-regions-003
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
Scope: post-v1

#### Scenario: Due or overdue obligation

- **WHEN** an enabled obligation has a parseable next occurrence
- **THEN** the UI distinguishes due, overdue and future from finite stored timestamps; disabled, missing or invalid timestamps never contribute to the due count.

#### Scenario: Changed enabled state

- **WHEN** the owner uses an existing authorized obligation form
- **THEN** it sends the explicit desired enabled value, waits for a server response and validates rollback/refusal; no optimistic toggle or generic MCP shortcut is added.

#### Scenario: Schedule cadence forecast

- **WHEN** a spend forecast covers a recurring obligation
- **THEN** the cron’s own repeat-cycle monthly basis remains stated and deterministic, independent of the observation moment; an unestablished cadence yields no number.

#### Scenario: Obligation origin ceiling

- **WHEN** a runtime-created obligation causes a goal
- **THEN** the dashboard shows its recorded origin, creator lineage, declared set and exposure; it never widens the grant or implies its standing occurrence approves non-owner communication.


### Requirement: System settings and capability authority

After the signed D19 design and the existing program M6 cutover, service health, backups, endpoint visibility, runtime/model catalog management and existing system configuration SHALL remain infrastructure in System/Settings. Region capability metadata SHALL be descriptive; the legacy permissions matrix and per-butler model override/trigger console SHALL NOT grant invocation authority after cutover. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change. The proposed regional Pause disposition SHALL be explicit: today’s per-butler quarantine Pause remains unchanged on the frozen path before cutover; the future Region has no regional Hold/Pause/Resume mutation. Global Hold is the Overview-owned operation and per-goal Stop remains the goal control. This proposed retirement is part of final D19 owner signoff and exact current-owner integration, not a new regional suspension policy.

ID: REQ-dashboard-regions-004
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md; bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md
Scope: post-v1

#### Scenario: Capability inventory

- **WHEN** the owner reads region capability metadata
- **THEN** the projection shows effect class, trust and schema/description digests; effective grant and denial facts come from the goal broker, not ToolMeta, core_groups, a visibility flag or a legacy permissions cell.

#### Scenario: Catalog verify and benchmark

- **WHEN** the owner invokes an already authorized model catalog verify or scheduled sweep runs
- **THEN** the existing finite catalog-verification/benchmark D18 model-function category, rate limit, bad-tick tolerance, health receipts and failure tail remain; it is not a new regional agent or a bypass to a read-capability goal.

#### Scenario: Monthly ceiling

- **WHEN** dispatch is denied by a stored spend ceiling
- **THEN** System/Spend shows the exact current halt and recent denial receipts with degraded-source honesty and once-per-breach owner notification; the dashboard cannot reset goal budgets or call that a stopped goal.

#### Scenario: Legacy permissions and controls

- **WHEN** the owner opens a legacy grant or unit-trigger URL after cutover
- **THEN** the page names the retired control and points to read-only capability/grant facts or a named existing Settings function; it never silently executes, redirects a pending mutation or recreates a caller-asserted authority boundary.

#### Scenario: Regional Pause has no silent global successor

- **WHEN** an owner opens a regional Pause entry or its historical receipt at cutover
- **THEN** the retired unit control is explained with its actual historical identity; a named Overview door describes global Hold and a goal door describes D14 Stop, without redirecting or executing the old pending mutation.
- **AND** the Region and read-only Operator expose no regional hold, resume or implicit Stop-all, and the current quarantine behavior remains authoritative until the approved cutover.


### Requirement: Situations view over fleet cases

After the signed D19 design and the existing program M6 cutover, the owner surface SHALL show a read-only Situations list and situation detail that project RFC 0032 `public.fleet_cases` through the bounded Switchboard case read surface (REQ-fleet-case-file-007). A region page SHALL show the situations its butler contributed evidence to, using the server-side contributor filter of REQ-fleet-case-file-015, and the Overview SHALL link to the fleet-wide list. Each situation SHALL show its readable correlation key, canonical state (`open`, `watching`, `closing`, `closed`), canonical posture (`silent`, `routine`, `active`, `urgent`), terminal outcome when closed, attributed evidence history and links to the goals and receipts it references. No page action SHALL create, reopen, close, re-posture or merge a case, or trigger a producer. A title or kind taxonomy beyond the correlation key SHALL NOT be added without a separate owner decision. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-regions-005
Source: RFC 0038 D19 and owner decision 11; RFC 0032 (fleet case file); REQ-fleet-case-file-002, REQ-fleet-case-file-007 and REQ-fleet-case-file-015; docs/api_and_protocols/response-conventions.md; bu-h40h2b.8
Scope: post-v1

#### Scenario: Region situations

- **WHEN** the owner opens a region
- **THEN** its Situations section requests the case list with that region's butler as the contributor filter and lists the returned non-closed cases, newest update first, in bounded cursor pages, each with state, posture and correlation key, and a door to the fleet-wide list including closed cases.

#### Scenario: Situation detail

- **WHEN** the owner opens one situation
- **THEN** the detail shows its state, posture, outcome when closed, oldest-first attributed evidence and links within the read surface's caps, and states when history was truncated at a cap.

#### Scenario: Empty, unavailable and partial are distinct

- **WHEN** there are no cases, the case source fails, or only part of the evidence or links could be read
- **THEN** the view says "no situations", "situations unavailable" with the failure class, or shows the readable part marked partial; an unavailable source is never shown as empty.

#### Scenario: No case mutation from the page

- **WHEN** the owner views any situation
- **THEN** the page offers no create, reopen, close, posture, merge or producer-trigger control; changes arrive only from the governed Switchboard writers and appear on refresh.

#### Scenario: Canonical values only

- **WHEN** a case carries a state or posture outside the canonical sets
- **THEN** the view shows it as an unrecognized value rather than mapping it to a nearby canonical value.


### Requirement: Region standing orders listing

After the signed D19 design and the existing program M6 cutover, each region SHALL list its standing orders, and the Overview SHALL link to a fleet-wide list, as a cursor-paginated read projection of the schedules behind REQ-dashboard-regions-003, filterable by region, source and enabled state. Each row SHALL show name, cadence, enabled state, next due instant, last run outcome and source. Schedules declared in roster TOML SHALL be labelled Git-managed and SHALL offer no edit, toggle or delete. Database-held schedules SHALL be content-blind in the listing: no prompt text, arguments, credentials or raw error text. Owner changes to database-held schedules SHALL go only through the named authenticated administer form of REQ-dashboard-regions-003. A "run now" verb is outside this requirement. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-regions-006
Source: RFC 0038 D19 and owner decision 11; REQ-dashboard-regions-003; docs/api_and_protocols/response-conventions.md; bu-h40h2b.11
Scope: post-v1

#### Scenario: Region standing orders

- **WHEN** the owner opens a region's standing orders
- **THEN** each schedule shows name, cadence, enabled state, next due instant, last run outcome and source, and links to the goal of its last occurrence when one exists.

#### Scenario: Git-managed schedule

- **WHEN** a schedule comes from roster TOML
- **THEN** it is labelled Git-managed, names its roster file, and offers no edit, toggle or delete.

#### Scenario: Content-blind database schedule

- **WHEN** a schedule is held in the database
- **THEN** the listing omits prompt text, arguments, credentials and raw error text, and a failed last run shows only its failure class.

#### Scenario: Edits use the existing form

- **WHEN** the owner changes a database-held schedule
- **THEN** the change goes through the authenticated administer form of REQ-dashboard-regions-003 with its receipt; the listing adds no inline edit path.

#### Scenario: No run now

- **WHEN** the owner looks for an immediate run of a standing order
- **THEN** the listing offers none; an immediate run is governed by its own requirement when adopted.

#### Scenario: Pagination and unavailable source

- **WHEN** the listing spans more than one page or a region's schedule source fails
- **THEN** the next page uses an opaque cursor, and the failed region is shown as unavailable rather than as having no schedules.


### Requirement: Page verbs from one registry

After the signed D19 design and the existing program M6 cutover, every verb shown on a region, situation, standing-order or goal detail page SHALL come from one capability registry entry that names its destination, its eligibility rule and the existing authorized owner form or route it opens. The same registry SHALL feed the page's verb bar, the command palette, keyboard help, breadcrumbs and the NotFound page. A verb SHALL NOT exist without an existing authorized route, and opening a verb SHALL NOT itself perform an effect; effects stay behind the owning form's single commit action and server receipt. Region pages SHALL register no per-region agent, Sessions tab or trigger verb. Before that cutover, this requirement SHALL remain dark and SHALL authorize no implementation or activation in this change.

ID: REQ-dashboard-regions-007
Source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D13; REQ-dashboard-regions-001; REQ-dashboard-mind-overview-002; bu-h40h2b.11; bu-h40h2b.15
Scope: post-v1

#### Scenario: Same verbs on page and palette

- **WHEN** the owner opens a detail page and then the command palette
- **THEN** both list the same eligible verbs for that object with the same names and eligibility.

#### Scenario: Ineligible verb

- **WHEN** a registered verb is not eligible for the object or owner state
- **THEN** it is shown disabled with the reason, or omitted where the registry marks it hidden, and the palette matches the page.

#### Scenario: Verb opens the owning form

- **WHEN** the owner activates a verb
- **THEN** it navigates to or opens the owning authorized form; no effect happens until that form's commit and receipt.

#### Scenario: Unregistered destination

- **WHEN** the owner opens a URL that matches no registered destination
- **THEN** NotFound names the nearest registered destinations from the registry and offers no unregistered verb.

#### Scenario: No regional agent verbs

- **WHEN** the owner opens a region page
- **THEN** no verb starts, prompts or resumes a per-region agent or session.


## Source References

- Non-Negotiable Rules 1, 3, 4 and 6 in about/heart-and-soul/vision.md.
- RFC 0038 D19 and owner decision 11; further exact decisions and current source boundaries remain the per-requirement Source fields.
- Requirement-level source: RFC 0038 D19 and owner decision 11; RFC 0038 D7, D11, D13; design.md
- Amendment source: bu-idcqzy design and D19 owner note (2026-10-15); RFC 0038 D7 and D14; design.md
- Gap amendment source (2026-10-22): bu-85bqeq.1; RFC 0038 D19; RFC 0032 and openspec/specs/fleet-case-file for situations, plus the contributor-filter delta REQ-fleet-case-file-015 in this change; owner adoption recorded 2026-10-27.
