# Finance field and delivery integrity design

## Status, baseline and adoption

**Governing source preparation authorized; not merged or implemented.** Source baseline:
`3e07dfec26de9544d9e3bbb30e2186df83e1a127`. This design makes the new behavior reviewable;
ROOT recorded the existing security-maintenance authority and selected the owning MCP mechanism on 2026-10-25 (decision 3736). The exact resulting canonical/archive source still requires DIFFERENT conformance before publication. RFC 0038 D4/D6 and the delivered Messenger
renderer constrain the design. REQ-finance-alerts-001, existing .10 cancellation/status guarantees
and the separate RFC 0029 expected-signal authority remain intact.

The amendment changes one old scenario only by an explicit additional qualification. In
REQ-finance-alerts-002, “Owner-set cancellation link is unchanged” retains its old THEN verbatim.
Its added AND distinguishes stored owner authority from message rendering: ordinary live rendering
requires an owner-only independently unexposed envelope; mixed or exposed output makes every link
inert. ROOT authorized this exact security-maintenance qualification under the original .19 scope. It is not a selective-span exemption, runtime delivery, or claim of merged governing state.

## Observed current path and counterexamples

1. `roster/finance/modules/tools.py::register_tools` registers `track_subscription`. It derives
   `_expected_signal_source` through `runtime_signal_source`, while the subscription helper derives
   owner attestation for guarded fields. These origins must not be conflated.
2. `roster/finance/tools/subscriptions.py::track_subscription` currently guards status and
   cancellation URL only. Its existing SELECT/UPDATE/INSERT replaces amount, currency and date.
   Concurrent initial creation is not a demonstrated unique logical-subscription transaction.
3. `roster/finance/tools/bills.py::track_bill` uses actual bill persistence and destination
   fingerprinting. Name provenance must accompany that writer without changing payment authority.
4. `src/butlers/scheduled_jobs.py::_run_finance_insight_scan_job` loads the real Finance scan.
   `roster/finance/jobs/finance_jobs.py::run_insight_scan` filters scalar `next_renewal` in SQL
   before creating candidates. A later extracted date outside the window therefore removes an
   earlier reminder before Python can recover it. Both the query and ledger registration consume
   the effective date, not just warning wording.
5. The Finance job's `_propose` calls the actual broker directly in the same process. Public MCP
   registration binds origin but accepts ordinary metadata; it is not a trusted field-origin proof.
6. `roster/switchboard/tools/insight/broker.py` persists messages and metadata and later selects,
   synthesizes, digests, folds amendments and retries them. It has no current durable Finance
   contribution stamp that can certify owner-only display.
7. `src/butlers/scheduled_jobs.py::_build_switchboard_insight_notify_fn` currently calls direct
   `deliver`. `roster/switchboard/tools/notification/deliver.py::_build_channel_args` forwards raw
   text/body in that branch. Merely setting metadata `render_links_inert` does not enforce rendering.
8. The delivered canonical `notify.v1` Messenger route authenticates its envelope, combines trusted
   private protection monotonically, and enforces final Telegram/email rendering. The existing
   `src/butlers/core/delivery_rendering.py` owns neutralization; no Finance URL parser is proposed.

## Proposed owning data model

A subscription retains its raw service, baseline renewal date, baseline complete price pair,
per-field origin, row revision and independent date/pair confirmation epochs. A bill retains its raw
payee and its origin/value revision. Origins are `owner` or `extracted`; unknown evidence reads as
extracted. A server-held authenticated owner binding, not a caller label, permits owner origin.
Raw names are not rewritten to store a safe display spelling.

One immutable typed claim relation stores subscription identity, field kind (`renewal_date` or
`price_pair`), normalized typed value, server-derived origin, available authenticated source binding,
observed row revision, confirmation epoch and resolution state. Price is a single amount/currency
value with an all-or-nothing invariant. A missing event/source identity is represented as missing,
not fabricated from caller metadata. Replay uniqueness covers subject, kind, epoch, normalized
value and the available source binding. Distinct observations remain auditable; repeated observation
must not add another claim or warning.

The canonical implementation packet must serialize the logical service/frequency identity even
before a row exists, then lock/update baseline, claim and revision in one owning transaction. It
must choose the actual migration/index/lock mechanism against current schema and prove two real
writers plus an independent read. This design does not reserve a migration number or widen a role.

