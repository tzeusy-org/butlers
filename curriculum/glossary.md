# Glossary

`MCP`: Model Context Protocol, the tool-calling protocol this repo uses between LLM runtimes and butler daemons.

`FastMCP`: Python framework used to expose MCP tools over HTTP/SSE.

`Butler`: A long-running domain-specific daemon with a scoped MCP tool surface, local schema, prompt/personality files, modules, and schedules.

`Switchboard`: The routing butler that accepts external ingress, classifies or triages messages, and sends route envelopes to target butlers.

`Module`: In-process first-party capability loaded by a daemon. Modules register MCP tools, manage lifecycle hooks, and may own migrations.

`Connector`: Out-of-process transport adapter that reads external services, normalizes events, checkpoints progress, and submits canonical ingress to the Switchboard.

`Runtime adapter`: Code that launches and supervises an LLM CLI subprocess such as Codex or Claude Code.

`Ephemeral session`: A short-lived LLM runtime invocation. Durable orchestration, state, tools, and logs remain in the daemon/database.

`Route envelope`: Structured routing payload carrying request context, source metadata, trace context, input, and subrequest details.

`Request context`: Correlation and provenance fields such as `request_id`, sender identity, source channel, thread, and trace context.

`Idempotency`: The property that retrying the same operation does not create unintended duplicate effects.

`Backpressure`: A mechanism that slows or rejects new work when queues or downstream processors are saturated.

`JSONB`: PostgreSQL binary JSON type used for flexible persisted payloads such as tool calls, state values, job args, and envelopes.

`search_path`: PostgreSQL setting that decides which schema unqualified SQL names resolve against.

`Alembic chain`: A linear migration sequence. This repo has core, module, and roster-specific chains.

`Guarded DDL`: Migration code that checks whether tables/schemas/columns exist before changing them, allowing partial deployments or optional modules.

`CredentialStore`: DB-first credential resolver used by modules and integrations.

`OAuth state`: Short-lived value used to bind an OAuth callback to the initiating flow and defend against CSRF.

`Approval gate`: Policy layer that parks, approves, denies, redacts, or audits sensitive tool calls.

`Tool sensitivity`: Metadata or heuristic classification that identifies arguments or tools requiring extra care.

`Cron`: Repeating schedule expression used by autonomous tasks.

`Projection`: Materialized view-like table rows derived from another source, such as calendar provider events or internal reminders.

`OpenTelemetry`: Observability standard used here for traces and metrics.

`Trace context`: Correlation metadata used to connect spans across daemons, subprocesses, and HTTP/MCP tool calls.

`testcontainers`: Test library that starts real Docker services, especially PostgreSQL, during integration tests.

`xdist`: Pytest plugin for parallel test execution.

`pgvector`: PostgreSQL extension used for vector embeddings and similarity search.

`Embedding`: Numeric vector representation of text used for semantic retrieval.

`Provenance`: Information explaining where a fact, event, or record came from and which session/source created it.

`OpenSpec`: Spec workflow used in this repository to describe planned and implemented behavior.
