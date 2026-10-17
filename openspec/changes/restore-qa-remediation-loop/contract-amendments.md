# Proposed RFC0015 and QA manifesto amendment payload

Status: proposed, held for exact owner adoption with this changeset. These are precise successor clauses, not claims that current runtime behavior already conforms. Before affected implementation is released, apply this payload as an explicitly adopted amendment to `about/legends-and-lore/rfcs/0015-qa-staffer-discovery-investigation-pipeline.md` and the named sections of `roster/qa/MANIFESTO.md`. Preserve all unrelated text, particularly the adopted restricted publisher, authenticated dashboard inbox, policy projection, independently supervised liveness and sanctioned read-view boundaries. B owns notes/outcome clauses; C owns admission/lifecycle clauses; D owns source progress; F owns metric/recovery wording. Their PRs must carry the corresponding doc changes, not defer them to closeout.

## RFC0015 D1: discovery protocol and source coverage

Replace the interpretation that the list-return method alone certifies a complete scan with:

> `DiscoverySource` retains `name` and `discover(lookback_minutes: int) -> list[QaFinding]` for existing callers. Every source used by the patrol coordinator additionally implements `discover_batch(lookback_minutes: int, checkpoint: SourceCheckpoint | None) -> DiscoveryBatch`. The batch carries findings, source generation, next checkpoint, covered boundary, complete/partial/unavailable status and optional gap reason. `discover` is a projection of that same bounded batch, never an additional scan or competing queue. The coordinator alone advances trusted progress, atomically with persisted findings or explicit durable exclusions. All paths are deterministic and bounded before reading/materializing unbounded source data.
>
> The normal lookback is overlap, not a discard boundary for retained unread evidence. Source generation changes, unavailable reads and lost retention ranges produce explicit gaps. For file sources, retained rotations are read when needed and supported; otherwise the missing interval is unknown. Database/view sources use stable occurrence identity plus replay overlap. Snapshot sources report their current observation boundary; no source fabricates historical coverage. A source with no replay history, such as the ordinary volatile relay, cannot claim durable capture before persistence.

This explicitly supersedes D1's current-file-only/skip-rotation filtering sentence. It does not replace the active durable-dashboard change's authentication, canonical fingerprint, internal receipt, fenced inbox or volatile ordinary-report clauses. `qa-log-scanner` remains list-protocol compliant through its projection; the new batch requirement is an extension, not a contradictory return type. D consumes the source successor from `bu-dss7he.13`; no log-only coverage may be dropped under the protocol amendment.

## RFC0015 D2: triage and deferred work

Replace “clears all three checks is novel and eligible” with:

> Clearing the fingerprint fast paths makes a finding a candidate for incident-aware admission. Trusted common occurrence/session/request/condition or verified goal/invocation lineage may join source observations; message similarity cannot. Material evidence/configuration/deployment/severity change can qualify new reasoning, subject to every normal gate and remaining lifetime budget. Cooldown or dismissal expiry, count-only changes and polling time cannot reset an unchanged supported or inconclusive outcome. Source observations remain individually inspectable.

Replace D2's parenthetical “gate 6, novelty gate via `SELECT … FOR UPDATE`” with “the post-eligibility atomic admission/novelty claim in D4, after active-condition and incident-state checks”. Clearing the triage fast paths never itself authorizes that claim.

Replace fetch-and-clear `dispatch_queued` handling with:

> Pending work is persisted separately from attempt status. A fenced lease claims it without erasing its pending disposition. Acknowledgement and authoritative attempt linkage or explicit final suppression commit together. Lease expiry permits a successor claimant; stale fences cannot acknowledge it. Capacity deferral and crashes never rely on rediscovery beyond the source lookback. Persistence unavailable means no new reasoning launch.

## RFC0015 D4: exact admission order

Replace D4's introductory “authoritative 10-gate sequence” sentence and entire numbered list with this successor. Apply the same sequence to the QA manifesto's **Investigation Dispatch Policy**, replacing its claim-first four-step list:

