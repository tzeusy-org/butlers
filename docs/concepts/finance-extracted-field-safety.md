# Finance extracted-field safety: source and proof contract

## Status and scope

This document describes the **proposed**, unadopted full bu-svqpzo.19 contract at
`3e07dfec26de9544d9e3bbb30e2186df83e1a127`. It does not deliver .16 or .17. The
[proposal](../../openspec/changes/finance-extracted-field-safety/proposal.md),
[design](../../openspec/changes/finance-extracted-field-safety/design.md) and four delta specs
are one review unit. New dates, price pairs, confirmation epochs, storage and lookup rules need
exact adoption before implementation. Tests: +0 ~0 -0 for this text-only change.

## Actual source trace

| Stage | Delivered source and symbol | Current behavior and proposed seam |
| --- | --- | --- |
| Owner/extracted writer | `roster/finance/modules/tools.py::register_tools`, `roster/finance/tools/subscriptions.py::track_subscription` | Server-derived context protects URL/status today; date/pair replacement needs the new owning transaction |
| Source authority | `roster/finance/tools/expected_signals.py::runtime_signal_source` | Owner/public entity and ingress attestation; caller metadata cannot supply field authority |
| Payee writer | `roster/finance/tools/bills.py::track_bill` | Actual bill writer and destination fingerprint remain; add only name origin/value revision |
| Scan registration | `src/butlers/scheduled_jobs.py::_run_finance_insight_scan_job` | Loads actual roster job; no invented adapter |
| Candidate producer | `roster/finance/jobs/finance_jobs.py::run_insight_scan`, `_propose` | SQL scalar date prefilter precedes candidate creation; selected date and private origin proof must be bound here |
| Ledger and price | `roster/finance/tools/alerts.py::register_obligations`, `detect_price_changes` | Keep current horizons, thresholds, zero guard and absence policy; selected date and unverified pairs cannot be bypassed |
| Broker | `roster/switchboard/tools/insight/broker.py::propose_insight_candidate`, `delivery_cycle` | Persist floor and exact contributions across hold, synthesis, digest, folds and retry |
| Public registration | `roster/switchboard/modules/insight_broker.py::register_tools` | JSON origin/metadata cannot manufacture private owner-only proof |
| Production notification | `src/butlers/scheduled_jobs.py::_build_switchboard_insight_notify_fn` | Currently direct `deliver`; ignored metadata is not protection; bridge to canonical authenticated envelope |
| In-place amendment | `roster/switchboard/tools/insight/broker.py::apply_pending_amendments`, `src/butlers/scheduled_jobs.py::_build_switchboard_insight_amend_fn`, `roster/switchboard/tools/notification/deliver.py::amend_delivery`, `src/butlers/modules/telegram.py::_edit_message_text` | Successful replacement precedes normal notify; canonical protection or truthful pre-provider refusal→safe fold is required |
| Canonical renderer | `src/butlers/core_tools/_routing.py`, `src/butlers/core/delivery_rendering.py`, `src/butlers/modules/telegram.py`, `src/butlers/modules/email.py` | Reuse delivered Messenger neutralization, permissions and recovery; no second URL parser |
| Candidate readback | `roster/switchboard/api/router.py::list_insight_candidates` | Message and metadata must use safe projection; aggregate counts do not prove it |
| Currentness | `roster/switchboard/tools/insight/premises.py`, `src/butlers/core/insight_premise.py` | Existing fixed bill probe is precedent; subscription probe/owning narrow lookup are new proposed interfaces |

The current GET-only Finance subscriptions API is not a mutation interface. The subscription
property-fact writer is not a hidden renewal input. Current Finance reads the dedicated table.
Generic temporal storage exists, but its coalescer and remote replay are not credited as carrying
this protection. The smallest proposed .17 bridge retains broker-owned pending/retry instead.

## Complete behavior and proof matrix

