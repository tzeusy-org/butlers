# OpenSpec ↔ Project Shape Reconciliation — 2026-04-25

**Scope:** Project-shape reconciliation following the archive of 17 OpenSpec changes
(commits `f553ed7e`, `9abb53df`). Audits how the 12 newly-created and 11 merged-into
capabilities under `openspec/specs/` integrate with the four `about/` knowledge pillars.
Not a deep spec-to-code reconciliation.

**Inputs surveyed:**

- `openspec/specs/` — 159 capability directories (+ 1 stray `predicate-taxonomy.md`).
- `about/heart-and-soul/` — 5 docs (vision, architecture, v1, security, development).
- `about/lay-and-land/` — 5 docs (components, data-flow, deployment, dependencies, integration).
- `about/legends-and-lore/rfcs/` — 14 RFCs (0001–0014).
- `about/craft-and-care/` — 7 docs (engineering bar, testing, review, observability, security, etc.).
- `openspec/changes/` — 2 open changes + 2 loose `.md` files.

---

## Summary

The newly-archived sync covers two coherent landings (QA staffer, Chronicler butler) and
three cross-cutting infrastructure moves (runtime config seed-and-manage, S3 blob storage,
docs IA rewrite) plus several smaller dashboard/connector deltas. Doctrine-level coverage
is largely in place — `v1.md` already names QA and Chronicler, RFC 0014 covers Chronicler,
and `runtime-config` is referenced from RFCs 0001/0002 — but **`about/lay-and-land/components.md`
is now meaningfully behind reality** (no Chronicler row in either daemon list or the high-level
Mermaid graph; no QA sub-component breakdown; no `runtime_config` accessor entry; the QA node
in the diagram exists but is unannotated). Two open changes remain
(`add-degenerate-session-guardrails`, `k3s-deployment-helm-chart`) — neither is stale yet but
the k3s one will need a refresh once S3 blob storage lands fully.

---

## Newly-created capability slotting

One-line verdict per capability: *acknowledged where, or what to add.*

- **`butler-chronicler`** — RFC 0014 covers; `v1.md` lists it. **Missing in `lay-and-land/components.md`** (not in §1 daemon list nor in the high-level Mermaid graph alongside GEN/REL/HLT/etc.).
- **`chronicler-api`** — RFC 0014 §`/api/chronicler/*` namespace covers contract; `v1.md` mentions it. **Add to `lay-and-land/components.md` §5 Dashboard "Auto-discovered butler routes"** mention block (or a one-liner in components §5 noting `/api/chronicler/*` lives under `roster/chronicler/api/`).
- **`chronicler-source-compatibility`** — RFC 0014 implies the contract but does not enumerate the per-source compatibility-declaration requirement. **Worth a 1-line back-pointer from RFC 0014 → this spec**, or a short subsection added to RFC 0014 §"Source projection" enumerating the required compatibility fields.
- **`conversation-decomposition`** — Switchboard pipeline step. RFC 0003 (Switchboard Routing and Ingestion) is silent on per-butler decomposition for `payload_type=conversation_history`. **Should be referenced from RFC 0003** (one-paragraph addition in the classification/routing section: "batched conversation envelopes take a fan-out path through `conversation-decomposition` before classification").
- **`dashboard-connector-batch-settings`** — UI surface only; covered implicitly by RFC 0007's auto-discovered route model. Code present (`frontend/src/components/ingestion/BatchSettingsCard.tsx`). **OK** — no doctrine pointer needed; a sentence in `lay-and-land/components.md` §5 dashboard would be nice-to-have but not required.
- **`docs-information-architecture`** — meta-spec defining `docs/` taxonomy. **Mildly orthogonal to the five-pillar model** (it governs `docs/`, not `about/` or `openspec/`). Should be cross-referenced from `about/README.md` (the pillar README) and from `about/craft-and-care/review-and-documentation.md`. Currently neither mentions it.
- **`qa-dashboard`** — Covered by `v1.md` (QA is named) and RFC 0007 implicitly (auto-discovered routes). **Missing from `lay-and-land/components.md` §5** — no QA dashboard rows. Add a sub-row.
- **`qa-investigation-dispatch`** — Subsumes the legacy `core/healing/dispatch.py`. **Missing from `lay-and-land/components.md` §1** (the daemon table still lists "Self-Healing | core/healing/" as an Evolving sub-component without noting that QA staffer now owns the unified pipeline). Doctrine fine via `v1.md`. **Add a note to components.md §1 or a new §X "QA Staffer" section** mirroring §4 Switchboard.
- **`qa-log-scanner`** — Internal QA discovery source; **OK** at the spec level, no doctrine pointer needed. A passing mention in a future "QA Staffer" section in `components.md` would fit.
- **`qa-triage`** — As above. **OK**, but should be co-located conceptually under a future `components.md` "QA Staffer" subsection.
- **`staffer-qa`** — Roster identity for the QA staffer. **`v1.md` already names QA as the third staffer.** **OK at doctrine level.** Missing from `components.md` §1 / new section as above.
- **`runtime-config-table`** — Referenced from RFC 0001 §9b, RFC 0002 §`core_groups`. **OK at RFC level.** **Missing from `components.md` §1** (no `runtime_config` row in daemon sub-components table — should appear next to "State Store" / "Session Log").
- **`runtime-config-api`** — RFC 0007 §"core API routes" covers the location pattern. **OK** — no specific pointer required.
- **`runtime-config-dashboard-ui`** — UI only; covered by RFC 0007 generically. **OK.**
- **`s3-blob-storage`** — `lay-and-land/components.md` §High-level View shows `MinIO / S3`; `lay-and-land/dependencies.md` §"MinIO/S3 down" covers failure mode; `lay-and-land/deployment.md` lists ports/services. **OK at topology level.** **No RFC** covers the wire contract / bucket layout / access pattern. Consider an RFC if access patterns continue to grow (see "RFCs to consider").

