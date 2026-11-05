# Owner delivery: subjects, presentations and single-use action tokens

> **Status:** source-complete and **dark**. Everything here is inert until
> `public.owner_delivery_rollout.enabled` is set (default `false`). Contract: RFC 0038 S6,
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
| `owner_delivery_receipts` | append-only history: opened, presented, sent, send_failed, send_unknown, acknowledged, decided, closed, refused, retracted, retract_failed, bridged | append-only |
| `owner_delivery_source_map` | old-store id → subject (and presentation) | append-only |
| `owner_delivery_rollout` | singleton `enabled` flag | readable by runtimes; no runtime write policy |

Subject states: `pending → delivered/unknown → acknowledged → decided/cancelled/expired`.
Row security is ENABLE (not FORCE), as in core_303: the table owner (dashboard, migrations,
backups) bypasses it and a butler role sees only rows whose `owning_schema` is its own. RLS is
not the only fence: the triggers above apply to every role, and every runtime query also
filters on `current_schema()`.

## Tokens

`od1:` + base64url of 32 random bytes (47 bytes, inside Telegram's 64-byte callback limit).
Only `sha256(nonce)` is stored; digests are compared in constant time; tokens, nonces and
provider ids never enter logs, receipts or the stored outcome (`{action, generation, won}`).
A token is not authentication: the caller authenticates first, and the **surface is derived
from how the request authenticated**, never from the body.

Redemption (`butlers.core.owner_delivery.redeem`) runs inside the transaction that already
holds the authority row's lock, in this order:

1. lock token, lock subject; the subject must be the one named by the locked authority row
   in the current schema (`subject_mismatch`);
2. owner (`wrong_owner`) and surface (`wrong_surface`) are checked before any replay answer;
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
| `POST /actions {token}` | connector (surface `telegram`, needs `X-Butlers-Decision-Actor: owner@telegram`) or owner browser session (surface `dashboard`) | Redeems through the normal approve/deny path (audit, dispatch). `200` with the action on a win or a replay (a replay dispatches nothing); `409 {"error":"owner_delivery_refused","reason":…}` otherwise. |
| `GET /retractions` | connector only | This deployment's stale Telegram cards: `{presentation_id, route_ref, provider_ref}`. |
| `POST /retractions/{presentation_id} {retracted, reason?}` | connector only | Receipts one retraction attempt. `retracted=false` keeps the card listed; it never claims success. |

## Delivery path

With the flag on, the RFC 0023 worker stays the transport queue: for a single card,
`ApprovalDeliveryRepository.prepare_owner_delivery` opens the subject and the Telegram
presentation under the claim's action lock, maps the RFC 0023 intent/presentation in
`owner_delivery_source_map`, and returns fresh tokens that replace the `apr1` buttons. The
handoff marker moves the presentation to `unknown` in the same transaction; the handoff
result settles it (`confirmed → sent` with the provider message id, `safe_retry → failed`,
ambiguous stays `unknown` and never marks the subject delivered). A presentation that already
left `pending` mints nothing, so a retry renders Open only rather than a second live card.

The dashboard dossier is a presentation too (`present_on_dashboard`); every render is a
delivery into the owner's authenticated response and carries its own tokens. Wiring the
dashboard UI to `POST /actions` is follow-up work; until then the dashboard keeps its
displayed-call path, whose decision still closes the subject.

The Telegram connector handles `od1:` taps before `apr1:` (primary owner channel only),
posts the token to `/actions`, edits the tapped card, then drains `/retractions`.

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

Rollback: disable the flag. Verifiers, receipts and source maps stay; consumed and stale
tokens stay unusable; core_317's downgrade refuses while any subject exists.
