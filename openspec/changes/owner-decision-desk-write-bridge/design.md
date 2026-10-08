## Context

- The Decision Desk read side (`bu-ckkpz.1`, `.2` and `.4`) reads a JSONL export of the Dolt
  tracker.
- No runtime component can write the tracker, and on `butlers-dev` the export itself is disabled.
- The 2026-10-08 owner ruling (`bu-sng0tu`, `bu-ckkpz.3` notes) allows a Dolt credential and
  tracker reachability in `butlers-dev`, confined to one management workload.

### Facts verified on 2026-10-08 (dev)

| Question | Answer |
| --- | --- |
| Tracker endpoint | `dolt.<tailnet>:3307`, a dedicated tailnet address, database `butlers`, Dolt 2.3.1 |
| Pod reachability | A throwaway `butlers-dev` pod resolves the host and opens TCP 3307 |
| Auth | `root@%` has no password and full grants. Any tailnet peer, including every pod in the namespace, can write the tracker. |
| Tracker schema | `local_metadata.bd_version` = `bd_version_max` = `1.0.4`; 66 `schema_migrations` rows. `bd` 1.3.1 clients read and write it daily without migrating it. |
| `bd export` workspace | Needs a `.beads/metadata.json` (`backend: dolt`, `dolt_mode: server`, `dolt_database`), pointed at with `BEADS_DIR`. The host and port come from the env. |
| `bd` env names (1.3.1) | `BEADS_DOLT_SERVER_HOST`, `BEADS_DOLT_SERVER_PORT`, `BEADS_DOLT_SERVER_USER`, `BEADS_DOLT_PASSWORD`, `BEADS_DOLT_SERVER_TLS`. The database comes from `metadata.json`. |
| Least-privilege user | A user granted `SELECT, INSERT, UPDATE, DELETE, EXECUTE, CREATE TEMPORARY TABLES, LOCK TABLES` on `butlers.*` only can export, show, create and close beads. |
| Egress isolation | A NetworkPolicy allowing `0.0.0.0/0 except <dolt>/32` plus all pods blocks TCP 3307 to the tracker and still allows Postgres, Telegram, OTLP, the registry and in-namespace services. Removing it restores reachability. |

## Goals / Non-Goals

**Goals**

- One tap (Telegram) or one owner API call records a choice.
- The choice is applied to the tracker at most once.
- Its state is honest everywhere: Postgres, the API, the dashboard and the Telegram message.
- Tracker capability is confined to one workload, enforced by configuration and by network
  policy.
- Decision prompts respect quiet hours, the context bus and a daily budget, and every branch is
  ledgered.

**Non-Goals**

- Replacing the RFC 0025 projection.
- Making the dashboard lane a mutation surface. Choice controls in the browser are a follow-up.
- Applying a decision's default at its deadline.
- Re-prompting after a prompt was sent.
- Production deployment.

## Decisions

### D1. One management workload: apply, then export

- The existing CronJob `butlers-beads-export` runs `beads_cycle.sh` in a dedicated image
  (`Dockerfile.beads`). The cycle:
  - writes the scratch workspace;
  - runs the applier;
  - runs the existing export script;
  - fails the Job if either step failed, after both have run.
- Only this pod receives:
  - `BEADS_DOLT_*`;
  - the `<release>-beads-dolt` Secret, which is synced by its own ExternalSecret and not by the
    shared `<release>-bws` Secret that runtime pods consume;
  - the egress allowance to the tracker.
- It also gets the runtime Postgres env (`POSTGRES_*` from `<release>-bws`). That widens nothing:
  every runtime pod already holds that credential.
- Rejected: an HTTP applier API on `dashboard-api`. It would need a new bearer credential, and the
  applier would still need the tracker.
- Rejected: a sibling CronJob. Two pods holding the tracker credential would be two trust points,
  and their export and apply cadences could interleave.
