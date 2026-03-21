# Runtime

> **Scope:** How the system behaves when running — processes, scheduling, sessions, tool execution.
> **Belongs here:** Spawner flow, scheduler execution, session lifecycle, model routing, tool call capture.
> **Does NOT belong here:** Static architecture (see [Architecture](../architecture/index.md)), initial setup, testing.

- [Spawner](spawner.md) — LLM CLI spawner: lock, config gen, SDK invoke, parse
- [Scheduler Execution](scheduler-execution.md) — tick loop, cron dispatch, staggering
- [Session Lifecycle](session-lifecycle.md) — session creation, logging, completion
- [Model Routing](model-routing.md) — model catalog, selection, complexity classification
- [Tool Call Capture](tool-call-capture.md) — tool call interception and analysis
