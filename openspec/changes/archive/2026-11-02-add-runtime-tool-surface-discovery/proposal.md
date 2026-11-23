## Terminal gen1 reconciliation (2026-11-02)

This archive records a completed reconciliation, not completion of the original
native-discovery implementation. The original approved target was split: PR 4091
landed the policy DB/control plane and PR 4244 landed the catalog, inventory and
skill identity. Unlanded per-attempt planning, adapter presentation, replay,
receipts, native search and admission are **superseded by RFC0038 (bu-r980fk)**;
adapter implementation remains with `bu-kiuh8v`. No broker completion, native
admission, provider evaluation, live activation, or token-reduction claim follows.

The reconciled deltas below contain only landed behavior. The original 22
requirements are accounted for individually in `design.md`; omitted clauses
remain successor obligations, not completed implementation. Current Git-owned
core-group/narrowing, catalog read sensitivity and descriptive authorization
metadata corrections remain intact. Later dark-build catalog entries remain
separate from the live list/call surface.

## Why

The original gen1 delta still describes unlanded native implementation as active
work while its catalog/control-plane slices already shipped. This terminal
reconciliation gives the landed contracts canonical homes and transfers the
remaining obligations explicitly instead of falsely certifying them by archive.

## What Changes

- Synchronize actual policy storage/seed/GET/PATCH/editor contracts, preserving
  conservative defaults without promising an implemented runtime consumer.
- Synchronize immutable definition catalogs, exhaustive presentation inventory,
  descriptive module metadata and canonical skill identity without new authority.
- Archive only this gen1 change after applying those deltas to canonical specs.
- Name the broker successor in RFC 0027 and remove the false active policy-consumer
  promise from model-routing documentation.
- Rebuild only the existing generalist PATCH delta against the resulting canonical
  block, preserving its two delegation cutover clauses and every other delta.

## Policy handoff to bu-li92cu lineage

`tool_exposure_policy` remains landed DB/control-plane state. Its direct accessor
has no runtime source consumer. Keeping or deleting that field at the RFC 0038
switch remains **undecided**; this archive neither deletes it nor activates
planning/native discovery. The closed `bu-li92cu` collision lineage and the
`generalist-lead-focused-delegation` archive-first collision row carry that input
forward. The eventual PR body must repeat this handoff and the explicit unlanded
broker/adapters disposition. No new switch decision is made here.

## Delivered provenance

| Delivery | Actual merged identity | Scope |
|---|---|---|
| PR 3945 | `ff9315a2d0b2b425e13777516951f238242894d9` | Original spec, not native implementation |
| PR 3952 | `daba5fc15c1cfeafb8a4b3cf97b79e719417c5d3` | Owner Option B amendment |
| PR 4091 | `e214128b4` | Policy DB/control-plane vertical |
| PR 4244 | `a87cc3c7b5de50ca9ca21fdb10baed516297d98a` | Catalog/inventory/skill identity |
| PR 4874 / 4918 / 4947 | `d8e745989` / `3b986ad7f` / `66de7ad73` | Later descriptive authorization/selection/recipient corrections, preserved |

Historical PR 4764 was closed unmerged and is not delivery evidence or a source
for this reconciliation. RFC 0038's signed wording, switch conditions and runtime
code remain untouched. The blocked living-spec migration still has its separate
canonical-citation prerequisite; unshipped native requirements are not folded in.

## Historical approval record

The following sign-offs record the adopted design and sequencing authority at
their original dates. References to pending amendment integration or successor
work in that historical record are superseded by the delivered provenance and
terminal disposition above; they are not current readiness claims.

## Owner Sign-off

- **Status:** Approved
- **Approved by:** owner
- **Date:** 2026-08-30
- **Approved artifact:** commit `3448804aaff7b163b4b81deb646db2c9f4ae1397`
- **Approval scope:** proposal, RFC 0027 decisions, v1 non-goals, and the
  `eager_filtered`/`auto` operational policy
- **Authority:** clears the specification gate and permits handoff for
  sequencing; it does not itself request implementation, merge, deployment,
  runtime binary upgrades, or live canary activation

## Owner Amendment — Option B

