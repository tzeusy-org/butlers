# Goal ledger and admission

RFC0038's first control-plane slice is additive and dark: nothing in ingress,
scheduling, the daemon, a module, or an API calls it. `GoalAdmission` accepts a
durable `SourceRef` and loads evidence through an injected trusted source reader.
It is not a public request model, authentication adapter, runner, broker or Stop
implementation. Actual producer integration and runtime execution remain future
work. Project references and budget pools belong to separate milestones.

`goal_admission.py` owns deterministic ceilings/exposure and the transaction;
`goal_ledger.py` owns qualified SQL. The injected `CatalogProvider` returns a verified current immutable `CatalogView`
publication in the transaction or refuses. The view is a pinned protocol snapshot.
Its publisher must validate the
real catalog's complete authorization metadata and exclude private custody and
owner-decision capabilities. Fixtures prove that protocol's consumption, not
real catalog publication. Contracts and catalog cannot be loaded from prompt
text. A later adapter must use RFC0017 verified owner-channel authorization or
the authenticated dashboard principal; successfully resolved unknown senders
are third party, while an unavailable verifier returns a typed refusal.

## Durable records

All seven tables live in Switchboard's schema:

| Table | Retained invariant |
| --- | --- |
| `goals` | Unique normalized source identity; immutable origin, admitted exposure and admitted label, ceiling and policy digests; monotone reporting label union and exposure; `thread_id` bound at most once; same-goal cause/effect FK; D11 state |
| `invocations` | Same-goal parent which is a lead; depth 0/1 shape; one launching/running lead; immutable grants/scopes and finite inert turn/token caps; monotone S1 label and derived exposure (starts at the admitted label joined with its caller or predecessors), once-set `exposure_cause`; timing placeholders and token hash only |
| `effects` | Same-goal invocation FK; capability/argument digests and D11 state; immutable requesting label; exact unique delivery, verified recipient and finite prompt expiry |
| `budgets` | One per goal; explicit finite maxima, reserved/used counters and child occupancy; sums bounded by maxima; absolute deadline and config snapshot |
| `admission_decisions` | Append-only attempts; at most one accepted association per source; fixed refusal reasons, immutable quota window/retry-after, delivery/effect and valid cause references |
| `admission_attempt_bindings` | Every returned server attempt maps immutably to its canonical decision and exact source, including a retry which reuses another attempt's accepted decision; composite FK, no extra goal/budget/decision |
| `goal_inbox` | One row per joined reply: its decision, source, delivery, same-goal prompt effect and immutable label; immutable except a once-set `consumed_at`; never deleted by the runtime |

No message body, arguments, results, bearer token, approval permission or
executable predicate is stored here. Telemetry consumers must use identifiers
and digests only. A refusal creates no goal or budget. Downgrade refuses any
persisted goal or decision history rather than discarding evidence.

## Authority and failure

Owner messages derive broad non-transact ceilings. Third-party goals begin
exposed and omit owner-authority records, actuation and administration; ordinary
communication is park-only except owner notify. An obligation requires a genuine
declaration, defaults to its owning domain's read/record plus owner notify, and
may narrow to exact declared grants. Missing creation exposure lineage is
exposed. Domain/feed inputs also need the publishing domain's typed untainted
owner-authority stamp to start unexposed. Runtime-created obligations intersect
the creator invocation's immutable grant. QA staffers always start exposed.

Staffer contract version 1 identifies staffer, owning domain, Git declaration
digest and pinned catalog digest, with a closed list of exact capability IDs,
exact resource ID sets and park-only restrictions. Missing/stale contracts,
unknown/duplicate/excluded capabilities and wildcard constraints refuse. All
ceilings exclude transact; admission never bypasses later approval policy.
Caused goals inherit the causing effect's label, intersect the declaration
with that goal's origin ceiling, and persist valid cause references even when
the configured chain limit (default two) refuses further admission.

The fresh `switchboard.runtime_config.goal_admission_config` snapshot requires
an explicit version, all four origins exactly once, finite origin/source COUNT
quotas and fixed UTC windows, plus each origin's complete finite goal/invocation
limits. NULL, malformed or unreadable configuration cannot fall back to a cache.
No numeric launch defaults are seeded. Each admitted decision records the exact
captured window charged; waiting across a boundary never changes that window.
Only new admitted goals consume slots; joins/refusals do not. Different origins
cannot consume owner capacity. Repeated attempts return their exact decision;
after a retryable refusal, a new server attempt reuses the same durable source.
SW050 persists a binding before returning any deduped decision in that same
transaction. S/B followed by S/A therefore binds both attempts to the same
decision; T/A refuses even on a fresh connection. Historical canonical attempts
are backfilled from their actual decisions. Earlier unrecorded losing retries
cannot be reconstructed and receive no invented historical binding. Migration
050 descends from delivered 047; separately held 048/049 require reparenting
after its delivery. Downgrade refuses to discard any attempt binding.

