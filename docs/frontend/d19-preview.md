# D19 dark preview

The adopted [D19 change](../../openspec/changes/dashboard-one-mind-goal-surface/design.md)
governs this preview. It is a partial implementation of bu-vvsfi8, not M6 activation or
completion of that bead. The existing dashboard, navigation, Day Sheet, approvals and
unit controls remain the default.

## Admission and routes

`VITE_D19_PREVIEW=true` explicitly registers `/d19`, `/d19/goals`,
`/d19/goals/:goalId`, `/d19/regions`, `/d19/regions/:region`, and
`/d19/approvals[/:id]` at build time. Any other value leaves these routes and
their command-palette entries unregistered. No browser preference or query parameter
activates them. The default sidebar receives no new entries. The enabled shell registry
provides the preview's finder destinations and bounded read warmup; preview page
context is never attached to chat.

`?view=operator` selects the region's read-only projection. It renders names,
explicit enabled state, service status and guidance names from existing reads; raw
module configuration, prompts, credentials and error bodies are not displayed.
Finance, travel, home, lifestyle and general compose their existing domain components.
Other regions retain named doors to the existing domain record workspaces.

Goal reads mirror `src/butlers/api/models/goals.py` and use the existing API client,
owner authentication, abort handling and cursor envelope. The list resets its cursor
when filters change. Goals poll in the foreground every 30 seconds until the ledger
has an event-bus invalidation contract. Queries do not substitute stale or previous-key
results after failure. Source degradation remains distinct from a confirmed empty page.

The approval preview is a read projection of the existing waiting queue and dossier.
It displays the server's safe exact-call preview, digest and producer lineage; it never
reconstructs recipients or origin. Eligible pending previews open the current authorized
review form. Cancelled/history/unknown dossiers remain read-only here. Full waiting,
stalled, history, undo, safe retry, edits and autonomy behavior stay in the current
Approvals workspace. The preview grants none of those transitions itself.

## One voice

`BUTLERS_D19_ONE_VOICE=true` is a separate, default-off presentation switch in the
notify-to-Messenger delivery path. It changes only the source-prefix formatter used
when materializing native delivery commands and owner-only approval notifications,
including recovery delivery. Ordinary sends/replies first resolve their actual transport
target through the server's owner-channel resolver. An owner who spoke in a group is
not proof that the group is an owner destination. Without a verified owner target,
the existing prefix remains. The default-off path performs no additional identity read.

The exact materialized call still goes through the existing approval gate; provenance,
canonical digests, receipts, origin and exposure are not rewritten. A stored leading
source marker can be removed for the verified source only; quoted markers and unknown
historical authors are not guessed or rewritten. No runtime, deployment, credential or
feature setting was changed to publish this implementation.

## Remaining acceptance and dependency map

The coordinator authorized the useful dark subset with unavailable presentation for
absent contracts. Every original acceptance criterion remains in force. The full bead
must stay open until these remaining items have their owning contracts and evidence:

| Required behavior | Current boundary and exact dependency |
| --- | --- |
| Per-goal Stop and durable unconfirmed/settled receipts | No callable service route; bu-dhloi1.255, blocked by bu-jnhxc5. No actionable Stop is rendered. |
| Hold, optional until, Release, Stop-all and five-surface persisted status | No published client DTO/operations; bu-idcqzy, blocked by bu-o9qax4. The shell names status and counts unavailable. |
| Completed goals today and delivered owner replies | The goal list filters `created_at`, not completion time; no completion/delivery-coverage aggregate exists. The hero is unavailable rather than a fabricated count. |
| Research ledger, prompt/model evidence, independent coverage and safe research doors | Requires bu-6qaju2.4 and the actual read-model owners; the landed goal DTO supplies none of these fields. |
| Grant ceiling, narrowed-by, remaining/limiting budgets, pricing and exact receipt navigation | Absent from the landed goal DTO. Stored budget counters, deadline, causing IDs and references are shown; missing metadata is not inferred. |
| Situations and `/now` | Typed situation reads and the `/now` seam remain with bu-dhloi1.21; region case integration remains bu-h40h2b.8. This lane creates no competing case reader. |
| Full standing orders and a shared object-verb registry | bu-h40h2b.11 and bu-h40h2b.15; current summaries contain only enabled upcoming obligations, not source/last-run/cursor metadata. |
| Complete future approval transitions, promotion exclusions and five-surface parity | Current authorized Approvals retains its own contracts. This preview adds read-only lineage and review doors; it does not adopt the future backend transitions. |
| Waiting goals, owner threads, continuity, cross-channel reply binding, decision sets and answer/QA/replay successors | Remain with their adopted continuity/admission and presentation owners; these APIs are not implemented by this preview. |
| Authenticated dev render and screenshot review | Pending an authorized dev build with the flag enabled, owner authentication and the required services. Synthetic fixtures do not establish this acceptance. |
| Independent review, required checks and landing | Required on the actual PR head through the repository's PR and merge-queue route. Source merge alone does not close activation-inclusive acceptance. |

The page tests cover the implemented subset at the actual page/API-client seam,
including empty/degraded/error and retry, opaque cursors/filter reset, exposure,
invocations/effects/budgets, stored links, read-only Operator and cancelled approvals.
They do not claim one passing test for every scenario in the entire D19 specification.
The copy extractor emits an on-demand inventory; no copy inventory is tracked in
this repository. `make check-guards` remains required.

## Synthetic rendering

`frontend/src/pages/d19/__fixtures__/ledger.json` contains synthetic ledger, region,
service and cancelled-approval records using reserved `example.test` addresses.
The local render uses these records through an isolated fixture entry, the same page
components and the real typed client; it performs no live service mutation.
The screenshot review below is synthetic, not authenticated dev evidence.

| Synthetic page capture | Comparison with the adopted mockup |
| --- | --- |
| [Overview, light desktop](images/d19-preview/overview-light.png) | Editorial voice, hero, active work, decision rail and region obligations use the existing Dispatch typography and rules. The unavailable hero and controls explicitly retain the missing backend boundary. |
| [Goal detail, light desktop](images/d19-preview/goal-light.png) | Summary, invocation/effect disclosures and budget receipts follow the mock's information order. Stop and research are unavailable rather than simulated controls. |
| [Region, light desktop](images/d19-preview/region-light.png) | Resident responsibilities, related goal and due obligation retain their exact identities; existing record doors remain usable. |
| [Operator, dark desktop](images/d19-preview/operator-dark.png) | A separate read-only view contains service and guidance facts, with no regional controls or raw configuration. |
| [Cancelled approval, light desktop](images/d19-preview/approvals-light.png) | Exact recipient/content, digest and producer lineage remain readable without a decision action. |
| [Overview, dark phone](images/d19-preview/overview-dark-phone.png) | The 360 CSS-pixel coarse-pointer page wraps without horizontal overflow. |
| [Goal detail, dark tablet](images/d19-preview/goal-dark-tablet.png) | The 768 CSS-pixel page contains receipt text without horizontal overflow. |

These are author-inspected page renders in the Orca browser, not the five complete
future experiences, an independent design review, or an authenticated full-shell
render. Native focus plus Enter opened Goals from Overview. The browser reported
`scrollWidth <= innerWidth` at 360, 768 and 1440 CSS pixels; phone emulation reported
`pointer: coarse`. The fixture entry used the real page components, typed fetch client
and canonical stylesheet with synthetic GET responses only. No owner authentication,
live backend, control activation, delivery or cutover is proved by these images.
