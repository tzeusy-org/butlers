# Doctrine / Spec / Code Reconciliation: Synthesis and Cross-Stream Sync

Bead: bu-0ss2f3 (epic bu-17axsl). Synthesis over 18 completed reconciliation
beads: 5 heart-and-soul doctrine reconciliations (H1 to H5) and 13 spec-to-code
audit shards (S1 to S13, covering ~15 audit areas).

Evidence labels: [Observed] verified against current code or docs with a
citation; [Inferred] reasoned from observed facts; [Unknown] not verifiable
from the repo. This report contains no em-dashes in prose (non-negotiable #6).

Location note: the bead asked for this under `docs/reports/`, but that path is
gitignored by the docs-IA retention policy (`.gitignore:107`, "point-in-time
reports are not retained in-tree"). It is placed in the tracked `docs/archive/`
instead so it is reviewable in the PR and retained alongside the other dated
reconciliation and decision records.

## 1. Executive summary

The two reconciliation streams agree. Every code-versus-doc conflict the 18
beads surfaced was resolved on the authority policy (the more mature and more
complete side wins), and the resulting doc and spec edits are mutually
consistent. The three layers (doctrine in `about/heart-and-soul`, contracts in
`openspec/specs`, reality in `src/butlers` and `frontend`) are now aligned on
the major themes.

- No genuine doctrine-versus-mature-design contradiction remains unresolved.
- The ~99 remediation beads filed across the 18 audits are the tracked backlog
  for specified-but-unbuilt or built-but-buggy items; a meaningful fraction of
  the P1 items has already been closed since the June 27 audit wave (see 6).
- This PR makes one small in-scope spec edit (adding non-negotiable #6, the
  em-dash ban, to the binding design-language spec, where it was spec-silent).
- Two proposed synthesis-level remediation items and three owner scope
  decisions are surfaced in 7 and 8 for the coordinator and owner.

## 2. What is now authoritative per area

The reconciled truth after both streams, with the authoritative source in each
area:

| Area | Authoritative model (post-reconciliation) | Evidence |
|---|---|---|
| Model / runtime / session-timeout config | `public.model_catalog`, resolved per complexity tier by `core/model_routing.py`. NOT `runtime_config` (which holds only cold fields `core_groups`, `max_concurrent`, `max_queued`). | [Observed] `alembic/versions/core/core_073_model_catalog_session_timeout.py:19-29`; `src/butlers/core/model_routing.py:790,833-838`; `about/lay-and-land/components.md:95` |
| Default runtime adapter | `codex` remains the roster-wide default runtime type (a constant, not a per-catalog-row selection). | [Observed] `src/butlers/core/runtimes/base.py:18` (`DEFAULT_RUNTIME_TYPE = "codex"`) |
| Identity model | Entity graph: `public.entities` (anchor + roles) plus `relationship.entity_facts` triples (`has-handle`, `has-email`, `has-phone`). `public.contacts` and `public.contact_info` are DROPPED. | [Observed] `openspec/specs/contacts-identity/spec.md:1-17`; `about/heart-and-soul/v1.md:155`; `about/heart-and-soul/v1-status.md:322`; `src/butlers/identity.py` |
| Security posture | Network-level trust boundary (localhost plus Tailscale) enforced by `egress-firewall.sh` per-bridge iptables, plus per-butler LOGIN roles and runtime `SET ROLE`. Not app-API-key fail-closed. | [Observed] `about/heart-and-soul/security.md` (synced by bu-t17bvu, PR #2758); mirrored by bu-kialt9 |
| Dashboard design language | The Dispatch spec (`openspec/specs/dashboard-design-language/spec.md`) is binding; `about/heart-and-soul/design-language.md` is the WHY; `index.css` is normative for token values. Archetypes: overview, list, detail, workspace, editor, editorial, status-board. | [Observed] design-language spec Purpose; bu-f5wryw (PR #2753) |
| Traces dashboard page | Retired. Replaced by the Timeline tab on `/ingestion`, unified under `request_id`. No live route, nav item, or current spec. | [Observed] `openspec/changes/archive/connector-ingestion-request-id/design.md:5,84`; `openspec/specs/dashboard-api/spec.md:1140-1148`; no `/traces` route in `frontend/src` |

## 3. Doc and spec edits made by the 18 beads

Each bead synced its layer to the mature code and merged its own PR. Summary
(bead, PR, layer, headline sync):

Heart-and-soul stream (doctrine):

| Bead | PR | Doctrine doc | Headline sync |
|---|---|---|---|
| bu-rn38bw | #2757 | vision / v1 / v1-status | Non-negotiable #5 corrected (model selection lives in `public.model_catalog` per core_073, not runtime_config); Steam added to inventory; v1-status refreshed |
| bu-nhtjw6 | #2756 | architecture.md | Module ABC method is `migration_revisions()` (Alembic branch label); core-tools is a thin dispatcher (`ToolContext` plus declarative `core_groups`) |
| bu-t17bvu | #2758 | security.md | Network-isolation / egress-firewall model; identity via `entity_facts`; added schema-isolation section |
| bu-n2mqlb | #2755 | development.md | Test-infra markers, beads (shared Dolt 3307), 3 CI Actions jobs, git-workflow / worktree discipline |
| bu-f5wryw | #2753 | design-language.md | Archetype list reconciled; shared `<Page>` / `<Time>` shipped; drift items annotated resolved |

Spec-audit stream (contracts). All 13 shards merged; ~15 audit areas; the
recurring pattern was SPEC-STALE requirements synced to shipped code:

| Bead | PR | Area | Headline |
|---|---|---|---|
| bu-40m11d | #2697 | core runtime and spawner | 7 specs synced (core-spawner model_catalog, core-notify entity_facts, core-sessions, session-process-logs, core-telemetry, context-bus, conversation-decomposition) |
| bu-thwo5a | #2742 | butlers (base plus per-butler) | 13 per-butler specs; ~8 SPEC-STALE synced |
| bu-ofs851 | #2734 | modules (core set) | 11 module specs; home-assistant largest drift |
| bu-q1iz1d | #2737 | connectors and ingestion | 22 specs; spotify full rewrite; source-filter specs correctly archived; discord GAP |
| bu-2vxk1e | #2698 | memory, relationship, identity | 5 specs synced; contacts-identity and module-contacts superseded by the entity graph (banners added) |
| bu-70ilhw | #2725 | chronicler, briefing, insight, autonomy | 12/13 MATCH; chronicler-api gained 3 shipped-endpoint requirements |
| bu-u2io1e | #2745 | calendar, time, scheduling | 6 MATCH, 2 SPEC-STALE synced |
| bu-vovelb | #2747 | finance | 6 MATCH, 5 SPEC-STALE synced |
| bu-5fjyxz | #2705 | education | all ~10 specs SPEC-STALE synced; several P1 code bugs found |
| bu-kialt9 | #2732 | security, credentials, storage, deployment | 8 MATCH, 5 SPEC-STALE synced |
| bu-58rlw7 | #2704 | dashboard | 23 specs; ~12 SPEC-STALE synced; 28 remediation beads filed |
| bu-80x05t | #2728 | model-routing, runtime-config | model-catalog deepest drift (priority MAX-wins, 5-tuple resolve, core_073/core_093) synced |
| bu-5ro3ci | #2724 | self-healing, QA, telemetry, testing | 6 MATCH, ~9 SPEC-STALE synced |

## 4. Cross-stream consistency check (the core of this bead)

For each theme that appears in BOTH streams, the doctrine conclusion and the
spec-audit conclusion agree:

### Theme A: model / runtime / session-timeout config moved to `model_catalog`
- Doctrine (H): bu-rn38bw corrected non-negotiable #5; bu-nhtjw6 confirmed
  `components.md` (now current at `:95`).
- Spec (S): bu-80x05t (model-catalog deepest drift synced) and bu-40m11d
  (core-spawner) both point at `public.model_catalog` / core_073.
- [Observed] Consistent across all three layers. `resolve_model` returns
  `(runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s)`
  from `public.model_catalog` (`src/butlers/core/model_routing.py:833-838`).

Deferred decision resolved here: bu-thwo5a flagged that "codex runtime adapter"
recurs across the per-butler specs and left the "uniform-rewrite decision
pending" for this synthesis bead. [Observed] `codex` is still
`DEFAULT_RUNTIME_TYPE` (`src/butlers/core/runtimes/base.py:18`), so those spec
references are ACCURATE, not stale; what moved to `model_catalog` is the
per-tier model/runtime/session-timeout CONFIG, which every stream already
synced. Decision: NO uniform rewrite is required. The per-butler specs correctly
name codex as the default runtime; no spec claims model config lives in
`butler.toml` / `runtime_config` after the core_073 syncs.

### Theme B: identity is the entity graph; the contact schema is retired
- Doctrine (H): bu-t17bvu synced security.md identity-resolution to
  `relationship.entity_facts` plus `public.entities`; bu-rn38bw / v1 doctrine
  already frames identity via `entity_facts` (`v1.md:155`, `v1-status.md:322`).
- Spec (S): bu-2vxk1e added supersession banners to contacts-identity and
  module-contacts; bu-58rlw7 and bu-q1iz1d cross-flagged the contacts / google
  health cross-layer items to this synthesis bead.
- [Observed] The code retirement is complete (`public.contact_info` dropped by
  core_115, `public.contacts` by core_134). The spec archival is owner-decided:
  the `retire-contacts-table-specs` change is archived
  (`openspec/changes/archive/2026-07-10-retire-contacts-table-specs`), owner
  decision on bu-qtsy4 was "archive, do not rewrite"; the residual
  contacts-identity spec documents the entity graph as authoritative
  (`contacts-identity/spec.md:16-17`). Consistent; no open action.

### Theme C: security is a network-level trust boundary
- Doctrine (H): bu-t17bvu tempered security.md against network-level doctrine
  (no alarmist auth/root findings); added the schema-isolation section.
- Spec (S): bu-kialt9 reached the same conclusion ("security posture tempered
  against network-level doctrine"), synced 5 specs, MATCH on 8.
- [Observed] Consistent. Both streams independently landed on the same
  network-boundary framing.

### Theme D: non-negotiable #6 (no em-dashes) is a doctrine-versus-code gap
- Doctrine (H): bu-f5wryw filed a P2 remediation for ~66 em-dashes in rendered
  dashboard copy (a rule #6 violation).
- Tooling reality: [Observed] `scripts/check-no-em-dashes.py` exists but is NOT
  wired into CI, Make, or pre-commit (grep across `.github/workflows/*.yml`,
  `Makefile`, and repo config returns zero references outside the script), and
  it scans only `about/heart-and-soul`, `about/lay-and-land`, `about/craft-and-care`
  (not `roster/*` doctrine files, not `frontend/` copy). [Inferred] The
  non-negotiable is under-enforced: even the content fix bu-f5wryw filed would
  not be regression-guarded.
- Spec gap closed by THIS PR: the binding design-language spec's "Interface
  Copy" requirement enumerated its sibling Voice-and-Copy bans (no exclamation
  marks, no first person, no hedging adverbs) but was silent on the em-dash ban.
  This PR adds it (see 5) so the non-negotiable is binding at the spec layer.

## 5. Edits made by THIS PR

- `openspec/specs/dashboard-design-language/spec.md` (Requirement: Interface
  Copy): added "no em-dashes in prose (use a comma, colon, or parentheses
  instead; doctrine non-negotiable #6)", with the sanctioned bare `"—"`
  null-display placeholder exception. This is the single "doctrine
  non-negotiable was spec-silent" gap found; every other cross-stream area was
  already consistent after the 18 beads plus subsequent merges.
  `openspec validate --strict dashboard-design-language` passes.
- This report.

No code, migration, or frontend change is made. No doctrine `about/` file
needed a new edit: the H-stream beads plus subsequent work (for example
`components.md:95`) already brought the doctrine layer current on the mature
features (Steam, entity-graph identity, model_catalog, network security).

## 6. Remediation beads filed by the streams, and current status

The 18 audits filed roughly 99 discovered-from remediation beads (H: about 7;
S: about 92, of which bu-58rlw7 dashboard filed 28). They are the tracked
backlog for specified-but-unbuilt or built-but-buggy items; per policy the
specs were NOT deleted.

Current-state spot check (the audit wave was 2026-06-27; several P1 items are
already resolved):

| Bead | Audit finding | Status now | Evidence |
|---|---|---|---|
| bu-mj2k2 | dashboard conversations fully STUBBED (P1) | [Observed] CLOSED, shipped PR #2899 | `bd show bu-mj2k2` |
| bu-7oquu | system/egress owner-assertion stub (P2) | [Observed] CLOSED, PR #2710 | `bd show bu-7oquu` |
| bu-of9cp | finance compose_bills_digest unregistered (P1) | [Observed] CLOSED (already fixed, PR #2656) | `bd show bu-of9cp` |
| bu-80x05t follow-ups (benchmark loop, email display-name) | model-routing / ingestion | [Observed] two closed by this worker (bu-mxex1 PR #3214, bu-vs9cr PR #3212) | recent main history |

[Inferred] The backlog is being actively worked; the report does not re-list all
99 (they carry their own priorities and discovered-from links). The coordinator
owns dispatch.

## 7. Proposed synthesis-level remediation beads

These are cross-stream items no single audit owned. The synthesis bead cannot
mutate beads; the coordinator should file them (verify no duplicate first).

1. Wire the em-dash gate and extend its scope. `scripts/check-no-em-dashes.py`
   is not invoked by CI, Make, or pre-commit, and scans only the three `about/`
   doctrine dirs. Non-negotiable #6 covers "all doctrine documents" and "all
   dashboard copy". Proposal: add a CI step (or Make target) that runs the
   checker, and extend its path set to `roster/*/MANIFESTO.md` plus
   `roster/*/AGENTS.md` (doctrine) and, separately, a dashboard-copy lint over
   `frontend/src`. Priority 3. Distinct from bu-f5wryw's P2 (which fixes the ~66
   existing violations); this closes the enforcement gap. Discovered-from
   bu-0ss2f3.
2. Fix `openspec validate --strict` failures on `core-notify` and
   `core-telemetry`. [Observed] both fail with "Requirement must contain SHALL
   or MUST keyword" (prose-style requirement lines); `context-bus` (previously
   flagged) now passes. openspec validate is not CI-gated, so these are latent.
   Priority 3, spec hygiene. Discovered-from bu-0ss2f3. (Out of scope for this
   PR: they are pre-existing violations on requirements this reconciliation did
   not touch.)

## 8. Owner decisions and escalations

No genuine doctrine-versus-mature-design CONTRADICTION remains: every conflict
resolved cleanly on the authority policy (code-wins syncs), and Traces
retirement and the contacts-schema archival were already owner-decided and
executed. The following are OWNER SCOPE or POLICY decisions surfaced by the
streams that remain open for owner input (they are choices, not contradictions):

1. Education progress-digest channel three-way inconsistency (bu-5fjyxz flagged
   this explicitly for owner escalation): the notification channel differs
   across the spec, the code, and the digest config. Owner should pick the
   intended channel so the three agree.
2. Defense-in-depth: deny public-internet egress on non-egress bridges
   (bu-t17bvu P3). The auditor labelled this an owner decision that is NOT
   doctrine-breaking (current posture is already sound at the network layer);
   it is a hardening choice, not a gap.

[Observed] Resolved since the audit and NO longer open: the bu-rn38bw doctrine
inventory scope decisions (whether Steam belongs in SC-2, whether `contacts` and
`google_health` belong in the v1 module inventory) were decided and merged by
bu-rrpdc (PR #2806: Steam excluded from SC-2, contacts and google_health added
to the v1.md module inventory).

## 9. Three-layer consistency verdict

[Observed] After the 18 beads plus subsequent merges, the doctrine, spec, and
code layers are mutually consistent on every major theme (model config,
identity, security, design language, Traces). The residual work is the tracked
remediation backlog (specified-but-unbuilt or built-but-buggy) and the small
owner scope decisions above. openspec validate health: `context-bus` now passes
`--strict`; `core-notify` and `core-telemetry` still fail on prose-style
requirements (proposed bead 2), and openspec validate is not CI-enforced.
