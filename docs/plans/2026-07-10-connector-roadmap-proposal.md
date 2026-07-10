# Connector Roadmap Proposal — targeted capture for uncovered life domains

**Date:** 2026-07-10
**Bead:** bu-dyq22 (proposal deliverable, gated behind bu-shk4p — released 2026-07-05)
**Status:** Proposal only. **No connector is implemented under this bead.** The
owner picks which candidates become implementation epics.
**Pattern:** follows the `adding-connectors-and-modules` skill's four-component
model (account registry → module → connector → dashboard) and the
`about/heart-and-soul` doctrine gates (§7).

---

## 0. tl;dr

The 2026-07-05 ingestion audit finding that motivated this bead — "existing
channels skew heavily to ambient home telemetry" — is correct, but the fix is
**not** four greenfield connectors. Every one of the four candidate domains
already has *partial* infrastructure in the tree. The real work per candidate is
narrower and cheaper than "add a connector," and in two cases is a **capture
path**, not a connector at all.

| # | Candidate | What already exists | The actual gap | Recommendation |
|---|---|---|---|---|
| 1 | **Financial transactions** | Email scraping (Gmail connector → finance LLM sessions); a real CSV import pipeline (`data_import.py`: Chase/Amex/Capital One/generic); `finance.transactions.source` enum already has `csv_import`/`api` | No *drop-folder / auto-ingest* — CSV import is tool/blob-triggered; no SG-bank statement path | **Build (P1): statement drop-folder + PDF→CSV extraction.** SGFinDex/aggregators are a deferred side-channel (§5.1) |
| 2 | **Browsing / screen-time** | **ActivityWatch connector already shipped** (bu-whhll.6: `connectors.activitywatch_events`, `chronicler/adapters/activitywatch.py`) | URL-level browser history is *not* captured (window titles only); multi-machine deploy pending | **Extend (P2): browser-history layer + wider deployment.** Not greenfield |
| 3 | **Food / nutrition** | `health.meals` table + health butler `meal_log` tool + chronicler `meals.py` `eating_event` adapter; telegram interactive mode | Capture is freeform-text-only; no low-friction **photo→log** flow, no app-export connector | **Build (P2): telegram `/meal` photo-to-log module.** Storage/adapter spine already there |
| 4 | **Work signals** | `metadata_only` ingestion tier + `switchboard.email_metadata_refs` store; GitHub already arrives as bulk email | No **daily rollup/digest** layer over metadata; Slack is *deferred* (RFC 0018), not skip-ruled | **Build (P3): metadata-only work digest into Chronicler.** Reuses the telemetry-distillation rollup pattern |

**Recommended sequencing:** 1 → 3 → 2 → 4 (see §6). Candidate 1 is the highest
value-per-effort (finance is a live butler running on the weakest ingestion
substrate). Candidate 4 is lowest priority and most doctrine-constrained.

**One premise correction the owner should note:** the audit's "165,259 skip"
mountain is Home Assistant + OwnTracks + Spotify telemetry, correctly
`skip`-routed by design — **not** suppressed work signals
(`docs/plans/2026-07-06-telemetry-distillation-design.md:10-12,67`). Work
signals are absent because no connector emits them, not because a skip rule eats
them. This proposal adds capture where it is genuinely missing; it does not
re-open a noise firehose.

---

## 1. Grounding: the coverage skew, precisely

The 100-day audit behind gate bu-shk4p: 171,619 ledger events + ~800k
pre-filtered; **96% Home Assistant telemetry**; triage 165,259 skip / 6,052
pass_through / 233 metadata_only / 75 rule-routed. Refreshed 2026-07-06 figures
(170,378 skip / 6,078 pass_through / 234 metadata_only / 148 route_to) are in
`docs/plans/2026-07-06-switchboard-rule-promotion-design.md:17-20`.

