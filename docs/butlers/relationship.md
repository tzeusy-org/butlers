# Relationship Butler

A personal CRM: contacts, relationships, important dates, interactions, gifts, loans, and
reminders, so the owner never forgets what matters about the people in their life. Data is
entity-first: every contact links to a shared entity, and facts attach to the entity.

- **Identity and scope:** [`roster/relationship/MANIFESTO.md`](../../roster/relationship/MANIFESTO.md)
- **Required behavior:** [`butler-relationship` spec](../../openspec/specs/butler-relationship/spec.md)
- **Schedules, modules, and port:** [`roster/relationship/butler.toml`](../../roster/relationship/butler.toml)
- **Entity model:** [Identity Model](../concepts/identity-model.md)

![Relationship Butler Flows](./relationship-flows.svg)

## Stale-contact source authority

Elapsed time alone does not prove a contact has gone quiet. A contact can be surfaced as overdue
(in insight scans, the weekly maintenance message, the reconnect planner, the Contacts overdue
panel, or the attention rail) only when exactly one server-attested producer was expected to
record the next interaction, and the heartbeat for that exact producer endpoint is healthy. A
healthy sibling endpoint never substitutes; stale, missing, unhealthy, or mixed provenance makes
the contact `unmeasurable` and pauses every nudge. Caller-settable `extra_metadata.source` is not
producer authority. `unmeasurable` describes the instrument, not the owner's behavior, so an
empty overdue list is not an all-clear.

The channel-to-producer mapping lives in the `butler-relationship` spec and
[RFC 0029](../../about/legends-and-lore/rfcs/0029-expected-signals-and-honest-absence.md). For
triage, check the contact's expected-signal state and exact producer, then that same connector
endpoint's heartbeat.

## Effective time on structural facts

`relationship.entity_facts` rows can say *when* a relationship held, separately from when the
assertion was made (`created_at`), observed (`observed_at`, `last_seen`), how certain it is
(`conf`), and whether the assertion version is current (`validity`). None of those ever defaults
or derives an effective bound. The contract is the
[`relationship-fact-effective-time`](../../openspec/changes/relationship-fact-effective-time/design.md)
change; the wire rules live in `roster/relationship/tools/fact_temporal.py`.

- **Packet.** `effective_period_id` names one occurrence of a triple (NULL is the default
  occurrence every legacy row uses). The interval is half-open `[effective_from, effective_to)`
  with a precision per bound: `instant`, `day`, `month`, `year` or `unbounded`. A NULL bound with
  NULL precision is **unknown**; a NULL bound with `unbounded` is an explicit open end. Unknown
  never means "for all time". Coarse bounds normalize to UTC unit starts, and an upper bound to
  the first instant after its unit (`2024-05`/`month` stores `2024-06-01T00:00:00Z`).
- **Writer modes.** With no temporal argument (omitted and `null` are identical) a write is
  *ordinary*: it targets the default occurrence and copies whatever packet that row already has
  into any provenance replacement. Any non-null bound, precision or period id is an *explicit*
  packet; a different packet for an occupied occurrence is refused, never silently corrected.
  `corrects_fact_id` is a compare-and-swap correction of one exact active version; a stale
  target fails without writing. Parked owner approvals carry all six temporal keys canonically
  and freeze the resolved mode and base version in `fact_approval_context`.
- **Readers stay assertion-current.** Existing queries select `validity = 'active'` only; an
  active row whose interval closed in the past is still returned. As-of reads belong to a
  separate change.
- **Transition stage, no live cutover.** Migration `rel_035` adds the columns, checks and the
  occurrence index but keeps `uq_ef_spo_active`, the deployed writer's conflict target. While
  that index exists every temporal argument fails `temporal_cutover_pending` before approval
  parking or any write, so only unknown default rows can exist. Dropping it is a later cutover
  migration that is not in the chain; it needs proof that no old writer is live and that every
  production mutator in `tests/contracts/test_entity_facts_mutator_inventory.py` preserves
  occurrences or refuses first. Until then, rollback is: drain the transition writer, restore
  the old one, optionally downgrade `rel_035` (facts and rel_034 evidence, coverage and approval
  context survive). The downgrade refuses once the legacy index is gone or any temporal value
  exists; after cutover, recovery rolls forward. The proof and enforcement that cutover must
  require (signed receipt, lifecycle fence, gated rel036) are drafted in the
  [effective-time cutover packet](../operations/relationship-effective-time-cutover.md).
