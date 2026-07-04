# Runtime Verification — drive the flow, don't just read it

Load when the Docker Compose dev stack is up and you want to **confirm** a flow rather than infer
it from code. Static FE→BE tracing tells you what *should* happen; driving the flow tells you what
*does*.

## Confidence is three levels, not a binary

Grade every finding: `live-confirmed` (reproduced against the running stack) > `source-confirmed`
(you read the *exact* decisive mechanism statically — the SQL clause, the handler body, the writer
*and* every reader) > `inferred` (static reasoning without reading the line that proves it).
Collapsing "I read the SQL that proves no consumer exists" into the same "suspected" bucket as a
guess undersells solid findings — keep them distinct so the reader can triage.

## Drive reads only — never fire a mutation on shared dev

Live verification is **GET/read-side only**. The dev stack is a shared database; do **not** trigger
a mutation (any merge / archive / forget / delete / POST / PATCH / PUT that writes) to "confirm" it
— you would corrupt shared state and possibly destroy real entities/rows. Verify mutation paths by
static trace (handler → client → route → does a reader consume the write). A read-only compare/diff
endpoint *is* safe to drive; the merge it precedes is not. When in doubt, treat it as a write and
trace it statically.

## Resolve the API base once (orchestrator, Phase 0.5)

The public/tailnet URL (e.g. `https://<host>.ts.net/butlers-dev/`) routes `/` to the Vite SPA and
frequently returns the **SPA `index.html` (or a bare `404 page not found`) for `/api/*`** even when
the backend is perfectly healthy — so a naive `curl .../butlers-dev/api/relationship/...` "fails"
misleadingly. Resolve the real JSON-returning base **once** in the orchestrator and hand it to every
agent (don't make N agents each try three wrong prefixes):

1. Read `about/lay-and-land/deployment.md` + `docs/getting_started/dev-environment.md` for the
   dashboard-API container name and **host port** (source of truth — don't hardcode).
2. Confirm the container is `Up` (not `Created`/`Restarting`): `docker ps --format '{{.Names}}\t{{.Status}}' | grep dashboard-api`. If app containers are still `Created`, the stack is mid-boot — wait for readiness before fan-out.
3. Probe the **direct host port** for JSON (e.g. `curl -s localhost:<port>/api/relationship/entities?limit=1`), not only the proxy. Pin whichever base returns JSON as the agents' live base; if none does, declare **static-only** explicitly.

## This builds on `/butler-dev-debug`

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

Say so in the report and grade findings `source-confirmed` (you read the decisive mechanism) or
`inferred` (you didn't) — never `live-confirmed`. Do not fabricate live evidence. Static tracing —
handler → client fn → route → `grep` for the runtime reader — is still the backbone and catches
most shapes; live driving is what upgrades a finding to `live-confirmed` and is the only reliable
way to catch fake-but-shaped-correct responses (shape 4) and silent watchdog deferral (shape 2).