What this means for a *connector* roadmap: the volume skew is not a routing bug
(the sibling telemetry-distillation and switchboard-rule-promotion designs own
the "distill / promote what we already ingest" problem). It is a **capture
coverage** skew — the *domains* a person lives in are unevenly sensed. Ambient
home + location + music are over-sensed; money, attention, food, and work are
under-sensed. This proposal is strictly the supply-side complement: where should
new *capture* land, and at what cost.

**Existing external-service capture surfaces** (for calibration — what "solid"
looks like): Gmail, Telegram (bot + user client), Home Assistant, OwnTracks,
Spotify, Steam, Google Health, ActivityWatch, Google Calendar. The bar RFC 0018
sets is "existing connectors solid before breadth"
(`about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md:213-216`)
— each candidate below is weighed against that bar in its Privacy/Doctrine note.

---

## 2. Evaluation framework

Each candidate is scored on five axes, plus a doctrine gate:

- **Value hypothesis** — what owner-visible capability it unlocks, and which
  existing butler/surface consumes it.
- **Data source options** — with **SG-specific** availability where relevant.
- **Ingestion shape** — connector (background ingest) vs module (LLM tools) vs
  drop-folder (owner-initiated file) vs chronicler adapter (projection only).
  Per the skill: *read-only query → module; background activity → connector +
  registry; owner-initiated file → drop-folder.*
- **Effort** — S / M / L, expressed in the four-component vocabulary.
- **Privacy / security** — against the network-boundary doctrine (§7). The trust
  boundary is **localhost + Tailscale + egress firewall**, not an app key
  (`about/heart-and-soul/security.md`); new credentials follow the three-tier
  authority model (Tier 1 `butler_secrets` for ecosystem creds; Tier 2 secured
  `entity_info` on a companion entity for per-account tokens).
- **Operational cost** — poll frequency, LLM calls (RFC 0014 §D5: no per-event
  LLM), storage, third-party fees.

---

## 3. Candidate 1 — Financial transactions

### 3.1 Value hypothesis

Finance is a **live butler** (`roster/finance/`) whose entire ingestion runs on
the *weakest* substrate in the system: bank/receipt **emails** scraped inside
LLM sessions (`roster/finance/MANIFESTO.md`; `AGENTS.md:5,62`). Email capture is
lossy (not every transaction emails; promotional receipts are noisy; extraction
is LLM-fuzzy). A structured transaction feed would raise finance from
"best-effort inbox archaeology" to "authoritative ledger," directly serving the
manifesto's promise and the anomaly/subscription/budget tools that already exist
but are starved of complete data.

### 3.2 Data source options (SG-specific)

| Source | SG availability | Verdict |
|---|---|---|
| **Bank CSV export** | OCBC exports CSV (business accounts); **DBS is PDF-only**, no CSV | Partial — needs a PDF→CSV step for DBS/POSB |
| **Bank PDF statement → parse** | Community parsers cover DBS/POSB, UOB, OCBC, SCB (e.g. `exportsg`, `dbs-transaction-parser`) | **Viable** — proven approach, owner-initiated |
| **SGFinDex** (national data exchange) | Singpass-gated, 15 institutions (DBS/POSB, OCBC, UOB, Citi, HSBC, StanChart, SGX CDP, insurers, CPF/HDB) | **Deferred** — see §5.1 |
| **Open-banking aggregator** (Finverse, Lunch Flow; Plaid/Tink/TrueLayer thin in SG) | B2B/enterprise, paid, licensing | **Deferred** — cost + not personal-use |

**Finding:** there is no free, personal-use, transaction-level *API* for SG
retail banks. The realistic feed is **the monthly statement** (CSV where the
bank offers it, PDF otherwise), which the owner already receives.

### 3.3 Ingestion shape — **drop-folder** (not a connector)

The skill's decision tree points at a drop-folder: owner-initiated file, not a
background poll of a remote service. The CSV *parsing* engine already exists
(`roster/finance/tools/data_import.py` — format auto-detection, 500-row batches,
tiered dedup `roster/finance/migrations/005_add_csv_dedup_index.py`). (The former
`import_batches` audit table was dropped as verified-dead in migration
`007_drop_import_batches.py`, so an import-status surface would be new work, not
reuse.) The
missing piece is a **watched-directory trigger** (grep confirms no `drop_folder`
/ watch-folder anywhere today) plus a **PDF-statement→CSV extraction** front-end
for DBS-style PDF-only banks.

