## Context and funnel

Baseline: public `main` at `08d03fa7e64c1f6f8047d961e3e8cece327ab0a2` on 2026-10-15. Original bead: `bu-0ynlk.18`; parent pursuit: `bu-0ynlk`; definition release gate: `bu-eu1mo` closed. Size: Medium. This is a concrete proposal for owner review, not an adoption record.

- Gate 0: doctrine, RFCs, capability specs, topology, and craft standards are present. Doctrine and specs govern behavior; topology locates the seams.
- Gate 1 motif: brief continuity without an unexpected destination. The operator opens chat while investigating a page, resumes a recent question in that family, or gets a fresh composer with the earlier thread one explicit action away.
- Gate 2: aligned with `heart-and-soul/vision.md` § What Success Looks Like (mental labor absorbed and trustworthy behavior), `design-language.md` § What the Dashboard Is Not (secondary operator lane, not a chat application), and security's sole explicit page-context path. No authority or new content collection is introduced.
- Gate 3: global selection belongs above the `ChatDock`/`WidgetPanel` remount boundary in the existing shell, separate from instance-local turns. `ChatPage` resolves explicit route intent; `ChatRecallCommands` remains always mounted. Existing API list/detail reads supply identity/status only.
- Gates 4-5: Decisions 1-7 and the two complete MODIFIED requirements define bounded observable behavior, including edge cases. No canonical spec is edited.
- Gate 6: the spec-only verification recipe and future owning component behavior suites are in tasks.md. Owner adoption and implementation filing remain incomplete.

## Current evidence and preserved contracts

`ChatDock.tsx` uses a `hasResumedRef` effect to select `conversations[0]` after a nonempty list arrives; `FloatingChatWidget.tsx` does the same inside a panel remounted at every open. Neither checks age or page family. Empty lists do not settle the current ref, so a later result can select a thread after the owner starts fresh. Both hold one `inputValue` per mount rather than a delivered shared per-identity draft store.

`useConversations` reads the default active list, stale for 10 seconds, with default page size 20 and server `updated_at DESC` ordering. `ConversationSummary` contains no owner-use or route provenance. `updated_at` can move for messages, title/status changes, and routing. `latest_assistant_reply_at` is the unread watermark, not owner intent. `GET /api/conversations/{id}` resolves exact identity, butler, and status; messages are a separate default-50, maximum-200 bounded ascending read. No oldest/first message, list absence, latest assistant timestamp, or raw page-context scan can establish a local resume hint.

`useConversationTurn` already owns immutable client message IDs and send-time page-context capture. The backend persists the user turn before returning the successful SSE response; a new id arrives in `conversation_created`. Stop and unknown outcomes keep their durable-recovery semantics. `ChatPage` uses explicit cross-butler route identity and bare `/chat` starts fresh. Full-page fragments, unknown-id recovery, independent live turns, and the butler-detail panel are preserved.

The owner adopted `persist-safe-dashboard-unsent-drafts` on 2026-09-12, exact PR #4056 commit `d2dc32ffb001398af169363064b7610595512f9d`, as recorded on `bu-2jtfw.15`. Its browser-local shared store and exact-attempt recovery seam are not delivered in this baseline. Its old chat IDs `004`/`005` collide with canonical Message Thread Display; public bookkeeping PR #4497 proposes `014`/`015` without changing behavior. Refer to requirement titles until that bookkeeping lands. The contract, including remount/reload restoration, remains binding despite the code gap.

PR #4396 at `e681a7a8fa089ebd5ca9cbb5b64c9779f0294d30` proposes the queue/composer amendment for `bu-0ynlk.14`; its full amendment awaits owner adoption. The owner separately chose Option A on 2026-10-09: retain queued text under the adopted draft policy, restore as Not sent, never persist queue auto-send intent, queued message ID, or context. This proposal preserves that choice; it does not adopt or implement the rest of the sibling. The in-memory per-identity composer helper is that sibling's seam, later backed by the one adopted IndexedDB store. Whichever implementation lands second adapts to the seam instead of creating another store.

## Decisions

### 1. One candidate and a 30-minute half-open window

Candidate means the last durably accepted local owner submission to a Switchboard conversation through the global dock or popover in this dashboard lifetime. Keep only one observation. Every newer confirmed local global submission replaces this one candidate. If the latest confirmed submission has an ineligible route or query, a `none` policy, or no eligible prefix, clear the prior candidate instead of retaining an older eligible thread. A submission whose acceptance is unknown does not clear or replace a confirmed candidate merely by being attempted; its unresolved-attempt ownership still takes precedence. Do not search a map of prior families for an older match.