- **Status:** Selected; amendment integration pending
- **Selected by:** owner
- **Date:** 2026-08-31
- **Decision bead:** `bu-g5fha`
- **Decision:** Keep canonical FastMCP listing complete and make each adapter
  render a fresh per-attempt search corpus through public runtime-host
  configuration. Verified native tuples search that corpus and load typed
  schemas on demand; the allowlist bounds eligibility but is not the
  context-saving mechanism.
- **Reason:** No supported FastMCP request-aware pagination seam or committed
  upstream roadmap justifies holding delivery. Private hooks, duplicate
  servers, proxies, and wire rewriting remain rejected.
- **Cursor refinement:** Adapters do not own opaque host MCP cursors.
  Host-internal pagination is invocation-local and non-model-visible;
  conformance proves hidden definitions never enter model context/search.
- **Strict policy:** A host that mandates native deferral cannot satisfy
  `eager_filtered` and is ineligible under that policy.
- **Doctrine amendment status:** Pending exact-artifact owner approval. On an
  owner-approved merge, `about/heart-and-soul/architecture.md` makes the 30-50
  target and initial-context cost refer to definitions initially loaded per
  session rather than every canonical registered handler. The prior Option B
  selection did not itself adopt this Heart-and-Soul amendment.
- **Authority:** authorizes RFC/OpenSpec/Beads amendment only. Implementation,
  dependency upgrades, amendment merge, provider evaluation, policy activation,
  deployment, and canary retain separate gates.

## Exact Artifact Approval History

- `f95052bc1ae721b11fc19f1aed8ac9e58e5c318d`: **Approved by owner on
  2026-09-05.** Superseded as the approval artifact by the corrective successor
  that completes the exact current core-tool registration inventory; its
  accepted Option B and doctrine decisions remain historical provenance. Its
  hosted CI run `33952958265` is also historical and non-transferable.
- `b7ff6c2bf064742907ac14db7e6a5f8e7c4df407`: **Approved by owner.**
  Superseded as the approval artifact after live `main` added
  `conversation_recall` and `conversation_thread_read`; its accepted Option B,
  doctrine, and earlier inventory corrections remain historical provenance.
  Hosted CI run `33962391110` and the independent artifact ACCEPT applied only
  to that head and are superseded.
- `99ecce950314ac597802ed42fca81c7fdb04c65a`: **Not owner-approved as an
  exact artifact.** Hosted CI run `33978679113` and the independent alignment
  GO applied only to that head. PR #4045 subsequently added the `graph` group
  to the live runtime-config vocabulary after the branch had already gained
  the seven-tool `fleet_cases` group, so that review and CI are superseded and
  non-transferable.
- `263d9a6c211fbf0a501cda68ef22f296dc73b2c7`: **Not owner-approved as an
  exact artifact.** Hosted CI run `34028358724` succeeded, but the independent
  exact-head review returned NO-GO on runtime-seed, transport, wrapper, retry,
  and legacy-catalog current-truth drift. That review and CI apply only to this
  superseded head and do not transfer to its correction.
- `d3423639661a4cdd348b4831e909c165f1f9bce1`: **Not owner-approved as an
  exact artifact.** Hosted CI run `34031392691` succeeded and the correction
  resolved the recorded current-truth findings. It is nevertheless superseded
  because the separately reviewed catalog-cleanup prerequisite had not yet
  been integrated into that tree; neither its CI nor any review transfers.
- Post-cleanup integrated successor: **Pending fresh exact-head owner approval.**
  The approval must cite the immutable PR head presented in the final review
  packet. Every artifact-specific approval, review, and CI result at or before
  `d3423639661a4cdd348b4831e909c165f1f9bce1` is explicitly superseded; none
  transfers to the successor. Any later edit likewise creates another artifact
  and invalidates exact-head evidence.
- **Cleanup prerequisite satisfied:** `bu-hxirg` landed through PR #4048 as
  `13d269b1ef80a5815a6ef2567b139398940196a0`. PR #3952 integrated that exact
  `main` through merge commit
  `364585ccf7ffef82b04b64172e5d71d8cdec1576`, whose parents are
  `d3423639661a4cdd348b4831e909c165f1f9bce1` and
  `13d269b1ef80a5815a6ef2567b139398940196a0`. Final review and owner approval
  SHALL bind the later immutable PR head containing this integration and SHALL
  be repeated if `main` or the PR head moves again.
