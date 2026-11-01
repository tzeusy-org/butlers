# Signed informational confirms

The signed-confirm implementation is opt-in infrastructure. Normal daemon/API startup
keeps it disabled until the canonical goal-runtime switch installs its trusted composition.
It does not activate the Chronicler migration or remove the older `cgi:` path.

## Custody and authority

`ConfirmAuthorityTopology` binds code-owned source verifiers to separate kernel-peer MCP
endpoints. A source verifier reloads its owning domain state, exact resolved recipient and
authorized effect. Model arguments are data; they cannot choose a producer, sink, goal,
recipient authority or endpoint. `attach_notify()` installs a daemon's private producer
client only for the topology lifetime. Its session identifier is correlation, never proof.

Conversational creation requires a dispatched `messenger.notify` effect with
an exact `effect_digest(producer, prompt)`, matching recipient and accepting Telegram
binding. Repeated producer requests return their existing immutable row, including after
expiry or effect completion. The digest includes the question, ordered options, card, sender/chat, entity,
owner classification and timeout. The effect's deadline becomes the immutable confirm
deadline. A delivery reference can back at most one confirm. The canonical broker owns
authorization and effect/receipt lifecycle; an existing unrelated delivered message is
not sufficient authority. No live broker adapter is installed by this change.

Deterministic sources register one fixed sink and closed option vocabulary. Application
runs through its protected domain endpoint. The domain must atomically commit its mutation
and a receipt keyed by `confirm_id` in its own transaction. Switchboard stores the accepted
choice and application obligation, and retries that same key after loss or restart. A sink
that cannot provide this contract must not be registered.

The service refuses superuser, bypass-RLS, table-owner and unrelated connections. Its
actual connection must be `butler_switchboard_rw` or `butler_messenger_rw`. The public
coordination table has forced RLS; bootstrap's broad grants do not admit connector roles.
The backup script separately preserves its durable history under owner-scoped policy
proof and fail-closed restore custody; see [backup and restore](../operations/backup-restore.md).

## Callback and owner surfaces

`cfm1:<uuid>:<index>:<hmac>` signs the exact option position and destination chat with a
domain-separated HMAC. The connector verifies before forwarding any lookup request.
`POST /api/confirms/callback` accepts only the existing internal callback credential under
the exact `X-Butlers-Confirm-Callback-Token` header. This narrowly scoped route does not
install dashboard owner authority. Approval tokens and existing callbacks retain their
separate handlers.

The resolver verifies HMAC again, locks the stored row, matches exact sender and chat,
and checks authoritative database time after lock acquisition. Owner-targeted Telegram
prompts additionally verify the current primary owner and entity through the identity
definer. Dashboard answers require ordinary owner authentication/CSRF and use the same
resolver. Acceptance records its server-verified channel provenance in that transaction;
queue delays or later Telegram unlinking cannot rewrite who supplied the answer.

`GET /api/confirms` returns owner cards with a stable `before` cursor and `meta.next_cursor`
so older pending/retrying questions remain reachable and `POST /api/confirms/{id}/answer` records an
option index. Without installed composition these routes return an explicit 503. The
Notifications page polls pending application state and distinguishes accepted, applied,
failed/retrying and expired receipts. Both surfaces use the persisted card; assistant
claims are labelled and quoted input stays literal/inert.

## Recovery and admission

Messenger rechecks shared owner quiet/sleep policy at delivery. An unavailable policy
retains pending work; a producer callback cannot override it. Every attempted reconciliation, including a failed edit or unavailable policy, rotates behind
other work without changing their immutable identity.

A committed `sending` boundary precedes any provider call. A lost or ambiguous initial
Telegram send never causes another send. Only explicit evidence that no attempt occurred
permits retry. Rendering retries edit the known message, atomically remove its keyboard,
and serialize with application updates. They cannot reapply an answer. An exact Telegram
already-current receipt confirms an idempotent edit; other failures remain retryable.

The worker expires unanswered rows, leases bounded application attempts and reconciles
terminal rendering. Each row is attempted at most once in a tick, so a failing sink cannot
starve other answers. Timeout without a declared option never invents a domain choice.
Conversational timeout enters admission as system record-only evidence, not owner speech.

`ConfirmEvidence` reloads accepted provenance and immutable question/option content.
The question is a structural data segment; a verified owner's chosen value is owner input.
Non-owner values remain data and exposed. `GoalAdmission.admit_submission` alone applies
F3: an accepting live prompt can join its goal; terminal/nonjoinable goals receive new
owner intent with inherited exposure. No pinned routing, approval executor or grant
creation is part of confirm resolution.

## Verification and activation

Tests exercise the actual connector through mounted API authentication, restricted-role
migrated PostgreSQL races/restart and goal admission, kernel-peer MCP boundaries, provider
ambiguity/edit receipts, dashboard behavior and backup/restore. PostgreSQL evidence must
come from a real migrated database; mocked SQL is not migration proof.

Live emission remains dark. Chronicler's `bu-jjy9p` also retains its existing decision-loop
closeout dependency. Completing transport infrastructure does not authorize a live message,
remove that gate, deploy the stack or declare the original phone interaction repaired.