---

## RFCs to consider filing

Five candidates ordered by leverage:

1. **RFC 0015 — QA Staffer Discovery & Investigation Pipeline.** Five new specs (`staffer-qa`, `qa-dashboard`, `qa-triage`, `qa-investigation-dispatch`, `qa-log-scanner`) define a coherent staffer with discovery sources, triage, dispatch, and dashboard. There is no design contract tying them together at the RFC level. The QA staffer has clear wire contracts (DiscoverySource protocol, fingerprint-based triage, `healing_attempts` table reuse) that warrant an accepted-status RFC.
2. **RFC 0016 — S3 Blob Storage Contract.** `s3-blob-storage` capability landed but no RFC defines bucket layout, key naming, retention/lifecycle, attachment-vs-export bucket separation, or the `BlobStorage` access pattern. Topology mentions S3 but the data-plane contract is undocumented at RFC tier.
3. **RFC 0017 — Conversation Decomposition / Per-Butler Fan-Out.** Modifies the RFC 0003 routing flow for batched envelopes. Either extend RFC 0003 with a new section or file a successor RFC; the former is cheaper and more correct.
4. **RFC for Runtime Config Seed-and-Manage Pattern.** Already partially covered in RFC 0001 §9b and RFC 0002 — a short standalone RFC enumerating hot vs cold fields, TTL semantics, and `[butler.runtime_seed]` -> DB precedence would consolidate the contract that `runtime-config-table`/`runtime-config-api`/`runtime-config-dashboard-ui` collectively implement. Lower priority than the QA RFC.
5. **RFC for Chronicler Source Compatibility (extension to RFC 0014).** Not necessarily a new RFC — the cleaner path is appending a "Source compatibility declaration" section to RFC 0014. Filed here as the smallest of the five but worth ordering before any new timestamped source ships.

---

## Open changes status

### `add-degenerate-session-guardrails` (proposal mtime 2026-04-15)

**Scope:** spawner-side detection of degenerate tool-call loops, typed termination
error taxonomy on sessions (`degenerate_tool_loop`, `tool_call_budget_exceeded`,
`token_budget_exceeded`), per-butler thresholds in `[butler.runtime_seed]`,
cross-cutting MCP "raise on invalid input" rule, OpenCode timeout reconciliation,
dashboard surfacing. Modifies `core-spawner`, `core-sessions`, `core-modules`,
`module-memory`, `runtime-opencode`.

