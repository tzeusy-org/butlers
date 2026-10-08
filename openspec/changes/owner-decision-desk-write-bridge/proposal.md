## Why

The Owner Decision Desk (epic `bu-ckkpz`) shipped its read side: the decision-bead convention,
the `/decisions` lane, the weekly review and P1/deploy escalation. It is dark on `butlers-dev`
because `beadsExport.enabled=false`, and its last slice (`bu-ckkpz.3`) cannot close a decision:
no runtime component can write to the tracker.

On 2026-10-08 the owner chose to build the whole desk, including Telegram one-tap close, and
superseded the 2026-10-04 `bu-sng0tu` ruling ("no Dolt credential in `butlers-dev`; wait for the
RFC 0025 PostgreSQL exporter"). A Dolt tracker credential and reachability are now allowed in
`butlers-dev`, with one constraint: tracker access lives in **one management workload**, never in
`dashboard-api` or `butlers-up`. The runtime records decision intents in Butlers Postgres. That
workload applies them with `bd` and re-exports.

This change is a feature-request amendment. It records that ruling against
`REQ-beads-projection-001`'s runtime boundary and specifies the write bridge it permits.

## What Changes

- **Tracker bridge boundary (amends `beads-projection`).**
  - One CronJob in `butlers-dev` holds the tracker endpoint and a least-privilege Dolt
    credential, and runs a pinned `bd` image.
  - It applies recorded intents, then exports `issues.export.jsonl` to the existing read-only
    PVC.
  - A NetworkPolicy denies every other pod in the namespace egress to the tracker address, so
    "only one workload can reach the tracker" holds at the network layer as well as in
    configuration.
  - The RFC 0025 projection stays the long-term direction. This bridge is an explicit,
    owner-authorized interim, not a fallback.
- **Decision intents (new `owner-decision-desk` capability).**
  - Each owner choice is a durable row in `switchboard.decision_intents`.
  - It has an honest lifecycle: `pending` -> `applying` -> `applied` | `failed`.
  - At most one live intent is allowed per bead.
  - A repeated identical choice is idempotent; a conflicting one is refused.
- **Apply contract.**
  - The applier claims each intent under a row lock and re-validates it against the tracker's
    current state.
  - It closes the bead with a reason that carries the intent id.
  - A claimed intent whose outcome is unknown after a crash is reconciled against tracker state
    before any retry, so the tracker effect happens at most once.
  - Failures keep a categorical reason.
- **Attention-ledger routing.**
  - A deterministic Switchboard job offers each eligible decision to the owner as one Telegram
    prompt.
  - The prompt is subject to quiet hours and the context bus, a rolling daily budget, and
    at-most-once prompt delivery using the confirmed / not-attempted / rejected / uncertain
    transport vocabulary.
  - Every branch records an attention-ledger row.
  - Routing ships disabled and is enabled per deployment.
- **Telegram one-tap close.**
  - A new `notify.v1` intent, `decision_request`, carries one signed `choose` affordance per
    option. It reuses the RFC 0021 primitives:
    - `reply_markup` inline keyboards;
    - the `APPROVAL_CALLBACK_SECRET` HMAC;
    - owner-channel verification;
    - connector callback ingestion over the narrowly scoped connector credential.
  - A tap records an intent and edits the prompt to its recorded state.
- **Dashboard.**
  - `POST /api/decisions/{bead_id}/intent` records an owner intent.
  - Two connector-scoped prompt routes serve the callback.
  - `GET /api/decisions` items carry the recorded intent state.
  - The Decisions detail renders that state read-only.

## Capabilities

### New Capabilities

- `owner-decision-desk`: decision intents, the apply contract, attention-ledger routing of
  decision prompts, and Telegram one-tap capture.

### Modified Capabilities

- `beads-projection`: adds the interim `butlers-dev` tracker bridge requirement, which amends
  `REQ-beads-projection-001`'s "runtime has no tracker capability" boundary for exactly one
  management workload.
- `dashboard-api`: adds the decision-intent endpoints and the additive `intent` field on
  `GET /api/decisions`.
- `dashboard-decisions`: adds read-only rendering of a recorded intent's state.

## Impact

- Code:
  - `roster/switchboard/migrations` (decision prompt and intent tables);
  - `src/butlers/core/decision_desk.py` (intent store);
  - `src/butlers/jobs/decision_routing.py`;
  - `src/butlers/api/routers/decisions.py`;
  - `src/butlers/core/approval_callbacks.py` (shared signer);
  - `src/butlers/connectors/telegram_bot.py`;
  - the `notify.v1` contract and Messenger routing;
  - `scripts/beads_decision_applier.py`;
  - `frontend/src/pages/DecisionsPage.tsx`.
- Platform:
  - `Dockerfile.beads` (pinned `bd`);
  - `scripts/k8s/build-push.sh`;
  - the chart's beads CronJob, ExternalSecret and NetworkPolicy;
  - `values.dev.yaml` enables the bridge.
- Credentials:
  - a dedicated Dolt user with grants on the `butlers` database only;
  - BWS dev keys `BUTLERS_RUNTIME_BEADS_DOLT_USER` / `BUTLERS_RUNTIME_BEADS_DOLT_PASSWORD`;
  - no value is committed or logged.
- Not changed:
  - Beads/Dolt stays the sole tracker authority.
  - The RFC 0025 projection plan, `GET /api/beads/{id}`, approval semantics and RFC 0021 tokens
    are unchanged.
  - No production deployment exists.
