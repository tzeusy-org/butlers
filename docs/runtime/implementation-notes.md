# Runtime Implementation Notes

> **Purpose:** Hard-won runtime rules: session routing context, approval authority, runtime-attention reissue and QA suppression.
> **Audience:** Developers changing the spawner, approvals, runtime attention or QA assurance.
> **Prerequisites:** [Spawner](spawner.md), [Session lifecycle](session-lifecycle.md).

Moved verbatim from `AGENTS.md`.

- Session routing context is binding-only: a `runtime_session_id` query locator without a valid
  `X-Butlers-Fact-Invocation` binding is diagnostic, so
  `get_current_runtime_session_routing_context()` returns `None` and memory content authority is
  `third_party`.

- `ContextVar.reset()` does not revoke values inherited by an `asyncio.create_task()` child.
  Executor-scoped approval authority must also bind the authorising task, tool name and canonical
  argument digest; Home treats inherited-child, wrong-tool and wrong-argument context as unapproved.
- Runtime-attention manual reissue creates a new episode, never a replay:
  `public.reissue_runtime_attention_episode(uuid)` admits only an uncertain original after the
  delivery lease is inactive and never mutates the original, calls transport or writes breaker
  provenance. The Models/Spend attention reads sit behind `require_dashboard_owner_control` (503
  without `DASHBOARD_API_KEY`, 401 on a missing or wrong key). Stored-function and audit
  contracts live in [`runtime-attention.md`](../operations/runtime-attention.md).
- Process-fenced supervised-job health (bu-c6wjr) covers Dashboard lifespan loops, not
  Switchboard's runtime-attention delivery worker, whose truth is its attention condition and
  outbox evidence.
- QA `suppressed` is not synonymous with synthetic: the patrol uses it for filtered findings and the
  dashboard for a synthetic placeholder. Assurance reads durable origin, the enabled-source snapshot
  and all-source completion; a newer non-qualifying row does not erase a still-fresh qualifying
  patrol.