- **Mutator fences.** Entity merge locks and plans every affected fact first, repoints rows with
  their packets and evidence intact, and refuses `temporal_occurrence_collision` before any write
  rather than collapse an occurrence that carries effective time. Legacy `contact_merge`,
  hash-addressed contact routes, SPO retraction and `prefers-channel` refuse
  (`temporal_mutator_unsupported` / `temporal_occurrence_ambiguous`) whenever they would have to
  choose between occurrences or drop a packet. `contact_merge` does every Relationship write in
  one transaction that locks both entity rows and every affected fact, re-runs its fence under
  those locks, and commits whole or writes nothing; the memory entity merge runs only after that
  commit. A contact value edit re-locks its hash-selected row before retracting it and refuses
  `contact_fact_changed` (409) if the row was retracted or re-valued meanwhile. Entity forget
  retracts every occurrence as-is and removes their graph edges; Google/Steam hard deletes keep
  their all-version cascade.
- **Lock order.** Every `entity_facts` write locks `public.entities` rows before fact rows,
  each set in ascending id order. Both merges take their entities `FOR UPDATE` first; the central
  writer (and the contact value edit that calls it) takes the subject and any entity object
  `FOR KEY SHARE` (`_lock_fact_entities`) before its first fact lock, so a correction racing a
  merge waits instead of deadlocking. A caller that makes several writer calls in one transaction
  (`promote_entity`'s `initial_facts`) takes all of their entities in one ascending batch
  (`_lock_fact_entities_batch`) before the first, so the order holds across calls too.
  Retract/verify-only paths write no FK column and take no entity lock. A write that waits out
  a merge still lands on the tombstoned source; whether the writer refuses or follows
  `merged_into` is open (bu-gm93xc).

## Meeting debrief

An ended calendar occurrence the owner attended with other people gets one
`relationship.meeting_debriefs` row, keyed `(event_id, occurrence_start)`, so a recurring series
is debriefed per occurrence. The zero-LLM `meeting_debrief` job (daily, 18:00) records the rows
and proposes one batched insight ("Anything agreed?") through the insight broker. A declined
meeting, a solo block, free (transparent) time, an all-day event and a butler-generated event are
never debriefed, and neither is a meeting whose other attendees are all not `active` in
`public.entities.posture`; such a person is never named in the prompt, and posture is re-read at
prompt time.

The owner's reply is recorded by `meeting_debrief_answer` (guided by the `meeting-debrief`
skill): `none_agreed`, or commitments created through `create_commitment` with
`evidence_opened = {source: "meeting_debrief", event_id, occurrence_start, debrief_id}`. The
counterparty must be an attendee of that meeting; an attendee with no resolved entity gives a
null counterparty, never a dropped commitment. `sphere` (`work` or `personal`) is stored on the
commitment only when the owner declares it. The next calendar prep envelope for that person
carries the commitment.

Three consecutive unanswered batches drop the cadence to once a week (the batch that crosses the
threshold says so); any answer restores the daily cadence. Not built yet: the dashboard Debrief
card with a pinned reply (replies go through ordinary routing today), a "work commitments" filter
on the condition ledger, and deriving `sphere` from endpoint custody.

## Implementation Notes

### Meeting debrief answer transactions

The answer handler holds the debrief row and rechecks answerability in the same owning
transaction as every public `create_commitment` call and the final state update. The additive
internal `transaction_connection` option retains the ordinary pool wrapper; it must not reacquire
the held pool connection, so a size-one pool works. Batch preflight reuses common validation.
Required propagated graph/final-state failures and cancellation before commit roll back the batch,
including changes to existing confirmations. Typed invalid responses follow rollback; captured/
none_agreed follow commit. Lost commit acknowledgment remains uncertain, and durable row-lock
retry cannot append a second answer. The job's stale unaskable expiry rechecks pending.

