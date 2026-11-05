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
| `ledger.py` | SQL over the sw_047 goal ledger, its sw_057 labels and sw_059 owner-law holds; one goal row lock per transaction |
| `service.py` | `Broker`: authorize, sign, forward or park, settle; approved execution; Stop cancellation |
| `assertions.py` | Ed25519 `X-Butlers-Assertion` signer and the pure verifier domains wire in |
| `domains.py` | The `Domains` seam and `ApprovalsParking`, the in-process park/cancel adapter |
| `server.py` | MCP streamable HTTP goal endpoint at `/goals/{goal_id}/mcp`, `BrokerSwitch`, `session_app` |
| `typed.py` | M7: the one adopted closed typed shape, exact value validation, the held typed-value relaxation |
| `boundaries.py` | M7: SQL over the sw_058 handles, extraction cache and child results; the `Extractor` seam |
| `m7_report.py` | M7: content-blind gate report and the exact held owner decisions |

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
   Then apply the owner law ([Owner law](#owner-law-s1-bu-dhloi110)), which can
   only refuse or turn a dispatch into a per-event park.
4. Check the budget (exhaustion commits `budget_exhausted` on the goal), refuse a
   repeat of a live, committed or unknown effect with the same capability and
   argument digest (returning that effect's id), insert the effect row
   (`dispatched`, or `requested` when it will park) and charge one call.

The forward happens after commit, signed with the D7 field list. Tools declaring
`idempotency = key_required` receive the `effect_id` as the idempotency key. The
broker retries once only on a `not_received` receipt; any other transport
failure settles the effect `unknown`. A committed receipt's output label is
joined into the calling invocation in the settling transaction, before the
result reaches the model (see [Invocation labels](#invocation-labels-s1-bu-dhloi16)).

## Invocation labels (S1, bu-dhloi1.6)

Policy reads the calling invocation's own S1 label (`Principal.label`, from
`sw_057`), never the goal's union: `Principal.exposed` is the label's exposure
(or a legacy Boolean, which sw_057 keeps equal to it). A clean sibling keeps
dispatching after another sibling reads untrusted content. An unreadable,
missing or unknown label refuses `authentication_refused`. The label is not
added to the signed assertion; the assertion's `exposed` is the invocation's.
`butlers.core.goal_labels` is the Python grammar; sw_057 CHECKs are the store's.

Every producer, deferred path and reader of a label:

| Path | Label effect | Where |
| --- | --- | --- |
| Admission | admitted label from `admitted_exposed` (unknown or owner), joined with a causing effect's label | sw_057 `goal_label_goal`; `goal_admission.effect_exposed` |
| Invocation start | admitted label; a child joins its caller; a replacement lead joins every earlier lead and recorded effect (`child` / `recovery`) | sw_057 `goal_label_invocation` |
| Broker read | committed output joins `{owner}` (trusted) or `{world}` (untrusted) plus the capability's categories; stamped output, see D6 stores (`read`) | `BrokerLedger.settle` |
| Child result | the lead joins its child's label (`child`) | `Broker.read_child_result` |
| Joined reply | inbox row = owner ∪ answered effect's label ∪ unknown for outside content; the reader joins it on consumption (`inbox`) | sw_057 `goal_label_inbox`; `GoalLedger.deliver`; `Broker.consume_inbox` |
| Effect | snapshots its requesting label at insert; immutable | sw_057 `goal_label_effect` |
| Park, approval, edit, retry | the approved call runs under the requesting invocation's current label, which must cover the effect's snapshot; an edited effect row snapshots that label | `BrokerLedger.begin_approved` |
| Receipt / unknown / not_received | no output join unless committed; the effect label never changes | `Broker._forward` |
| Goal union | every invocation and inbox label joins it; reporting only | sw_057 `goal_invocation_exposure` |
| D6 stores | an exposed write keeps the writer pointer (`taint_goal_id`, `taint_invocation_id`) and content authority, not a per-row label. A stamped read joins `{owner}`, the capability's categories and, for each tainted record, its writer invocation's persisted label resolved through the pointer. A missing, dangling or unreadable pointer, or no complete stamp account from the store, joins `{world}` and all six | `DomainReceipt.stamped_owner_untainted` / `stamped_writers`; `BrokerLedger.writer_label` |
| Reply evidence | a reply to a prompt of an exposed goal is outside content (goal union; conservative) | `accepted_source.py` `_reply` |

Stored labels resolve through the writer pointer (lead decision on
bu-dhloi1.6): no memory, relationship or `public` store gains label columns.
Unstamped legacy records keep their content-authority behaviour (original
authority preserved, or unknown). No domain reports its stamp account over the
wire yet, so `HttpForwarder` receipts make every stamped read the unknown label
until one does.

Until the per-tool `categories` declaration lands and is made required
(bu-dhloi1.27, a switch-release blocker), categories are derived from the descriptor's D4
`output_sensitivity` (`contracts.declared_categories`): `ordinary` is an
explicit owner-catalog declaration of no sensitive category and carries none;
anything undeclared or not `ordinary` carries all six.
Concurrent joins are single-statement unions under the row lock, so neither
principal nor category is lost.

`switchboard.invocation_exposure_measurements` (owner-only view) counts
invocations, effects and approval-routed effects by role, `exposure_cause`, the
retained pre-switch gate (`goal_union_exposed`: the goal aggregate sw_047 used)
and the switched gate (`invocation_exposed`). Rows with the first true and the
second false are the invocations the switch stopped restricting. There is no
target value; it is evidence for the rollout.

Typed boundaries and containment are built (next section) but dark; no
typed-value or cited-evidence relaxation is adopted.

## Typed boundaries (M7, bu-4c7nvp.1.2)

Built dark and unconditional on the owner's typed-value choice: they constrain
what reaches a model, never what a value may do. Every row below joins the
label it reads; none narrows one.

| Boundary | Behaviour | Where |
| --- | --- | --- |
| Quarantine | With `quarantine_reads` on, a committed result whose label would newly expose the reader (exposed I/D the reader does not hold) is stored in `goal_handles` with that full label, writers included, and the model gets only `{"handle": <id>}`. A reader already holding the label, or content with no canonical form, takes the ordinary join. Categories alone never quarantine | `Broker._settle_holding` |
| `handle.read` | Free-text dereference: joins the handle's label (cause `read`) before the content is returned. Only a handle of the caller's goal, by exact id; anything else is `resource_outside_grant` | `Broker.read_handle` |
| Extract | The injected `Extractor` sees only the handle's content and the schema. The schema must be the closed shape and may not hold handle slots (content never selects the next dereference); the output must match exactly or the call refuses and nothing is cached. The caller joins the handle's whole label | `Broker.extract` |
| Extraction cache | Keyed by content digest, source label, schema digest and extractor identity; no caller identity. A hit joins the cached (source) label into each reader | `extraction_cache` |
| `goal.return` | Only a child, only once, only a value matching the result schema the runner fixed at launch (`fix_child_result_schema`); handle values must be handles of the same goal. Stores the result and ends the child | `Broker.goal_return` |
| `goal.await` | The lead's own child: status, result (when returned) and effect statuses from the ledger. Every await joins the child's label (cause `child`), since all of it reflects the child's behaviour | `Broker.goal_await` |
| Contain | Owner D13 action under dashboard step-up; later envelopes from the source are record-only. See [goal ledger and admission](goal-ledger-and-admission.md#contained-sources-m7-bu-4c7nvp12) | `core.source_containment` |

The adopted shape (`typed.parse_schema`) is an object of at most 16 named
slots, each a boolean, a bounded integer or number, a token from a bounded enum
(identifier characters only), a calendar date, a UTC instant (`...Z`), an
opaque same-goal handle, or a bounded unique array (`maxItems` <= 32,
`uniqueItems`) of one of those. A free or patterned string, URL, hostname,
address, UUID selector (recipient, device or resource), nested object, `$ref`,
description, default or any unknown keyword refuses the whole schema.

`typed.TYPED_VALUE_RELAXATION` is `None` and `typed.typed_value_label` returns
the source label unchanged: with the typed-value decision held on
bu-4c7nvp.1.1, a typed date or amount carries its source's exposure into the
next effect, which therefore keeps the under-exposure policy. Recipient,
device and resource selectors are not typed values at all.
`m7_report.m7_report` (owner connection) returns the exposure and
approval-cause rows of `invocation_exposure_measurements`, sw_058 store counts,
the exact typed-value question and the cited-label hold (bu-7ry1sy);
`release_gate_satisfied()` is false until an adopted decision replaces the
held constant.

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
become `cancelled` (an `action_cancelled` event; owner prompts are fenced
with the `action_cancelled` delivery reason, admitted by `approvals_022`, so
`action_rejected` still means only an owner rejection) and
approved, never-executed ones become `abandoned`. An action whose effect was
dispatched or settled is never touched (D14).

## Owner law (S1, bu-dhloi1.10)

The owner can hold calls with a tighten-only overlay (REQ-capability-authorization-020).
`butlers.core.owner_policy` is the reader and the one writer; storage is
`sw_059`'s `switchboard.owner_policy_*`.

A term is `never` or `always_ask`. It selects by one of three things: an exact
catalog capability identity, a region (the catalog `owner_domain`) or an effect
class. It can be narrowed by resource arguments. It is matched against the
current catalog on every call, so a later capability in a selected region or
class is held too. A narrowing may name only resource arguments the current
catalog declares for the selected capabilities; any other key is refused when
the term is written (`owner_policy_invalid`).

Canonicalisation cannot make an aliasable identifier safe. Calendar's
`calendar_id: "primary"` reaches the owner's calendar, so a deny on
`owner@gmail.com` would be released by `primary`. A narrowing is therefore
admitted only on params the catalog declares `alias_free_params` for every
selected capability ([MCP model](../concepts/mcp-model.md)). Any other
narrowing is refused at write with 422 `owner_policy_narrowing_unsupported`. At
match time, a stored term narrowed on a param the current catalog no longer
marks alias-free for the called capability holds every call of that capability.
A domain alias-resolver seam would let aliasable params narrow later; it is not
built.

Identifiers are compared in canonical form, both when
stored and when matched: a UUID in any spelling becomes its lowercase
hyphenated form, and any other identifier is NFKC-normalised, trimmed and
case-folded. A term is a deny-list, so matching fails closed. A narrowed term
holds a call in any of these cases:

- the call omits the narrowed argument;
- the argument is an empty list;
- the argument is a non-string or has no canonical form;
- the argument names a held resource in any spelling.

The term is lifted only when every identifier the call names is canonical and
outside the narrowing.

The constraint order is catalog/origin grant, then exposure/category/slot (step
2 and `decide`), then owner law, then health/admission (budget and duplicate in
`reserve`). The broker has no mode stage, and none is invented.

- `never` refuses `policy_refused`.
- `always_ask` turns the decision into a per-event park: no standing rule
  applies, and the exact call is parked. Reads cannot park yet (a read has no
  pending action), so on a `read` it refuses with its own reason,
  `owner_policy_ask_on_read`, which sw_059 adds to the `broker_refusals` reason
  set; the owner can tell it apart from `never`.
- Neither grants. An ungranted, out-of-scope, transact or otherwise refused call
  stays refused for its own reason, before the law is read.
- A park the domain cannot hold (`approvals_enabled` false) refuses
  `broker_unavailable`.

The broker reads the singleton `owner_policy_state.generation` `FOR SHARE` in
the goal-locked call transaction. A writer takes it `FOR UPDATE`, so a change
lands before a call or after its effect fence, never between. The next call,
including the next one in the same invocation, sees it. `execute_approved`
re-reads the law before dispatch, so a `never` added after a park refuses the
approved call. The owner's approval stays recorded (`approved`); a committed
effect is never described as undone.

A missing state row, an unreadable term or a read failure refuses
`broker_unavailable`. Expiry is judged on the database clock and drops only its
own term.

Every held call writes one content-blind row to `owner_policy_holds`. The
disposition is `refused` (linked to its `broker_refusals` row and, at execution,
the effect) or `parked` (the effect id, which is also the pending action id).
The row also carries the holding constraint, the policy generation and every
holding term as `{id, version, term, selector_kind, selector}`, `never` first.
Its typed receipt `{stage: owner_law, constraint, generation, terms}` is exactly
`BrokerRefused.hold` / `PolicyHold.receipt()`; the owner read serializes it
identically for the refusal, the effect and its pending action
(`test_the_owner_read_and_the_broker_receipts_serialize_identically`). The
session sees only the closed reason.

Changes go through expected-version CAS, with one receipt per owner action id:
a retry returns that receipt, and a different change under the same id
conflicts. `tighten` admits only a tightening: `always_ask` to `never`, or a
later or no expiry. Anything else is a loosening (including an earlier expiry,
which can lapse a term now) and goes through `loosen` under the owner's
`policy_loosen` dashboard step-up (bu-dhloi1.29): `loosen_binding` binds the
owner action, term, expected version, resulting constraint and expiry, the
term row and the policy generation, so a confirmation is spent once, on that
exact change, and a later policy change makes it stale. Without an admitted
step-up the change refuses `dashboard_step_up_required` and writes nothing; its
receipt has action `loosen`. sw_060's term trigger refuses any loosening whose
resulting `id:version:term:expiry` no step-up consumed in the same transaction.
An expiry still lapses its term.

Mutation paths. This is the whole set, pinned by
`tests/contracts/test_owner_policy_mutation_paths.py`.

| Path | Writes | Guard |
| --- | --- | --- |
| `POST /api/owner-policy/terms`, `POST /api/owner-policy/terms/{id}/tighten` | terms, receipts, generation via `OwnerPolicyWriter` | verified owner dashboard session (`owner_dashboard_session_required`); tighten-only CAS |
| `POST /api/owner-policy/step-up/options`, `POST /api/owner-policy/terms/{id}/loosen` | the same, via `OwnerPolicyWriter.loosen` | verified owner dashboard session plus a `policy_loosen` step-up (`X-Dashboard-Step-Up`) bound to the exact change; CAS |
| `Broker` ledger `record_hold` | holds | runtime role, insert-only |
| Switchboard daemon role (broker, every MCP tool, connector callbacks) | nothing else | sw_059 RLS: terms and generation read-only, receipts invisible |
| Direct SQL, any role | — | triggers refuse DELETE/TRUNCATE everywhere, UPDATE of receipts/holds, and any term UPDATE that skips a version, rewrites the selector, reuses the owner action, or loosens without a matching `policy_loosen` step-up consumed in its transaction |

`GET /api/owner-policy` lists every term with its version, expiry and whether it
is active, plus the generation. `GET /api/owner-policy/holds` takes exactly
one of `goal_id`, `effect_id` (also the pending action id) or `refusal_id` and
returns those holds with their typed receipts. Both need the dashboard session.
Not here: `/status` and owner cards rendering the receipt (bu-dhloi1.13, S6
surfaces), and the dashboard UI for the loosen ceremony.

## Refusal records and the goal filter (bu-m1u7zy.12)

A refused `tools/call` rolls back its own transaction, so `Broker.call` then
writes one row to `switchboard.broker_refusals` (`sw_056`) in a transaction of
its own: the closed reason, the requested capability, the effect id a
duplicate refusal names, and the goal and invocation only when the bearer token
resolved. The capability is kept only when the live catalog publishes it;
anything else, including any session-chosen text, is stored as the fixed
`<unpublished>` marker, and a CHECK admits nothing else. Nothing else from the
call is stored. The runtime role can insert and read. A statement-level trigger
refuses every UPDATE, DELETE and TRUNCATE with SQLSTATE 42501, for the runtime
role and the owner alike. The trigger is
the guard, not the table grants, because core_001 and init-db.sql replays
re-grant own-schema DML on every table. If the record cannot be written, the
session still gets the same refusal, and the log line is still emitted.

`GET /api/approvals/actions?goal_id=<uuid>` lists the actions one goal parked.
Each action carries a `goal` block (goal, invocation, depth, exposure, origin),
which is null for actions no goal parked.

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
- The M7 goal tools on the MCP endpoint (`handle.read`, extract, `goal.return`,
  `goal.await`), a production `Extractor`, `quarantine_reads` and an owner
  Contain route (the step-up interface admits `contain` since bu-dhloi1.29).