Every row is a **proposed, not executed** implementation proof obligation. W01–W16 appear as named
scenarios in finance-alerts or finance-supporting-tables. R01–R15 appear in
proactive-insight-engine or insight-delivery. Existing four REQ-finance-alerts-002 scenarios retain
their names; only the owner-door scenario gains the explicit whole-envelope qualification.

| ID | Trigger | Required result | Actual executing seam |
| --- | --- | --- | --- |
| W01 | Owner baseline inside14; extracted later outside14 | Earlier date warning selected in actual SQL, same date in ledger/key/expiry; disagreement unverified | real routed tool→migrated Finance→run_insight_scan→broker |
| W02 | Extracted-only baseline then later extracted | Baseline and pending retained; earlier actionable warning survives, no owner origin | same real producer |
| W03 | Extracted earlier date | Earlier pending date selected without converting it to confirmed; clear confirmation request | real query/window boundaries |
| W04 | Several date disagreements/duplicate message/retry | All distinct claims retained, duplicate value/source idempotent; semantic dedup unchanged | PG uniqueness/retry + real broker dedup |
| W05 | Old earliest date elapsed, another unresolved date inside14 | Expired history cannot mask still-actionable date; no missed-renewal allegation/new alert | fixed clock/DST query test |
| W06 | Explicit owner date moves earlier/later | Owner set commits new epoch; obsolete pending candidate withdrawn before delivery; sent history/cooldown not erased | real owner binding + concurrent broker final recheck |
| W07 | Owner/extracted race in each order and initial insert race | No duplicate logical subscription; no partial pair; owner state survives, pending exacthistory | two real runtime-role acquisitions/barriers + independent observer |
| W08 | Attestation None/missing/raises/forged metadata or argument | Extracted only; no provenance upgrade or change in expected-signal measurability | actual routing context + reserved-metadata adversary |
| W09 | Owner-set cancelled/paused vs extracted status | Original .10 owner suppression/extracted warning remain | existing cancellation/status regression nodes |
| W10 | Amount-only or currency-only changed extracted update | Whole supplied price pair pending atomically; baseline preserved; no mixed-currency numeric arithmetic | real writer + pair CHECK + observer |
| W11 | Extracted-only unequal pair | Pending not overwrite initial baseline; existing 5% price detector still compares true tracked baseline | detect_price_changes + precharge ledger consumer |
| W12 | Repeated identical pair | No extra claim/warning, no authority upgrade | PG same-event replay |
| W13 | Explicit owner pair set/confirmation | Exact supplied pair and source commit together; retained resolvedhistory; no latest-pending inference | registered tool/owner context |
| W14 | Currency change pending or zero tracked amount | Unverified currency change named without FX/percent; old zero division guard and same-currency thresholds remain | existing price nodes + changed pair seam |
| W15 | Legacy row misleading last-writer owner metadata | Date/pair/name extracted; raw data unchanged; existing .10 fields stay asdelivered | real migration/readback |
| W16 | Migration rollback/reapply/failure/concurrent write | No lost claims/origin/earlier warning; unsafe downgrade refuses atomically; no oldcode bypass | migrated realPG runtime roles/savepoints + independent commit |
| R01 | Extracted subscription service: bare host, https, markup | Persisted names reach actual provider payload with no live link or preview; metadata safe projection | actual tool→row→scan→broker→scheduled notify→registered Messenger→fake provider |
| R02 | Extracted bill payee same variants | Same guarantee through due/reconciliation/predicted consumers that include payee | real bill writer/job paths |
| R03 | Owner-only unexposed exact names | Raw stored values and ordinary rendering unchanged | registered final Telegram/email regression |
| R04 | Owner name + extracted name in one digest | Whole digest inert; both raw records unchanged; owner origin is not declassification of other text | real broker multi-candidate digest |
| R05 | All owner names but independently exposed envelope | ALL links inert, previews off, current canonical unsupported channel gates | existing canonical Messenger boundary |
| R06 | Missing/forged/malformed/legacy provenance | Conservative inert; no metadata false flag/current owner session upgrade | public MCP admission + persisted readback |
| R07 | Deferred broker hold/day boundary/restart | Persisted protection survives; quiet-hours/current context/budget/expiry unchanged | broker real pending rows/restart consumption |
| R08 | Delivery failure and retry/replay | Protection unchanged; existing pending attempt accounting/3fail rule/no duplicate send claims | same broker failure/retry boundary |
| R09 | Synthesis/folded amendment/prepared action door | No derived summary or folded line clears any contributor floor; canonical server-created controls retain their separately bound authority | actual _format_digest/_pending_fold_lines/render door |
| R10 | Dashboard API candidate message + metadata | Exact actual /api/switchboard/insights projection cannot yield live extracted labels; count-only insights_v1 not substituted | real API DTO + actual literal UI test |
| R11 | Stored owner door plus extracted service | Door rawvalue/door_verified source retained, but canonical whole-envelope rendering makes its URL inert; new qualification explicit | REQ002 successor + final payload |
| R12 | Mixed candidate order/tie/channel routing/caller false flag | OR is order-independent, preserves budget/recipient/failure semantics, cannot be disabled | actual scheduled notify closure/private sender scope |
| R13 | Unsupported rendering channel or permission outage | Canonical fail-closed/PARK; no fallback raw send or counterfeit sent status | existing adapter and permission tests |
| R14 | Any explicitly used generic temporal path | Stored envelope/restored trusted protection/coalescer OR verified at real background consumer; otherwise document it is not used by Finance bridge, never grant credit from storage alone | conditional exact scheduler/background software+PG boundary |
| R15 | Successful in-place amendment or protected edit unavailable/rejected | Complete replacement inherits original/new contributor union; canonical whole-replacement inert links/previews or pre-provider refusal→safe fold; owner-only rendering, CAS/retry/unknown preserved | actual production amend closure→registered Telegram edit→fake provider capture; independent edit-boundary removal/restoration |

