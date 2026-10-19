## Why

`POST /api/butlers/{name}/schedules/{schedule_id}/trigger` answered 200 whenever the
`schedule_trigger` tool replied, including an error reply for a schedule that does not
exist or a dispatch that failed. The dashboard toasts "triggered" on any 2xx, so the owner
was told a trigger that never happened had succeeded.

## What Changes

- MCP Client Proxy: a schedule trigger's outcome maps to the HTTP status. Triggered is 200,
  busy is 409, a nonexistent schedule is 404, and any other reply is 502, each non-200 with
  its reason in `detail`.

## Impact

- Spec: `dashboard-api` (MODIFIED "MCP Client Proxy", adding one scenario).
- Code: `src/butlers/api/routers/schedules.py`, `src/butlers/core_tools/_scheduling.py`.
- The retained DB audit case `trigger-refused` pins the old 200 for a failed trigger and
  moves to 502 under contract evolution.
