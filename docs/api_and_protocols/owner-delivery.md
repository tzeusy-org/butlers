# Owner delivery: subjects, presentations and single-use action tokens

> **Status:** source-complete and **dark**. No token is minted until
> `public.owner_delivery_rollout.enabled` is set (default `false`); see
> [Flag semantics](#flag-semantics). Contract: RFC 0038 S6,
> `openspec/changes/generalist-lead-focused-delegation/specs/owner-delivery/spec.md`
> (REQ-owner-delivery-001..004). Bead: bu-dhloi1.13.

Owner delivery gives every item that needs the owner one canonical **subject**, rendered as
one **presentation** per surface (Telegram card, dashboard dossier), each carrying
single-use **action tokens**. Whichever action wins (approve, reject, or an acknowledgement)
bumps the subject's generation in the same transaction, so every other token on every other
surface is stale. Retraction of the other cards is then attempted and receipted. A failed
retraction leaves only harmless stale UI.

The subject is **not** an authority. For an approval it references the existing
`pending_actions` row (which also decides a parked goal effect), and the decision still runs
through `transition_pending_action` in the same transaction as the token redemption.

## Model

| Table (`public`, core_317) | Holds | Invariants (trigger-enforced, owner included) |
| --- | --- | --- |
| `owner_delivery_subjects` | one row per `(owning_schema, kind, source_ref)`: owner entity, content and action digests, `generation`, `state`, typed `urgency_class` / `holding_reason` slots | identity and digests immutable; generation never decreases; closed state edges; resolved rows frozen; `decision` set only when `decided` |
| `owner_delivery_presentations` | one row per `(subject, surface, generation)` (`retry_key`), route, provider ref, `pending/sent/failed/unknown/retracted` | inserted only at an open subject's current generation; provider ref set once; a provider-proven `failed` send may return to `pending` (its tokens are revoked) |
| `owner_delivery_tokens` | SHA-256 **verifier** only, plus the bindings: subject, presentation, action, action digest, owner, surface, generation, expiry; `consumed_at` + closed `outcome` | minted only at the current generation of an open subject; bindings immutable; consumed once |
| `owner_delivery_receipts` | append-only history: opened, presented, sent, send_failed, send_unknown, acknowledged, decided, closed, refused, retracted, retract_failed, bridged | append-only; a presentation is referenced by `(presentation_id, subject_id)`, so it must belong to the same subject |
| `owner_delivery_source_map` | old-store id → subject (and presentation) | append-only; same composite presentation reference |
| `owner_delivery_rollout` | singleton `enabled` flag | readable by runtimes; no runtime write policy |

Subject states: `pending → delivered/unknown → acknowledged → decided/cancelled/expired`.
`decided_surface` is set only on `decided`; an acknowledged subject that later expires or is
cancelled clears it.
Row security is ENABLE (not FORCE), as in core_303: the table owner (the migration login, see
below) bypasses it and a butler role sees only rows whose `owning_schema` is its own. RLS is
not the only fence: the triggers above apply to every role, the listing and lookup queries
also filter on `current_schema()`, and a statement-level `BEFORE TRUNCATE` trigger on each of
the five ledger tables refuses truncation (`42501`) for every role, owner included (the
core_287 / core_293 precedent).

Which role runs what matters here. A butler daemon's pool is `butler_<schema>_rw` under row
security. The dashboard API's pools log in as `POSTGRES_USER` and never `SET ROLE`
(`init_db_manager`). Row security therefore does not apply to the API only if that login owns
these tables. That is inferred from the shared migration login, not yet observed on a deployed
ledger, so it has to be checked at rollout.

- Daemon only: worker delivery and the handoff (`prepare_owner_delivery`, `mark_handoff_started`,
  `complete_handoff`).
- API only: `redeem`, `pending_retractions`, `record_retraction` and the dashboard card.
- Both: closing a subject (`close_for_authority`, through `transition_pending_action`). The
  daemon's approval paths call it, and so does the API, including its expiry sweep.
- No production path: acknowledgement. The API passes only approve or reject, and
  `/api/owner-delivery/actions` refuses an ack token as `action_unsupported`.

In the API the schema fence is code: `_locate` and the retraction queries filter on
`current_schema()`, the retraction report checks ownership before calling `record_retraction`,
and `redeem` refuses a subject from another schema. Most functions that take a subject or
presentation id address rows by that id alone, so a new caller on an owner pool must make the
same ownership check first:

| Function | Today's callers and why the id is already this schema's |
| --- | --- |
| `record_retraction` | API; the route's ownership check runs first in the same transaction |
| `present` | Daemon, under row security, and the API dashboard card; both with a subject just opened for the locked action in `current_schema()` |
| `append_receipt` | The ledger functions listed here, after they resolve the subject, and the bridge's `prepare_telegram_presentation`; also `bridge_existing`, below |
| `_retraction_targets` | `redeem`, after its schema check, and `close_for_authority`, whose update filters on `current_schema()` |
| `begin_send`, `lock_subject_of` | Daemon, under row security; the id comes from `owner_presentation_for`, which filters on `current_schema()` |
| `record_send` | Daemon, under row security; same source of id |
| `delivery_inputs`, `record_holding_reason`, `bridge_existing` | No production caller yet; a first caller must supply the check |

Lock order (`owner_delivery.LOCK_ORDER`): the subject, then the action's RFC 0023 delivery
rows, then the subject's presentations and tokens. A path that also needs the authority row
(`pending_actions`) takes it before all of them. Every path that touches both stores takes the
subject before an RFC 0023 row:

- decisions: `transition_pending_action` holds the action and closes the subject before it
  locks RFC 0023 rows;
- the handoff marker: `mark_handoff_started` locks the action, then the subject;
- the handoff result: `complete_handoff` locks a bridged card's subject only. It never takes
  the action row, because the executor holds that row across the tool call and a result must
  not stall the sequential worker behind it.

A writer that starts from a presentation or token reads its `subject_id` unlocked, locks the
subject, then the child. So a handoff result, a send settlement and a token decision on one
card queue, never deadlock.

Three kinds of path stay out of that chain. `defer_pending_action` takes the action, then the
RFC 0023 rows, and never the subject (its already-expired branch is a decision and does).
`heartbeat` and `record_prestart_retry` touch one RFC 0023 presentation row, so they hold nothing
else while waiting on it. `claim_next` also takes one row, with `SKIP LOCKED`, so it never waits.
`cancel_ineligible_presentations` takes no action, subject or intent lock either. It can update
several presentation rows in one statement, but it skips a locked row and takes it on a later
tick, so it waits on nothing.
`cancel_goal_effects` decides its actions in id order, whatever order it is handed them.
`tests/integration/test_owner_delivery_lock_order.py` forces these interleavings for single-card
paths. It does not force cohort cards, which take several actions in id order.

## Tokens

`od1:` + base64url of 32 random bytes (47 bytes, inside Telegram's 64-byte callback limit).
Only `sha256(nonce)` is stored; digests are compared in constant time; tokens, nonces and
provider ids never enter logs, receipts or the stored outcome (`{action, generation, won}`).
A token is not authentication: the caller authenticates first, and the **surface is derived
from how the request authenticated**, never from the body.

Redemption (`butlers.core.owner_delivery.redeem`) runs inside the transaction that already
holds the authority row's lock, in this order:

1. lock subject, lock token; the subject must be the one named by the locked authority row
   in the current schema (`subject_mismatch`);
2. owner (`wrong_owner`) and surface (`wrong_surface`) are checked before any replay answer,
   then the token's action must be the caller's `expected_action` (`action_mismatch`), so an
   acknowledgement token can never reach approve or deny even without the router's filter;
3. a consumed token answers `duplicate` and acts again never; a revoked one refuses;
4. expiry, open state (`subject_resolved`), generation (`stale_generation`) and the exact
   action digest — token, subject and the currently locked call must all match
   (`action_changed`);
5. subject CAS (generation + 1), then the owning decision; a refusal by the authority raises
   and rolls everything back; consume the token; receipt.

Every other path that takes an approval out of `pending` (legacy `apr1` tap, dashboard
approve/deny, expiry, cancellation, broker) calls `close_for_authority` from
`transition_pending_action`, so it stales every token too.

## HTTP routes

All under `/api/owner-delivery`; the connector callback credential
(`X-Butlers-Approval-Callback-Token`) reaches exactly these three plus the approval callback
routes (`_is_approval_callback_route`).

| Route | Who | Behaviour |
| --- | --- | --- |
| `POST /actions {token}` | connector (surface `telegram`, needs `X-Butlers-Decision-Actor: owner@telegram`) or owner browser session (surface `dashboard`) | Redeems through the normal approve/deny path (audit, dispatch). `200` with the action on a win or a replay (a replay dispatches nothing); `409 {"error":"owner_delivery_refused","reason":…}` otherwise. The dashboard must echo the `displayed_call` it showed to approve (`409` if it differs, or if the call is open-only, before the token is touched) and may add a denial `reason`; the connector may not. |
| `GET /retractions` | connector only | This deployment's stale Telegram cards: `{presentation_id, route_ref, provider_ref}`. A failed card returns after a 15-minute cooldown, and not at all after 5 failed attempts. |
| `POST /retractions/{presentation_id} {retracted, reason?}` | connector only | Receipts one retraction attempt. `retracted=false` never claims success; the card stays eligible within the bound above. A live card (open subject, current generation) is refused with `409 presentation_live`. |

## Delivery path

With the flag on, the RFC 0023 worker stays the transport queue: for a single card,
`ApprovalDeliveryRepository.prepare_owner_delivery` opens the subject and the Telegram
presentation under the claim's action lock, maps the RFC 0023 intent/presentation in
`owner_delivery_source_map`, and returns fresh tokens that replace the `apr1` buttons. The
handoff marker moves the presentation to `unknown` in the same transaction; the handoff
result settles it (`confirmed → sent` with the provider message id, `safe_retry → failed`,
ambiguous stays `unknown` and never marks the subject delivered). A presentation that already
left `pending` mints nothing, so a retry renders Open only rather than a second live card.

The dashboard dossier is a presentation too: for a pending action, `GET /api/approvals/{id}`
from the owner's session returns `owner_delivery: {subject_id, generation, approve_token,
reject_token}` (null while off, for connector reads and review sources, or on any failure).
Every render is a delivery into the owner's authenticated response and carries its own
tokens. The dossier's and the review dialog's Approve, and the dossier's Deny, post those tokens to
`POST /actions` when the detail carries them; a refusal is shown and the dossier re-read, never
retried on another route. With no tokens (rollout off, or a read that failed closed) they use
the displayed-call routes, whose decision still closes the subject. So do the one-tap Deny
rows on summary surfaces, which never load a dossier.

The Telegram connector handles `od1:` taps before `apr1:` (primary owner channel only),
posts the token to `/actions`, edits the tapped card, then drains `/retractions`. It also
drains on a timer (`CONNECTOR_OWNER_DELIVERY_SWEEP_INTERVAL_S`, default 60 seconds, polling and
webhook modes), so a card staled by a dashboard decision, expiry or cancellation is retracted
with no tap. One drain runs at a time. Telegram's "message is not modified" counts as retracted,
because the card already shows that end state; any other refusal is `provider_error` and is logged
with the presentation id, as is a refused listing or receipt with its status. Every attempt is
receipted: a card given up after 5
failures keeps its state and receipts, and its buttons stay harmless because its tokens are
stale.

## Later-owned inputs (not active here)

`owner_delivery.delivery_inputs(conn, subject_id)` exposes the typed inputs later lanes read:
`urgency_class` (bu-dhloi1.22; no value is admissible yet), `holding_reason` (the broker's
owner-policy hold receipt `{stage, terms, generation}`, written with `record_holding_reason`;
rendering it on /status and cards is bu-dhloi1.10's surface half), and the current
presentations. Presence-aware surface selection, Why doors and push (bu-4c7nvp.1.8,
bu-dhloi1.24) consume these; nothing here chooses among surfaces.

## RFC 0023 successor map and cutover

| RFC 0023 store | Owner-delivery successor |
| --- | --- |
| `pending_actions.id` | subject `(kind='approval', source_ref)`; source map `approval_action` |
| `approval_delivery_intents.id` | source map `rfc0023_intent` |
| `approval_delivery_presentations (id, generation)` | the Telegram presentation (`legacy_ref`); source map `rfc0023_presentation` |
| `approval_delivery_attempts` | receipts (`sent/send_failed/send_unknown`); source map `rfc0023_attempt` |
| presentation `args_digest` | subject `action_digest` (same `args_digest`) |
| `apr1` HMAC callback | `od1` single-use token |

State map for backfilled cards: `ready/claimed/retry_wait → pending`,
`handoff_started/ambiguous → unknown`, `delivered → sent`,
`cancelled/superseded/collapsed → retracted`.

Cutover (operator, separately authorized; not executed by this change), per schema:

1. `owner_delivery_bridge.bridge_existing(conn, owner_recipient=…)` with the trusted owner
   route: opens a subject per pending delivered action and maps its RFC 0023 rows. It mints
   no token; a backfilled card keeps its `apr1` buttons, whose decision closes the subject.
   Idempotent.
2. `owner_delivery_bridge.parity(conn)` must report `ok` (every subject agrees with its
   action; every delivered pending card is mapped).
3. Enable `public.owner_delivery_rollout`. From then on new cards carry only `od1` tokens,
   so this is the sole token writer.

Rollback: disable the flag (see below). Verifiers, receipts and source maps stay; consumed
and stale tokens stay unusable; core_317's downgrade refuses while any subject exists.

## Flag semantics

The rollout flag governs **minting only**:

- off: `prepare_owner_delivery` and the dashboard dossier mint nothing; new cards carry
  `apr1` buttons as before.
- tokens already minted stay redeemable after the flag is turned off, so live `od1` cards are
  not stranded. Their decisions still run through `transition_pending_action` with the same
  checks.
- the retraction drain and its timer (`GET/POST /retractions`) run regardless of the flag, so
  stale cards from an enabled period are still retracted after a rollback. With no ledger rows
  the listing is empty, so a deployment that never enabled the flag retracts nothing.
- with the flag off the dossier carries no tokens, so the dashboard decides on its
  displayed-call routes.

To stop `od1` decisions entirely, the subject must close (decide, expire or cancel the
action), which stales its tokens; turning the flag off is not a kill switch for live cards.
