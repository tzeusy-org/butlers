## Context and Status

DRAFT CANDIDATE, NOT ADOPTED. Original bu-0ynlk.16 has eight design clauses and four acceptance items; `baseline.json` retains them verbatim. Current actual builder is under `api/`, not the stale `modules/` path. This change authors recognized native OpenSpec deltas only; no skip_specs/ignored payload is counted as a validated specification. .15 is unfinished and unadopted, so placement is conditional.

## Decisions

### 1. Same owner, same final thread

Use the authenticated server principal and final adopted owner-thread/binding resolver. `owner_entity_id=NULL`, caller owner/actor fields, unknown binding or a globally unique UUID alone are not a summary authority. Current by-ID and butler routing helpers are not proof of final owner access. Generator and context consumer get the same request-bound owner/thread scope; neither enumerates foreign threads. Dashboard-origin visible messages and their persisted dashboard-visible replies are the initial input set; no Telegram consumer/input widening is implicit.

### 2. Visible and secret-admitted raw inputs

Select persisted visible `content` only, never hidden prompts, raw tool arguments/results/tool_calls, page_context (even visible_summary), credentials, provider handles or other threads. Trusted server visibility/secret admissibility is representation that future implementation must supply before propagation/enforcement; role names alone are insufficient. Secret/uncertain content cannot be passed to a model as a way to prove it safe. A complete safe prefix must be provable or summary generation is unavailable, with an authorized marked tail only. The current message and original page_context forwarding remain separate existing contracts. Raw history itself is never rewritten/redacted by this feature.

### 3. Exact source and generator provenance

A snapshot orders source rows by trusted `(created_at, message_id)` with UUID tie-break, binds full membership/content/eligibility revision digest and identifies exact `summary_through_message_id`. The current user row is excluded. A late/backdated earlier insert changes the prefix digest. Use a server-issued monotonic `summary_version` for each logical generation; record separate generator/model/exclusion/schema versions and UTC created_at. Generate only from admitted raw prefix, not previous derivative text. Bound input to 1000 messages/64000 code points/one active owner-thread generation/60 seconds through a configured existing ephemeral model lane; no hot-path wait, new tool grants, default budget or provider activation.

### 4. Untrusted derivative, raw truth

An eligible runtime receives a leased private user-context overlay; durable normalized_text/session prompts remain derivative-free. Trusted framing encodes derivative and historical text as data, with explicit untrusted label and through-source reference; model text cannot escape to a privileged message or overwrite current raw message, control pin, actor or attribution. A summary cannot prove an owner command, permission, evidence or citation. Existing registered-tool/risk/approval/audit and source citation boundaries remain. A new generation or evidence-bearing answer reads authorized raw history, never recursive summary-as-fact. No steering-memory writes are introduced.

### 5. Real continuity plus bounded marked fallback

Successful older-prefix summary plus every eligible post-boundary recent raw message provides real continuity. The resolved runtime historical context cap is 4000 code points excluding current text: summary<=1600, encoded tail<=2000, trusted framing<=400. Default last 5 exchange pairs remains at most 10 messages; each historical content suffix<=500. Reserve framing, choose/drop complete oldest entries deterministically and disclose any omission/clipping. Never slice the completed serialized preamble. A missing/failed/stale/over-budget summary uses an authorized bounded raw tail with explicit summary-unavailable/context-truncated boundary/count, not silence or fabricated recollection. Marker-only fallback is failure behavior, never acceptance of the original success outcome.

### 6. CAS, replay and invalidation

CAS checks expected private-store revision and exact source identity at durable publication. Tuple `(thread, through_message_id, summary_version)` binds owner/source/config/output digests. Identical replay has no generation/write; changed same-key content conflicts; stale/non-monotonic worker rejects. Later append can reuse a prefix only if the complete post-prefix interval fits the admitted tail; no gap may be silently dropped. Source edit/delete/hide/rebind or backdated insert invalidates derivative before consumption and fences in-flight work/restart. Content-blind receipt replay cannot resurrect deleted/superseded text. Dashboard ingress/Stop/resume idempotence is unchanged, including current retries rebuilding context while retaining original message_id/page_context.

### 7. No client exposure and exact retention

One governed current durable payload is private internal state across all holders, not merely a .15 row. Replace/discard old full text and generation buffers under the every-holder revocation barrier; minimal opaque receipt/provenance metadata follows raw thread lifetime without retaining payload or extending raw retention. Source removal/hiding disposes affected derivative and buffers; thread deletion removes all associated records/jobs. Archive is not deletion and adds no TTL. No summaries/provenance in client thread/conversation/list/search/message models, SSE, logs/audit/errors, memory or existing MCP recall. Physical public-table placement must preserve effective least privilege under bootstrap; generic public GRANTs/SELECT-star are not a boundary. Future schema/ACL design is separate owner/source allocation, not a migration number in this draft.