**Status:** Not stale. Ten days old, well-grounded in a documented incident
(`46f18840-…`). The one cross-reference worth adding: this proposal touches the same
`[butler.runtime_seed]` block introduced by the now-archived runtime-config seed-and-manage
work, so the implementation will need to merge cleanly with the new `RuntimeConfigAccessor`
plumbing rather than treating `[butler.runtime_seed]` as a greenfield section.

### `k3s-deployment-helm-chart` (proposal mtime 2026-04-06)

**Scope:** Helm chart at `homelab/.../helm/butlers_local/`, CloudNativePG integration,
init-container migration job, AGENTS.md DB fallback, `BUTLERS_DISABLE_FILE_LOGGING=1`,
per-connector Deployments, Tailscale Operator Ingress, ExternalSecret secrets, healing
disable flag, k8s DNS service discovery. Adds 6 capabilities, modifies 5.

**Status:** Borderline-stale. Three weeks old. The proposal cites the S3 blob storage
migration as "the largest blocker" — that capability has now landed in the archive sync,
which is a positive signal but also means the proposal text references it as an upcoming
prerequisite rather than a completed substrate. Worth a short refresh of the "Why" section
to reflect current ground truth before implementation begins. Otherwise sound; the
cross-repo nature (homelab repo + butlers repo) means it benefits from being landed as a
single coordinated effort rather than incrementally.

---

## Orphan-spec spot check

Sample of 11 capabilities whose connection to live code is non-obvious. Verdicts:

| Capability | Code path verdict |
|---|---|
| `catalog-token-limits` | **Implemented.** `src/butlers/core/model_routing.py` and `src/butlers/api/routers/model_settings.py` reference `token_usage_ledger`. |
| `routing-scorecard` | **Partial.** `tests/benchmarks/switchboard/` exists; no production code path (correctly so — this is a benchmark/eval surface). |
| `tool-call-scorecard` | **Implemented.** Referenced in `src/butlers/core/spawner.py`. |
| `scorecard-reporting` | **Likely test-only / report-only.** No production source matches; lives in `tests/benchmarks/`. Acceptable scope but worth confirming the spec explicitly scopes to the benchmark harness. |
| `model-benchmark-harness` | **Implemented in tests.** `tests/benchmarks/` and `tests/e2e/benchmark.py` present. Production-facing? No — and that's correct per the spec's purpose. |
| `adapter-integration-testing` | **Implemented.** `tests/adapters/` present. |
| `complexity-classification` | **Implemented.** `src/butlers/core/model_routing.py`, `api/routers/butlers.py`, `api/routers/schedules.py`. |
| `healing-anonymizer` | **Implemented.** `src/butlers/core/healing/anonymizer.py`. |
| `ingestion-event-registry` | **Implemented.** `src/butlers/switchboard_wiring.py`, `api/deps.py`. |
| `e2e-ecosystem-staging` | **Test-only / harness spec.** Lives entirely in `tests/e2e/`. **OK** — same caveat as `scorecard-reporting`: the spec should be explicit it governs the test harness, not a runtime component. |
| `insight-delivery` | **Implemented.** `roster/switchboard/modules/insight_broker.py`, `roster/switchboard/tools/insight/broker.py`, `src/butlers/scheduled_jobs.py`. |

**No true orphans found in the sample.** The two harness-flavored specs
(`scorecard-reporting`, `e2e-ecosystem-staging`) are intentionally test-only and should
be flagged in their `Purpose` sections as such if not already.

---

## Loose `openspec/changes/` artifacts

Two `.md` files at `openspec/changes/` root (both timestamped 2026-04-06 — predate the
April archive cycle):

- **`gen-2-reconciliation-final.md`** — Closure report for the gen-2 reconciliation cycle
  (issue `bu-gjb1.5.5`). Verifies 4 gap beads are resolved. Historical record.
- **`module-education-analytics-verification.md`** — Per-metric verification report for
  the education analytics module spec. Historical record.

**Recommendation:** Move both to **`docs/reports/`** (this directory) with date-prefixed
names matching the existing `docs/archive/` convention (`2026-03-15-…`). Rationale:

