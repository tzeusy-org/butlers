# Capability broker

The RFC 0038 capability broker is the only path from a goal session to a domain
capability. It is built and tested but dark: nothing constructs a `Broker`, and
the control plane's session listener keeps serving `session_refusal_app` until
the M6 switch release turns `BrokerSwitch` on.

Code lives in `src/butlers/capability_broker/`, beside `src/butlers/control.py`
(the `butlers-control` process that will host it):

| Module | Owns |
| --- | --- |
| `contracts.py` | Closed refusal reasons, 256-bit invocation tokens (hash only persisted), `Principal`, the broker's `Capability` projection of a published D4 descriptor |
| `catalog.py` | Per-call read of the published fleet catalog on the call's own connection |
| `recipients.py` | Owner rule 2: classify each `recipient_params` party with the core-notify owner-resolution rule |
| `policy.py` | Dispatch, park or refuse from effect class, exposure, park-only, approval mode and approval policy |
| `ledger.py` | SQL over the sw_047 goal ledger; one goal row lock per transaction |
| `service.py` | `Broker`: authorize, sign, forward or park, settle; approved execution; Stop cancellation |
| `assertions.py` | Ed25519 `X-Butlers-Assertion` signer and the pure verifier domains wire in |
| `domains.py` | The `Domains` seam and `ApprovalsParking`, the in-process park/cancel adapter |
| `server.py` | MCP streamable HTTP goal endpoint at `/goals/{goal_id}/mcp`, `BrokerSwitch`, `session_app` |

`src/butlers/core/capability_json.py` holds the one RFC 8785 canonicalizer and
the golden vectors (`capability_digest_vectors.json`, `golden_vectors()`) that
the broker and domain verification both test against.

## Per-call transaction (D7)

Each `tools/call` runs in one transaction that first takes the goal row
`FOR UPDATE`, the order Stop uses, so a call commits before a Stop or is refused
after it, and concurrent calls of one goal serialise (two children racing the
last unit of budget: exactly one wins).

1. Resolve the bearer token (SHA-256 of 32 random bytes) to one `running`
   invocation of a `running` goal with a live lease. A lead's `lead_epoch` must
   be the goal's newest; a child's token survives lead replacement.
2. Read the current catalog. A digest different from the goal's pinned
   `catalog_digest` refuses until the catalog matches again. The capability must
   be in the grant and listable (never `transact`; never `administer` for a
   child), and every restricted resource argument must name granted
   identifiers. A selection-bound parameter without a grant restriction has an
   empty scope; today's catalog declares none.
3. Classify recipients and decide (`policy.decide`). Refusals contact no domain.
4. Check the budget (exhaustion commits `budget_exhausted` on the goal), refuse a
   repeat of a live, committed or unknown effect with the same capability and
   argument digest (returning that effect's id), insert the effect row
   (`dispatched`, or `requested` when it will park) and charge one call.

The forward happens after commit, signed with the D7 field list. Tools declaring
`idempotency = key_required` receive the `effect_id` as the idempotency key. The
broker retries once only on a `not_received` receipt; any other transport
failure settles the effect `unknown`. Untrusted output, and stamped output the
domain does not vouch for, exposes the invocation (and so the goal).

## Recipients and owner rules

A party is the owner only when it is the UUID of the single live, unmerged,
undeleted owner entity, or an email address that `resolve_owner_channel_via_definer`
resolves to that entity. Anything else, including ambiguous, unknown and
undeterminable parties, is non-owner. A call naming nobody is owner
communication only through notify's declared Telegram send/insight branch.
Unrecognised forms can only over-park.

## Parking and approved execution (D13)

Parking goes through the owning domain's approvals module
(`butlers.modules.approvals.goal_park.park_goal_effect`). The pending action's id
is the effect id; it records the exact arguments and the goal lineage (goal,
invocation, depth, exposure, goal origin). The broker passes whether a standing
rule may apply: never for park-only grants, `always_per_event` tools,
`administer`, or non-safe actuation. Rules are admitted by the approval gate's
own path (`gate.admit_standing_rule`: owner-permission admission for fact rules,
every safety argument pinned). A tool whose rule approval must freeze its
arguments (`relationship_assert_fact`) always parks, because the freeze would
rewrite the digested arguments. For an exposed lineage the rule itself must
qualify: it exactly pins every recipient argument of a call to the owner, or it
exactly pins every supplied argument (at least one); typed-slot templates are
not yet recognised and so park. A matching rule approves at once and the broker
runs the stored call.

`Broker.execute_approved` is the trusted owner-decision entry: it re-checks that
the goal has not ended, the grant and the catalog, then dispatches the stored
call, or the owner's edit. An edit cannot change the immutable effect digest, so
the original effect becomes `rejected` and the edited call gets its own effect
row under the same approval reference. An approval for an ended goal cancels
the effect and its pending action instead.

After Stop commits, `Broker.cancel_parked` cancels every effect that has not
reached a domain, and ends exactly those effects' domain actions: pending ones
become `cancelled` (an `action_cancelled` event, owner prompts fenced) and
approved, never-executed ones become `abandoned`. An action whose effect was
dispatched or settled is never touched (D14).

## Fail closed

Without a signing key, catalog source or domain seam the broker refuses every
call (`broker_unavailable`); so does any ledger or catalog read failure. The
signing key loads once from a 0400 file owned by the process whose public half
must equal the configured verification key for its `kid`.

## Not yet wired

- The HTTP forwarder to domains and domain-side assertion verification
  (bu-pkuny2); until then `ApprovalsParking` has no dispatch transport.
- The runner (token issuance on launch, goal tools, Stop itself) and the M6
  switch that constructs the broker.
- Marking the domain's pending action executed when the broker dispatches it,
  and syncing approval expiry back to the effect.
