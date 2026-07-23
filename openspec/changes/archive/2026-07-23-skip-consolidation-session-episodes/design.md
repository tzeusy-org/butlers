## Context

The live `Spawner` records every invocation in the normal session lifecycle and,
when memory is enabled, writes successful non-empty runtime output through
`store_session_episode()`. The deterministic consolidation handler deliberately
uses that same Spawner with `trigger_source="schedule:consolidation"`; recording
that worker output as a new raw episode feeds the consolidation system back into
its own input stream.

The session row remains the audit and observability record for consolidation.
The correction therefore belongs at the single Spawner episode-write boundary,
after session completion, rather than in scheduler dispatch or memory storage.

## Goals / Non-Goals

**Goals:**

- Keep successful `schedule:consolidation` sessions in the ordinary session
  lifecycle while preventing their automatic raw-episode write.
- Match the one canonical trigger source exactly.
- Preserve the existing fail-open memory behavior and every other trigger
  source's episode-write behavior.

**Non-Goals:**

- Changing scheduler dispatch, consolidation grouping, cross-schema hooks,
  cleanup/backfill, batching/deadman controls, or tier and API policy.
- Suppressing session records, memory-context reads, or explicit memory tool
  calls made by an LLM session.
- Generalizing the exclusion to all `schedule:*` work.

## Decisions

### Guard the existing write call with exact equality

The write condition will retain its existing memory-enabled, success, and
non-empty-output predicates and add `trigger_source != "schedule:consolidation"`.
This is the narrowest deterministic boundary: it preserves session completion
and avoids teaching the scheduler or storage layer about a Spawner-specific
policy.

**Alternatives considered:**

- Exclude all `schedule:*` sources: rejected because scheduled work such as
  `schedule:daily_digest` still represents a user-relevant observation.
- Skip memory context or the full session lifecycle: rejected because the
  problem is only the post-success automatic episode write and the session row
  remains required for audit.
- Teach `store_session_episode()` about trigger sources: rejected because that
  low-level storage boundary lacks the lifecycle context and could silently
  affect direct callers.

### Protect the carve-out with focused integration-style Spawner tests

Tests will exercise the public `Spawner.trigger()` path with a memory-enabled
configuration and a successful adapter. They will assert that the exact
consolidation source does not await the write hook while an ordinary scheduled
source does. Existing tests retain the memory-disabled and failed-session
negative behavior.

## Risks / Trade-offs

- [Future consolidation aliases may be introduced] → The exact string is
  intentional. A new alias must receive an explicit spec and test rather than
  silently widening the guard.
- [The session remains visible but has no episode] → This is desired; the
  session record is the audit trail and the absence of an episode prevents the
  self-referential input.
- [A condition at a shared boundary can regress ordinary schedules] → Add an
  explicit `schedule:daily_digest` positive regression assertion alongside the
  exact-source exclusion.