Proposed shape:
- A blob/drop location (reuse the existing blob store — `import_transactions`
  already reads by `storage_ref`) monitored by a lightweight connector or
  scheduled job.
- New file → detect CSV vs PDF → (PDF) run a bank-statement table extractor →
  feed the existing `import_transactions` path.
- No new transaction schema — lands in `finance.transactions` with
  `source='csv_import'` and the existing Priority-3 dedup
  `(account_id, posted_at, amount, merchant)` (RFC 0012).

### 3.4 Effort — **M**

- Account registry: **none** (accounts already modelled in `finance.accounts`).
- Connector/job: **S–M** — a directory/blob watcher + the PDF-extraction step is
  the only genuinely new code; CSV parsing is done.
- Module: **none** (import tools exist).
- Dashboard: **S** — surface a drop/upload target + import progress/error status
  (a bulk endpoint `POST /transactions/bulk` already exists; the old
  `import_batches` audit table is gone, so the status surface is new).

### 3.5 Privacy / security

- Statements are highly sensitive but stay **local**: drop-folder ingestion adds
  **zero new egress** and **zero third-party credential** — a strict positive
  under the network-boundary doctrine (no external API, nothing to firewall).
- PDF extraction must run **locally** (deterministic parser or local model), not
  a cloud OCR service, to keep statement content inside the trust boundary.
- No new secrets tier engaged.

### 3.6 Operational cost

Near-zero: no poll, no external API, no per-event LLM (parsing is
deterministic). Storage is bounded (statements are small, monthly).

### 3.7 Epic decomposition sketch (bead titles only)

- Finance statement drop-folder: watched blob location + auto-detect + route to existing `import_transactions`
- PDF bank-statement → CSV extraction (DBS/POSB/UOB/OCBC/SCB), local-only, no cloud OCR
- Dashboard: statement drop/upload target + import progress & error surface (new; the `import_batches` audit table was dropped in migration 007)
- Reconciliation pass: dedup imported rows against existing email-sourced transactions (RFC 0012 tiers)
- (Deferred, owner-gated) SGFinDex balance-snapshot side-channel spike — see §5.1

---

## 4. Candidate 2 — Browsing / screen-time

### 4.1 Value hypothesis

Feeds Chronicler workday visibility (complements the shipped occupation work,
bu-whhll.10). Attention/screen data is the strongest deterministic corroborator
for "was the owner working," which the `occupation_inferred` adapter currently
leans on thinly (desk-Spotify is its main weak signal today).

### 4.2 What already exists — **most of this is shipped**