Creation and reconfirmation use `snapshot_complete=False` and produce no resolved transition,
so they do not generate premise amendments. The existing resolved-only premise hook retains its
savepoint/best-effort ordinary-error behavior; this existing partial conformance does not waive the
mandatory resolved-enqueue contract. Conditional enqueue commit/rollback proof uses a real
resolution control. Counterparty/null, evidence, declared sphere and current prompt posture policy
are preserved. Answer-time posture (.69) and free-text/LLM verification (.66) remain separately
uncompleted. The source PR retains separate baseline and corrected-source receipts: the actual
before-fix head `220cf3eb` executed 23 migrated PG cases with 10 causal failures and 13 passing
controls in CI 37286612106. Corrected-source named PG proof, independent review and the protected
gate remain required; source inspection and collection alone are not runtime proof.


- `relationship.facts` is a multi-valued log store: `activity` and `interaction_*` carry many
  active rows per `(entity_id, predicate)`. Contradiction detection in
  `run_fact_retraction_curation` (`roster/relationship/jobs/relationship_jobs.py`) is therefore
  gated to the `_CONTRADICTION_FUNCTIONAL_PREDICATES` allowlist, so a new log predicate can never
  flood approvals. Cardinality cannot be read from `entity_predicate_registry`, which covers only
  the `entity_facts` store.
- `run_interaction_sync_job` reads `switchboard.message_inbox` directly, so `scripts/init-db.sql`
  grants `butler_relationship_rw` read-only access to schema `switchboard` (plus matching default
  privileges).
- Calendar interaction sync reads `relationship.calendar_events` explicitly through the
  Relationship role. It consumes the local Calendar module projection, including its attendee
  metadata, rather than falling back to a public or another butler's projection.
  Calendar query failures increment the existing `errors` result counter and log the failure;
  a missing table emits a warning. The shared checkpoint remains unchanged after a failed
  calendar query, preserving the bounded interval for the next scheduled run while committed
  message interactions deduplicate. A successful empty read advances normally. The scheduler
  still records the returned dict as dispatch success, so callers must inspect `errors`.
- Routine Calendar provider polling remains disabled by default in the checked-in Relationship
  configuration. Existing internal projection, explicit force-sync and mutation projection
  paths remain available. Reading this table proves local consumption only; it does not prove
  provider ingestion or deployed source population, and does not subscribe to foreign schemas.
- `POST /api/relationship/contacts/sync` dispatches to the `contacts_sync_now` MCP tool with
  `{"provider": "google", "mode": "incremental|full"}`. `mode` is strict, and credential failures
  surface as `400` errors pointing at `/api/oauth/google/start`.
- `relationship.facts` and `relationship.predicate_registry` belong to the memory module; domain
  triple-store work uses `relationship.entity_facts` and `relationship.entity_predicate_registry`.
  Interaction facts use `subject='entity:{entity_id}'` (`interaction_log` / `interaction_list`
  still resolve legacy contact UUIDs).
- Active-surface queries share one filter excluding `metadata.archived = true`, `archived_at`,
  `tombstone = true`, `deleted_at` and `merged_into`.
- Dunbar decay counts connector LLM-extraction facts as mentions unless
  `extra_metadata.source == "interaction_sync"`; `email`, `interview` and `calendar_event`
  interactions weigh `0.2`.

## Upcoming date push

The registered `upcoming_dates` tool selects only `public.entities.posture = 'active'`
in both contact-anchored and contactless entity-anchored UNION arms. Existing listed/archive
mapping, ordering, anniversaries and year wrapping remain in effect. The 08:00
`upcoming-dates-check` prompt calls `upcoming_dates(days_ahead=7)`: an empty successful read
is silent, and a failed read must not become a reminder or an all-clear via a fallback query.
This is a read-time selection contract; prompt registration does not prove an LLM obeyed it,
a notification was delivered, or a deployed fleet was exercised.

Memorial, quiet and no_contact people remain available to appropriate history/display readers.
Reactivation makes the date eligible again. The separately adopted calendar remembrance overlay
retains its own policy; this birthday push introduces no memorial opt-in or changed posture writer.

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes people-related messages here
- [General Butler](general.md) -- handles freeform data that is not contact-specific
