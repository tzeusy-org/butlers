## Why

PR #3027 (`feat(chronicler): day-close gap interview one-tap confirmation
[bu-whhll.12]`, merged as `1973cd191`) shipped the day-close gap interview —
the first real tenant of the corrections machinery — **without** its OpenSpec
delta. This was an ordering race: the author-before-merge instruction crossed
the merge in flight. The code is the source of truth and is already live; this
is the fast-follow spec delta describing the **shipped reality** so the specs
stop drifting from the running system.

Two shipped surfaces are undocumented:

1. **`POST /api/chronicler/gap-interview/resolve`** — a connector-facing
   internal endpoint that applies a one-tap gap-interview answer
   (`confirm`/`correct`/`dismiss`) by delegating to the shared resolver
   (`resolve_gap_interview_callback`). It is idempotent, always returns HTTP
   200 with a `status` field, and never raises for owner-facing conditions
   (unknown/expired interview, unparseable answer). The `telegram_bot`
   connector runs as the restricted `connector_writer` role and cannot write
   the chronicler schema itself, so it POSTs here; the endpoint runs with the
   chronicler pool that can. The same resolver backs the
   `chronicler_resolve_gap_interview` MCP tool, so the write shape and
   idempotency are identical across both entry points.

2. **The `telegram_bot` connector's `cgi:`-prefixed `callback_query`
   ingress** — a strictly additive carve-out in the connector's update-type
   handling: a `callback_query` whose `callback_data` carries the `cgi:`
   gap-interview prefix is routed to the resolve path (and acknowledged via
   `answerCallbackQuery`), while every other `callback_query` retains its
   existing drop behavior.

## What Changes

- **MODIFIED** `chronicler-api` → *Chronicler Corrections*: add
  `POST /api/chronicler/gap-interview/resolve` to the correction endpoint set
  and a scenario capturing its idempotent, always-200-with-status,
  gracefully-degrading contract (shared resolver across the HTTP endpoint and
  the `chronicler_resolve_gap_interview` MCP tool). No behavior change — this
  documents what shipped.
- **MODIFIED** `connector-telegram-bot` → *Update Type Handling*: carve out the
  additive `cgi:` gap-interview exception to the "callback_query is silently
  skipped" rule and a scenario for the one-tap routing, preserving the drop
  behavior for all non-`cgi:` callbacks.

Scope is spec/docs only — no code, no migrations. No MCP-tool count or
tool-name enumeration is pinned in any chronicler module/butler spec, so the
`chronicler_gap_interview` scheduled surface and `chronicler_resolve_gap_interview`
MCP tool need no enumeration update.

**Forward note:** `bu-jjy9p` will later migrate this ask/answer transport onto
the decision loop (RFC 0021, epic `bu-24lu6`, owner-gated behind `bu-24lu6.1`).
This delta deliberately describes the **current shipped transport** regardless
— spec follows code; that future change updates these requirements again when
it lands.

**Pre-existing drift fixed (mechanical, meaning-preserving):** archiving the
`connector-telegram-bot` delta rebuilds and strict-validates the whole spec,
which surfaced 11 pre-existing requirements whose intro sentence lacked a
`SHALL`/`MUST` keyword (a validator requirement). Those 11 requirement intros
were reworded to use `SHALL` without changing their meaning (e.g. "The
connector supports two modes" → "The connector SHALL support two modes"), so
the spec passes `--strict` on archive rather than being carried as
`--no-validate` debt. No scenario text or normative behavior changed.

## Impact

- Affected specs: `chronicler-api`, `connector-telegram-bot`
- Affected code: none (documentation of already-merged PR #3027)