Lock order is dedupe attempt/source keys, all referenced goals in sorted order,
then effect rows, then sorted aggregate origin/window and source/window quota
keys. Database constraints and unique indexes remain final race authority.
Future Stop, broker and budget mutators must share goal-before-effect ordering
and hold the relevant budget row while changing its counters. No such runtime
mutators are implemented here.

An owner reply joins only an exact server-held delivery/effect bound to the
verified owner entity/channel and a live goal whose prompt accepts input. Expiry
is checked after lock waits. Joining preserves origin, ceiling, cause and budget;
the goal's reporting union can only grow, no running invocation is exposed by
the join itself, and no invocation or approval is produced. The join writes one
labelled `goal_inbox` row in the same transaction; that row is the goal's only
delivery path for the reply, and the M2 runner consumes it through
`Broker.consume_inbox`, which joins its label into the reader. A `waiting`
goal (settle envelope) is not live for joins (there is no waiting -> running edge yet, so
nothing could consume its inbox row). A reply to a waiting, terminal or expired prompt creates
an ordinary verified owner goal with delivery/effect
reference and no caused-goal link. Unknown/foreign/mismatched bindings follow
ordinary message admission without guessing continuity.

### Contained sources (M7, bu-4c7nvp.1.2)

`core.source_containment.contain_source` is the owner's D13 Contain for one
admission `source_partition`. It runs only under a verified dashboard step-up
(bu-dhloi1.5) bound to kind `contain`, the partition digest and the current
containment state, consumed atomically with the `sw_058`
`switchboard.source_containments` insert. Without an admitted consumption it
writes nothing and returns the open-dashboard handoff. The trusted step-up
interface admits `contain` (bu-dhloi1.29); `contain_binding` derives the exact
binding the owner confirms, and a real step-up is spent once with the insert
(`tests/integration/test_owner_stepup_consumers.py`). No route constructs a
caller yet, so Contain stays dark.

A containment is permanent evidence: it records the goals already descended
from the partition (admitted or joined from it, then goals caused by those,
transitively) and changes none of them. After it commits, every new envelope
from the partition takes the terminal `recorded_without_goal` decision: no
goal, inbox row or wake, and replay returns that decision. There is no release.
Admission takes the per-partition lock (`contain:<partition>`) after loading
its evidence and before any decision; Contain takes the same lock before
reading descendants, so an envelope either committed first (and is a
descendant) or is recorded without a goal. Descendants follow admission and
cause lineage: S1 labels carry no per-source principal yet.

Database errors, failed decision persistence and cancellation roll back partial
writes. Commit uncertainty raises `AdmissionUnavailable`; retrying the same
durable source resolves any committed association without replenishing budget.

### Owner audio (bu-dhloi1.20, REQ-audio-authorship-001..003)

