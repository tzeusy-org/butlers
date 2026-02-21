# Spawner Streamable HTTP Rollout and Fallback Runbook

Status: Normative (Operations Guide)  
Last updated: 2026-02-21  
Owner epic: `butlers-1017`

## 1. Scope

This runbook covers runtime MCP transport cutover for **spawner-launched runtime
sessions**:

- `claude-code`
- `codex`
- `gemini`

Target state:

- Runtime sessions default to streamable HTTP MCP endpoint:
  `http://localhost:<butler-port>/mcp`
- Butler daemons expose both:
  - streamable HTTP (`/mcp`) for runtime sessions
  - legacy SSE (`/sse` + `/messages`) for compatibility during rollout

Out of scope:

- Connector transport migration (connectors still target Switchboard `/sse`)
- Dashboard REST API transport

## 2. Compatibility Contract

| Caller / Client | During rollout | Notes |
|---|---|---|
| Spawner-launched runtime sessions | `/mcp` (streamable HTTP) | Canonical default via `runtime_mcp_url()` |
| Existing SSE clients/connectors | `/sse` | Remains supported; do not force-migrate during this rollout |
| External modern MCP clients | Prefer `/mcp` | `/sse` remains available for legacy-only clients |

Compatibility guidance:

- Keep `SWITCHBOARD_MCP_URL` on connector deployments pointed at `/sse` unless
  and until connector-specific migration is approved.
- Do not remove `/sse` while any internal client still depends on SSE transport.

## 3. Preconditions

Before cutover in any environment:

1. Confirm deployment includes runtime transport changes (`butlers-1017.1`,
   `butlers-1017.2` lineage).
2. Confirm each butler daemon process can start normally.
3. Confirm connectors continue to run with existing `/sse` URL configuration.

## 4. Validation Probes

Run these probes against at least one canary butler first, then all butlers in
the environment.

1. Verify streamable endpoint is wired:

```bash
curl -sS \
  -o /tmp/butler-mcp-probe.txt \
  -w "%{http_code}\n" \
  -X POST "http://localhost:40101/mcp" \
  -H "accept: application/json, text/event-stream" \
  -H "content-type: text/plain" \
  --data '{}'
```

Expected result: `400` with a response mentioning `Content-Type` (proves route
exists and request reached MCP streamable handler).

2. Verify legacy SSE endpoint is still reachable:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" "http://localhost:40101/sse"
```

Expected result: `200`.

3. Verify runtime URL selection remains canonical:

```bash
uv run pytest tests/core/test_mcp_urls.py tests/core/test_spawner_mcp_config.py -q
```

Expected result: passing tests; spawner URL assertions remain `/mcp`.

4. Verify at least one notify-capable routed prompt records runtime tool calls:

```sql
SELECT id, prompt, jsonb_array_length(tool_calls) AS tool_call_count
FROM sessions
WHERE started_at > NOW() - INTERVAL '30 minutes'
  AND prompt ILIKE '%notify%'
ORDER BY started_at DESC
LIMIT 20;
```

Expected result: at least one recent row with `tool_call_count > 0`.

## 5. Rollout Sequence

### 5.1 Development

1. Deploy and restart all butlers.
2. Run validation probes in Section 4.
3. Execute at least one end-to-end routed prompt requiring tool usage.
4. Confirm no connector ingestion regression (connectors still pointing at `/sse`).

### 5.2 Staging

1. Deploy to staging.
2. Run Section 4 probes across all staging butlers.
3. Run targeted notify/routing regression flow and inspect `sessions.tool_calls`.
4. Observe for at least one traffic window before production promotion.

### 5.3 Production

1. Canary one low-risk butler first.
2. Run Section 4 probes on canary.
3. Expand rollout to remaining butlers.
4. Keep `/sse` path enabled throughout rollout window for compatibility.

## 6. Fallback and Rollback

Use the smallest blast-radius option first.

### 6.1 Adapter-level fallback (preferred short-term)

If streamable `/mcp` behavior regresses for a specific butler:

1. Temporarily switch that butler runtime to an adapter with known SSE fallback
   behavior (`claude-code` or `gemini`) in `roster/<butler>/butler.toml`:

```toml
[runtime]
type = "claude-code"
```

2. Restart only the affected butler.
3. Re-run Section 4 probe #2 (`/sse`) and routed tool-call validation.

Important tradeoff:

- Codex relies on streamable HTTP MCP. Treat Codex as **not SSE-fallback-safe**
  for production reliability if `/mcp` is unavailable.

### 6.2 Release rollback (environment rollback)

If failures are broad or cross-adapter:

1. Roll back deployment to the last release before streamable cutover commits.
2. Restart butlers.
3. Re-run smoke probes:
   - `/sse` reachable
   - routed prompts complete without transport errors
4. Record incident notes and open a follow-up issue before retrying cutover.

### 6.3 Code rollback (hotfix branch)

For source-based emergency rollback:

```bash
git revert --no-edit 2081ba7f b13d51b2
```

Then redeploy and validate as above.

## 7. Exit Criteria

Cutover is considered stable when all are true:

1. `/mcp` and `/sse` probes pass for every butler in target environment.
2. Runtime sessions in active traffic continue recording expected tool calls.
3. No connector regressions are observed while connectors stay on `/sse`.
4. Rollback rehearsal (Section 6.2 or 6.3) has been executed in non-prod and
   documented.
