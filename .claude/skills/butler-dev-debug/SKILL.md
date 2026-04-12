---
name: butler-dev-debug
description: Use when debugging a Butlers Docker Compose dev-stack session failure, routing problem, connector/runtime error, or when given a session ID, request ID, or trace ID to investigate.
compatibility: Designed for this repo's Docker Compose dev environment; requires docker, psql, grep, python3, and repo-root access to .env.dev.
metadata:
  owner: tze
  authors:
    - tze
    - OpenAI Codex
  status: active
  last_reviewed: "2026-04-12"
---

# Butler Dev Debug

Investigate failed butler sessions and connector/runtime issues in the Docker Compose dev stack.

## When to Use

- Given a session UUID, request ID, or trace ID to investigate
- Debugging a failed routing, delivery, tool call, or connector action
- Tracing a request through switchboard, a target butler, and one or more connectors
- Checking whether a compose service is unhealthy, restarting, or logging runtime errors

## Do Not Use

- For tmux-based local `scripts/dev.sh` debugging where the primary surface is pane output rather than Docker Compose containers
- For production or staging incident response; this skill assumes repo-root `.env.dev` and local compose container names
- For schema design or migration debugging detached from a concrete runtime or session symptom

## First Principles

- Run commands from the repo root so `.env.dev` resolves correctly.
- Treat `docker logs <container>` as the primary log source in dev. Do not start from the repo-local `logs/` folder.
- Use explicit DB credentials from `.env.dev`; do not assume `localhost:54320`.
- Identify the butler/schema before querying `sessions`.

## Project Grounding

This skill is a navigation layer over the repo's operational docs:

- `about/lay-and-land/deployment.md` is the source of truth for service topology and ports.
- `docs/getting_started/dev-environment.md` is the source of truth for local dev-environment assumptions.
- `docs/api_and_protocols/dashboard-api.md` is the source of truth for the dashboard API surface.

If this skill disagrees with those docs or with `docker-compose.yml`, fix the inconsistency instead of preserving two truths.

## Canonical Postgres Invocation

Use the helper script for every SQL query in this skill. `.env.dev` may omit `POSTGRES_DB`; the helper defaults it to `butlers`.

```bash
./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -c "SELECT 1"
```

## Debug Flow

1. Search `docker logs` in `butlers-dev-butlers-up-1` or the relevant connector container for the session/request ID.
2. Determine the butler/schema from the log lines or dashboard response.
3. Query the `sessions` row, then inspect `prompt`, `result`, `tool_calls`, and `session_process_logs`.
4. If the issue crosses services, follow the same ID through dashboard and connector container logs.
5. Check container health or restarts before assuming an application-level bug.

## Minimum Command Set

Search the main daemon logs first:

```bash
docker logs butlers-dev-butlers-up-1 --since 10m 2>&1 | grep "<session-id>"
```

Then fetch the session row from the right schema:

```bash
./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -c "
SET search_path TO <butler-schema>;
SELECT id, trigger_source, model, success, error,
       left(result, 500) AS result_preview,
       duration_ms, input_tokens, output_tokens,
       started_at, completed_at
FROM sessions
WHERE id = '<session-id>';
"
```

Then inspect the dashboard/API view:

```bash
curl -s http://localhost:41200/api/butlers/<butler-name>/sessions/<session-id> | python3 -m json.tool
```

## Reference

Use these progressively, not all at once:

- [references/topology.md](references/topology.md) when you need container names, ports, or source-of-truth docs
- [references/logs-and-health.md](references/logs-and-health.md) when you are following logs or checking service health
- [references/session-queries.md](references/session-queries.md) when you need SQL snippets for `sessions` and `session_process_logs`
- [references/error-patterns.md](references/error-patterns.md) when the symptom matches a known failure mode
- [scripts/dev-psql.sh](scripts/dev-psql.sh) for the standardized `.env.dev`-backed `psql` entrypoint

## Verification

Before calling the skill update complete:

1. Verify every referenced file exists.
2. Verify the helper script invocation still works with `--help`.
3. Verify at least one should-trigger case.
4. Verify at least one should-not-trigger case when scope is ambiguous.
