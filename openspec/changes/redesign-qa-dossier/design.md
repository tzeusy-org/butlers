## Context

The QA staffer has been in production since 2026-04-25 (RFC 0015). Its dashboard surface (`/qa`) was built as a status-and-pipeline operator console: status banner, raw-count KPI cards, Kanban pipeline, recharts trend + source-breakdown, recent-patrols table, known-issues panel. That surface predates the Dispatch design language that now governs `/overview` and the Butler-detail pages, and it presents QA as a SaaS dashboard rather than as a discreet staffer narrating its work.

The current capture layer produces enough data to compute the page's existing widgets but not the narrative artifacts the redesign asks for — claim-anchored diagnosis prose, evidence anchored to claims, a step-by-step patrol journal, counter-evidence, a one-line "why this fix", and an inline diff preview. The investigation agent already runs in a sandboxed worktree and files anonymized PRs; it just doesn't emit a structured note artifact when it terminates.

This change re-skins the surface to match Dispatch, extends the capture layer just enough to feed the dossier, and adjusts the QA doctrine to permit raw log lines on the internal dashboard (while keeping the anonymization-on-egress guarantee that protects public GitHub).

Stakeholders: operator (Tze), QA staffer maintenance, and any future operator-facing surfaces that consume `/api/qa/*`.

## Goals / Non-Goals

**Goals:**

- Replace `/qa`, `/qa/investigations`, `/qa/investigations/:id`, `/qa/patrols/:id` with Dispatch-language dossier surfaces in a single hard cut.
- Extend the investigation agent contract to emit an `investigation_notes` JSON artifact at terminal state.
- Persist a `qa_investigation_events` journal that aggregates every QA decision on a case across triage, dispatch, and patrol loop.
- Capture a commit-time diff snapshot from the worktree so the inline diff survives PR force-pushes.
- Add a small Cases API resource (`/api/qa/cases`, `/api/qa/cases/:id`, `/api/qa/cases/:id/journal`) shaped for the dossier renderer.
- Update RFC 0015's doctrine to allow raw log storage on the internal dashboard, enforce 30-day retention, and re-state the anonymization-on-egress guarantee.
- Replace the 4-cell KPI strip with: `prs landed · 24h`, `mttr · 24h`, `self-resolved · 7d`, `active cases · now`.
- Keep the existing `/api/qa/*` endpoints functional — additive change at the API boundary, only the page consumers change.

**Non-Goals:**

