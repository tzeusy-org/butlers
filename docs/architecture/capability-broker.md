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
| `recipients.py` | Owner rule 2: classify each `recipient_params` party (UUID, email, Telegram, WhatsApp) with the core-notify owner-resolution rule |
| `policy.py` | Dispatch, park or refuse from effect class, exposure, park-only, approval mode and approval policy |
| `actuation.py` | RFC 0028 risk map adapter: the tier of an `actuate` call (`ha_call_service`, `ha_activate_scene`) |
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
undeleted owner entity, or a channel identifier that the core-notify rule
(`resolve_owner_channel_via_definer`, with notify's channel-to-identity map
`ROUTED_COMMUNICATION_CHANNEL_IDENTITY_TYPES`) resolves to that entity. The
channel comes from notify's `channel` argument (Telegram when it names neither a
channel nor an entity) or from a transport tool's own name (`telegram_*`: chat
id or username; `whatsapp_*`: individual JID; `email_*`: address); any other
party list resolves only UUIDs and email addresses. Broker authorization is
exact. The definer matches a WhatsApp JID's digits against stored phones by
the custody suffix rule (lengths within two digits), so the broker also
requires the exact JID handle, or the full number exactly as stored
(`resolve_owner_triple('has-phone', …)`), to be the owner's. A lookalike or
2-digit-off number is non-owner and parks; core notify keeps the definer's
rule. A bare phone number, another channel's identifier, an identifier two
live entities share, and a failed lookup are non-owner, as are all
unknown and undeterminable parties. A call naming nobody is owner
communication only through notify's declared Telegram send/insight branch.
Unrecognised forms can only over-park.

## Actuation risk (RFC 0028)

An `actuate` call's tier comes from the Home butler's RFC 0028 map
(`roster/home/modules/actuation.py`, read through `actuation.rfc0028_actuation_risk`,
the `Broker` default). `ha_call_service` is classified by its domain, service,
target and data, and `ha_activate_scene` as `scene.turn_on`. Safe and reversible
calls inside the automatic subset are `safe` and follow the tool's approval
policy (dispatch, or a rule-eligible park when the tool is gated or names a
non-owner party). Consequential and protected calls, including any service the
map does not declare, park for per-event approval. A capability outside the
map, or a call whose arguments the map cannot read, is `unknown` and parks the
same way. The broker has no entity unit cache, so climate set-points park.
Today's catalog declares `ha_call_service` recipients undeterminable, so even
a safe call parks rule-eligible instead of dispatching.

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

`Broker.execute_approved` first commits the owner's approval on its own
(`parked -> approved`, D11), so the decision survives a refused or interrupted
dispatch; it then begins dispatch in a second transaction. The settled outcome
is written back onto the domain action (`executed` with the receipt, or
`approved` with a failed `execution_result`), and `Broker.sync_parked` ends a
parked effect whose domain action was rejected, expired or cancelled. Migration
`approvals_021` marks pre-cutover actions `goal_origin = 'legacy'`.

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

## Forwarding and domain verification (D7, bu-pkuny2)

`forwarder.HttpForwarder` sends each dispatched call as one MCP `tools/call` to
the owning domain's endpoint with exactly one `X-Butlers-Assertion` header. A
session that never opened is `not_received` (the one D12 retry); a result is
`committed`; an error result, including a refused assertion, is `failed`;
anything after the call was sent propagates and the effect becomes `unknown`.

On the domain, `verification.AssertionVerificationMiddleware` is the outermost
FastMCP middleware, so it runs before argument coercion, before the core tool
wrapper and before any handler. It refuses (`assertion_refused`, which never
says which check failed) a missing or repeated header, a forged signature, an
unknown `kid`, a wrong `aud`, an expired assertion, a capability other than the
called tool, an `args_digest` other than the digest of the raw wire arguments
(so coercion cannot launder a difference), and a `call_id` seen within twice
the assertion lifetime. Replay across a restart: the seen set is in memory, so
an assertion issued before the process started is refused; D12 effect
idempotency makes the one broker retry that costs safe.

Keys come from a `version: 1` verifier keyring at
`/run/secrets/capability_broker_assertion_verifiers`, parsed by the
runtime-probe keyring codec (one current entry, at most one retiring). A domain
with no valid keyring refuses every call; there is no unsigned or shared-secret
fallback.

The verified lineage is bound (`core.goal_lineage`) for exactly that call and
replaces the spawner-derived context:

- the core tool wrapper's first step refuses, while enforcement is on, any
  call with no lineage bound for its tool. The refusal is raised as a
  `ToolError`, so it reaches the broker as an MCP error result and the effect
  is recorded `failed`, never `committed`;
- `get_approval_execution_context` (RFC 0028's executor binding) and the
  RFC 0021 replay seam treat a lineage whose `approval_ref` covers the exact
  tool and argument digest, in the task it was verified in, as the approval;
  it carries no session id;
- the domain approval gate runs such a call once instead of parking it again,
  after checking the domain's own action is `approved`, unsettled and for this
  goal and tool;
- memory authority is `third_party` when exposed or third-party, `owner` for
  unexposed owner lineage and `system` for obligation and staffer lineage; an
  explicit owner-class authority, or an owner-class fact report, under exposure
  is refused;
- an exposed write stamps `taint_goal_id` / `taint_invocation_id` on every D6
  store: memory episodes, facts and rules (`mem_017`; contact notes and
  decision memory are facts), relationship entity facts (`rel_039`), captures,
  conversation history (`public.dashboard_messages`) and insight candidates
  (`core_313`). Goal outcomes have no store yet. The shared `public` writers
  name the taint columns only for an exposed write, so an unexposed insert is
  the pre-switch statement (pinned-revision and stand-in schemas predate
  `core_313`). Read-back, per store:

  | D6 store | Columns from | Read back by |
  | --- | --- | --- |
  | memory episodes, facts, rules | `mem_017` | memory search and recall (`SELECT *`) |
  | contact notes (memory facts, `contact_note`) | `mem_017` | `relationship.notes.note_list` |
  | decision memory (memory facts) | `mem_017` | memory search and recall |
  | relationship entity facts | `rel_039` | `relationship_lookup` identity facts |
  | narrative facts (memory facts) | `mem_017` | `relationship_lookup` narrative facts |
  | captures | `core_313` | `CaptureService` rows (`SELECT *` / `RETURNING *`) |
  | conversation history | `core_313` | `butlers.api.conversations` message readers and `ConversationMessage` |
  | insight candidates | `core_313` | `GET /api/switchboard/insights` and the delivery-cycle read |

  The shared `public` readers take the stamp with `goal_lineage.taint_read`
  (`to_jsonb(<row>) ->> …`), so a schema before `core_313` reads null instead
  of failing;
- `public.domain_events` rows record the producing call's `goal_id`,
  `goal_invocation_id`, `goal_effect_id` and `goal_exposed` (`core_313`), read
  back by `get_event` and `list_events`;
- audit writes made inside a verified handler can read the same lineage
  (`goal_lineage.event_lineage()`); stamping audit rows is bu-5arfj.

`lifecycle` calls `attach_domain_verification` with the default-off
`DomainVerificationSwitch`; off, nothing is installed and the runtime is
unchanged.

## Not yet wired

- The runner (token issuance on launch, goal tools, Stop itself) and the M6
  switch that constructs the broker and turns domain verification on.
- The service-plane tool inventory that domain verification exempts.