## Exact proposed canonical implementation packets

These are proposed replacements for the operative implementation packet fields. They are not
canonical Beads writes; ROOT applies them only after merged governing adoption. References to .19
mean that full gate, not this pending draft. No source revision number or new role/grant is reserved.

### bu-svqpzo.16: Stop extracted subscription updates postponing actionable warnings, with atomic pending price claims

Implement the exact merged bu-svqpzo.19 storage/date/pair/confirmation contract. Security maintenance only, no deployment or payment. Earlier still-actionable recorded date survives SQL prefilter, ledger/key/expiry and delayed broker read; unequal untrusted pair stays pending. Reuse .10 attestation and cancellation/status protection.

Acceptance criteria:

- 1. Exact adopted .19 requirements and representation are cited; genuine runtime origin, legacy defaults, amount pair and owner confirmation matrix implemented at named existing tools, additive Finance migration and currentness probe. No authority from caller metadata or source_message_id.
- 2. Actual registered routed-extracted tool write then migrated real SQL renewal scan/broker proposes earlier relevant date with unverified disagreement; later outside-window date cannot suppress it. Same date drives warning, ledger period, priority/key and expiry; unrelated thresholds, quiet/context/budget/dedup intact.
- 3. Explicit authenticated owner values supersede pending under atomic current-row transaction; two independent real runtime-role writers/observer prove both owner/extracted orders, concurrent first insert uniqueness, retry/duplicate idempotency and no erased history/partial state. Currentness probe withdraws obsolete pending on known owner supersession, UNKNOWN never invents confirmation.
- 4. Amount/currency remain one pair and origin. Extracted unequal updates never silently replace baseline, pending pairs and currency mismatch are explicit; existing >5%/60-day post-charge and pre-charge alerts retain executing coverage. Existing .10 cancellation/status and source-failure cases remain.
- 5. Actual migration/reapply/rollback/refusal, existing read-only DTO/docs/spec, measured named test delta and exact-head hosted broad/realPG evidence pass; no live notification or feature end-to-end delivery claim from software collection.

Source allocation to bind at implementation dispatch:

