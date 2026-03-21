# Documentation Information Architecture

## Purpose
Defines the target documentation taxonomy, page template standards, diagram standards, navigation model, migration handling, and long-term maintenance contract for the Butlers project. This spec treats documentation as a maintained product surface — an interface between the system and its contributors, operators, and future maintainers.

## ADDED Requirements

### Requirement: Contributor-mental-model information architecture
The documentation SHALL provide a top-level information architecture organized around how a contributor learns the system, not around implementation directory structure. The taxonomy SHALL include at minimum: overview, getting started, concepts, architecture, runtime, butlers, modules, connectors, frontend, data and storage, identity and secrets, API and protocols, operations, testing, roadmap, diagrams, and archive.

#### Scenario: New contributor follows linear reading path
- **WHEN** a new contributor opens `docs/index.md`
- **THEN** they SHALL find a numbered reading path that progresses from "what is this?" through "how do I run it?" to "how does it work?" to "how do I extend it?"
- **AND** each step SHALL link to the next logical page

#### Scenario: Experienced developer performs targeted lookup
- **WHEN** a developer needs to find information on a specific topic (e.g., connector setup, memory module, OAuth flow)
- **THEN** `docs/index.md` SHALL provide a grouped topic index with one-line descriptions and direct links
- **AND** the developer SHALL reach the target page in at most two clicks from the index

#### Scenario: Category boundary is clear
- **WHEN** a contributor is deciding where to place new documentation
- **THEN** each top-level category SHALL have a brief scope statement (what belongs, what does not) accessible from the category index page
- **AND** no single topic SHALL appear as a primary page in more than one category

### Requirement: Documentation entry point
The documentation SHALL provide a single entry point at `docs/index.md` that serves as both a linear reading guide and a targeted lookup table.

#### Scenario: Index exists and is discoverable
- **WHEN** a contributor navigates to the `docs/` directory
- **THEN** `docs/index.md` SHALL exist and be the first file listed
- **AND** README.md SHALL link to `docs/index.md` as the primary documentation entry point

#### Scenario: Index covers all categories
- **WHEN** a new top-level category is added to `docs/`
- **THEN** `docs/index.md` SHALL be updated to include the new category with description and links
- **AND** the category SHALL appear in both the linear reading path and the lookup table

### Requirement: Page template standard
Every documentation page SHALL follow a consistent page template that includes, where applicable: a purpose statement, intended audience, prerequisites, content sections, operational caveats, related pages, and verification guidance.

#### Scenario: Page has purpose statement
- **WHEN** a reader opens any documentation page
- **THEN** the page SHALL begin with a one-sentence purpose statement explaining what the page covers
- **AND** the purpose statement SHALL be formatted as a blockquote header

#### Scenario: Page has audience indicator
- **WHEN** a page targets a specific audience (e.g., operators, module developers)
- **THEN** the page SHALL declare its intended audience in the header block

#### Scenario: Page has prerequisites
- **WHEN** a page assumes prior knowledge from other pages
- **THEN** the page SHALL list prerequisite pages as links in the header block

#### Scenario: Page has verification guidance
- **WHEN** a page describes system behavior, configuration, or architecture that could drift from implementation
- **THEN** the page SHALL include a "Verification" section with concrete steps to confirm the page is still accurate
- **AND** the verification steps SHALL be executable commands or observable checks, not subjective assessments

#### Scenario: Page has related pages
- **WHEN** a page has logical relationships to other documentation pages
- **THEN** the page SHALL include a "Related Pages" section at the bottom with links and brief relationship descriptions

### Requirement: Diagram generation standard
The documentation SHALL include explanatory diagrams generated via `/excalidraw-diagram`, exported as SVG, and generated in dark mode. Diagrams SHALL be used on every page where visual explanation materially improves understanding.

#### Scenario: Diagram uses correct tooling and format
- **WHEN** a new diagram is created for the documentation
- **THEN** it SHALL be generated using `/excalidraw-diagram`
- **AND** it SHALL be exported as SVG format
- **AND** it SHALL be generated in dark mode

#### Scenario: Diagram is scoped and readable
- **WHEN** a diagram is included in a documentation page
- **THEN** the diagram SHALL focus on a single concept, flow, or relationship
- **AND** the diagram SHALL be legible without zooming
- **AND** several small focused diagrams SHALL be preferred over one large comprehensive diagram

#### Scenario: Diagram has caption and context
- **WHEN** a diagram is embedded in a markdown page
- **THEN** the diagram SHALL have a descriptive caption in the markdown
- **AND** the diagram SHALL complement surrounding text rather than replace it