Record `{conversationId, prefix, acceptedAt}` in tab-local memory above the global-posture mount boundary. This is selection metadata, never a draft or message record. No localStorage, sessionStorage, IndexedDB, BroadcastChannel, URL, server field, log, telemetry, or provider payload is added for it. Reload, new tab, shell destruction, and invalid time observation remove its evidentiary value. Sharing a browser profile does not share a resume choice.

Use elapsed local time with the precise predicate `0 <= age < 1,800,000 ms`. A backwards or invalid observation fails closed. The age is checked at the one entry decision; expiry later does not eject an open thread. A successful create/send response proves persistence of the exact attempt. For new conversations, retain the acceptance instant while waiting for that same attempt's conversation id; do not start the window again on `conversation_created`. A detached, stale entry cannot mutate its successor. Unknown acceptance remains unknown and does not renew the hint. Queuing alone is not a submission; actual dispatch acceptance can renew it.

Why 30 minutes: this is a proposed default for a short investigation or immediate follow-up, not a measurement or an existing owner decision. Five minutes would routinely break a task interrupted by reading a page; hours or a day would bring an earlier task back on a later visit. Thirty minutes is finite, easy to test at the boundary, and allows explicit recall for all other cases. The owner reviews this exact choice before implementation.

### 2. Prefix preserves registered route and ownership scope

Resolve the actual current pathname against the existing route registry, including dynamic parameter patterns, without using its permissive Dashboard fallback as proof of registration. Normalize only the query/fragment exclusion and a single trailing slash. Match static segments case-sensitively. Empty dynamic values, duplicate separators, encoded separators, malformed paths, and unmatched routes are ineligible. Do not percent-decode a static segment into a different family.

For a known non-chat route whose context policy is not `none`, a static registered pattern uses its first literal path segment, prefixed with `/`; the root maps only to `/`. If the registered pattern has dynamic parameters, retain the concrete path through the last dynamic segment, including every intervening literal and bound parameter segment. Compare these keys for exact equality, not startsWith. Static routes win before dynamic patterns, as in the current registry. A route category such as `operations`, `detail`, or `settings` never defines equivalence. Thus `/health/conditions` and `/health/medications` share a task family, but `/butlers/health` and `/butlers/finance`, `/sessions/S1` and `/sessions/S2`, and `/entities/A` and `/entities/B` are distinct. No query value, visible_resource payload, title, label, or prose enters the key. Bound path identifiers exist only as part of this one tab-memory scope key and are never persisted, logged, synchronized, or attached to a request by the resume mechanism.

The distinction follows actual ownership contracts: ButlerDetailPage takes `name` to select the butler; SessionDetailPage takes exact `id`; the manifest's fact/rule/episode/approval/bead prefetches query the exact bound identity; ConnectorDetailPage has two independent scope parameters; QA detail pages bind a patrol or investigation attempt. A shared first segment would silently treat different targets as the same context. Static aggregate health pages have no bound record or owner in their route, so the broader `/health` match is deliberate. The route registry remains the single authority, without a parallel hard-coded ownership list.

| Pair | Result |
| --- | --- |
| `/health/conditions` and `/health/medications` when registered | Same `/health` |
| `/entities/A?tab=facts#x` and `/entities/A/` when registered | Same `/entities/A` |
| `/entities/A` and `/entities/B` | Different record scope |
| `/butlers/health` and `/butlers/finance` | Different butler scope |
| `/butlers/health?tab=overview` and `/butlers/health?tab=logs` | Same `/butlers/health`; query tabs do not change the target butler |
| `/sessions/S1` and `/sessions/S2` | Different session scope |
| `/memory/facts/F1` and `/memory/rules/F1` | Different kind and record scope |
| `/ingestion/connectors/telegram-bot/E1` and `/ingestion/connectors/telegram-bot/E2` | Different endpoint scope; changing connector type also differs |
| `/qa/patrols/P1` and `/qa/investigations/P1` | Different detail kind and identity |
| `/health` and `/healthcare` | Different; an unknown route is ineligible |
| `/` and `/spend` | Different |
| `/settings` and `/secrets` | Different; `/secrets` has `none` policy |
| `/settings/models` and `/settings/permissions` | The latter is ineligible under its `none` policy |
| `/chat` and `/chat/C` | Both excluded from this automatic policy; route intent governs |

The current pages also carry explicit scope in queries: SessionsPage selects `selected` and filters owning `butler`/URL key `request` (the internal field is `request_id`); the ingestion event drawer and timelines use `event`/`trace`; QA uses `case`/`butler`; DecisionsPage uses `bead`; NotificationsPage uses `notification`/`butler`; Spend and the ingestion filter pipeline use `rule`; CalendarWorkspacePage selects `source`/`calendar`; ApprovalsPage can use `review_source`. Presence of any of these exact query key names (`butler`, `selected`, `request`, `request_id`, `event`, `trace`, `case`, `bead`, `notification`, `rule`, `source`, `calendar`, `review_source`), even empty, excludes both seeding and automatic entry. The existing `request_id` exclusion is also retained as a conservative alias; the actual SessionsPage URL key is `request`. No selector value is retained; the conservative fresh choice avoids an unreviewed parallel query-identity scheme. The rule is global for those key names, even on a route that would ignore the parameter.