| Incoming write | No baseline yet | Existing baseline or owner state |
| --- | --- | --- |
| Explicit authenticated owner values | Store exact supplied values as owner | Set exact supplied fields, increment their confirmation epoch, resolve their older claims |
| Extracted values | Store initial values as unverified extracted baseline | Equal values do not upgrade authority; unequal dates or complete price pairs add unresolved claims |
| Missing or unreadable attestation | Same as extracted | Same as extracted; never displace owner state |
| Caller provenance label or pending-claim identifier | No authority | Cannot confirm a claim or select a latest pending value |

The existing registered tool requires amount, currency and next renewal; an owner write therefore
confirms those exact supplied values. The operation must not infer that an owner accepted a pending
claim because a prompt mentioned its identifier. There is no new dashboard editor or URL-based
confirmation surface. The current Finance dashboard routes are GET-only.

## Date selection and pending-candidate currentness

For each active yearly subscription, form eligible baseline and unresolved same-epoch dates, filter
each date into the current local-day through existing 14-day horizon, then choose the earliest.
Filtering must precede the minimum: an old elapsed claim must not mask a different actionable date.
Use this selected date in SQL eligibility/order, warning text, event date, ledger period,
subscription/date deduplication identity and expiry at the next owner-local midnight after that selected date. A row revision is a
validation token, not a fresh warning identity. Preserve DST and current priorities/cooldowns.
There is no implicit rollover, missed-charge allegation or new elapsed-recurrence consumer.

An explicit owner set resolves older claims for that field and advances its confirmation epoch.
A final delivery-time currentness check withdraws a pending warning only when it is **known** to
describe a superseded date/epoch. Delivered rows, deduplication history and cooldowns remain.
An unavailable read is unknown under the existing as-of policy, not a fabricated supersession.

**Owning currentness interface:** Finance owns a fixed read-only `subscription_warning_still_relevant(subject, selected_date, confirmation_epoch)` operation. The Switchboard's fixed Finance premise entry invokes that registered operation through the existing control-plane MCP route to Finance; it never imports Finance implementation, shares its pool, grants cross-schema SELECT/EXECUTE, or accepts caller-selected target/tool/SQL. The exact UUID, canonical ISO date and nonnegative signed 64-bit epoch are validated at both ends; Finance decodes the date to a native date before its own SQL. It returns only nullable BOOLEAN: true for the same-epoch relevant warning, false for known owner supersession/expiry, null for missing/unreadable/unregistered/unavailable evidence. Route/permission/validation/transport failures remain UNKNOWN with existing as-of honesty and cannot confirm claims, erase protection or certify fresh evidence. Registration, current catalog/role authorization, authenticated routing, original finite cancellation/deadline ownership and independent owner-commit readback must be exercised by .16. No new session or model invocation is needed. The earlier proposed `public.resolve_finance_subscription_warning` SQL function is not authorized by this application and is not installed.

## Atomic amount and currency behavior

Keep the initial baseline pair for tracked-price comparisons even if it is extracted-only. A later
unequal extracted pair is pending, not the new denominator. If either supplied component differs,
store the whole supplied pair together. An owner explicitly sets both components atomically.
Pending currency changes cannot be compared numerically across currencies or converted using a new
FX policy. Existing greater-than-five-percent, 60-day comparison and zero-baseline protections
remain. An untrusted `metadata.next_amount` is a proposal, never confirmed precharge authority.
Warn about unverified disagreement without claiming a future charge was observed or confirmed.

## Trusted contribution and durable rendering bridge

The deterministic Finance producer constructs a private typed contribution proof from actual owning
reads: subject, exact raw display value, value revision, origin and final content binding. Public
MCP JSON cannot construct that proof or assert owner-only status. The broker validates its fixed
producer boundary before storing the contribution binding and a monotonic protection floor.
Unbound, legacy, malformed or mismatched Finance provenance is inert-required. A mismatch can narrow
to inert; it cannot recover owner authority from the current session.

Every derived summary, cluster, folded amendment, prepared action and digest ORs all contributor
floors. A single extracted/unknown contributor protects the whole final message, including links in
owner-origin contributions. Independent exposure has the same whole-message consequence. Raw
stored data and owner field authority stay unchanged. Generated prose cannot declassify inputs.
Server-created structured controls keep their separately authenticated authority and do not make
untrusted text into live controls.

The concrete .17 bridge is the existing production insight notification closure: keep owner
resolution, intent feedback and error mapping, build the normal authenticated `notify.v1` envelope,
and carry the trusted persisted floor through the established private sender mechanism to the
registered Messenger route. Do not treat a caller Boolean or direct branch as protection. The
canonical Telegram final payload uses literal preformatted entities and disables previews; email
uses the canonical neutralizer before SMTP permission/start boundaries. Unsupported channels refuse
or PARK under the delivered contract. Unknown outcomes and at-most-once accounting remain intact.