> After triage's fast checks, deterministic dispatch applies these gates in order:
>
> 1. Enforce the no-recursion guard, including QA meta-review routing and verified goal-origin or engineering-executor provenance under RFC0038; never trust caller-asserted provenance.
> 2. Enforce owner policy and opt-in.
> 3. Accept triage's canonical fingerprint, not an unvalidated caller hint.
> 4. Persist the finding and its durable pending disposition before any launch. Preserve the existing best-effort source-session fingerprint projection where applicable; its failure does not replace required durable QA persistence. Unavailable required persistence refuses launch.
> 5. Enforce severity eligibility (default threshold 2).
> 6. Enforce cooldown.
> 7. Enforce the QA-only concurrency cap (default 2, `qa_patrol_id IS NOT NULL`); preserve the shared global semaphore. Capacity deferral remains durable pending work.
> 8. Enforce the execution circuit breaker.
> 9. Resolve and pin an eligible model in the `self_healing` tier. Missing eligibility is a prelaunch dispatch decision, not an execution failure.
> 10. For `infra_state`, match explicit canonical source and fingerprint against active `open` or `aging` conditions. A match records `infra_condition_open` and returns before `create_or_join_attempt`, without creating, joining, deleting or changing a healing attempt, worktree or session.
> 11. Check material evidence revision, typed prior outcome, durable pending work, remaining incident-generation lifetime budget and publication lineage holds. These checks cannot bypass any earlier protection or turn unchanged evidence/cooldown expiry into new work.
> 12. Only after all checks pass, atomically revalidate admission state and the checked evidence revision, perform the authoritative novelty claim (`create_or_join_attempt` / row lock), and reserve the launch with durable attempt/session/phase lineage. Concurrent losers join or defer without launching twice. A changed model resolution repeats admission; an uncertain prior launch reconciles its receipt rather than relaunching blindly.
>
> Worktree preparation and credential-free QA execution follow successful admission, with the existing finite phase timeout and smaller remaining lifetime budget. Rejections before the first investigation session launches persist an explicit dispatch decision and `qa_findings.dedup_reason`, never an execution failure or breaker failure event. No pre-claim rejection manufactures an investigation attempt; rejected or deferred pending work receives its authoritative disposition through fenced acknowledgement. Existing no-user-delivery, restricted publisher and human merge boundaries remain unchanged.

Also replace RFC0015's fleet-condition appendix reference “dispatch Gate 5.5” with “the pre-claim active-condition suppression gate in D4”. Preserve its feature flag, episode lineage and legacy-condition carry-forward behavior; this is a cross-reference repair, not a new suppression policy. C applies all D2/D4/appendix/manifesto ordering replacements together before or with admission implementation.

## RFC0015 D4/D5: launch, notes and outcome axes

Replace post-return phase bookkeeping and inherited checkout notes authority with:

> Core owns the QA-bound, lifecycle-fenced launch capability. Initial and follow-up execution use QA prompt/configuration, session store, accounting and pinned model resolution. Attempt/session/phase lineage and launch reservation exist before execution. Uncertain launch is reconciled, not assumed absent. The isolated checkout contains an invocation-fresh dispatcher-created output directory outside tracked paths, excluded from every export, with no credential/publisher-state access. A quiesced writer and bounded no-follow descriptor bind notes to trusted invocation/session/attempt/evidence/base/head metadata; parsing or process success alone never establishes diagnosis.

Add the exact terminal mapping from `qa-investigation-dispatch` → **Investigation Outcome Recording / Terminal lifecycle and retention for no-PR results**. This intentionally extends existing attempt statuses with `already_fixed`, `design_required`, `proposal_ready` and `held`; every status constraint, writer, reader, active-attempt query and API projection must be updated together. `proposal_ready` and `held` are nonterminal. Supported no-PR completions, PR merge and PR closure have authoritative `closed_at`; recovery remains independent. Retain anonymization_failed for existing validation refusals; other terminal publisher refusal uses failed with typed publication outcome. Raw evidence retention still uses that terminal timestamp. All breaker consumers use the idempotent session-completion event projection (initial and follow-up) ordered by authoritative ended_at after auditable reset cutoff, never attempt closed_at. PR creation, merge, closure and publication-only failure add no execution event. Late pre-reset completions cannot revive the old streak. Session execution outcome, attempt lifecycle, diagnostic outcome and publisher receipt remain separate axes.