Examples: `/sessions?selected=S1` and `/sessions?selected=S2` are both automatic-ineligible, as are `/sessions?request=R1`, `/sessions?request=R2`, and `/sessions?request=`, along with `/ingestion?event=E1`, `/timeline?trace=T1`, `/calendar?calendar=C1`, and `/qa?butler=health`. `/butlers/health?tab=logs` remains eligible with scope `/butlers/health`. Date/range, pagination, display-mode, and ordinary search filters deliberately leave the aggregate prefix unchanged. This is a declared family-level continuity choice, not equivalent-resource or equal-page-context proof; the current chip and fresh send-time capture still ground a later message. Future route/query ownership changes require revalidating this bounded projection and its behavior cases, rather than silently interpreting a new target selector as presentation.

Current dynamic-pattern disposition (complete registry population): `/butlers/:name`, `/sessions/:id`, `/entities/:entityId`, `/memory/facts/:factId`, `/memory/rules/:ruleId`, `/memory/episodes/:episodeId`, `/approvals/:id`, `/beads/:beadId`, `/ingestion/connectors/:connectorType/:endpointIdentity`, `/qa/patrols/:patrolId`, and `/qa/investigations/:attemptId` retain every bound identity through the final parameter. `/chat/:conversationId` is the twelfth pattern and is excluded because its explicit route id governs. No nested butler/session route is declared at this baseline; a later registered dynamic route follows the same scope-preserving rule rather than defaulting to a bare family.

Context-chip inclusion does not create or suppress this coarse in-memory observation; the route's registered policy does. A detached context chip still omits page_context from the message. This proposal never attaches a hidden resume hint or cached route context to a send.

### 3. Positive candidate eligibility, one settled entry

Before automatic selection, positively verify the exact candidate as active and owned by Switchboard using existing identity/status reads. A bounded list containing that exact valid record can supply it; absence cannot prove deletion, and the existing exact-id GET can resolve it without a scan or a new endpoint. A fresh entry does not wait indefinitely for proof: it remains a usable fresh composer while the existing read is pending or fails. There is no added retry loop, pagination scan, background polling, or new timeout value. Existing request/error bounds and retry controls govern.

A pending eligibility result may settle the still-unselected, empty entry once. Typing in the fresh composer, New conversation, explicit selection, leaving the entry, or a change in current route before settlement invalidates that result. Route changes after settlement never choose a different conversation. A later retry is useful for explicit recall, but cannot reopen a settled automatic decision. Re-rendering and cache updates are not new entries. Reopening an unprotected popover or changing global posture can begin a new entry; its shared tab hint survives, while its own stream state does not mirror another instance.

### 4. Precedence protects deliberate and unsent identities

Apply this order:

1. A full-page route's explicit id, or bare `/chat`'s explicit fresh composer, uses its existing route contract.
2. An explicit history, search, keyboard, palette, Earlier conversation, or New conversation choice wins.
3. A current retained or explicitly restored identity with unsent text, queued text, or an unresolved submission keeps its ownership. A current fresh draft protects `new`; a current selected C protects C. Never enumerate storage to select some other draft identity.
4. Only an otherwise unselected and empty global entry considers the single automatic candidate.
5. Every other case starts fresh, with explicit recall where available.

Opening C restores C's matching eligible draft. The decision does not read every draft or migrate text from `new` to C. A saved draft under another identity remains available when that identity is explicitly opened, including after reload. This does not add a promise to remember an arbitrary prior selected id across reload: the shared draft contract restores content once the exact surface identity is opened; it is not a global conversation selection history.

### 5. Preserve adopted text, queue, and attempt ownership

No new storage policy is introduced. Preserve 24-hour sliding expiry, 64 KiB, immutable content revision separate from the store fence, transactional tombstones, explicit cross-tab conflict choice, and no timestamp last-write-wins. Switching never copies or merges text between identities. A restored draft uses current context at eventual send. A successful attempt clears only its exact submitted revision; later edits, byte-identical recreation, expiry/reset successors, and compaction remain protected.

When queued-turn support is present under its governing amendment, an explicit departure or posture unmount demotes a still-queued item under that contract: queued text precedes existing composer text with one blank line, its ephemeral unsent message ID and context snapshot are discarded, and it does not auto-send on return. The owner's Option A stores only the text in the adopted record and restores Not sent. The queue's only release remains a committed prior `message_complete` while the item is still queued in that original mounted turn. A dispatched item follows the existing attempt/retry contract and retains its immutable message ID; this proposal never replaces it with a demoted duplicate.

