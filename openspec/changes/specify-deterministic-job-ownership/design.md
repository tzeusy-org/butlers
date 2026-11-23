## Exact text for adoption

The principle applies to all enabled modules, including modules that have
scheduled maintenance without exposing a separate MCP tool for every
handler. The existing scheduler and spawner ownership in Rules 2 and 4
remains binding. Rule 5 continues to govern roster schedules and operational
tuning. Rule 6 continues to constrain each handler to the owning manifesto
or infrastructure contract.

### Vision Rule 2

Current text in [vision.md](../../../about/heart-and-soul/vision.md) is reproduced below.

> 2. **Modules only add tools --- they never touch core infrastructure.** A module
>    registers MCP tools, declares database migrations, and hooks into the daemon
>    lifecycle. It must not modify the state store, the scheduler, the spawner, or
>    the session log. If a capability requires core changes, it belongs in core,
>    not in a module.

Proposed replacement

> 2. **Modules declare capabilities. Core owns infrastructure.** A module
>    registers MCP tools, declares database migrations and deterministic job
>    handlers, and hooks into the daemon lifecycle. Core owns the state store,
>    schedule synchronization, execution, the spawner, and the session log.
>    A job declaration gives the scheduler an owned handler. Schedule state,
>    dispatch authority, and runtime lifecycle remain under core control.
>    Changes to that infrastructure belong in core.

### Architecture module contract

In `about/heart-and-soul/architecture.md`, add this item to the Module
interface list after the lifecycle hooks.

> - `jobs()` supplies deterministic handlers owned by the enabled module.
>   Core resolves the declarations for the owning daemon and runs them when
>   the scheduler claims a matching occurrence.

Replace the existing constraint paragraph

> **The constraint:** Modules only add tools. They must never modify core
> infrastructure --- the state store, the scheduler, the spawner, or the session
> log. If a capability requires changes to core, it belongs in core.

with

> **The constraint:** A module supplies its tools, data migrations, lifecycle
> hooks, and deterministic job declarations. Core composes and validates the
> owning daemon's declarations and retains exclusive control over the state
> store, schedule synchronization, claims, dispatch, the spawner, and the
> session log.

The owner reviews these exact passages as one decision. The architecture
paragraph mirrors Rule 2, which otherwise still says modules only add tools.
Rules 4, 5 and 6 retain their current text.

## Proposed capability acceptance

These scenarios would govern the existing `Schedule declarations` scenario
in `openspec/specs/butler-base-spec/spec.md` and the synchronization and
module-default guarantees in `openspec/specs/core-scheduler/spec.md`. They
leave the existing native dispatch and capability-governed Spawner clauses in
place.

### Job declarations are scoped to an owning daemon

- **WHEN** core prepares a daemon's job-mode schedules
- **THEN** core resolves each job name from its retained infrastructure
  declarations, the roster's `JOBS`, or an enabled loaded module's
  `Module.jobs()`.
- **AND** the empty module declaration requires no scheduler registration.
  Declarations provide names and callables. Core owns publication of the
  daemon-local registry and the scope in which a callable runs.
- **AND** each name identifies one handler per daemon. The same name may
  name a separate handler in another daemon.
- **AND** the handler receives the owning daemon's pool and existing job
  arguments. Its current capability authority and role grants govern any
  existing public-schema or brokered access.

### Invalid declarations prevent a scheduled dispatch

- **WHEN** a daemon composes or synchronizes schedules with an ambiguous
  name, an unknown name, or a reference to a disabled module's handler
- **THEN** core refuses the invalid candidate before schedule state for that
  candidate is published or dispatched. Existing committed schedule rows and
  their due occurrences are preserved.
- **AND** the failure identifies the daemon and offending job name. A
  registry from another daemon, a retired alias or a session cannot satisfy
  the failed lookup.
- **WHEN** a job-mode claim or deadline is evaluated with the wrong pool
- **THEN** the scheduler rejects dispatch before claiming the occurrence,
  acquiring a lease or calling the handler on the wrong pool.

### Registration preserves existing schedule behavior

- **WHEN** the daemon restarts or reapplies an unchanged, valid declaration
- **THEN** the existing schedule identity, cadence, timezone, stagger,
  arguments, enablement, ownership and next-due state survive according to
  their current contracts. Registration neither claims an occurrence nor
  creates a second schedule.
- **AND** eligible Memory defaults still use core's existing TOML-orphan
  recovery. An active TOML override keeps its cadence. An operator-disabled
  DB row stays disabled. The recovered row and its audit entry commit
  together, with the existing payload and replay behavior.
- **WHEN** a registered native job becomes due
- **THEN** core retains the current lease, idempotency, cancellation, retry,
  timeout and failure handling. Only a capability that already authorizes
  an explicit Spawner or goal action retains that action. Declaration and
  synchronization start no session.

## Design and transition

Core composes one per-daemon registry from the loaded module declarations,
canonical roster imports and the retained infrastructure factories. It checks
names and scope before using the map for cron or job-mode deadlines. The
existing `butlers.roster_imports` contract supplies roster module identity.
The source inventory separates current registrations from currently scheduled
TOML rows and distinguishes module defaults, dormant registrations and
non-scheduler API callers.

Move placement and registration together in bu-dss7he.10 after adoption.
Keep existing persisted names and default state throughout the transition.
Prove all current call sites resolve through the new map before deleting the
central roster map and its rebuild-on-miss fallback. Rollback restores matching
declarations and resolver together while preserving schedule rows and leases.
Scheduled jobs retain their current API consumers. The dashboard's supervised
loops retain their existing owning process, timers and shutdown.

Preserve the daemon's existing dispatch contexts for credentials, Switchboard,
ApprovalPush and Memory. Chronicler's private continuation can observe a
successful eligible projection or daily rollup only through the trusted
declaration's handler identity. Another callable under the same name cannot
trigger that continuation. Preserve the existing tick-tail durable discovery
and day-close participation as the centralized lookup is removed.

Keeping the central map would retain the split between declaration and
ownership. Giving modules independent timers would duplicate schedule
persistence, lifecycle and claims. The proposed contract keeps one scheduler
and validates the declarations where it already owns those guarantees.

## Adoption sweep

On exact owner approval, apply the supplied Rule 2 and architecture text
together. Carry the meaning into `about/README.md`,
`about/lay-and-land/module-vs-butler.md`,
`docs/concepts/modules-and-connectors.md`, `docs/modules/module-system.md`,
`docs/runtime/scheduler-execution.md`, and the roster/module authoring skills.
Update the affected canonical scheduler and base capability specs in their
governing source change. Keep the source citations to Rules 2, 4, 5 and 6.
RFC 0038's distinction between deterministic job dispatch and scheduled
reasoning obligations stays intact. RFC 0026's rejection of a cross-butler
commitments module remains valid because the declaration is daemon-local.
The old connector-roadmap proposal remains historical evidence.