#### Scenario: Diagram source files are organized
- **WHEN** a diagram source file (`.excalidraw`) is created
- **THEN** it SHALL be stored in `docs/diagrams/<category>/` where category matches the docs taxonomy
- **AND** it SHALL be named using kebab-case: `<topic>-<aspect>.excalidraw`

#### Scenario: Diagram exports are co-located
- **WHEN** a diagram SVG export is referenced by a markdown page
- **THEN** the SVG file SHALL be co-located in the same directory as the referencing markdown page
- **AND** the markdown SHALL reference it with a relative path

#### Scenario: Diagram frequency meets minimum bar
- **WHEN** a documentation page describes architecture, flows, lifecycles, topology, data movement, or state transitions
- **THEN** the page SHALL include at least one diagram
- **AND** pages that are purely reference tables or configuration lists MAY omit diagrams

### Requirement: Content migration handling
Every existing file under `docs/` SHALL receive an explicit disposition during the documentation rewrite: move, rewrite, split, merge, archive, or delete. No file SHALL remain in a legacy location after migration is complete.

#### Scenario: Every existing file has a disposition
- **WHEN** the documentation migration is executed
- **THEN** every file that existed under `docs/` before the migration SHALL have a documented disposition in the migration table
- **AND** no file SHALL be left in its original location without an explicit "keep" disposition

#### Scenario: Moved files update cross-references
- **WHEN** a documentation file is moved to a new path
- **THEN** all internal cross-references to the old path SHALL be updated to the new path
- **AND** no broken internal links SHALL exist after migration

#### Scenario: Archived files have archival notice
- **WHEN** a documentation file is moved to `docs/archive/`
- **THEN** the file SHALL begin with an archival notice stating: the archive date, the reason for archival, and a link to the successor document (if any)

### Requirement: Draft and normative content separation
The documentation SHALL clearly distinguish between normative (authoritative, target-state) content and draft (speculative, not-yet-implemented) content. Draft content SHALL NOT be co-located with normative content in the same directory.

#### Scenario: Draft content is in archive
- **WHEN** a document describes a feature that is not yet implemented
- **THEN** the document SHALL be placed in `docs/archive/` with a "Draft" status indicator
- **AND** it SHALL NOT appear in the same directory as normative documentation for implemented features

#### Scenario: Normative content is marked
- **WHEN** a documentation page describes current, implemented system behavior
- **THEN** the page SHALL be treated as normative by default
- **AND** if the page covers an experimental or unstable feature, it SHALL include a visible "Experimental" notice

### Requirement: Docs-code co-evolution contract
The documentation SHALL define triggers for when code changes require documentation updates, ensuring documentation does not drift from implementation.

#### Scenario: New butler triggers doc update
- **WHEN** a new butler is added to the `roster/` directory
- **THEN** a corresponding page SHALL be created in `docs/butlers/`
- **AND** `docs/index.md` SHALL be updated to include the new butler

#### Scenario: New module triggers doc update
- **WHEN** a new module is created under `src/butlers/modules/`
- **THEN** a corresponding page SHALL be created in `docs/modules/`

#### Scenario: New connector triggers doc update
- **WHEN** a new connector is created under `src/butlers/connectors/`
- **THEN** a corresponding page SHALL be created in `docs/connectors/`

#### Scenario: Schema change triggers doc update
- **WHEN** a new Alembic migration is added that alters database schema structure
- **THEN** the relevant page in `docs/data_and_storage/` SHALL be reviewed and updated if affected

#### Scenario: Diagram refresh on page update
- **WHEN** a documentation page is updated with changed system behavior
- **THEN** any associated diagram SHALL be reviewed for accuracy
- **AND** if the diagram no longer matches the described behavior, it SHALL be regenerated via `/excalidraw-diagram`

### Requirement: README scope boundary
README.md SHALL serve as a concise project introduction and entry point. Detailed documentation SHALL live in `docs/`, not in README.md.

#### Scenario: README links to docs for details
- **WHEN** README.md describes architecture, setup procedures, environment variables, testing, or operational concerns
- **THEN** README.md SHALL provide a brief summary (no more than one paragraph per topic) and link to the corresponding `docs/` page for full details

#### Scenario: README does not duplicate docs content
- **WHEN** detailed content exists in both README.md and a `docs/` page
- **THEN** README.md SHALL be thinned to a summary with a link
- **AND** the `docs/` page SHALL be the authoritative source

### Requirement: Cross-reference integrity
The documentation SHALL maintain valid internal cross-references. No documentation page SHALL contain broken relative links to other documentation pages.