- `bd` is pinned to 1.3.1 by release-tarball SHA-256: it is the version the agent fleet uses
  against this tracker today. The tracker's own stamp is 1.0.4 / 66 migrations, and 1.3.1 does
  not migrate it.
- An upgrade is a reviewed change to `BD_VERSION`/`BD_SHA256`.
- The image is small (`python:3.12-slim`, `asyncpg` pinned to the lockfile version, the applier
  script, the shell scripts). It carries no Butlers package code.

### D2. Network enforcement

- `<release>-tracker-egress` selects every pod whose `app.kubernetes.io/component` is not
  `beads-export`. Its egress rules allow:
  - all pods in all namespaces;
  - `0.0.0.0/0` except `beadsExport.doltEgressCidr`.
- The CIDR is site-specific. `scripts/k8s/site-helm-args.sh` resolves it from
  `BEADS_DOLT_SERVER_HOST`, following the existing site-env pattern.
- With the passwordless tracker root, this policy, not the credential, is what makes "one
  workload" true.
- Setting a root password on the tracker is an owner follow-up outside this namespace.

### D3. Intent store (Switchboard schema)

- `switchboard.decision_prompts`: one row per bead.
  - Holds the snapshot of options/default offered, `created_at` (the HMAC binding) and the
    delivery outcome.
- `switchboard.decision_intents`:
  - `status` is `pending | applying | applied | failed`;
  - `failure_reason` and `last_error` are categorical;
  - `attempts` and the timestamps are recorded;
  - a partial unique index allows at most one row per bead in `pending | applying | applied`.
- Recording an intent validates against the live digest: the bead must be an open decision with
  available structured details, and the option must be one of its options.
  - An identical repeat returns the existing row (`created: false`).
  - A different option conflicts.
  - A `failed` intent does not block a new one.
- Both writers, the owner route and the connector-scoped prompt route, call one store function.

### D4. Apply contract

The applier runs as a single process because the CronJob uses `concurrencyPolicy: Forbid`.

1. Reconcile every `applying` row left by an earlier run.
   - `bd show` is the evidence. A close reason containing `decision-intent <uuid>` means
     `applied`; any other close means `failed`/`bead_closed_elsewhere`; a bead still open means
     the row returns to `pending`.
   - A crash between claim and close therefore never double-applies and never strands an intent.
2. Claim each `pending` row (oldest first, bounded batch) with
   `UPDATE ... WHERE status='pending'` and commit it before any tracker call.
3. `bd show --json` re-validates the bead:
   - it exists;
   - it is open;
   - it carries the `decision` label;
   - the option is still offered.

   Each violation is a terminal `failed` with a categorical reason (`bead_not_found`,
   `bead_not_open`, `not_a_decision`, `option_not_offered`).
4. `bd close <id> --reason "Decision: <option> (decision-intent <uuid>, via <source>)"` runs with
   `BD_ACTOR=butlers-decision-desk`.
   - Success, or a re-read showing our marker, means `applied`.
   - A tracker or `bd` failure returns the row to `pending` with `last_error`.
     - `bd_close_failed` becomes terminal after three attempts.
     - `tracker_unavailable` (the show itself failed) does not consume the attempt budget and
       stops the batch.
5. Then export, so the lane drops the decision.

### D5. Attention-ledger routing

- Switchboard job `decision_routing` runs every 15 minutes, a deterministic template with no LLM.
- It does nothing unless `BUTLERS_DECISION_ROUTING_ENABLED=1`. Dev ships it off until the owner
  enables real Telegram prompts.
- Candidates:
  - open decisions with structured details;
  - no prompt row and no live intent;
  - escalated decisions first, then oldest.
- Gates, in order:
  1. Quiet hours / context bus (`_check_suppression`) produce `deferred`, `quiet_hours` or
     `context_bus:*`.
  2. The budget, 3 prompts per rolling 24 hours counting delivered, uncertain and in-flight
     prompts, produces `deferred`, `budget_exhausted`.
  - Each gate records at most one ledger row per bead per 12 hours (deferrals) via the ledger's
    `dedup_key = decision_prompt:<bead>`.