### 8. Final .15 placement and ordering

Prefer only final owner-thread identity from .15; no interim summary/last_seen_at column on dashboard_conversations, no .15 migration/schema edits. Before implementation, independently reviewed/adopted/landed .15 must provide canonical owner, binding and stable total-order/source revision seam, while this candidate supplies private payload/visibility/retention integration. Current .15 text still mentions client summary and nullable owner: those cannot authorize consuming this candidate or projecting this private derivative. Integrate the exact private-store projection/exclusion contract under the owning allocations first. Preserve composer PR4396, actions PR4495 and cross-channel/handoff/resume owners.

## Exact Options Requiring Future Adoption

Recommended: the full private raw-prefix candidate above. Status quo: no summary execution and original silent truncation stays unmet. Alternatives require a fresh complete exact artifact after review: interim store with .15 migration protocol; separately specified client exposure; different finite budgets/retention. Recursive derivative-as-source conflicts with clause 2/raw-truth and is not offered as a shortcut. No current owner decision or waiver is recorded.

## Integration, Docs and Verification

See `integration.md` for ordered adoption/placement/source gates and `verification.md` for real future seams. Only this new change directory is edited. No application behavior, schema or API documentation claims a delivered feature. Tests: +0 ~0 -0; future executing cases belong to adopted-change implementation followups, not source-only authoring. All future tasks remain unchecked.

## R1: Full Transit and Custody Correction

The previous normalized_text injection would persist full copies in message_inbox normalized_text/raw_payload, route state and sessions.prompt, then expose them through generic payload/session doors. Those current paths are unsafe for this private derivative; auth/audit, TTL and private-row cleanup are insufficient. Native deltas now require a derivative-free durable source/base view and a separate private attempt binding, resolved only into eligible leased transient user context after safe session creation. Exact system receipt remains honest because no derivative enters its system bytes.

A binding carries canonical owner/thread/current message/turn, accepted request/ingestion identity, runtime attempt, exact generation/source digests, custody epoch and revocable lease. It lives only in final private custody, never exported envelope/request/trace/client fields. Normal registered infrastructure/MCP/config boundaries and final .15 authority must carry it without a foreign SQL or generic LLM tool shortcut. Current implementation of that resolver/transport is UNKNOWN and separately allocated. A marker/caller UUID/model-written context cannot authorize resolution.

Replacement/source mutation/deletion/hiding/thread disposal revokes epoch before publication/resolution/copy-bearing output can pass; cancellation, late completions, restart and lost ack stay fenced until all holders are disposed. One readable durable payload is the full retention choice; transient users are finite and revocable. Backups/WAL/replicas/restores need disposal or unreadable ciphertext with irreversible generation-capability retirement and non-resurrectable invalidation. The exact key/restore protocol is an owned prerequisite, not a claim about current backup safety. Native private-overlay eligibility forbids unknown provider/CLI persistence/resume/cache modes; local cleanup cannot erase remote retention.

Output/tools/errors/process/memory are potential copy holders. Source-backed ordinary answers/effects remain governed normally; designated private payload/excerpts are not admitted simply because a model echoes or relabels them. Unknown copy/raw-source admission withholds the copy-bearing result and uses authorized fallback. Implementation must prove these source/projection seams before private invocation; prompt-only assurances are insufficient.

All original budgets, raw-prefix inputs, same-owner/thread, provenance, untrusted treatment, CAS tuple and private final placement remain. The positive runtime summary-plus-tail case is explicit and mandatory; missing integration/provider proof is failure behavior, never replacement of the original outcome. R1 closure and original AC1 semantic acceptance remain independent-review gates. AC2/AC3 remain unearned. See custody-map.md for every holder and integration.md for ordered owning prerequisites.

The private composition/use receipt describes the complete actual user input (base/overlay/effective digests and byte/codepoint counts, deterministic composition/framing/order), exact persisted system-receipt linkage and source/generation/attempt/epoch/lease/mode. The eligible adapter must actually receive the full intended context; a digest/marker/base-only invocation cannot fulfill success. Original full exact system-prompt receipt bytes and all receipt scenarios remain. Existing full user-prompt views become full admitted stored-base views with honest omission for this future summary path; this necessary custody amendment requires exact adoption and owning integration, rather than claiming those views show the private runtime input. Private content-blind user-input receipts do not expose/reconstruct retired text. Provider, temporary-file, replica/WAL/backup/key/restore retirement remain proposed UNADOPTED owned work with current proof UNKNOWN.
