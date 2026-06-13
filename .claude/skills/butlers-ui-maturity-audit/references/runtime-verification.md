# Runtime Verification — drive the flow, don't just read it

Load when the Docker Compose dev stack is up and you want to **confirm** a flow rather than infer
it from code. Static FE→BE tracing tells you what *should* happen; driving the flow tells you what
*does*. A finding you reproduced live is "confirmed"; a finding from reading code alone is
"suspected" — label them differently in the report.

## This builds on `/butler-dev-debug`

`/butler-dev-debug` owns the live-stack investigation primitives — the canonical `.env.dev`-backed
psql entrypoint (`scripts/dev-psql.sh`), the `docker logs` conventions, the `sessions` /
`session_process_logs` query snippets, and the request/session/trace-ID follow across switchboard →
butler → connector. **Invoke it** for those; do not duplicate them here. Its project-grounding
docs (`about/lay-and-land/deployment.md` for topology/ports, `docs/getting_started/dev-environment.md`)
are the source of truth for container names and ports — read them rather than hardcoding.

This file adds only the **flow-QC-specific way** to use those primitives.

## The QC verification loop (per flow step that mutates or fetches)

For each step where the user clicks something or expects real data:

1. **Find the real endpoint.** From the React handler, get the API client fn, then the actual HTTP
   method + path it calls. (This is the static trace; do it first.)
2. **Hit it the way the UI does.** `curl` the dashboard API endpoint with a realistic payload and
   confirm the response is what the component expects — real data, not a stub, not zeros, correct
   shape. A 200 with hardcoded/empty fields is shape-4 (fake data) caught live.
3. **Confirm the write landed AND is consumed.** For a mutation, query the table via `dev-psql.sh`
   to confirm the row changed — then check that the runtime *reads* it (the consumer you found by
   `grep`). A write you can see in the DB that no runtime path reads is shape-1 (decorative
   persistence) proven live.
4. **Follow the request through the logs.** `docker logs` the daemon/connector container filtered
   by the request/session/trace ID (per `/butler-dev-debug`). If the UI toasted "done" but no
   work appears in the logs, that's shape-2 (the lie) proven live.
5. **Walk the unhappy branches.** Force the states the happy-path glosses: empty result, expired/
   revoked token, permission denied, a second concurrent edit. Confirm the UI degrades honestly
   (shape-8) rather than blank-rendering or erroring.

## When the stack is NOT up

Say so in the report and mark all findings "suspected (static only)." Do not fabricate live
evidence. Static tracing — handler → client fn → route → `grep` for the runtime reader — is still
the backbone and catches most shapes; live driving is what upgrades a finding from plausible to
proven and is the only reliable way to catch fake-but-shaped-correct responses (shape 4) and
silent watchdog deferral (shape 2).