- Delivery reserves the prompt row first: `INSERT ... ON CONFLICT DO UPDATE ... WHERE
  delivery_outcome = 'not_attempted'`. Only a proven not-attempted prompt is ever re-sent.
- The Switchboard `deliver` transport vocabulary maps onto the prompt outcome:

  | Transport | Prompt outcome | Ledger row |
  | --- | --- | --- |
  | `confirmed` | `delivered` | `delivered` |
  | `not_attempted` | `not_attempted` (retried next tick) | `failed` |
  | `rejected` | `rejected` | `failed` |
  | `uncertain` or unknown | `uncertain` | `failed`, `delivery_uncertain` |

- A reservation still in flight after 10 minutes becomes `uncertain`; it is never resent.
- The callback secret and owner recipient come from the daemon's bound `ApprovalPushRuntime`,
  the same authority the approval push uses. A missing one is `not_attempted`.

### D6. Telegram one-tap

- `notify.v1` gains `intent: decision_request`.
- `actions` carries one `choose` affordance per option, at most 16 (the projection's option bound), each
  with a `label` (at most 64 characters) and a `dsk1` callback token, plus one `open_dashboard`.
- Messenger:
  - verifies the recipient is a verified owner channel;
  - renders one button row per option and a dashboard URL row;
  - non-Telegram channels get the text and the link;
  - deferred-notification coalescing never folds it.
- Token: `dsk1:<prompt uuid>:<option index>:<hmac16>`, at most 61 bytes.
  - The HMAC uses `APPROVAL_CALLBACK_SECRET` over `dsk1:<uuid>:<index>:<prompt created_at>`. The
    prefix domain-separates it from `apr1`.
  - `approval_callbacks` exposes the shared signer; it is not reimplemented.
- The connector checks `dsk1:` before the generic acknowledgement:
  1. parse;
  2. verify the owner is the primary owner channel;
  3. `GET /api/decisions/prompts/{id}`;
  4. verify the HMAC against the prompt's `created_at`;
  5. `POST /api/decisions/prompts/{id}/choose {option_index}`;
  6. answer the callback with the outcome;
  7. on a recorded choice, edit the message to the recorded state and remove the keyboard.
- The callback is answered after the choose call, not before, so the toast states the real
  outcome. Telegram allows one answer per callback.
- Both routes join the connector-credential allowlist, and only those exact routes. Choose also
  requires actor `owner@telegram`, so the dashboard owner records through the owner route only.
- Toasts:
  - a malformed or invalid token, a non-owner or the prompt missing: generic acknowledgement,
    nothing changes;
  - a recorded choice: "Choice recorded." (a repeat of the same choice: "Already handled.");
  - another live choice, or the decision closed: "Already handled.", keyboard removed;
  - changed options: "Options changed; open the dashboard.", keyboard removed;
  - the API unreachable: a retry hint, keyboard kept.

## Risks / Trade-offs

- Passwordless tracker root: mitigated in-namespace by D2. Tailnet-wide exposure stays an owner
  follow-up.
- RWO export PVC, now written by a pod that also applies: single-node scheduling remains a
  precondition, as already documented.
- Export lag between apply and lane refresh: bounded by the CronJob schedule (dev: 5 minutes).
  The lane shows `applied` from Postgres in the meantime.
- Prompts sent but never answered stay on the dashboard and in the weekly digest. Re-prompting
  needs its own design.

## Migration Plan

1. Merge, then build and push the app, frontend and `butlers-beads` images. Migration `sw_042`
   runs in the `dashboard-api` initContainer.
2. `values.dev.yaml` enables `beadsExport` and the egress policy. Roll back with
   `beadsExport.enabled=false`; the intent tables stay, unused.
3. Enable routing with `BUTLERS_DECISION_ROUTING_ENABLED=1` only after owner consent to live
   prompts.