The separate successful in-place amendment path is mandatory too. Actual
`apply_pending_amendments` calls `_build_switchboard_insight_amend_fn` → `amend_delivery` →
registered `telegram_edit_message_text` before normal notification. The current edit rebuilds HTML
from stored text without entering `notify.v1`; folded-amendment proof cannot stand for that path.
Retain the persisted original contribution union plus any new resolution-note contributors through
the complete replacement, with exact values/revisions. A public Boolean or latest session cannot
clear it. Successful protected edits use the same canonical rendering primitive: all replacement
links inert and previews disabled, preserving authenticated chat/message identity, current edit
permission and separately bound server controls.

If that trusted edit path cannot enforce protection, refuse before provider execution and return
the existing truthful rejected/edit-unavailable outcome. The existing fold path must retain the
complete persisted union into its later safe digest; never mark the raw edit applied. Preserve
ordinary owner-only independently unexposed edits, state='pending' CAS, bounded attempts,
restart/replay, cancellation and ambiguous outcomes. No unknown edit becomes success, a duplicate
send or a new safe-retry claim. Choosing the trusted edit bridge or this existing refusal/fold
mechanism is engineering within required containment, not another owner-policy approval gate.

The proof must execute actual production amendment closure→registered edit→fake provider capture:
successful owner-only edit, protected successful edit if supported, and protected pre-provider
refusal→real safe folded digest. Cover original/new contributors, restart/retry and mixed origins.
Independently remove the edit protection/refusal boundary, require the synthetic active-link
replacement/provider-call control to fail, then restore exact source. Notify-only, fold-only and
injected safe callbacks are insufficient.

Use the broker's existing pending/hold/retry persistence; do not add a second temporal queue.
If implementation genuinely chooses generic temporal delivery instead, it needs an explicit
additional allocation proving envelope storage, `_format_deferred_digest` protection union and
`background.py` trusted replay restoration. The current coalescer rebuilds a subset of the envelope
and a ContextVar does not cross MCP. Storage of a flag alone is insufficient.

The actual candidate readback is `/api/switchboard/insights` in
`roster/switchboard/api/router.py`, including message and metadata. Its display projection must be
safe; the aggregate `insights_v1` count/version endpoint is not a substitute. Existing React service
and payee cells render literal text, but that fact does not prove downstream candidate safety.

## Migration, compatibility and rollback

Add origins/revisions/epochs/claims without rewriting raw values. Legacy date/pair/name origins read
as extracted despite historical last-writer metadata. Keep finance_017 cancellation/status sources
and every existing expected-signal producer rule. Reapply must be idempotent. Each implementation
must test old/full chain, roles, constraints, commit, rollback and concurrent writer behavior.

An unsafe downgrade must refuse atomically, or preserve all data and enforcement necessary to avoid
restoring either gap. It must not discard unresolved claims, erase owner authority, reinterpret old
metadata as owner proof or restore raw extracted rendering. Runtime code rollback cannot bypass the
new boundary. Existing roles/grants are not widened to make tests pass.

## Review and handoff

All 31 cases and exact source/test seams are in the linked Finance safety document. The exact .16/.17 implementation packets are bound in the Finance safety document; only ROOT may apply them to Beads
after the governing canonical/archive source actually merges. .17 waits for delivered .16 shared provenance. Delivered .10 and canonical
Messenger .1 are reused; no duplicate renderer owner, active .1 assignee or reverse dependency is
invented. Current work adds zero tests and no runtime authority. A reviewed draft is not .19
completion: this bound source application prepares four canonical specs and the dated archive; actual merge and post-merge canonical packet application remain mandatory.

## Source references and format compatibility

- Non-Negotiable Rule 3: existing MCP inter-butler boundaries remain.
- RFC 0038 D4/D6: effect authority, exposure and inert final rendering.
- RFC 0029: honest recurrence observations, no implicit absence alert.

The repository scaffold config asks for a Source References H2 in each spec, but the actual
complete-file authoring predicate rejects every non-operation H2 in delta specs. These references
live here and in each contiguous Source line instead. The old REQ002 Scope text was a descriptive
security-maintenance label; its restatement uses the permitted `v1-mandatory` trace enum, retaining
the classify-and-route security-maintenance scope above. Neither correction changes behavior.