Add lifetime work bound: at most three launched sessions and sixty cumulative active minutes per incident generation, shared initial/follow-up budget; apply the smaller remaining budget or existing per-phase limit, exclude human-review wait, never reset on restart/cooldown/new feedback. Exhaustion records operator action needed. This introduces no budget-reset/retry endpoint.

## RFC0015 D6: crash recovery and completion assurance

Replace the sentence that undispatched novel findings are not durable and will be rediscovered with:

> Persisted pending findings survive daemon restart and source retention/lookback expiration. Recovery reclaims expired leases with a new fence and reconciles existing launch receipts before any relaunch. Discovery progress commits only with durable disposition. Complete clean/findings-dispatched/suppressed scheduled patrols with matching enabled-source provenance can renew discovery assurance; bounded partial or unavailable sources cannot. Patrol liveness and discovery completeness are separate facts. Independent observer supervision, administrative-policy checks and dashboard-inbox acknowledgement remain unchanged.

## RFC0015 D7/D8: truthful dossier and metrics

Replace inherited-note, forced Retry, merge-as-repair and missing-PR escalation implications with the exact proposed `qa-dashboard` requirements. Keep current routes, force patrol, reset, dismissal authorization and session evidence doors. `proposal_state` includes ambiguous/unavailable and only trusted publisher evidence proves unpublished. Automated review-comment posting/resolution is outside QA publication authority; local response drafts can be handed to the operator.

Recovery requires matching deployment and a safe passing reproducer or complete relevant recurrence opportunity, with explicit check/workload, boundary and result. Idle or partial scans cannot establish recovery. Funnel units/cohorts and usage coverage are the explicit summary contract; no missing cost becomes zero or model-name-derived spend. No deployment or merge capability is introduced.

## QA manifesto sections

Apply the corresponding clauses above to **Responsibilities** (Triage, Investigation dispatch, Investigation notes, Journal capture), **Discovery Sources**, **Issue Triage Policy**, **Investigation Dispatch Policy**, **Failure Modes and Recovery**, and **Observability**:

- Replace `./.qa/investigation_notes.json` with the invocation-fresh isolated checkout output described above; preserve its private raw evidence field and retention policy.
- Describe pending work as fenced durable claim/ack after finding persistence, not rediscovery or cooldown-only retry. State source progress, partial coverage and ordinary-relay volatility.
- Add separate diagnosis/execution/publication/recovery facts and lifetime budget. Worktree/prelaunch refusal is a dispatch result; missing-token/rate-limit publication resumes retained immutable proposals only through trusted publisher state. No blind coding or remote retry after cooldown.
- State lifecycle closure/retention mapping and that successful process exit or merged PR alone is not recovery. Unsupported/inconclusive outcomes cannot reset the execution breaker as successful resolution. Existing no-user-delivery, read-view, no-merge/review/approval/queue and anonymization boundaries remain verbatim.

## Application and overlapping-change order

Owner adoption must identify this exact payload plus the existing active changes. Apply documentary amendments before or alongside each affected behavior, while the implementation gate remains held until source acceptance. For archive, first synchronize the durable-dashboard, control-plane, restricted-publisher and `generalist-lead-focused-delegation` owners, then rebuild this changeset against their resulting baseline, diffing every retained scenario and authorization clause. RFC0038 D18 retains QA as an engineering executor with shell/worktree and empty MCP, without situational context; this proposal does not turn it into a lead/child session. At generalist cutover, reactive reports use only the deterministic Switchboard service-plane relay, not the deleted generic `route` tool or a session listener, and start no goal. Discovery consumes the sanctioned goal-invocation/executor-session view with its existing exclusions; trusted verified goal origin drives recursion checks for invocation failures. Preserve those adopted clauses when composing discovery and triage changes. This proposal neither implements nor gates on the entire generalist runtime rollout. Never archive a stale whole-requirement block over a newer parent. A material predecessor contract change returns here for reconciliation and fresh owner adoption; it does not authorize a worker to choose a new design.