| Path | Exact seam |
| --- | --- |
| `roster/finance/tools/subscriptions.py` | Server-reserved field provenance + atomic logical identity/claim transaction + owner explicit set |
| `roster/finance/tools/expected_signals.py` | Reuse real attestation; add no caller authority; reserved metadata sanitization only if current helper needs exact new reserved keys |
| `roster/finance/modules/tools.py` | Existing registered explicit owner input/result state; no new tool/confirmation form |
| `roster/finance/tools/alerts.py` | Same effective date in obligation ledger and pending pair pre-charge presentation; current thresholds remain |
| `roster/finance/jobs/finance_jobs.py` | Real SQL selection over baseline and unresolved dates; fixed warning/key/expiry; current pending discrepancy wording |
| `roster/finance/api/models.py` | Add read-only field origins/pending confirmation DTO |
| `roster/finance/api/router.py` | Existing GET projection only; no mutation endpoint |
| `roster/finance/migrations/<next actual revision>` | Add claims/revision/origins/constraints/atomic identity and non-destructive rollback; number unallocated |
| `roster/switchboard/tools/insight/premises.py` | Existing actual PREMISE_PROBES registry contains finance.bill_still_pending and Health refill only. Proposed fixed entry finance.subscription_warning_still_relevant uses a narrow owning read, no client-selected reader or dynamic callback. True=relevant, False=owner-superseded/expired; None=UNKNOWN with existing as-of honesty, never an owner change. |
| `roster/finance/migrations/<next actual revision>` | Install exact narrow public.resolve_finance_subscription_warning(subject UUID, selected DATE, confirmation epoch) lookup with reviewed definer owner/search_path/execute ACL modeled on actual core255 Finance bill lookup, exposing only tri-state currentness, not raw tables or write authority. The new name/interface is explicitly PROPOSED, not existing; no cross-schema broad SELECT grant. |
| `src/butlers/core/insight_premise.py` | REUSE probe_premise/normalize_premise; no new generic premise kind is required. |

Existing test owners:

- `roster/finance/tests/test_tools.py`
- `roster/finance/tests/test_expected_signals.py`
- `roster/finance/tests/test_jobs.py`
- `roster/finance/tests/test_alerts.py`
- `roster/finance/tests/test_api.py`
- `tests/migrations/test_finance_migrations.py`
- `tests/modules/test_insight_premise_binding.py`
- `tests/modules/test_module_insight_broker.py`

Causal evidence: Actual tool+real routed binding→owning PostgreSQL transaction→query prefilter→real job→real broker. Remove field protection then SQL date selection independently; each old counterexample must fail and exact restore pass. Genuine PG race/commit/rollback proof cannot be a recording fake or parser-only check.

### bu-svqpzo.17: Preserve Finance field origins through durable insight delivery and canonical inert rendering

Use delivered .16 field origin and actual closed Messenger .1 renderer. Raw Finance names remain intact. Extracted/unknown contribution or independent exposure requires whole final envelope inert; only owner-only independently unexposed output retains ordinary rendering. Bridge actual scheduled notify consumer rather than setting an ignored metadata bit.

Acceptance criteria:

