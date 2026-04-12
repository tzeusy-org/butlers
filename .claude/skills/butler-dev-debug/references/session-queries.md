# Butler Dev Debug Session Queries

Use this file when you already know the target butler/schema and need SQL.

## Entry Point

All SQL examples use the standardized helper:

```bash
./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -c "SELECT 1"
```

## Session Summary

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

## Full Prompt

```bash
./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -t -A -c "
SET search_path TO <butler-schema>;
SELECT prompt FROM sessions WHERE id = '<session-id>';
"
```

## Full Result

```bash
./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -t -A -c "
SET search_path TO <butler-schema>;
SELECT result FROM sessions WHERE id = '<session-id>';
"
```

## Tool Calls

```bash
./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -t -A -c "
SET search_path TO <butler-schema>;
SELECT tool_calls FROM sessions WHERE id = '<session-id>';
" | python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
for tool in data:
    if tool.get('name'):
        print(json.dumps({
            'name': tool['name'],
            'input_keys': list(tool.get('input', {}).keys()),
            'result': tool.get('result', {}),
            'outcome': tool.get('outcome', 'unknown'),
        }, indent=2))
"
```

## Process Logs

```bash
./.claude/skills/butler-dev-debug/scripts/dev-psql.sh -c "
SET search_path TO <butler-schema>;
SELECT session_id, pid, exit_code, runtime_type,
       left(stderr, 1000) AS stderr_preview
FROM session_process_logs
WHERE session_id = '<session-id>';
"
```

## Dashboard API

```bash
curl -s http://localhost:41200/api/butlers/<butler-name>/sessions/<session-id> | python3 -m json.tool
curl -s "http://localhost:41200/api/butlers/<butler-name>/sessions?limit=10" | python3 -m json.tool
```
