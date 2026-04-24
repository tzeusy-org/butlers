# Async Runtime And Failure Semantics

Estimated smart-human study time: 9 hours

## Why This Module Matters

The repository is an async service system that supervises background tasks, queues, subprocesses, connectors, schedulers, and HTTP/MCP handlers. Correctness often depends less on happy-path logic than on cancellation, backpressure, retries, timeouts, and idempotent recovery.

## Learning Goals

- Explain `asyncio` task lifecycle, cancellation, semaphores, and shutdown.
- Understand bounded queues, backpressure, and durable crash recovery.
- Reason about subprocess runtime adapters and timeout propagation.
- Distinguish transient failures from terminal failures and design idempotent retries.

## Subsection: Async Tasks, Cancellation, And Concurrency Limits

### Why This Matters Here

Butler daemons run schedulers, module pollers, route processors, liveness tasks, and spawned runtime sessions concurrently. A small cancellation mistake can leak work, drop a session, or make shutdown hang.

### Technical Deep Dive

In `asyncio`, a task is a scheduled coroutine. Cancellation is cooperative: a task receives `CancelledError` at an await point and must either clean up or deliberately shield a critical section. Semaphores bound concurrency by making tasks wait before entering a limited resource area. Timeouts wrap tasks but do not automatically make inner operations safe; inner APIs may need their own timeout parameters.

For services, graceful shutdown requires tracking background tasks, cancelling them in a predictable order, awaiting cleanup, and preserving durable state before volatile work disappears. High-level retry wrappers are not substitutes for understanding what state was already committed.

### Where It Appears In The Repo

- `src/butlers/core/spawner.py`
- `src/butlers/background.py`
- `src/butlers/lifecycle.py`
- `src/butlers/modules/calendar.py`
- `tests/core/test_buffer.py`

### Sample Q&A

- Q: Why is an outer `asyncio.wait_for()` not always enough for runtime timeout correctness?
  A: Because the inner adapter may have its own timeout behavior and diagnostics; both layers must agree or records become misleading.
- Q: What should happen when a background poller is cancelled?
  A: It should exit promptly after cleanup without swallowing cancellation as a fake success.

### Progress

- [ ] Exposed: I can define task, cancellation, shield, semaphore, timeout, and graceful shutdown.
- [ ] Working: I can explain how cancellation reaches an awaited coroutine.
- [ ] Contribution-ready: I can identify a critical section that must finish or persist recovery state before cancellation.

### Mastery Check

Target level: `contribution-ready`

You should be able to inspect an async loop or session path and explain what happens when it is cancelled, times out, or competes for a concurrency limit.

## Subsection: Queues, Retries, Idempotency, And Crash Recovery

### Why This Matters Here

Switchboard ingestion and delivery paths assume work may be retried, replayed, or recovered from durable storage. Duplicate effects are a real risk.

### Technical Deep Dive

A bounded queue protects a service from unbounded memory growth. Backpressure is the signal that producers are outrunning consumers. In robust systems, queue rejection is only safe when the work is already durable somewhere else or the caller receives an explicit failure.

Retries turn many workflows into at-least-once delivery. At-least-once means duplicates can happen. Idempotency is the design property that lets replayed work converge on one intended effect. Typical tools are stable request IDs, unique constraints, `ON CONFLICT`, dedup tables, state machines, and careful distinction between source-less manual data and provider-sourced events.

Failure classification decides whether to retry. Transient failures include timeouts, temporary connection loss, and rate limits. Terminal failures include validation errors, missing authorization, and policy denials. Misclassifying these can either spam external systems or permanently drop recoverable work.

### Where It Appears In The Repo

- `src/butlers/core/buffer.py`
- `src/butlers/core_tools/_routing.py`
- `roster/messenger/tools/reliability/`
- `roster/finance/tools/transactions.py`
- `tests/core/test_dsa4_validation.py`
- `roster/messenger/tests/test_reliability_retry.py`

### Sample Q&A

- Q: Why does at-least-once delivery require idempotent writes?
  A: Because a retry may repeat work that already partially succeeded.
- Q: When is queue-full recoverable?
  A: When the event was durably recorded before enqueue, or when the caller can safely retry from the same stable request identity.

### Progress

- [ ] Exposed: I can define backpressure, at-least-once, idempotency, transient failure, and terminal failure.
- [ ] Working: I can explain why retries can create duplicate side effects.
- [ ] Contribution-ready: I can propose an idempotency key for a replayable write path.

### Mastery Check

Target level: `contribution-ready`

You should be able to review a retry loop or queue handoff and state how duplicate work, crash recovery, and terminal failures are handled.

## Subsection: Subprocess Runtime Adapters

### Why This Matters Here

LLM sessions are launched as external CLIs with environment isolation, MCP config files, output parsing, process diagnostics, and model/runtime arguments.

### Technical Deep Dive

Supervising a subprocess is different from calling a library. The parent must construct arguments, sanitize the environment, pass necessary config, stream or capture output, enforce timeouts, classify exit states, preserve stderr diagnostics, and clean up temporary files. The parent must also record what it launched so later debugging can distinguish model failure, CLI failure, timeout, MCP discovery failure, and policy rejection.

Runtime adapters provide a contract boundary. Each adapter translates the repo's session model into provider-specific CLI behavior while returning normalized results to the spawner.

### Where It Appears In The Repo

- `src/butlers/core/runtimes/base.py`
- `src/butlers/core/runtimes/codex.py`
- `src/butlers/core/runtimes/claude_code.py`
- `src/butlers/core/spawner.py`
- `tests/adapters/test_adapter_contract.py`

### Sample Q&A

- Q: Why keep process diagnostics separate from the append-only session log?
  A: Process logs are operational diagnostics with TTL and size limits, while sessions are durable behavioral records.
- Q: What can go wrong if runtime args are appended after a prompt delimiter?
  A: The CLI may treat flags as prompt text rather than configuration.

### Progress

- [ ] Exposed: I can define runtime adapter, subprocess, environment isolation, stdout/stderr capture, and exit classification.
- [ ] Working: I can explain how adapter-specific failure details become normalized session outcomes.
- [ ] Contribution-ready: I can identify which tests should protect an adapter contract change.

### Mastery Check

Target level: `contribution-ready`

You should be able to explain the lifecycle of one spawned runtime process from config generation through completion diagnostics.

## Module Mastery Gate

- [ ] I can explain async cancellation and concurrency limits in this repo.
- [ ] I can describe how queueing and retries interact with idempotency.
- [ ] I can distinguish transient, terminal, gate-rejected, and timeout failures.
- [ ] I can point to adapter, buffer, routing, and retry tests that encode these contracts.