**The ActivityWatch connector already landed** (bu-whhll.6, PR #2922):
`connectors.activitywatch_events` + `src/butlers/chronicler/adapters/activitywatch.py`
projecting screen/app-focus into Chronicler. This candidate is therefore **not
greenfield** — it is coverage extension.

### 4.3 The actual gap

1. **URL-level browser history is not captured.** ActivityWatch records active
   *window/app titles*, not the browsing graph (which domains, how long, what
   category). A browser-history layer (extension or the ActivityWatch web-watcher
   companion) would add per-domain attention — the difference between "Chrome was
   focused 3h" and "3h split across docs, GitHub, and news."
2. **Multi-machine deployment is pending** — the work laptop is gated on employer
   policy (`bu-pr47l`, open). Coverage is currently single-machine.

### 4.4 Ingestion shape

- Browser history: a **connector** (the ActivityWatch aw-watcher-web companion,
  or a browser-history reader) → extend `connectors.activitywatch_events` or a
  sibling table → a chronicler adapter for domain-category episodes. Reuses the
  existing connector; no new account registry (ActivityWatch is local, keyless).
- Category enrichment (domain → work/play/social/news) is a **deterministic
  mapping** table, not an LLM call.

### 4.5 Effort — **S–M**

Small relative to the others because the connector, table, and adapter pattern
already exist. The new work is the web-watcher wiring + a domain-category map +
one adapter. Multi-machine is deployment/config, not code.

### 4.6 Privacy / security

- Browser history is **maximally sensitive** and stays **local** (ActivityWatch
  is a localhost agent; no cloud). No new egress, no third-party credential.
- Recommend a **domain-allowlist or category-only projection** option so the
  Chronicler layer can store "3h in the *work* category" without persisting the
  full URL stream if the owner prefers — an owner toggle, defaulting to
  category-only.
- Keychain caveat: browser history files are OS-locked while the browser runs;
  the reader must handle locked-DB gracefully (skip, not crash).

### 4.7 Operational cost

Low: local agent already running; incremental poll of a local SQLite/JSON store;
deterministic categorisation; no external calls.

### 4.8 Epic decomposition sketch

- ActivityWatch web-watcher companion: capture per-domain browsing into `connectors.activitywatch_events` (or sibling table)
- Domain → category deterministic map + chronicler browsing-category adapter
- Owner privacy toggle: category-only vs full-URL retention (default category-only)
- Multi-machine ActivityWatch rollout tracking (depends on `bu-pr47l` employer-policy gate)

---

## 5. Candidate 1 addendum — SGFinDex & aggregators (why deferred)

### 5.1 SGFinDex

SGFinDex is national public infrastructure: Singpass-gated, per-pull consent, and
it explicitly **"neither stores nor reads"** the data — it brokers a one-shot
transfer. Two hard constraints make it wrong for a butler *transaction feed*:

1. **Snapshot, not stream.** It returns *balances and positions* (deposits,
   credit-card balances, loans, insurance, investments, CPF, HDB) — not a
   line-item transaction history. Useful for net-worth reconciliation, useless as
   a spend ledger.
2. **Singpass-in-the-loop + institutional onboarding.** Every pull needs an
   interactive Singpass consent (no background polling — violates the
   connector-as-background-poller model), and onboarding via the Developer &
   Partner Portal (JWKS endpoint, service-provider registration) is oriented to
   regulated apps, not a single-user hobby daemon.

**Verdict:** a *possible* low-frequency **balance-snapshot side-channel** (manual,
owner-triggered net-worth refresh) — file as a spike, not a v1 connector. It does
not solve the transaction-capture gap.

### 5.2 Aggregators (Finverse / Lunch Flow / Plaid / Tink / TrueLayer)

SG retail-bank coverage exists mainly through B2B aggregators (Finverse, Lunch
Flow); Plaid/Tink/TrueLayer coverage is thin. All are enterprise/paid with
licensing overhead and introduce a standing external dependency holding bank
credentials. Against the "one user, one machine, minimise external trust"
doctrine and the RFC 0018 "solidify before breadth" gate, this is **deferred** —
revisit only if the drop-folder path proves insufficient.

---

## 6. Candidate 3 — Food / nutrition

### 6.1 Value hypothesis

Nutrition is a genuine capture blind spot, but **the storage and projection spine
already exists**: `health.meals` table, health butler `meal_log`/`meal_history`/
`nutrition_summary` tools (`roster/health/tools/diet.py`), and a chronicler
`meals.py` adapter projecting `eating_event` point events. What is missing is a
**low-friction owner capture path** — today a meal is logged only if the owner
types a freeform message the health butler happens to classify as food.

### 6.2 Data source options

| Option | Shape | Notes |
|---|---|---|
| **Telegram `/meal` photo-to-log** | Module + telegram bot command | Owner photographs the plate; a bounded LLM **vision** call estimates dish + rough macros → `meal_log`. One call per capture (owner-initiated), not per-event ambient — RFC 0014 §D5 compliant |
| **Telegram text quick-log** | Already possible | Health butler processes telegram interactively (`roster/health/AGENTS.md:87-93`); a `/meal chicken rice` shortcut formalises it |
| **App export** (MyFitnessPal / Cronometer / Apple Health nutrition) | Drop-folder / connector | Structured but requires the owner to already use a tracking app; export cadence is manual |

### 6.3 Ingestion shape — **module** (telegram command), optional drop-folder for app export

The natural shape is a **module** exposing a `/meal` telegram command (photo or
text) that calls the existing `meal_log`. This is the skill's "read-write module,
no connector" case — no background ingestion, no account registry. App-export is
a secondary drop-folder path if the owner uses a tracker.

### 6.4 Effort — **S–M**

- Storage/adapter: **none** (exist).
- Module: **S–M** — a telegram command handler + a bounded vision-estimation
  prompt + mapping its output onto `meal_log`'s existing fields.
- Dashboard: **none required** (health butler already has diet endpoints).

### 6.5 Privacy / security

- Meal content is `sensitive` (already classified so in `meals.py`). Photos are
  personal; the vision call sends an image to the LLM provider — this is the
  **one candidate with a real new egress consideration**. It is owner-initiated
  and per-capture (not ambient), so it is bounded and consensual, but the
  proposal should make the vision call **opt-in** and note the image leaves the
  trust boundary to the model API (consistent with how all LLM sessions already
  send content out).
- No new persistent credential (reuses the existing telegram bot + LLM keys,
  Tier 1 `butler_secrets`).

### 6.6 Operational cost

One bounded vision LLM call per meal capture (owner-paced, low volume). No poll.
Photo storage in the existing blob store.

### 6.7 Epic decomposition sketch

- Telegram `/meal` command: photo + optional caption → bounded vision estimate → `meal_log`
- Vision meal-estimation prompt: dish identification + rough macro estimate, opt-in, single bounded call
- Text quick-log shortcut `/meal <description>` formalising the existing interactive path
- (Optional) App-export drop-folder: MyFitnessPal / Cronometer / Apple Health nutrition CSV → `health.meals`

---

## 7. Candidate 4 — Work signals (metadata-only digest)

### 7.1 Premise correction (important)

The bead framed GitHub/Slack as "deliberately skip-ruled for routing-noise
reasons." The precise reality:

- **Slack is *deferred*, never built** — RFC 0018 lists it P2 ("Only valuable
  for Slack-using individuals … workspace-scoped OAuth and enterprise barriers
  limit reach",
  `about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md:91`).
  It exists only as an enum placeholder in `SourceChannel`/`SourceProvider`.
- **GitHub is not a connector at all** — it arrives as *email* through Gmail and
  is the codebase's canonical example of automated-sender noise
  (`docs/plans/2026-07-06-switchboard-rule-promotion-design.md:100-103,204-211`:
  "three near-identical GitHub Actions CI-failure notifications … within about
  two minutes"). It is caught, if at all, by **generic** bulk-mail rules
  (`List-Unsubscribe → metadata_only`, `Auto-Submitted → skip`;
  `docs/architecture/pre-classification-triage.md:92-94`), not a GitHub-named
  rule.

So the "noise firehose" rationale is real and must be respected, but there is no
single skip rule to override — the task is to build a **digest that never
re-enters the full-fanout path**.

### 7.2 Value hypothesis

Make workdays visible in Chronicler *without* routing individual GitHub/CI/Slack
events to butlers. A once-daily metadata rollup ("14 PRs touched, 3 repos, 2
CI-failure bursts, active 09:12–18:40") is a strong deterministic workday
corroborator for the occupation adapter — the same role the telemetry-distillation
rollup plays for home telemetry.

### 7.3 What already exists vs the gap

- **Exists:** the `metadata_only` ingestion tier and its store
  `switchboard.email_metadata_refs` (slim envelope, subject-only, **no LLM
  classification**, 90-day retention;
  `docs/connectors/gmail-ingestion-policy.md:57-99`). GitHub notification email
  can already land here.
- **Missing:** any **daily aggregation/digest** over that store. The
  telemetry-distillation design states the identical gap for telemetry —
  "nothing rolls episodes up into daily/weekly aggregates … that materialization
  layer does not exist at all today"
  (`docs/plans/2026-07-06-telemetry-distillation-design.md:16-17`) — and its fix
  (a nightly deterministic rollup + **one bounded LLM call per day** to narrate,
  §3.3/§3.5) is exactly the pattern a work-signals digest should mirror.

### 7.4 Ingestion shape — **chronicler adapter + nightly rollup**, riding existing tiers

- For GitHub-via-email: ensure it routes `metadata_only` (generic bulk rules
  already tend to), then a **nightly job** aggregates `email_metadata_refs`
  work-sender rows into a `work_digest` episode. No new connector.
- For Slack (only if the owner wants it): a **new connector** using Socket Mode
  (tailnet-compatible per RFC 0018) that emits **only** aggregate/metadata events
  — never full-message fanout. This is the heaviest sub-option and should be
  gated separately.

### 7.5 Effort — **S** (email-digest) / **L** (Slack connector)

The email-metadata digest is small: a nightly deterministic aggregator + one
adapter + one bounded narration call. The Slack connector is a full
four-component build **and** must clear the RFC 0018 deferral bar — recommend
**not** doing Slack now.

### 7.6 Privacy / security & doctrine gates

- The digest must **not** mint standing skip/metadata rules unattended — RFC 0021
  owner-confirm ratchet (`switchboard-rule-promotion-design §3, :291-334`).
- Must **not** re-open per-event fanout — the whole point is metadata-only.
- Slack sub-option must clear RFC 0018 "existing connectors solid before breadth"
  (:213-216). Given finance (candidate 1) is the weakest live substrate, breadth
  into Slack is hard to justify now.
- No new egress for the email-digest path (data already ingested); Slack Socket
  Mode would add one tailnet-compatible outbound connection + a Tier 1 token.

### 7.7 Operational cost

Email-digest: one bounded LLM call/day (narration only), one nightly job.
Slack: standing websocket + poll — the reason it is deferred.

### 7.8 Epic decomposition sketch

- Nightly work-signals digest: aggregate `email_metadata_refs` work-senders → `work_digest` Chronicler episode
- Bounded daily narration LLM call over the digest (RFC 0014 §D5: one call/day, never per-event)
- Work-sender classification map (GitHub/CI/Jira/etc. senders → work category), owner-confirmed per RFC 0021
- (Deferred, separate gate) Slack Socket-Mode *metadata-only* connector — requires clearing RFC 0018 breadth bar

---

## 8. Comparison table & recommendation ranking

| # | Candidate | Value | Effort | New egress? | New credential? | Doctrine friction | Rank |
|---|---|---|---|---|---|---|---|
| 1 | Financial drop-folder + PDF→CSV | **High** (live butler, weakest substrate) | M | **None** (local) | None | Low — strict positive | **1** |
| 3 | Food photo-to-log (telegram module) | Medium–High (real blind spot, spine exists) | S–M | Vision call (opt-in, owner-paced) | None (reuse) | Low | **2** |
| 2 | Browsing URL layer (extend ActivityWatch) | Medium (workday corroborator) | S–M | **None** (local) | None | Low | **3** |
| 4a | Work-signals email digest | Medium (workday visibility) | S | None | None | Medium (RFC 0021 ratchet) | **4** |
| 4b | Slack metadata connector | Low–Medium (Slack-users only) | L | Tailnet socket | Tier 1 token | **High** (RFC 0018 breadth bar) | **Defer** |
| 1b | SGFinDex / aggregator | Low (snapshot, not stream) | L | External API | Tier 1/2 | High (Singpass loop, B2B) | **Defer** |

### Recommendation

**Do, in order:**

1. **Candidate 1 — financial statement drop-folder + local PDF→CSV.** Highest
   value-per-effort: a live butler running on the lossiest substrate, fixable
   with *zero new egress and zero new credentials* by wrapping the CSV pipeline
   that already exists in a watched-directory trigger. This is the single
   clearest win.
2. **Candidate 3 — telegram `/meal` photo-to-log.** Real blind spot; the
   `health.meals` storage + adapter + tools already exist, so the work is one
   module + a bounded opt-in vision call.
3. **Candidate 2 — browser-history layer on the shipped ActivityWatch
   connector.** Cheap coverage extension that materially strengthens the
   occupation adapter; entirely local.
4. **Candidate 4a — metadata-only work digest.** Do the *email-metadata* digest
   (small, reuses `email_metadata_refs` + the telemetry-distillation rollup
   pattern); it makes workdays visible without re-opening the firehose.

**Defer:** SGFinDex/aggregators (snapshot-only, Singpass/B2B friction) and the
Slack connector (RFC 0018 breadth bar; finance substrate should be solid first).

### Doctrine alignment (§7 of the skill)

- **Modules only add tools; connectors own transport** — all four respect the
  layer boundary; none touch core.
- **Network boundary** — candidates 1, 2 add *no* egress; 3 adds one opt-in
  owner-paced vision call; 4a adds none. Only deferred options (SGFinDex,
  aggregators, Slack) introduce standing external dependencies — which is a
  reason they are deferred.
- **v1 scope** — candidates 1–3 harden/extend *existing* capture surfaces (in
  the spirit of RFC 0018's "solidify before breadth"); candidate 4b (Slack) is
  the only genuine breadth expansion and is deferred.
- **No per-event LLM** (RFC 0014 §D5) — the food vision call is owner-initiated
  per-capture; the work-digest narration is one bounded call/day. Both compliant.

---

## 9. What this proposal deliberately does not do

- Does **not** implement any connector, module, migration, or schema change.
- Does **not** create the implementation beads — the sketches in each §.7/§.8 are
  *titles only*; the coordinator/owner creates the epics after selecting
  candidates.
- Does **not** re-litigate the telemetry `skip` volume — that is owned by the
  sibling telemetry-distillation and switchboard-rule-promotion designs; this is
  purely the new-*capture* complement.
- Does **not** commit to Slack or any third-party financial aggregator; both are
  explicitly deferred with rationale.

---

## Appendix: source citations

- Connector pattern: `.claude/skills/adding-connectors-and-modules/SKILL.md`
- Finance ingestion: `roster/finance/MANIFESTO.md`, `roster/finance/AGENTS.md`,
  `roster/finance/tools/data_import.py`, `roster/finance/migrations/005_add_csv_dedup_index.py`,
  `about/legends-and-lore/rfcs/0012-finance-transaction-data-model.md`
- ActivityWatch (shipped): `src/butlers/connectors/` + `src/butlers/chronicler/adapters/activitywatch.py` (bu-whhll.6, PR #2922); multi-machine gate bu-pr47l
- Food spine: `health.meals`, `roster/health/tools/diet.py`, `src/butlers/chronicler/adapters/meals.py`, `roster/health/AGENTS.md:87-93`
- Work signals: `about/legends-and-lore/rfcs/0018-connector-scope-and-deferral-rationale.md:91,213-216`,
  `docs/plans/2026-07-06-switchboard-rule-promotion-design.md:17-20,100-103,204-211,291-334`,
  `docs/connectors/gmail-ingestion-policy.md:57-99`,
  `docs/architecture/pre-classification-triage.md:92-94`,
  `openspec/specs/ingestion-policy/spec.md`
- Rollup pattern: `docs/plans/2026-07-06-telemetry-distillation-design.md:10-17,67,299-387`
- Doctrine: `about/heart-and-soul/security.md`, `about/heart-and-soul/vision.md`,
  `about/legends-and-lore/rfcs/0014` (§D5, no per-event LLM)
- SG financial data (web, 2026-07-10): SGFinDex (mas.gov.sg/development/fintech/sgfindex,
  sgfindex.gov.sg), OCBC/DBS/UOB export & aggregator coverage (openbankingtracker.com,
  community parsers `exportsg` / `dbs-transaction-parser`)