- 1. Adopted exact .19 matrix, actual .16 provenance and canonical Messenger .1/PR4875 are source-bound prerequisites. New private deterministic contribution proof cannot be supplied by public MCP JSON; unknown legacy or unreadable provenance cannot assert owner.
- 2. Real extracted subscription service and bill payee cases (bare host, https, hostile markup) survive owning persistence→real scan→broker persisted rows→registered production delivery closure→Messenger fake provider boundary with no active links/previews and safe metadata/readback. No real sends.
- 3. Stored owner exact names and owner-only independently unexposed rendering remain unchanged. Mixed-origin digest and any independently exposed envelope apply whole-envelope protection, including owner-origin links, as precisely adopted; verified cancellation-door source remains data authority, never rendering exemption.
- 4. Broker hold, restart, multi-candidate clustering/synthesis, folded amendments, failed delivery/retry/replay and actual candidate API cannot lose protection or recover an active extracted link. Test both digest orders, unknown/forged provenance, caller false/true flags, current recipient/permission failure and canonical unsupported channel gates. Any actually used generic temporal route needs source-bound storage+coalescer+trusted replay proof; no imaginary persisted flag credit. Successful in-place amendments are a distinct mandatory consumer: retain original/new contributor union through actual registered edit and canonical replacement rendering, or truthfully refuse before provider and retain it through the existing safe fold path. Preserve authenticated chat/message identity, current edit permission, CAS, attempts, cancellation and UNKNOWN/replay accounting.
- 5. Reuse sole canonical renderer/URL neutralizer, preserve all budgets/attempt/unknown outcome/currentness/owner-notification behavior and no new approval authority. Docs/spec align, measured test delta and exact-head hosted PG/required checks pass; no provider execution/deployment credit.

Source allocation to bind at implementation dispatch:

| Path | Exact seam |
| --- | --- |
| `roster/finance/tools/subscriptions.py` | service-name origin/value revision shares .16 |
| `roster/finance/tools/bills.py` | payee origin bound to actual insertion/update, destination logic unchanged |
| `roster/finance/jobs/finance_jobs.py` | All actual name-bearing producer branches pass fixed private contribution proof, safe display projection |
| `roster/switchboard/tools/insight/broker.py` | Persist protection/bindings, conservative legacy, OR across digest/synthesis/folds/retry/final recheck; apply_pending_amendments successful-edit and rejected-fold branches retain original/new contribution union, state CAS and attempt limits |
| `roster/switchboard/modules/insight_broker.py` | Public JSON cannot construct owner-proof or clear protection; no misleading origin flag |
| `src/butlers/scheduled_jobs.py` | Real production insight notify closure uses existing notify.v1/private mark; owner resolution and intent feedback/error mapping preserved; distinct _build_switchboard_insight_amend_fn carries trusted persisted protection or truthfully refuses protected edit before provider; notify-only proof is insufficient |
| `roster/switchboard/api/router.py` | Actual candidate text/metadata safe readback |
| `roster/switchboard/api/models.py` | Read-only safe display/provenance DTO as needed, no live-link raw field |
| `alembic/versions/core/<next actual revision>` | Candidate persisted binding/protection defaults and legacy migration if separate column used; number unallocated |
| `src/butlers/core/delivery_rendering.py` | REUSE helper, no new URL parser/semantic declassification; extension not assumed necessary |
| `roster/switchboard/tools/notification/deliver.py` | REUSE canonical authenticated notify.v1/private OR; direct branch must not be incorrectly counted as protected; amend_delivery preserves authenticated target/message binding through canonical replacement rendering or refuses before raw route |
| `src/butlers/core_tools/_routing.py` | REUSE existing Messenger final channel enforcement, preserve all recovery semantics |
| `src/butlers/core/scheduler.py + src/butlers/background.py + src/butlers/core_tools/_notifications.py` | CONDITIONAL ONLY: if implementation routes into generic temporal queue, exact producer-store/coalescer/restored-private-mark bridge is required. Smallest existing insight path uses broker hold/retry and does not add a second queue. |

| `src/butlers/modules/telegram.py` | Conditional narrow registered telegram_edit_message_text/_edit_message_text protection propagation only if supporting successful protected edits; reuse canonical primitive and preserve current permission/identity/server controls. Otherwise upstream truthful refusal/fold prevents raw provider edit; no generic edit API widening assumed. |

Existing test owners:

- `roster/finance/tests/test_tools.py`
- `roster/finance/tests/test_jobs.py`
- `roster/finance/tests/test_cancellation_door.py`
- `tests/modules/test_module_insight_broker.py`
- `tests/modules/test_insight_engine.py`
- `tests/modules/test_insight_cluster_synthesis.py`
- `tests/jobs/test_insight_delivery_job.py`
- `tests/api/test_switchboard_insights.py`
- `tests/daemon/test_route_execute_authz.py`
- `tests/modules/test_module_telegram.py`
- `tests/modules/test_module_email.py`
- `tests/modules/test_insight_premise_binding.py`