#### Scenario: All internal links resolve
- **WHEN** the documentation set is validated
- **THEN** every relative link in every markdown file under `docs/` SHALL resolve to an existing file
- **AND** no link SHALL point to a file that has been moved or deleted without a redirect

#### Scenario: Category index pages exist
- **WHEN** a top-level documentation category contains multiple pages
- **THEN** the category SHALL have an `index.md` file listing all pages with brief descriptions

### Requirement: Minimal duplication
The documentation SHALL minimize content duplication across pages. When the same concept is relevant to multiple pages, one page SHALL be the canonical source and other pages SHALL link to it.

#### Scenario: Shared concept has single canonical page
- **WHEN** a concept (e.g., "owner identity", "ingestion envelope", "module lifecycle") is referenced by multiple documentation pages
- **THEN** exactly one page SHALL provide the full explanation
- **AND** other pages SHALL link to the canonical page rather than duplicating the explanation

#### Scenario: Per-butler pages do not duplicate module docs
- **WHEN** a butler page in `docs/butlers/` references modules that the butler uses
- **THEN** the butler page SHALL list the modules with brief descriptions and link to `docs/modules/` for full details
- **AND** the butler page SHALL NOT reproduce the module's configuration schema, tool list, or migration details

### Requirement: Suggested target docs tree
The documentation SHALL conform to the following target directory structure:

#### Scenario: Target tree structure
- **WHEN** the documentation migration is complete
- **THEN** the `docs/` directory SHALL contain the following structure:

```
docs/
  index.md
  overview/
    index.md
    what-is-butlers.md
    project-goals.md
  getting_started/
    index.md
    prerequisites.md
    dev-environment.md
    first-butler-launch.md
    dashboard-access.md
  concepts/
    index.md
    butler-lifecycle.md
    modules-and-connectors.md
    switchboard-routing.md
    trigger-flow.md
    identity-model.md
    mcp-model.md
  architecture/
    index.md
    system-topology.md
    butler-daemon.md
    routing.md
    database-design.md
    startup-sequence.md
    observability.md
    email-priority-queuing.md
    pre-classification-triage.md
    thread-affinity-routing.md
  runtime/
    index.md
    spawner.md
    scheduler-execution.md
    session-lifecycle.md
    model-routing.md
    tool-call-capture.md
  butlers/
    index.md
    switchboard.md
    general.md
    relationship.md
    health.md
    messenger.md
    finance.md
    education.md
    travel.md
    home.md
  modules/
    index.md
    module-system.md
    memory.md
    calendar.md
    contacts.md
    approvals.md
    email.md
    telegram.md
    mailbox.md
    metrics.md
    pipeline.md
    knowledge-base.md
  connectors/
    index.md
    overview.md
    telegram-bot.md
    telegram-user-client.md
    gmail.md
    heartbeat.md
    live-listener.md
    attachment-handling.md
    metrics.md
  frontend/
    index.md
    purpose-and-single-pane.md
    information-architecture.md
    feature-inventory.md
    data-access-and-refresh.md
    backend-api-contract.md
  data_and_storage/
    index.md
    schema-topology.md
    migration-patterns.md
    state-store.md
    blob-storage.md
    credential-store.md
  identity_and_secrets/
    index.md
    owner-identity.md
    contact-system.md
    oauth-flows.md
    cli-runtime-auth.md
    environment-variables.md
  api_and_protocols/
    index.md
    mcp-tools.md
    ingestion-envelope.md
    dashboard-api.md
    inter-butler-communication.md
  operations/
    index.md
    docker-deployment.md
    environment-config.md
    grafana-monitoring.md
    connector-scaling.md
    troubleshooting.md
    backup-and-restore.md
  testing/
    index.md
    testing-strategy.md
    markers-and-fixtures.md
    e2e/
      README.md
      introduction.md
      infrastructure.md
      contracts.md
      flows.md
      approvals.md
      security.md
      resilience.md
      scheduling.md
      state.md
      observability.md
      performance.md
    benchmark-report.md
    test-audit-report.md
  roadmap/
    index.md
    project-plan.md
    openspec-overview.md
  diagrams/
    architecture/
    runtime/
    butlers/
    modules/
    connectors/
    frontend/
    identity/
    operations/
    testing/
  archive/
    switchboard-message-inbox-migration.md
    switchboard-spec-impl-delta-matrix.md
    connector-ingestion-migration-delta-matrix.md
    draft-discord.md
    health-wearable-draft.md
    home-assistant-draft.md
    photos-screenshots-draft.md
    voice-draft.md
    whatsapp-draft.md
    memory-improvements.md
    memory-improvements-pt2.md
    self-healing-gen1.md
    token-limits-gen1.md
    discretion-layer-adapter-migration.md
```