- Real-time live updates beyond patrol-cadence refresh. SSE for the case journal is permitted but optional; v1 may poll every 14 minutes.
- Backfilling `investigation_notes` for historical attempts. Old cases render in a degraded state showing whatever fields they have.
- Notification system integration for `escalated` events (Proactive Butler's domain per RFC 0015).
- New design tokens, fonts, or palette changes. The redesign consumes what `frontend/src/index.css` already ships.
- A separate user-facing "hours saved" KPI. Dropped from the design as semantically unhelpful.
- Forwarding raw log lines beyond `qa_findings.structured_evidence`. Anything bound for GitHub, the API to other butlers, or any external surface remains anonymized.

## Decisions

### D1 — Journal storage: new table, not JSONB

A new `public.qa_investigation_events` table holds every journal event (`flagged`, `sampled`, `cross-checked`, `considered`, `concluded`, `drafted`, `wait`, `merged`, `tick`, `escalated`). Rationale: `tick` events accrue at patrol cadence independent of the investigation agent, so embedding them in `qa_findings.structured_evidence.reasoning[]` would either bloat the JSON per case or require read-modify-write JSON updates from the patrol loop (race-prone). A small append-only table with a composite index on `(attempt_id, ts)` reads back in chronological order in one query and lets us range-query journal events for KPIs or live tails without unmarshalling JSONB.

Alternatives considered:
- Grow `qa_findings.structured_evidence.reasoning[]` (Option A from research). Rejected: append-only semantics under concurrent writers are awkward in JSONB; the `tick` cadence makes it worse.
- Reuse `public.healing_dispatch_events`. Rejected: that table is scoped to gate decisions and decision-keyed; the journal is a per-case timeline including non-gate events.

```sql
CREATE TABLE public.qa_investigation_events (
    id            UUID PRIMARY KEY,                       -- UUIDv7 for time-order
    attempt_id    UUID NOT NULL REFERENCES public.healing_attempts(id) ON DELETE CASCADE,
    finding_id    UUID REFERENCES public.qa_findings(id) ON DELETE SET NULL,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    step          TEXT NOT NULL
                  CHECK (step IN ('flagged','sampled','cross-checked','considered',
                                  'concluded','drafted','wait','merged','tick','escalated')),
    text          TEXT NOT NULL,
    detail        TEXT,
    data          JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX idx_qa_inv_events_attempt_ts ON public.qa_investigation_events (attempt_id, ts);
CREATE INDEX idx_qa_inv_events_step       ON public.qa_investigation_events (step);
```

### D2 — Investigation Notes Artifact: extend `qa_findings.structured_evidence`, no migration

The case-static narrative fields (`headline`, `hypothesis`, `blurb_segments`, `claims`, `evidence_lines`, `counter_evidence`, `why_this_fix`, `diff_snapshot`) live inside `qa_findings.structured_evidence` JSONB. That JSONB already exists; we only document a new shape and add a Pydantic model for parsing.

Shape:

```jsonc
// qa_findings.structured_evidence
{
  // Phase 1 fields (preserved, unchanged):
  "source": "session_records" | "log_scanner" | "butler_reports",
  "status": "error" | "timeout" | "crash",         // session_records only
  "session_ids": ["uuid", ...],                    // session_records only
  "log_file": "butlers/chronicler",                // log_scanner only
  "level": "error" | "critical",                   // log_scanner only
  "trigger_source": "...",                         // log_scanner / butler_reports

  // New: investigation_notes (added when investigation reaches a terminal state with a successful emission)
  "investigation_notes": {
    "schema_version": 1,
    "headline": "Spotify ingestion failing — scope rotated upstream",
    "hypothesis": "Hard-coded scope string drifted from Spotify's 2026-05-05 rename.",
    "blurb_segments": [
      { "claim": "c1", "text": "Spotify rotated the OAuth scope name..." },
      " ",
      { "claim": "c2", "text": "Chronicler's ingest call now returns 401." },
      " The runtime fix is mechanical..."
    ],
    "claims": {
      "c1": { "evidence_ids": ["e1"], "note": "Confirmed via Spotify changelog 2026-05-05." },
      "c2": { "evidence_ids": ["e1","e2","e3"], "note": "Failure streak of 4 across 18m." }
    },
    "evidence_lines": [
      { "id": "e1", "ts": "14:30:11", "lvl": "ERROR", "butler": "chronicler",
        "msg": "spotify.ingest 401 scope_mismatch /me/player/recently-played" }
    ],
    "counter_evidence": [
      { "hypothesis": "Token expiry", "verdict": "rejected",
        "reason": "refresh call succeeded at 13:58" }
    ],
    "why_this_fix": "Renames the scope string in one place, then surfaces the reauth on /settings/integrations so the human action is unblocked.",
    "diff_snapshot": [
      { "kind": "meta", "text": "butlers/chronicler/ingest/spotify.py" },
      { "kind": "-",    "text": "    \"user-read-recently-played\"," },
      { "kind": "+",    "text": "    \"user-recently-played\"," }
    ]
  }
}
```

The Pydantic model lives at `src/butlers/core/qa/notes.py` as `InvestigationNotes` with strict validation. When the agent emits malformed JSON, the dispatcher records `qa_investigation_notes_parse_total{status="partial"}` (best-effort extraction of recoverable fields) or `{status="failed"}` (drop the artifact) but never crashes the investigation.

### D3: Agent emission via portable file contract

The investigation agent's terminal step writes a JSON file at a known path inside the worktree (`./.qa/investigation_notes.json`) before signalling completion. The dispatcher reads that file *before* worktree teardown, validates against the schema, and writes the parsed payload into `qa_findings.structured_evidence.investigation_notes`.

Rationale: writing through a file is robust to streaming-output truncation and works whether the agent runs Claude, Codex, or any future runtime. The file path lives inside the worktree so cleanup is automatic if the artifact is missing.

Runtime contract note: the active `RuntimeAdapter.invoke()` signature accepts `prompt`, `system_prompt`, MCP servers, environment, model/runtime args, cwd, and timeout. It does not accept a structured-output schema tied to a file artifact. The active Claude CLI exposes `--json-schema`, but that validates the final printed response in `--print` mode; it does not validate JSON that the agent writes to `./.qa/investigation_notes.json` through file operations after doing investigation work. Treating `--json-schema` as an artifact-file schema hook would validate the wrong payload and could break the existing dispatcher, which reads the file rather than the final response.

Therefore the Investigation Notes Artifact is a portable file contract for all runtimes: the prompt instructs the agent to write plain JSON matching `InvestigationNotes`, and the dispatcher performs strict validation plus tolerant best-effort parsing after the runtime exits. If a future runtime adapter grows an explicit artifact-file structured-output channel, a later change can bind `InvestigationNotes` to that channel without changing the dispatcher persistence contract.

### D4 — Diff snapshot at commit time

After the agent's commit step succeeds and before worktree teardown, the dispatcher runs:

```bash
git -C <worktree> diff --no-color HEAD~1..HEAD --unified=3
```

…parses the unified-diff output into the `{ kind: "meta" | "+" | "-" | " ", text: str }` line shape used by the design, and writes it into `investigation_notes.diff_snapshot`. The snapshot is bounded — if the diff exceeds 10 000 lines, we truncate and append a `{ kind: "meta", text: "... (truncated, N more lines)" }` marker.

Rationale: capturing at commit time means the dossier shows what the agent actually proposed even after the PR is force-pushed or amended later. It also means we don't depend on `gh pr diff --json` at view time.

### D5 — Anonymization invariant: confirmed and instrumented

`anonymize()` + `validate_anonymized()` already gate the PR title and body. The redesign adds two more places where the invariant must hold:

1. Any QA API endpoint that returns content destined for non-operator surfaces must not include `investigation_notes.evidence_lines[].msg` (which may contain raw log content). The Cases API is operator-only and explicitly *does* return evidence_lines. The existing `/api/healing/attempts` endpoint already returns sanitized data and continues to do so.
2. The investigation prompt itself receives anonymized evidence; the agent is then trusted to author `evidence_lines[]` in the notes JSON. The dispatcher does **not** re-anonymize evidence_lines on persistence — the operator sees what the agent saw. (This is the doctrine change: previous wording forbade raw lines anywhere; new wording forbids raw lines only on egress paths.)

A new unit test in `tests/core/qa/test_anonymization_boundary.py` asserts that `anonymize()` is called on every code path that produces GitHub-bound content, and that no code path passes `evidence_lines` directly to `gh pr create` arguments.

### D6 — Retention: 30 days, terminal+14d for closed cases, daily cleanup job

The daily cleanup job runs in the QA module's scheduler (`src/butlers/modules/qa/__init__.py`) at 04:00 UTC by default (config: `[modules.qa].retention_cleanup_hour`). It performs:

```sql
-- Delete evidence_lines (and only evidence_lines) from structured_evidence for findings
-- where the linked healing_attempt is terminal AND closed_at < now() - 14 days,
-- OR the finding is not linked to a healing_attempt and created_at < now() - 30 days.
UPDATE public.qa_findings
   SET structured_evidence = structured_evidence - 'investigation_notes' || jsonb_build_object(
         'investigation_notes',
         structured_evidence->'investigation_notes' - 'evidence_lines'
       )
 WHERE id IN (
   SELECT f.id
     FROM public.qa_findings f
     LEFT JOIN public.healing_attempts h ON h.id = f.healing_attempt_id
    WHERE (
            h.closed_at IS NOT NULL
       AND h.closed_at < now() - INTERVAL '14 days'
          )
       OR (
            f.healing_attempt_id IS NULL
        AND f.created_at < now() - INTERVAL '30 days'
          )
 );
```

Rationale for stripping `evidence_lines` only (not the whole `investigation_notes` block): the narrative fields (`headline`, `hypothesis`, `why_this_fix`, `diff_snapshot`, `counter_evidence`, `blurb_segments`, `claims`) are already anonymized at agent emission time — they have no raw log content. Only `evidence_lines[].msg` carries raw content. Keeping the narrative alive after evidence retires preserves long-term dashboard value without long-term log retention.

A new Prometheus counter `qa_findings_retention_purged_total` tracks deletions per run.

### D7 — KPI definitions

| KPI | SQL source | Notes |
|---|---|---|
| `prs_landed_24h` | `COUNT(*) FROM healing_attempts WHERE status='pr_merged' AND closed_at >= now()-'24 hours'` | Returns int. |
| `mttr_24h_seconds` | `EXTRACT(EPOCH FROM AVG(closed_at - created_at)) FROM healing_attempts WHERE closed_at >= now()-'24 hours' AND status IN ('pr_merged','failed','timeout','unfixable')` | Returns int seconds or null when sample is empty. UI formats as `Xm` or `Xh Ym`. |
| `self_resolved_7d_pct` | `100.0 * SUM(CASE WHEN status='pr_merged' THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN status IN ('pr_merged','unfixable','failed') THEN 1 ELSE 0 END), 0) FROM healing_attempts WHERE closed_at >= now()-'7 days'` | Returns float pct; UI shows as integer `%`. |
| `active_cases_now` | `COUNT(*) FROM healing_attempts WHERE status IN ('dispatch_pending','investigating','pr_open')` | Returns int. UI sub-label: "N awaiting CI, M escalated" computed by partitioning the count. |

All four KPIs are computed in `src/butlers/api/routers/qa.py` inside the `/api/qa/summary` handler and exposed under `kpis: { ... }`. They reuse the existing DB session.

### D8 — Cases resource shape

The dossier renderer wants a per-case shape that the existing endpoints don't quite produce in one round trip. Rather than widening `/api/qa/investigations`, we add a small purpose-shaped resource:

```python
# GET /api/qa/cases?limit=25&sev=high&since=7d
# since accepts 24h, 7d, 30d, or all.
class QaCaseSummary(BaseModel):
    id: UUID                          # the healing_attempt id (canonical case id)
    short_id: str                     # "#218" — derived from id, stable per attempt
    sev: Literal["high", "medium", "low"]
    butler: str
    headline: str | None              # from investigation_notes.headline; None until notes emit
    detected: datetime                # earliest qa_findings.first_seen for this attempt
    age_seconds: int
    state: Literal["detect", "diagnose", "pr", "landed", "escalated"]
    pr_state: Literal["drafted", "open", "merged", "closed"] | None
    pr_url: str | None

# GET /api/qa/cases/:id
class QaCaseDossier(BaseModel):
    case: QaCaseSummary               # same shape as above, extended below
    state_track_stage: str            # for the StateTrack component
    investigation_notes: InvestigationNotes | None  # None until terminal emission lands
    pr: QaPrSummary | None            # see below
    journal: list[QaJournalEvent]     # most recent 50 events; the journal endpoint paginates

class QaPrSummary(BaseModel):
    number: int
    state: str
    title: str
    branch: str
    ci_status: Literal["passing","failing","pending","unknown"]
    additions: int
    deletions: int
    opened_at: datetime
    merged_at: datetime | None
    url: str

# GET /api/qa/cases/:id/journal?cursor=&limit=
class QaJournalEvent(BaseModel):
    id: UUID
    ts: datetime
    step: Literal['flagged','sampled','cross-checked','considered','concluded','drafted','wait','merged','tick','escalated']
    text: str
    detail: str | None
    data: dict
```

The Severity field maps the existing `0..4` integer to `high|medium|low` via:
- `0..1` → high
- `2`    → medium
- `3..4` → low

This map is documented as a single helper in `src/butlers/core/qa/severity.py` and used by both the API and the dashboard rendering. It does not change the underlying integer; it only labels it.

### D9 — Frontend component structure

A new `frontend/src/components/qa/` directory hosts the dossier-specific components, all consuming the Dispatch tokens already in `index.css`:

- `QaKpiStrip.tsx` — 4-cell grid with hairline dividers; reuses `<KpiStrip>` primitives from `frontend/src/components/overview/`.
- `CaseList.tsx` — 320 px rail; rule-separated rows; severity dot + #id + butler + headline + detected/age + PR-state dot.
- `CaseDossierHeader.tsx` — sev/id/butler/detected + StateTrack; H2 sans 500 22px headline.
- `StateTrack.tsx` — mono caps state pipe (`detect — diagnose — pr — landed`) with `escalated` variant.
- `ClaimAnchoredBlurb.tsx` — serif paragraph; per-claim hover linkage drives an `onHover` callback that an `EvidenceLog` row consumes.
- `EvidenceLog.tsx` — mono grid rows for `evidence_lines[]`; rows highlight when the parent's hover callback matches their claim.
- `CounterEvidence.tsx` — small mono table for `counter_evidence[]`.
- `PRPanel.tsx` — PR state chip, title, branch, CI, additions/deletions, "Why this fix" serif italic, embedded `DiffPreview`.
- `DiffPreview.tsx` — line-kind aware diff renderer for `diff_snapshot[]`.
- `PatrolJournal.tsx` — full-width row-grid for journal events.

Page-level shells:
- `frontend/src/pages/QaOverviewPage.tsx` — rewritten as the dossier (rail + body).
- `frontend/src/pages/QaInvestigationsPage.tsx` — rewritten as a Dispatch case index (rule-separated rows of `QaCaseSummary`, no Kanban).
- `frontend/src/pages/QaInvestigationDetailPage.tsx` — rewritten to use the same `CaseDossier` component as `/qa?case=`.
- `frontend/src/pages/QaPatrolDetailPage.tsx` — rewritten with the Dispatch primitives; findings table becomes a rule-separated list.

URL-driven case selection: `/qa?case=<short_id or uuid>` selects that case in the rail. Clicking a case updates the query string via `useSearchParams`.

### D10 — Journal event emitters

| Step | Emitter | Trigger |
|---|---|---|
| `flagged` | `src/butlers/core/qa/triage.py` | A novel finding is persisted with `dedup_reason = null`. |
| `sampled` | (optional) triage | When triage corroborates a fingerprint across two or more sources. Permitted but not required for v1. |
| `cross-checked` | (optional) triage | When triage runs the dispatch-events cross-reference. Permitted but not required for v1. |
| `considered` | investigation agent emission | Each "rejected hypothesis" the agent records; one event per `counter_evidence` entry. |
| `concluded` | investigation agent emission | Single event recorded when the agent finalizes its hypothesis and proposes the fix. |
| `drafted` | `src/butlers/core/qa/dispatch.py` | When the PR is created (status transitions to `pr_open`). |
| `wait` | dispatch.py / PR status poller | When the PR is `pr_open` and CI is still pending on a status check. |
| `merged` | dispatch.py / PR status poller | When the PR transitions to `pr_merged`. |
| `escalated` | dispatch.py | When the attempt transitions to `unfixable` or `failed` with `error_detail` indicating human action required. |
| `tick` | `src/butlers/modules/qa/__init__.py` patrol loop | On each patrol cycle, for every attempt still in `pr_open` or `investigating` whose journal had no new events since the last cycle. |

Every emission goes through a thin helper `record_event(attempt_id, step, text, detail=None, data=None)` in `src/butlers/core/qa/journal.py` that inserts into `qa_investigation_events`.

### D11 — Doctrine update to RFC 0015

A minimal edit to `about/legends-and-lore/rfcs/0015-qa-staffer-discovery-investigation-pipeline.md`:

In the **Non-Goals** section, replace this line:

> - QA does NOT store raw log lines in `qa_findings`; only computed fingerprints and sanitized summaries.

with:

> - QA does NOT leak un-anonymized log content beyond the private operator dashboard. PR titles, PR bodies, branch commit messages, and any externally-egress paths SHALL pass through `anonymize()` + `validate_anonymized()`. Raw log lines MAY be stored on `qa_findings.structured_evidence.evidence_lines[]` strictly to support the internal dossier UI.

And add, under §D7 (Dashboard Surfacing), a new bullet:

> **Retention.** Raw evidence stored on `qa_findings.structured_evidence.evidence_lines[]` is purged after 30 days. Cases still in non-terminal state are exempt until 14 days past their terminal transition. The cleanup job retains the narrative payload (`headline`, `hypothesis`, `why_this_fix`, `diff_snapshot`, `counter_evidence`, `blurb_segments`, `claims`) indefinitely; only `evidence_lines[]` is purged.

This is a doctrine clarification, not a contract break — anonymization-on-egress was already in spirit; we're now naming it explicitly and granting the inverse internal latitude.

## Risks / Trade-offs

- **[Risk] Agent emits malformed `investigation_notes` JSON.** → Mitigation: best-effort field-level parser falls back to the un-anchored fields; partial emissions still feed the dossier. Counter `qa_investigation_notes_parse_total{status="failed|partial|ok"}` tracks reliability; we set an alert at >10% failure rate over 24h.
- **[Risk] Hard cut breaks ad-hoc operator muscle memory.** → Mitigation: page header eyebrow ("QA Staffer · dossier") and the case-list rail make the new shape self-explanatory; we keep the URL paths unchanged so bookmarks still land.
- **[Risk] Cases API returns case `headline` as null until terminal emission, leaving the dossier partially blank for in-flight cases.** → Mitigation: when `investigation_notes.headline` is null, fall back to `finding.event_summary` (already anonymized). This gives every case *some* headline.
- **[Risk] Raw log lines in `evidence_lines[]` leak into the API via a bug.** → Mitigation: `tests/api/test_qa_anonymization_boundary.py` proves the boundary — every code path that builds PR-bound content goes through `anonymize()`; the Cases API is the only operator-facing surface and is documented as such. Add a CI lint that flags any new endpoint returning `evidence_lines` without an explicit acknowledgement in its docstring.
- **[Risk] `qa_investigation_events` grows without bound.** → Mitigation: the retention job sweeps events with `attempt_id` whose attempt is terminal + 30d. Events for active investigations are exempt. Index on `(attempt_id, ts)` keeps queries cheap regardless of total volume.
- **[Trade-off] `tick` events at patrol cadence increase row count.** → Accept: ~6 rows per active case per hour × 5–10 active cases × 24h = a few thousand rows per day worst case. Trivial for PG.
- **[Trade-off] Diff snapshot stored as JSON array of line objects rather than raw unified-diff text.** → Accept: makes the renderer simple and the truncation policy explicit. Recoverable via concat if we ever need raw form.

## Migration Plan

1. Land the OpenSpec change (this directory) and update RFC 0015 doctrine in the same PR. No code changes yet.
2. Add the `qa_investigation_events` Alembic migration. Verify with a no-op test that the table exists in fresh and existing DBs.
3. Implement the journal helper (`src/butlers/core/qa/journal.py`) and wire the `flagged` / `drafted` / `wait` / `merged` / `escalated` / `tick` emitters into existing call sites. No agent contract change yet.
4. Implement the `InvestigationNotes` Pydantic model and the parser in `src/butlers/core/qa/notes.py`. Update the investigation agent prompt to instruct emission of `./.qa/investigation_notes.json` through the portable file contract. Update `dispatch.py` to read, parse, and persist the file before worktree teardown.
5. Implement the diff snapshot capture in `dispatch.py`.
6. Add the Cases API endpoints (`/api/qa/cases`, `/api/qa/cases/:id`, `/api/qa/cases/:id/journal`) and extend `/api/qa/summary` with the KPI block.
7. Implement the retention cleanup job and schedule it daily at 04:00 UTC.
8. Frontend: introduce `frontend/src/components/qa/` components and the new hooks. Rewrite the four page files. Hard cut — the old page components are deleted in the same commit.
9. Roll to dev (`tzeusy.parrot-hen.ts.net/butlers-dev/qa`); smoke with `/butler-qa-invoke` canary. Confirm a forced patrol surfaces `flagged` and `tick` events end to end and that a real investigation reaches `merged` with a populated `investigation_notes`.
10. Promote.

Rollback: revert the frontend page commits to restore the previous renderer (the API endpoints remain functional; nothing structural removes them). The `qa_investigation_events` table is additive and can stay even if the UI rolls back — the data has no consumers beyond the new pages.

## Open Questions

- Should `sampled` / `cross-checked` journal events be required in v1 from triage, or remain optional? Current design defers to optional; revisit if operators ask for it.
- Should `/api/qa/cases/:id/journal` ship as SSE in v1 or land in a follow-up? Current design says polling at patrol cadence is fine; SSE deferred.
- Should `headline` fall back to `finding.event_summary` or be left null until terminal emission? Current design falls back, but we could ship without a fallback and let the rail show the case as "diagnosing…".