Causal evidence: Remove Finance producer provenance then the actual bridge/protection OR independently, each must re-expose synthetic link at the captured final payload and fail; exact restoration passes. Test the registered production closure, not an injected notify_fn that itself supplies safe rendering. Reuse canonical adapter suite rather than copying sanitizer cases. Separately execute real apply_pending_amendments→production _build_switchboard_insight_amend_fn→amend_delivery→registered Telegram edit→fake provider payload. Cover successful owner-only edit, protected success if supported, pre-provider refusal→safe fold, original/new contributor union, mixed origins, restart/retry, UNKNOWN and current edit permission. Remove edit-protection/refusal independently, require active synthetic replacement/provider-call FAIL, then exact restore PASS. No live provider operation.

The .16 allocation includes the new narrow owning subscription-warning lookup and fixed premise
entry described in the design; the design fixes UUID/DATE/nonnegative BIGINT inputs and nullable BOOLEAN output, while its
actual owning migration, ACL and same-transaction readback must be source-leased before edits. It cannot borrow authority from a returned DTO. The .17
metadata/storage migration is conditional on the actual representation selected at implementation;
there is no permission here to allocate a speculative extra migration or generic deferred queue.

Dependencies: .16 waits on .19 merged governing adoption. .17 waits on that adoption and delivered
.16 shared provenance. Canonical Messenger bu-vbdug9.1/PR4875 and Finance .10 are already closed;
there is no active foreign renderer owner to coordinate and no reverse dependency or duplicate bead.

## Verification, migration and rollback

For .16, start at the actual registered writer and actual SQL prefilter, then widen to Finance owners
and real migrated PostgreSQL role/race/commit/rollback controls. Two independent acquisitions plus a
committed observer must cover creation and both owner/extracted orders. Full migrations must retain
raw values, legacy-extracted origins, all claims and finance_017 semantics. Unsafe downgrade refuses
or retains required enforcement; code rollback cannot reactivate an unprotected path.

For .17, execute the actual deterministic producer through the real broker and the production
notification closure into registered Messenger with a fake provider capture. Test bare host, HTTPS
and markup in subscription service and bill payee; owner-only, mixed, exposed, held/restarted,
failed/retried, synthesized/folded and safe-API cases. Remove producer proof and final bridge/OR
independently; each must expose the synthetic payload counterexample and fail, then exact restoration
must pass. Do not replace the production closure with a test notify function that itself sanitizes.
Existing canonical adapter tests are reused, not copied into a second sanitizer suite.

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

For text authoring, run named strict validation and complete-file authoring, actual overwrite,
requirement-trace, countable-task and cited-requirement checks. Preserve any successor or inherited
non-pass; do not pad a contrary old clause, relabel MODIFIED as ADDED, change guard logic or ratchet
metadata to hide it. Full baseline application/archive follows exact adoption in a separate bounded
source allocation, preserving unrelated requirements and scenarios. No pending draft completes .19.

## Full five acceptance-criterion disposition

| Original criterion | Concrete contract output | Remaining gate |
| --- | --- | --- |
| Stored-field to delayed-render trace | Actual source table and design counterexamples | Independent source/conformance review |
| Date/pair/provenance/confirmation and races | W01–W16, explicit transition table and new narrow lookup | Exact normative adoption; .16 implementation and genuine migrated proof |
| Inert fields and canonical envelope semantics | R01–R15 and precise REQ002 qualification | Exact normative adoption; .17 actual bridge/renderer proof |
| Merged governing contract, migration/rollback and matrix | Four full delta specs, design, tasks and this matrix | Approved canonical application/archive and merged exact-head checks |
| Executable .16/.17 packets and real edges | Both complete proposed packets above | ROOT canonical application after adoption; no duplicate renderer owner |