A transcript is connector-produced text, so an owner sender alone does not make
it owner input (RFC0038 D5/D18). The accepted-source reader
(`roster/switchboard/tools/ingestion/audio_admission.py`) recognises speech-derived text
from the retained provider update (a caption-less Telegram voice/audio/video note
whose text is not the connector's descriptor) or from any transcript claim, never
from the words. Such text keeps owner authorship only when the producer's keyed
`payload.audio_authorship` binding (`butlers.audio_authorship`) verifies under the
server-held `AUDIO_AUTHORSHIP_KEY` the trusted reader is constructed with, and
every bound fact matches the accepted row: channel, endpoint, original
idempotency key and event id, sender, the update's provider media identity, a
transform declared instruction-free (`ASR_TRANSFORMS`; only the Wyoming ASR
protocol qualifies, generic HTTP/WebSocket STT does not), the exact stored text,
a `transcribed` outcome, and an update with no forward, quote, external reply,
inline-bot (`via_bot`) origin or connector span. Otherwise the owner stays the enclosing sender, the whole
transcript is fenced as data and the goal starts exposed, so its admitted label
is the unknown label. Non-owner senders stay third-party whatever their binding
says. `payload.transcription` is outcome metadata only.

A duplicate or replay of the same original identity resolves to the accepted
row, so a later binding can neither upgrade authorship nor lower exposure. An
STT failure keeps the connector's descriptor and named status, with no invented
words. Dashboard recordings have no ingress yet: any transcript claim on a
dashboard source fails closed. Without the key (the dark default) no transcript
verifies, so owner audio admits exposed.

## Isolation and verification

RFC0038 D6 tracks exposure per invocation. Since `sw_057` (bu-dhloi1.6, S1
first switch, REQ-capability-authorization-019) every goal, invocation, effect
and inbox row carries a version 1 label (`label_instr`, `label_data`,
`label_sens`) and `exposed` is derived from it:

- a goal keeps an immutable admitted label (D5's `admitted_exposed`, joined with
  the causing effect's label for a caused admission, never the causing goal's
  union) and a monotone reporting union; call policy never reads the union;
- an invocation starts from the admitted label, joined with its caller (a
  child) or with every earlier lead and recorded effect (a replacement lead).
  A clean sibling is not contaminated; `exposure_cause` records why an
  invocation first became exposed (`ingress`, `read`, `child`, `inbox`,
  `recovery`, `legacy_unknown`);
- an effect snapshots its requesting label, so park, approval, edit and retry
  never narrow it; an inbox row carries the owner reply joined with the
  answered effect's label, plus the unknown label for forwarded outside content;
- labels only grow; a Boolean-only exposure joins the unknown label (`{world}`,
  all six categories); an unknown principal or category is refused by CHECK;
- invocation and effect rows are durable: a statement-level trigger refuses
  DELETE and TRUNCATE (SQLSTATE 42501) for the runtime role and the owner, and
  their runtime DELETE policies admit nothing, so a replacement lead's
  recovery context cannot be erased first;
- the runtime role holds TRIGGER (core_001 re-grants it), so every label rule
  also runs as an AFTER ROW constraint trigger on the final row: an insert must
  cover the label it inherits, and an update cannot narrow or rewrite a fixed
  label, whatever a later-sorting BEFORE trigger did;
- legacy rows keep their restriction: Boolean exposure becomes `{world}` or
  `{owner}`, every legacy row assumes all six categories, and the downgrade
  refuses while any goal exists.

`sw_059` (bu-dhloi1.10, REQ-capability-authorization-020) adds the owner's
law (`never` / `always_ask` terms) under the same non-FORCE RLS model; `sw_060`
(bu-dhloi1.29) admits a loosening only under a `policy_loosen` dashboard
step-up consumed in the same transaction. The runtime may only read the terms and the generation and append
holds. Owner changes run on the trusted owner connection from the dashboard,
behind its verified session. The broker reads the generation `FOR SHARE`
inside the goal-locked call transaction. See
[capability broker: owner law](capability-broker.md#owner-law-s1-bu-dhloi110).

The waiting lifecycle with its owner thread (bu-dhloi1.7) awaits its RFC
amendment; until then `status` keeps the D11 set and `thread_id` is an
unreferenced, bind-once column.

`sw_047` uses enabled **non-FORCE RLS** with exact `current_user` Switchboard
runtime policies, following `sw_035`. The existing trusted migration/table owner
retains ordinary administration and `row_security=off` backup authority. No new
owner, definer, public SELECT, BYPASS, runtime fallback or backup exclusion is
introduced. The service checks its actual role and rejects superuser/BYPASS or
membership in a ledger table owner before doing admission. All other butler,
connector and Chronicler runtimes must be unable to assume a ledger owner.

`tests/switchboard/test_goal_admission.py` covers deterministic boundary cases.
The PostgreSQL and migration owners exercise real migrated core+Switchboard
constraints, source/attempt dedupe, last-slot and fixed-window races, reply/Stop
serialization, rollback/cancellation, bootstrap role denial and the existing full
backup/restore scripts with exact row/owner/policy/constraint parity. Collection,
static checks or synthetic pinned catalog fixtures do not prove those database
outcomes. Activation still requires real PostgreSQL execution and independent
review, then the later catalog/producer/runtime milestones.


## Conditional owner-notify catalog eligibility

The native `core.notifications` declaration carries the closed typed recipient
branch `OwnerNotifyBranch.TELEGRAM_SEND_INSIGHT_WITHOUT_PARTY`, serialized as
`recipient_params.owner_notify_branch=telegram_send_insight_without_party`.
It describes the existing Telegram send/insight fallback with no explicit
recipient, entity or inherited request context. It does not resolve a target,
make links inert, authorize an invocation or certify a call as owner-directed.

The verified fleet reader preserves this branch only for the complete,
grantable, matching-domain native notify declaration with its exact recipient
selector schema. Its existing admission projection's `owner_default` flag
means conditional eligibility: default obligations may include notify, and a
third-party ceiling need not make every possible notify call park-only. Actual
calls still pass the broker's recipient/exposure checks. Explicit non-owner,
ambiguous and inherited parties do not acquire an owner exemption from this
metadata; D4 owner resolution and approval requirements remain mandatory.

The marker is absent from serialized metadata unless declared. Old definitions
therefore retain their canonical bytes and remain readable with conservative
eligibility. Condition prose or a notify-looking name alone is insufficient;
unknown branch values refuse decoding. Publication generation and authorization
digests bind the typed marker, so changing/removing it changes currentness.
This is D4/D5 grant eligibility, not M2/D7, activation, selection/resource-value
authority or an effect receipt. The existing registry/repository/reader/admission
contract exercises real software components with a SQL protocol double; it is
not PostgreSQL/RLS/durable-publication execution evidence.
