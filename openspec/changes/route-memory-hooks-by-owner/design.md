## Context

`MemoryModule.on_startup()` creates context and episode-store closures that use
the module's authoritative memory pool.  This is required for a private
`memory_schema`, but `core.memory_hooks` currently keeps only one closure of
each kind for the whole process.  In a multi-daemon process, startup order
therefore decides which module owns every later request.

The scheduler's maintenance hooks already solve the analogous lifecycle
problem with an owner-keyed runtime registry and identity-safe unregister.

## Goals / Non-Goals

**Goals:**

- Select context and episode-store callbacks by the invoking butler identity.
- Keep a General, Travel, and private-schema Chronicler module independent in
  one process.
- Preserve a newer runtime when an older instance of the same owner shuts
  down.
- Fail safely for absent owners without using another owner's callback.

**Non-Goals:**

- Changing memory-forget or catalog-search registration.
- Changing maintenance runtime routing, storage schemas, migrations, cleanup,
  batching, deadman behavior, or model/tier policy.
- Retrying, queueing, or otherwise changing best-effort spawner semantics.

## Decisions

### Register a paired owner-keyed session runtime

`core.memory_hooks` will store a `MemorySessionRuntime` per normalized butler
name.  The value contains both the context and episode-store callbacks from one
started module.  `fetch_memory_context()` and `store_session_episode()` use
their existing `butler_name` parameter as the lookup key.

This preserves the current core-to-module dependency direction while ensuring
the two callbacks are replaced atomically as one lifecycle unit.

### Use identity-safe lifecycle removal

Registration returns the runtime object.  `MemoryModule` retains that object
and its daemon-schema owner, then passes both to unregister during restart or
shutdown.  Unregister deletes only when the dictionary still points to the
same object.  An old instance cannot erase a replacement that started later.

### Fail closed at the owner boundary

Context retrieval returns `None` and episode storage returns `False` when the
owner has no active runtime.  These are the existing best-effort outcomes for
an unloaded memory module, so the spawner continues safely while never
redirecting to the last registered daemon.

### Alternatives considered

- **Keep one global callback and pass an owner into it.** Rejected: each
  callback captures one `MemoryModule`, so it cannot resolve other module
  pools without reintroducing a global module lookup.
- **Use two independent owner-keyed callback maps.** Rejected: it permits
  context and episode callbacks from different lifecycle instances to be
  observed during replacement.  A paired runtime makes ownership atomic.
- **Use a ContextVar like maintenance dispatch.** Rejected: spawner calls
  already provide the owning butler identity explicitly, so ambient state adds
  coupling without improving isolation.

## Risks / Trade-offs

- **A caller supplies a missing or stale name** → return the established
  best-effort safe default and log at debug level; do not select another
  runtime.
- **A module restarts while an old instance shuts down** → identity comparison
  keeps the replacement registered.
- **Process-global test state leaks between tests** → tests register and
  unregister runtime objects in `try/finally` blocks.

## Migration Plan

No database or data migration is required.  Deploying the code changes makes
new daemon startup register its own session runtime.  Rollback restores the
previous hook implementation but does not alter stored memory data.
