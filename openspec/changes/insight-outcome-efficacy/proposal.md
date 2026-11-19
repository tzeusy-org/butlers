## Why

The insight broker learns whether a nudge helped from one proxy: any owner message to any butler
within 60 minutes of a delivery marks it engaged (`check_and_update_engagement`). That cannot tell a
nudge that changed something from one that resolves itself, such as an autopaid bill, so the owner
keeps being interrupted about things that would have happened anyway, and nudges that work can be
muted by a noisy proxy. Vision's success markers ask the system to reduce frequency when the owner
disengages and to measure success by the mental labour absorbed (bu-s11n0s.15, JARVIS pursuit
run 16).

Premise-bound insights already name the fact they assert (core_255), and the broker already holds
some candidates back for budget or context, which are natural holdouts. Comparing how often and how
fast the premise resolves with and without the nudge answers the question honestly, without
withholding anything deliberately.

## What changes

Slice 1 (this change, observe-only, no delivery change):

- `core_353` adds `public.insight_outcomes` (one row per terminal premised candidate, keyed by
  candidate id with no foreign key so it outlives the candidate pruner) and
  `public.insight_class_efficacy` (the per-class verdict rollup slice 2 fills). Switchboard only,
  fenced by row level security.
- `butlers.tools.switchboard.insight.outcomes`: enrollment with an exposure taxonomy (treated:
  delivered, coalesced; natural holdouts: held by budget or context; excluded: withdrawn,
  shadowed, held by an endpoint custody hold, unverified, failed) and a sweep that records once
  whether the premise resolved, when, and whether that time is exact or only observed by the
  sweep. Unknown reads settle `unmeasurable`, never "ineffective".
- A daily deterministic `insight_outcome_sweep` Switchboard job at 05:52 UTC, before the 06:15
  delivery window.

Slice 2 (observe-only, no delivery change):

- After each sweep, the job rolls settled rows up into `public.insight_class_efficacy` per class
  and priority band (effective, self_resolving, ignored, unmeasured; at least five per arm,
  compared only within one band, labelled observational).
- `GET /api/attention/efficacy` returns the latest row per class and band with the degraded
  envelope, and the Trust Console's attention-ledger panel lists the rows. No model-reachable tool
  (RFC 0038 regroom).

## Deferred

- Slice 3: an owner opt-in `outcome_shaping` setting under which a measured verdict overrides the
  click proxy; priority at or above the urgent threshold is never shaped. Needs an owner decision.
- Slice 4: one digest line per class transition with keep/quiet doors.
- Slice 5: retire the 60-minute any-message engagement mark for categories the outcome signal
  covers.

## Conformance

The added requirement conforms to the bead's adopted design (bu-s11n0s.15 regroom, 2026-10-16) and
to RFC 0011 as RFC 0038 keeps it ("Remains; candidates carry taint"). It is deterministic, adds no
route, session, delegation or model-reachable tool, and changes no delivery, so it is conforming
text under the recorded owner decision for conforming doctrine and spec text.

Beyond the adopted design, the requirement adds conservative refinements that only ever exclude
an observation or settle it as unknown, never count it in an arm:

- three excluded exposures the design does not name: `held_custody` (delivery refused by an
  endpoint custody hold), `held_unverified` (the premise could not be checked at send) and
  `failed` (the transport gave up);
- the SHALL line that a custody-refused delivery is `held_custody`, never a holdout, because a
  lost-device window says nothing about the owner's attention;
- a probe flip first observed after the window closed is never `resolved`: it is `persisted`
  when a check at or after the window end saw the premise holding, otherwise `unmeasurable`.

The adopted design names the verdicts, the five-per-arm floor and the priority bands but not the
cut points. Slice 2 picks them and keeps them in code constants, not spec text: `effective` needs
a 20-point resolve-rate gap or, at a comparable rate, a treated median at most half the holdout's;
`self_resolving` needs a comparable rate and a holdout rate of at least 60%; the rollup reads 90
days of settled rows. They only label an observation and change no delivery.

## Impact

- Code: `alembic/versions/core/core_353_insight_outcomes.py`,
  `roster/switchboard/tools/insight/outcomes.py`, `src/butlers/scheduled_jobs.py`,
  `roster/switchboard/butler.toml`, `src/butlers/db_authority/grants.py`.
- Docs: `docs/data_and_storage/schema-topology.md`, `docs/operations/data-retention.md`.
- Slice 2 code: `roster/switchboard/tools/insight/outcomes.py` (`compute_class_efficacy`),
  `src/butlers/api/routers/attention_ledger.py`, `src/butlers/api/models/attention_ledger.py`,
  `frontend/src/components/approvals/attention-ledger-panel.tsx`; docs
  `docs/butlers/switchboard.md`.
- Specs: `proactive-insight-engine` gains two requirements.