An unknown or interrupted submitted attempt stays owned by its original identity even after an explicit choice. Detaching a watch is not server cancellation. No resume decision replays, text-matches, rebinds an attempt, synthesizes terminal success, or resolves a message absent from a bounded read by assumption. The separately approved content-blind exact-attempt resolver (`bu-z4eqza`) and adopted shared-store implementation remain prerequisites for the complete promised reload recovery, not backend work imported into this bead.

A controlled departure with unsent text must preserve it through the adopted store contract. If save plus read-back is unconfirmed at 1 second, remain on the current surface and offer Keep editing, Retry saving, and Close with save unconfirmed with the adopted warning. An automatic choice never invokes close-with-uncertainty for the owner. Denied/full/blocked storage keeps current-memory text and discloses the recovery limit; it does not relax successful-storage remount/reload guarantees.

### 6. Preserve route, turn, and neighboring feature boundaries

The full original Global Chat Postures clauses and three scenario bodies remain. Exactly one posture, the existing breakpoint/collapse behavior, one shared turn implementation, and independent per-instance live turns are not amended. `/chat/:id` cross-butler resolution, 404 state, copy-link, focus anchor, and bare `/chat` behavior remain unchanged. The per-butler ChatPanel auto-selection policy is not widened to this global policy.

The existing shell currently appears to mount a dock alongside `/chat` at large viewport widths; that is an existing mismatch with the canonical mutual-exclusion requirement, not a new permission to drop it or an implementation change authorized here. Future selection work must preserve the adopted boundary and route implementation defects to their owning work rather than quietly changing the spec.

### 7. A quiet explanation and one explicit way back

On an automatic resume, show Resumed recent conversation plus title or Untitled conversation and the existing New conversation action. On fresh entry, show New conversation and, when an earlier active Switchboard thread is available, an Earlier conversation link with its title. Prefer the verified local candidate, even when stale or another prefix; otherwise use the most recently updated active row already loaded for recall. That fallback affects disclosure only. Do not manufacture an unavailable target or infer an archived thread from a missing list row.

Activating Earlier conversation navigates to that exact `/chat/C`, which supplies an explicit id; it does not change the default in the background. Existing cmdk recall remains always mounted, registered once, and independent of hint availability. Keep its title fallback and retry command on source failure. No shortcut is added: Cmd/Ctrl+K is already the discoverable recall path.

Use native link/button semantics, visible sentence-case text, a target-specific accessible name, the global focus outline and the Dispatch coarse-pointer 44 by 44 floor. The settled choice gets one polite status announcement per entry; no repeated announcements on polls and no focus steal from the composer. Disclosure uses Body/Mono metadata, not a decorative Voice paragraph, additional badge, colored success card, animation, or modal. Current focus/escape/restore behavior and unread announcements remain intact.

## Alternatives and limits

Using server updated_at was rejected because it measures more than owner use and has no route provenance. Scanning message page_context was rejected because bounded ascending history is incomplete and restoring old context is a different contract. A server field or browser-persisted resume history was rejected here because server/cross-device resume belongs to `bu-0ynlk.15` and a new persistent choice needs its own policy. Per-prefix history was rejected because it silently selects an older task. Holding the entire feature until a generic global permission check was rejected: drafting is authorized, and only exact owner adoption gates implementation.

## Verification, migration, and adoption

Spec-only phase: native scaffold, exact five-file write, full predecessor preservation, strict OpenSpec, overwrite guard, full ordinary guards, frozen trace before/after diagnostics and affected qualification against the stated baseline. New mandatory IDs will have future implementation citations, not fabricated current tests. Current legacy-ID-backfill checker qualification is reported separately from strict/overwrite/guards; do not remove the IDs or alter a canonical baseline to silence it.

After adoption, implement from the exact adopted change with behavior tests at both named posture seams. Cover 30-minute boundaries, all prefix cases, local accepted-at binding and late-created events, missing/archived/foreign candidates, New/typing/refetch races, explicit route precedence, unchanged current selection, draft/queue/unknown-attempt ownership, denied/blocked storage and bounded explicit departure, keyboard/focus/one-time disclosure, and unchanged recall errors. Reuse existing hook/route/recall suites for integration. Do not write source-grep copies of behavior tests or run product tests as evidence for a spec-only proposal.

No database migration or storage key is introduced by this selection proposal. A future frontend rollback clears the in-memory selection hint and restores the prior selection policy while leaving adopted draft records and attempt recovery intact. Owner adoption must name the final proposal/design/spec commit or digest, including the 30-minute window, registered prefix semantics, tab-lifetime scope, and precedence. No adoption is recorded here.