- The `openspec/changes/` directory convention is "one folder per change" plus an `archive/`
  subdirectory of completed changes; loose `.md` reports break that convention.
- `openspec/changes/archive/` itself contains zero loose `.md` files — the convention is
  enforced for archived changes, so it should be enforced for the active layer too.
- Both files are *reports about completed work*, not change proposals — they belong in
  the same conceptual bucket as this very report.

Suggested filenames:
- `docs/reports/2026-03-15-gen-2-reconciliation-final.md`
- `docs/reports/2026-03-15-module-education-analytics-verification.md`

(Both files internally date themselves 2026-03-15.)

Do *not* commit the move yet — file as a follow-up bead so the move is atomic with any
inbound links being updated.

---

## Recommended follow-up beads

Six concrete candidates, ordered by leverage:

1. **`docs(lay-and-land): add Chronicler + QA staffer + runtime_config to components.md`** —
   priority 1. Add Chronicler row to §1 daemon list and to the high-level Mermaid graph
   (alongside GEN/REL/HLT/etc.); add a new §X "QA Staffer" section mirroring §4 Switchboard
   structure (sub-components: Patrol Loop, Discovery Sources, Triage, Dispatch, Anonymizer,
   Dashboard); add `runtime_config` row to §1. Single small PR.
2. **`docs(legends-and-lore): file RFC 0015 — QA Staffer Discovery & Investigation Pipeline`** —
   priority 1. Five capability specs without a unifying design contract. Highest-value RFC
   gap.
3. **`docs(legends-and-lore): file RFC 0016 — S3 Blob Storage Contract`** — priority 2.
   Document bucket layout, key naming, retention, access pattern. Topology mentions S3 but
   no RFC owns the data-plane contract.
4. **`docs(legends-and-lore): extend RFC 0003 with conversation decomposition fan-out section`** —
   priority 2. One-paragraph addition (or §subsection) covering the
   `payload_type=conversation_history` branch. Cheaper than a new RFC.
5. **`chore(openspec): relocate two loose .md reports out of openspec/changes/ root`** —
   priority 3. Move `gen-2-reconciliation-final.md` and `module-education-analytics-verification.md`
   to `docs/reports/2026-03-15-…`. Update any inbound links (none expected; verify).
6. **`docs(legends-and-lore): append "Source compatibility declaration" section to RFC 0014`** —
   priority 3. Cross-link to `chronicler-source-compatibility` spec; enumerate the
   required compatibility fields inline so future timestamped-source proposals can be
   reviewed against the RFC, not just the spec.

**Optional (priority 4):** A short standalone RFC consolidating the runtime-config
seed-and-manage contract (currently spread across RFC 0001 §9b, RFC 0002 §core_groups,
and three new capability specs). Worth doing if/when a fourth runtime-config-shaped
capability appears; not yet.

---

## Doctrine conflicts found

**None urgent.** The newly-created capabilities are doctrine-aligned:

- **`staffer-qa`** explicitly inherits the staffer archetype contract added in
  `2026-04-04-add-staffer-concept` (Rule 6: every agent has a governing document — QA's is
  an infrastructure contract, not a manifesto).
- **`butler-chronicler`** explicitly affirms Rule 5 (roster owns identity drift) in its spec.
- **`runtime-config-table`** is the canonical implementation of Rule 5's "operational
  tuning lives in the database" half.
- **`s3-blob-storage`** does not introduce multi-tenancy concerns (Rule 1) — single bucket
  scoped to the single owner instance.
- **`conversation-decomposition`** correctly fans out via Switchboard MCP routes (Rule 3) —
  it does not bypass the Switchboard for the per-butler delivery.

The only soft tension worth naming: **the `qa-investigation-dispatch` spec subsumes the
existing `self-healing-dispatch` capability without that spec being marked deprecated.**
Both currently coexist in `openspec/specs/`. Resolution path: either mark
`self-healing-dispatch` (and `self-healing-module`, `self-healing-skill`, plus the
`healing-*` family) as deprecated/superseded with pointers to the QA specs, or fold them
into the QA capability set in a follow-up archive cycle. This is a coherence issue, not a
doctrine conflict, and is implicit in the proposal's "subsumes" language but not yet
reflected in the spec tree itself.
