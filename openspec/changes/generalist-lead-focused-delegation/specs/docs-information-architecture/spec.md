## MODIFIED Requirements

### Requirement: Suggested target docs tree
The documentation SHALL conform to the target directory structure in the scenario below. The tree SHALL NOT reserve pages for message classification, pre-classification triage, thread-affinity routing, route dispatch or per-butler session spawning, because those mechanisms are removed; their places are taken by pages for goal admission, the goal lifecycle, the control plane, capability authorization and the goal runner.

Source: RFC 0038 D1, D2, D3, D5
Scope: v1-mandatory

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
    goal-admission.md
    goal-lifecycle.md
    identity-model.md
    mcp-model.md
  architecture/
    index.md
    system-topology.md
    butler-daemon.md
    control-plane.md
    capability-authorization.md
    database-design.md
    startup-sequence.md
    observability.md
    email-priority-queuing.md
  runtime/
    index.md
    goal-runner.md
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
    data-access-and-refresh.md
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
    backup-restore.md
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
  archive/                      # retained research and evidence with an explicit purpose
    README.md                   # successors for retired bodies; git history preserves them
```

#### Scenario: Removed routing mechanisms have no reserved pages
- **WHEN** the documentation migration is complete
- **THEN** `docs/` contains no target page for pre-classification triage, thread-affinity routing or route dispatch
- **AND** goal admission, the goal lifecycle, the control plane, capability authorization and the goal runner each have a target page
