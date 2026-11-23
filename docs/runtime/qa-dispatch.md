# QA dispatch persistence

The infrastructure-condition gate in `butlers.core.qa.dispatch` preserves the adopted
[infrastructure reliability](../../openspec/specs/infrastructure-reliability/spec.md) and
[QA dispatch](../../openspec/specs/qa-investigation-dispatch/spec.md) decision-only contracts.
This note describes persistence; it does not change admission order or authorize investigation.

## Implementation Notes

### Dark dashboard custody and staffer admission

`core_363` adds `public.qa_dashboard_reports`; it changes no other migration
plane. Only QA's runtime role receives read/insert/update access. RLS denies
other runtime roles even after bootstrap regrants; table ownership remains the
trusted maintenance boundary. The store contains anonymized sensitive evidence,
not a public dashboard projection. Immutable key/hash/payload guards, retained
rows and unique finding linkage protect custody across restart and rollback.
Downgrade retains both the table and its guards deliberately.

`DashboardInbox` implements owner-side acceptance and exact durable receipts.
`ButlerReportsSource` accepts an optional inbox only in closed tests today;
default construction still drains only the existing volatile buffer. Discovery
claims through `core.queue_claim` in the adapter transaction. `insert_finding`
locks and checks the winning generation/lease before insertion, then commits
the canonical finding and acknowledgement together. A stale claimant inserts
nothing, while a same-generation acknowledgement retry returns the original ID.
Ordinary deterministic triage and the D18 dispatcher keep their ownership.

`QaTriageEvidence` checks `roster/qa/qa_triage_v1.toml`, the current complete
published fleet and an acknowledged owner snapshot. Its domain/resource ceiling
is explicitly empty. The finding is exposed data, and the existing admission
engine owns finite limits, source/attempt locks and durable decisions. Unknown
commit results reconcile the same source and attempt; a previous admission wins
across disabled-source or contract drift. No second admission ledger exists.

The released transport does **not** yet supply the exact authenticated QA peer
audience or QA-owner evidence reader. These APIs have no live caller, MCP
registration, credential, deployment or activation. The adapter defaults to
refusal without an injected trusted reader. The synthetic owner reader in the
PostgreSQL tests establishes storage/admission behavior, not network identity.
The remaining consumer must use the relay owner's exact released interface,
with subject/audience/session-listener refusal before storage access. It must
also connect exposed starting input to the runtime through the released owner
boundary; this dark source proof does not establish a running triage invocation.

Only authenticated exact `not_found` may permit same-key/content redelivery.
`found` pending/claimed is retained evidence, and `unavailable` remains unknown.
The calling terminal reconciler owns its existing five-attempt/15-minute bound
and explicit ambiguity; this store neither adds retries nor activates that
reconciler. Receipt fields contain no summary, context, hash or sensitive data.
See RFC 0015 D1a for ownership, permissions and service objectives.

### Infrastructure-condition persistence

An ordinary `infra_condition_open` result requires both the finding-reason write and the
condition-linked decision-event write to return successfully. Neither write is best effort.
If either raises, the existing dispatcher boundary reports `internal_error`, with no attempt
claim, model invocation, runtime session or worktree. It does not invent a retry queue.

The writes commit independently. A failed call can therefore leave partial evidence, and a lost
response can leave a committed event even though dispatch reported `internal_error`. Retrying the
same persisted finding uses a deterministic decision UUID derived from its finding ID. The event
writer reuses the original row and reason, checks its fingerprint/butler/decision/attempt binding,
and never changes it into an execution record. A separate finding still has a separate decision
identity. This preserves at most one decision per suppressed finding across retries/concurrency.

Existing callers that omit an event ID retain append-only event creation. The suppression metric
is emitted only after both persistence calls succeed; the error result does not advertise durable
suppression. PostgreSQL durability and concurrent retry are covered by the named roundtrip in
`tests/integration/test_infra_condition_suppression_persistence_roundtrip.py`; a collection result
or mocked writer is not proof that those database bodies executed.

## Log finding identity and the one-time fingerprint rotation

The log scanner fingerprints a logged exception by its class, its innermost repository frame
(`file:function`, no line number) and its normalized message, as written by
`core.logging.add_exception_identity`; the rendered traceback is evidence only
(`structured_evidence.traceback_tail`). One defect is therefore one fingerprint, and a deploy that
shifts line numbers does not change it.

**Rotation (bu-ejow5p.4).** Before this change the scanner hashed the rendered traceback as the
exception type, so existing log fingerprints never repeated. On the first patrol after deploy every
log-scanner fingerprint changes once. Dismissals, cooldowns, publication holds and active-attempt
joins keyed on old fingerprints go inert (they are not rewritten or backfilled), and each live
defect is investigated again once under its new fingerprint. Session-record and tool-call-failure
fingerprints are unchanged. Expect one patrol of novel findings, then the stable count.
